"""앱의 동작(초기화, 지원, 투표, 마감, 배정안 저장, 확정)을 DB 트랜잭션으로 처리한다.

모든 함수는 결과 안내 문자열을 돌려주며, 첫 단어는 항상 승인, 대기, 거절, 막힘 가운데 하나다.
같은 팀에 대한 처리는 팀 행 잠금(SELECT ... FOR UPDATE)으로 한 번에 하나씩 하고,
DB의 유일성 제약(tf_members 기본 키와 student_id unique, 대기 지원 부분 유일 인덱스)이 마지막 안전장치다.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime

import psycopg
from psycopg import errors as pg_errors

from . import db, rules
from .auth import hash_password
from .config import ADMIN_STUDENT_NO, ROLES, STUDENT_DEFAULT_PASSWORD, TEAM_MAX, now_kst
from .rules import Student

APPROVED, PENDING, REJECTED, BLOCKED = "승인", "대기", "거절", "막힘"


def _msg(kind: str, text: str) -> str:
    # 같은 문구가 이어져도 바뀐 것을 알 수 있게 한국 시각을 붙임 (첫 단어는 그대로)
    return f"{kind}: {text} ({now_kst():%H:%M:%S})"


def _role_order(role: str) -> int:
    return ROLES.index(role) if role in ROLES else 99


# =================================================================== 상태 읽기
@dataclass
class Application:
    id: int
    team_id: int
    student: Student
    status: str
    reason: str | None
    created_at: datetime
    decided_at: datetime | None
    votes: dict[int, str] = field(default_factory=dict)   # voter_id -> 찬성/거부


@dataclass
class AppState:
    closed: bool
    confirmed: bool
    teams: dict[int, str]                     # 팀 번호 -> 팀 이름
    members: dict[int, list[Student]]         # 팀 번호 -> 팀원 (자리 순서)
    students: dict[int, Student]              # 학생 id -> 학생 (관리자 제외)
    team_of: dict[int, int]                   # 학생 id -> 팀 번호
    applications: list[Application]           # 대기 중인 지원 + (학생 화면이면) 내 지원 기록
    proposal: dict | None

    @property
    def unassigned(self) -> list[Student]:
        return [s for s in self.students.values() if s.id not in self.team_of]

    def pending_for_team(self, team_id: int) -> list[Application]:
        return [a for a in self.applications if a.team_id == team_id and a.status == PENDING]

    def my_pending(self, student_id: int) -> Application | None:
        for a in self.applications:
            if a.student.id == student_id and a.status == PENDING:
                return a
        return None

    def my_latest(self, student_id: int) -> Application | None:
        mine = [a for a in self.applications if a.student.id == student_id]
        return mine[-1] if mine else None


def _load_students(conn: psycopg.Connection, where: str = "not s.is_admin", params: tuple = ()) -> dict[int, Student]:
    rows = conn.execute(
        f"""
        select s.id, s.student_no, s.name, s.main_role,
               coalesce(array_agg(r.role) filter (where r.role is not null), '{{}}') as subs
        from tf_students s
        left join tf_student_sub_roles r on r.student_id = s.id
        where {where}
        group by s.id
        order by s.student_no
        """,
        params,
    ).fetchall()
    return {
        r["id"]: Student(r["id"], r["student_no"], r["name"], r["main_role"],
                         tuple(sorted(r["subs"], key=_role_order)))
        for r in rows
    }


def _settings(conn: psycopg.Connection) -> dict[str, str]:
    return {r["key"]: r["value"] for r in conn.execute("select key, value from tf_settings").fetchall()}


def load_state(student_id: int | None = None) -> AppState:
    """화면 한 번을 그리는 데 필요한 상태를 연결 하나로 한 번에 읽는다."""
    with db.connection() as conn:
        students = _load_students(conn)
        teams = {r["id"]: r["name"] for r in conn.execute("select id, name from tf_teams order by id")}
        members: dict[int, list[Student]] = {t: [] for t in teams}
        team_of: dict[int, int] = {}
        for r in conn.execute("select team_id, student_id from tf_members order by team_id, slot_no"):
            if r["student_id"] in students:
                members.setdefault(r["team_id"], []).append(students[r["student_id"]])
                team_of[r["student_id"]] = r["team_id"]
        apps: list[Application] = []
        for r in conn.execute(
            """
            select a.id, a.team_id, a.student_id, a.status, a.reason, a.created_at, a.decided_at,
                   coalesce(json_object_agg(v.voter_id, v.vote) filter (where v.voter_id is not null), '{}') as votes
            from tf_applications a
            left join tf_votes v on v.application_id = a.id
            where a.status = '대기' or a.student_id = %s
            group by a.id
            order by a.id
            """,
            (student_id or -1,),
        ):
            if r["student_id"] in students:
                votes = {int(k): v for k, v in (r["votes"] or {}).items()}
                apps.append(Application(r["id"], r["team_id"], students[r["student_id"]], r["status"],
                                        r["reason"], r["created_at"], r["decided_at"], votes))
        st = _settings(conn)
    proposal = json.loads(st["proposal"]) if st.get("proposal") else None
    return AppState(st.get("closed") == "true", st.get("confirmed") == "true", teams, members,
                    students, team_of, apps, proposal)


# =================================================================== 공통 도우미
def _is_closed(conn: psycopg.Connection) -> bool:
    row = conn.execute("select value from tf_settings where key = 'closed'").fetchone()
    return bool(row and row["value"] == "true")


def _set_setting(conn: psycopg.Connection, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("delete from tf_settings where key = %s", (key,))
    else:
        conn.execute(
            "insert into tf_settings (key, value, updated_at) values (%s, %s, now()) "
            "on conflict (key) do update set value = excluded.value, updated_at = now()",
            (key, value),
        )


def _student(conn: psycopg.Connection, student_id: int) -> Student | None:
    return _load_students(conn, "s.id = %s and not s.is_admin", (student_id,)).get(student_id)


def _team_members(conn: psycopg.Connection, team_id: int) -> list[Student]:
    ids = [r["student_id"] for r in conn.execute(
        "select student_id from tf_members where team_id = %s order by slot_no", (team_id,))]
    if not ids:
        return []
    found = _load_students(conn, "s.id = any(%s)", (ids,))
    return [found[i] for i in ids if i in found]


def _free_slot(conn: psycopg.Connection, team_id: int) -> int | None:
    used = {r["slot_no"] for r in conn.execute("select slot_no from tf_members where team_id = %s", (team_id,))}
    for slot in range(1, TEAM_MAX + 1):
        if slot not in used:
            return slot
    return None


def _require_admin(conn: psycopg.Connection, actor_id: int) -> bool:
    row = conn.execute("select is_admin from tf_students where id = %s", (actor_id,)).fetchone()
    return bool(row and row["is_admin"])


def _team_name(team_id: int) -> str:
    return f"팀{team_id}"


# =================================================================== 명단 올리기
class RosterError(ValueError):
    pass


@dataclass(frozen=True)
class RosterRow:
    student_no: str
    name: str
    main_role: str
    sub_roles: tuple[str, ...]


def parse_roster(data: bytes) -> list[RosterRow]:
    """명단 CSV(UTF-8, BOM 있어도 됨)를 읽고 검사한다. 틀리면 RosterError."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp949")
        except UnicodeDecodeError as exc:
            raise RosterError("CSV를 UTF-8로 읽을 수 없음") from exc
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise RosterError("CSV가 비어 있음")
    header = [h.strip().lower() for h in rows[0]]
    expected = ["student_no", "name", "main_role", "sub_roles"]
    if header[:4] != expected:
        raise RosterError(f"첫 줄(머리글)이 {','.join(expected)} 이어야 함")
    out: list[RosterRow] = []
    seen: set[str] = set()
    for line_no, r in enumerate(rows[1:], start=2):
        if len(r) < 4:
            raise RosterError(f"{line_no}번째 줄의 열이 {len(r)}개임 (4개여야 함. 보조 역할이 없어도 마지막 쉼표를 남김)")
        no, name, main, subs_raw = (c.strip() for c in r[:4])
        if not no or not name:
            raise RosterError(f"{line_no}번째 줄에 학번이나 이름이 비어 있음")
        if no.lower() == ADMIN_STUDENT_NO:
            raise RosterError(f"{line_no}번째 줄: 학번 admin 은 관리자 전용임")
        if no in seen:
            raise RosterError(f"{line_no}번째 줄: 학번 {no} 이 중복됨")
        seen.add(no)
        if main not in ROLES:
            raise RosterError(f"{line_no}번째 줄: 주 역할 '{main}' 은 6개 역할이 아님")
        subs: list[str] = []
        for s in subs_raw.split(";"):
            s = s.strip()
            if not s or s == main or s in subs:
                continue
            if s not in ROLES:
                raise RosterError(f"{line_no}번째 줄: 보조 역할 '{s}' 은 6개 역할이 아님")
            subs.append(s)
        if len(subs) > 2:
            raise RosterError(f"{line_no}번째 줄: 보조 역할이 {len(subs)}개임 (0~2개여야 함)")
        out.append(RosterRow(no, name, main, tuple(subs)))
    if not out:
        raise RosterError("명단에 학생이 없음")
    return out


def reset_with_roster(actor_id: int, data: bytes | None, team_count: int) -> str:
    """지원, 투표, 팀원, 마감 표시, 배정안을 지우고 명단의 학생과 빈 팀을 만든다. 관리자 계정은 남김."""
    if data is None:
        return _msg(BLOCKED, "명단 CSV 파일을 먼저 올려야 함")
    try:
        team_count = int(team_count)
    except (TypeError, ValueError):
        return _msg(BLOCKED, "팀 수가 숫자가 아님")
    if not 1 <= team_count <= 20:
        return _msg(BLOCKED, "팀 수는 1~20 사이여야 함")
    try:
        roster = parse_roster(data)
    except RosterError as exc:
        return _msg(BLOCKED, f"명단을 읽을 수 없음: {exc}")

    hashed = [hash_password(STUDENT_DEFAULT_PASSWORD) for _ in roster]   # 학생마다 다른 salt
    with db.connection() as conn:
        with conn.transaction():
            if not _require_admin(conn, actor_id):
                return _msg(BLOCKED, "관리자만 할 수 있음")
            conn.execute("lock table tf_teams in exclusive mode")
            conn.execute("delete from tf_students where not is_admin")   # 보조 역할, 팀원, 지원, 투표도 함께 지워짐
            conn.execute("delete from tf_applications")
            conn.execute("delete from tf_members")
            conn.execute("delete from tf_teams")
            conn.execute("delete from tf_settings")
            with conn.cursor() as cur:
                for row, h in zip(roster, hashed):
                    sid = cur.execute(
                        "insert into tf_students (student_no, name, main_role, password_hash, is_admin) "
                        "values (%s, %s, %s, %s, false) returning id",
                        (row.student_no, row.name, row.main_role, h),
                    ).fetchone()["id"]
                    for role in row.sub_roles:
                        cur.execute("insert into tf_student_sub_roles (student_id, role) values (%s, %s)", (sid, role))
                cur.executemany("insert into tf_teams (id, name) values (%s, %s)",
                                [(i, _team_name(i)) for i in range(1, team_count + 1)])
            _set_setting(conn, "closed", "false")
    return _msg(APPROVED, f"명단 {len(roster)}명, 팀 {team_count}개로 처음 상태를 만듦")


# =================================================================== 지원
def apply(student_id: int, team_id: int) -> str:
    try:
        return _apply(student_id, team_id)
    except (pg_errors.UniqueViolation, pg_errors.CheckViolation):
        return _msg(BLOCKED, "다른 요청과 동시에 처리되어 접수하지 않음. 다시 시도하세요")


def _apply(student_id: int, team_id: int) -> str:
    with db.connection() as conn:
        with conn.transaction():
            if conn.execute("select id from tf_teams where id = %s for update", (team_id,)).fetchone() is None:
                return _msg(BLOCKED, "없는 팀임")
            if conn.execute("select id from tf_students where id = %s for update", (student_id,)).fetchone() is None:
                return _msg(BLOCKED, "명단에 없는 학생임")
            applicant = _student(conn, student_id)
            if applicant is None:
                return _msg(BLOCKED, "학생만 지원할 수 있음")
            if _is_closed(conn):
                return _msg(BLOCKED, "모집이 마감됨")
            if conn.execute("select 1 from tf_members where student_id = %s", (student_id,)).fetchone():
                return _msg(BLOCKED, "이미 팀이 있음")
            if conn.execute("select 1 from tf_applications where student_id = %s and status = '대기'",
                            (student_id,)).fetchone():
                return _msg(BLOCKED, "결과를 기다리는 지원이 있음")
            members = _team_members(conn, team_id)
            reason = rules.check_join(members, applicant)
            if reason:
                return _msg(BLOCKED, reason)
            if not members:
                conn.execute(
                    "insert into tf_applications (team_id, student_id, status, decided_at) values (%s, %s, '승인', now())",
                    (team_id, student_id),
                )
                conn.execute("insert into tf_members (team_id, slot_no, student_id) values (%s, 1, %s)",
                             (team_id, student_id))
                return _msg(APPROVED, f"빈 팀이라 바로 {_team_name(team_id)}에 들어감")
            conn.execute("insert into tf_applications (team_id, student_id) values (%s, %s)", (team_id, student_id))
            return _msg(PENDING, f"{_team_name(team_id)}에 지원함. 팀원 {len(members)}명의 투표를 기다림")


# =================================================================== 투표
def vote(voter_id: int, applicant_no: str, agree: bool) -> str:
    try:
        return _vote(voter_id, applicant_no, agree)
    except (pg_errors.UniqueViolation, pg_errors.CheckViolation):
        return _msg(BLOCKED, "다른 요청과 동시에 처리되어 반영하지 않음. 다시 시도하세요")


def _vote(voter_id: int, applicant_no: str, agree: bool) -> str:
    with db.connection() as conn:
        with conn.transaction():
            row = conn.execute("select team_id from tf_members where student_id = %s", (voter_id,)).fetchone()
            if row is None:
                return _msg(BLOCKED, "팀원만 투표할 수 있음")
            team_id = row["team_id"]
            conn.execute("select id from tf_teams where id = %s for update", (team_id,))
            app = conn.execute(
                """
                select a.id, a.status, a.reason, a.student_id, s.name
                from tf_applications a join tf_students s on s.id = a.student_id
                where s.student_no = %s and a.team_id = %s
                order by a.id desc limit 1
                """,
                (applicant_no, team_id),
            ).fetchone()
            if app is None:
                return _msg(BLOCKED, "내 팀에 들어온 이 학생의 지원이 없음")
            if app["status"] != PENDING:
                why = f" ({app['reason']})" if app["reason"] else ""
                return _msg(app["status"], f"{app['name']}의 지원은 이미 {app['status']}됨{why}")
            if _is_closed(conn):
                return _msg(BLOCKED, "모집이 마감됨")
            mine = conn.execute("select vote from tf_votes where application_id = %s and voter_id = %s",
                                (app["id"], voter_id)).fetchone()
            if mine:
                return _msg(PENDING, f"이미 투표함 (내 투표: {mine['vote']}). 투표는 바꿀 수 없음")

            conn.execute("insert into tf_votes (application_id, voter_id, vote) values (%s, %s, %s)",
                         (app["id"], voter_id, "찬성" if agree else "거부"))
            name = app["name"]
            if not agree:
                conn.execute("update tf_applications set status = '거절', reason = '팀원의 거부', decided_at = now() "
                             "where id = %s", (app["id"],))
                return _msg(REJECTED, f"팀원의 거부로 {name}의 지원이 거절됨")

            members = _team_members(conn, team_id)
            yes = {r["voter_id"] for r in conn.execute(
                "select voter_id from tf_votes where application_id = %s and vote = '찬성'", (app["id"],))}
            waiting = [m for m in members if m.id not in yes]
            if waiting:
                return _msg(PENDING, f"{name}의 지원: 찬성 {len(members) - len(waiting)}/{len(members)}, "
                                     f"남은 투표 {', '.join(m.name for m in waiting)}")

            # 지금 팀원 모두가 찬성 -> 승인 직전 재검사 (투표가 끝난 순서대로 처리됨)
            applicant = _student(conn, app["student_id"])
            reason = rules.check_join(members, applicant)
            if reason:
                conn.execute("update tf_applications set status = '거절', reason = %s, decided_at = now() "
                             "where id = %s", (f"승인 직전 검사: {reason}", app["id"]))
                return _msg(REJECTED, f"승인 직전 검사에서 {reason} ({name}의 지원)")
            slot = _free_slot(conn, team_id)
            conn.execute("insert into tf_members (team_id, slot_no, student_id) values (%s, %s, %s)",
                         (team_id, slot, app["student_id"]))
            conn.execute("update tf_applications set status = '승인', decided_at = now() where id = %s", (app["id"],))
            return _msg(APPROVED, f"모든 팀원이 찬성하여 {name}이(가) {_team_name(team_id)}에 들어감")


# =================================================================== 모집 마감
def close_recruitment(actor_id: int) -> str:
    with db.connection() as conn:
        with conn.transaction():
            if not _require_admin(conn, actor_id):
                return _msg(BLOCKED, "관리자만 할 수 있음")
            teams = conn.execute("select id from tf_teams order by id for update").fetchall()
            if not teams:
                return _msg(BLOCKED, "명단을 먼저 올려야 함")
            if _is_closed(conn):
                return _msg(BLOCKED, "이미 마감됨")
            _set_setting(conn, "closed", "true")
            rejected = conn.execute(
                "update tf_applications set status = '거절', reason = '모집 마감', decided_at = now() "
                "where status = '대기' returning id"
            ).fetchall()
            removed = conn.execute(
                """
                delete from tf_members m
                where m.team_id in (select team_id from tf_members group by team_id having count(*) = 1)
                returning (select name from tf_students s where s.id = m.student_id) as name
                """
            ).fetchall()
    names = ", ".join(r["name"] for r in removed) or "없음"
    return _msg(APPROVED, f"모집을 마감함. 대기 지원 {len(rejected)}건 거절, 1명뿐인 팀에서 빠진 학생: {names}")


# =================================================================== 배정안
def assign_context() -> tuple[dict[int, list[Student]], list[Student], bool, bool]:
    """(팀 구성, 팀이 없는 학생, 마감 여부, 확정 여부)"""
    s = load_state()
    return s.members, s.unassigned, s.closed, s.confirmed


def save_proposal(proposal: list[tuple[str, int]], source: str) -> None:
    payload = json.dumps({"assignments": [{"student_no": no, "team": t} for no, t in proposal],
                          "source": source, "created_at": now_kst().isoformat()}, ensure_ascii=False)
    with db.connection() as conn:
        _set_setting(conn, "proposal", payload)


def clear_proposal() -> None:
    with db.connection() as conn:
        _set_setting(conn, "proposal", None)


def confirm(actor_id: int) -> str:
    with db.connection() as conn:
        with conn.transaction():
            if not _require_admin(conn, actor_id):
                return _msg(BLOCKED, "관리자만 할 수 있음")
            conn.execute("select id from tf_teams order by id for update")
            st = _settings(conn)
            if st.get("closed") != "true":
                return _msg(BLOCKED, "먼저 모집을 마감해야 함")
            if st.get("confirmed") == "true":
                return _msg(BLOCKED, "이미 확정함")
            if not st.get("proposal"):
                return _msg(BLOCKED, "확정할 배정안이 없음. 먼저 AI 배정을 누르세요")
            data = json.loads(st["proposal"])
            proposal = [(str(a["student_no"]), int(a["team"])) for a in data.get("assignments", [])]

            students = _load_students(conn)
            teams = {r["id"]: [] for r in conn.execute("select id from tf_teams order by id")}
            placed: set[int] = set()
            for r in conn.execute("select team_id, student_id from tf_members order by team_id, slot_no"):
                teams[r["team_id"]].append(students[r["student_id"]])
                placed.add(r["student_id"])
            unassigned = [s for s in students.values() if s.id not in placed]
            problems = rules.validate_proposal(teams, unassigned, proposal)
            if problems:
                return _msg(BLOCKED, "배정안이 지금 상태와 맞지 않음: " + "; ".join(problems[:3]))
            by_no = {s.student_no: s for s in unassigned}
            for no, team_no in proposal:
                slot = _free_slot(conn, team_no)
                conn.execute("insert into tf_members (team_id, slot_no, student_id) values (%s, %s, %s)",
                             (team_no, slot, by_no[no].id))
            _set_setting(conn, "confirmed", "true")
    return _msg(APPROVED, f"배정안을 확정함 ({len(proposal)}명 배정, {data.get('source', '')})")


# =================================================================== AI 호출 기록
def log_ai(round_no: int, model: str, outcome: str, detail: str | None, elapsed: float | None) -> None:
    try:
        with db.connection() as conn:
            conn.execute(
                "insert into tf_ai_logs (round, model, outcome, detail, elapsed_sec) values (%s, %s, %s, %s, %s)",
                (round_no, model, outcome, (detail or "")[:2000], round(elapsed, 1) if elapsed is not None else None),
            )
    except Exception:   # 기록 실패가 배정을 막지 않게 함
        pass


def recent_ai_logs(limit: int = 30) -> list[dict]:
    with db.connection() as conn:
        return conn.execute(
            "select created_at, round, model, outcome, detail, elapsed_sec from tf_ai_logs order by id desc limit %s",
            (limit,),
        ).fetchall()
