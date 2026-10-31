"""부록의 연습용 시나리오 3개를 service 계층으로 그대로 실행해, 단계마다 결과 안내의 첫 단어를 확인한다.

로컬 PostgreSQL 이 필요하다. TEST_DATABASE_URL 환경 변수로 주소를 준다(없으면 건너뜀).
예) TEST_DATABASE_URL=postgresql://postgres@/tftest?host=/var/tmp&port=5433 pytest
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

TEST_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not TEST_URL, reason="TEST_DATABASE_URL 없음")

DATA = Path(__file__).parent / "data"
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", TEST_URL)
    monkeypatch.setenv("ADMIN_PASSWORD", "test-admin-pw")
    from tfapp import auth, db, service

    db.close_pool()
    with db.connection() as conn:
        conn.execute("drop table if exists tf_votes, tf_applications, tf_members, tf_teams, "
                     "tf_student_sub_roles, tf_students, tf_settings, tf_ai_logs cascade")
        conn.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
    auth.ensure_admin()
    yield Helper(auth, service)
    db.close_pool()


class Helper:
    def __init__(self, auth, service):
        self.auth, self.service = auth, service
        self.admin = auth.login("admin", "test-admin-pw")
        assert self.admin is not None

    def start(self, csv_name: str, teams: int) -> str:
        return self.service.reset_with_roster(self.admin.id, (DATA / csv_name).read_bytes(), teams)

    def user(self, no: str):
        u = self.auth.login(no, "1234")
        assert u is not None, no
        return u

    def apply(self, no: str, team: int) -> str:
        return self.service.apply(self.user(no).id, team)

    def vote(self, voter_no: str, applicant_no: str, agree: bool = True) -> str:
        return self.service.vote(self.user(voter_no).id, applicant_no, agree)

    def close(self) -> str:
        return self.service.close_recruitment(self.admin.id)

    def assign_and_confirm(self, failing_ai: bool = True):
        from tfapp import ai

        def fail(*_a, **_k):
            raise ai.AIFailure("HTTP 오류", "HTTP 429: 시험용 실패")

        out = ai.run_assignment(self.admin.id, models=["m1", "m2", "m3"], timeout=5, caller=fail)
        assert out.result.startswith("승인"), out.result
        res = self.service.confirm(self.admin.id)
        assert res.startswith("승인"), res
        return self.final_problems()

    def final_problems(self):
        from tfapp import rules

        s = self.service.load_state()
        assert not s.unassigned, [x.name for x in s.unassigned]
        probs = []
        for t, ms in s.members.items():
            probs += rules.team_problems(t, ms)
        return probs


def first(text: str) -> str:
    return text.split(":", 1)[0]


def test_login_rules(app):
    app.start("쉬움.csv", 3)
    assert app.auth.login("2026101", "1234") is not None
    assert app.auth.login("2026101", "wrong") is None
    assert app.auth.login("2026101", "") is None
    assert app.auth.login("admin", "1234") is None
    # 학생은 관리자 기능을 쓸 수 없음
    stu = app.user("2026101")
    assert first(app.service.close_recruitment(stu.id)) == "막힘"
    assert first(app.service.reset_with_roster(stu.id, b"x", 3)) == "막힘"
    assert first(app.service.confirm(stu.id)) == "막힘"
    # 비밀번호는 해시로 저장됨
    from tfapp import db
    with db.connection() as conn:
        h = conn.execute("select password_hash from tf_students where student_no='2026101'").fetchone()["password_hash"]
    assert h.startswith("$2") and "1234" not in h


def test_easy(app):
    assert first(app.start("쉬움.csv", 3)) == "승인"
    # 1
    assert first(app.apply("2026101", 1)) == "승인"
    assert first(app.apply("2026102", 2)) == "승인"
    assert first(app.apply("2026103", 3)) == "승인"
    # 2
    assert first(app.apply("2026104", 1)) == "대기"
    assert first(app.vote("2026101", "2026104")) == "승인"
    # 3
    assert first(app.apply("2026105", 2)) == "대기"
    assert first(app.vote("2026102", "2026105")) == "승인"
    # 4
    assert first(app.apply("2026107", 1)) == "대기"
    assert first(app.vote("2026101", "2026107")) == "대기"
    assert first(app.vote("2026104", "2026107", False)) == "거절"
    # 5
    assert first(app.apply("2026106", 3)) == "대기"
    assert first(app.vote("2026103", "2026106")) == "승인"
    # 6
    assert first(app.close()) == "승인"
    assert app.assign_and_confirm() == []


def test_medium(app):
    assert first(app.start("중간.csv", 4)) == "승인"
    assert first(app.apply("2026201", 1)) == "승인"                     # 1
    assert first(app.apply("2026205", 1)) == "대기"                     # 2
    assert first(app.vote("2026201", "2026205")) == "승인"
    r = app.apply("2026202", 1)                                         # 3 막힘(주 역할 중복)
    assert first(r) == "막힘" and "주 역할" in r
    assert first(app.vote("2026201", "2026202")) == "막힘"              # 막힌 지원에는 투표할 지원이 없음
    assert first(app.apply("2026206", 2)) == "승인"                     # 4
    assert first(app.apply("2026209", 2)) == "대기"                     # 5
    assert first(app.vote("2026206", "2026209")) == "승인"
    assert first(app.apply("2026210", 2)) == "대기"                     # 6
    assert first(app.vote("2026206", "2026210")) == "대기"
    assert first(app.vote("2026209", "2026210", False)) == "거절"
    assert first(app.apply("2026203", 3)) == "승인"                     # 7
    assert first(app.apply("2026211", 4)) == "승인"
    assert first(app.apply("2026212", 4)) == "대기"                     # 8
    assert first(app.apply("2026212", 3)) == "막힘"
    r = app.close()                                                     # 9
    assert first(r) == "승인"
    s = app.service.load_state()
    names = {x.name for x in s.unassigned}
    assert "학생3(PL;RW)" in names and "학생11(RW)" in names
    u12 = app.user("2026212").id
    latest = app.service.load_state(u12).my_latest(u12)
    assert latest.status == "거절" and latest.reason == "모집 마감"
    assert first(app.apply("2026202", 3)) == "막힘"                     # 10 (마감)
    assert app.assign_and_confirm() == []                              # 11


def test_hard(app):
    assert first(app.start("어려움.csv", 4)) == "승인"
    assert first(app.apply("2026301", 1)) == "승인"                     # 1
    assert first(app.apply("2026304", 1)) == "대기"                     # 2
    assert first(app.vote("2026301", "2026304")) == "승인"
    assert first(app.apply("2026307", 1)) == "대기"                     # 3
    assert first(app.vote("2026301", "2026307")) == "대기"
    assert first(app.vote("2026304", "2026307")) == "승인"
    assert first(app.apply("2026302", 2)) == "승인"                     # 4
    assert first(app.apply("2026308", 2)) == "대기"                     # 5
    assert first(app.vote("2026302", "2026308")) == "승인"
    assert first(app.apply("2026305", 2)) == "대기"                     # 6
    assert first(app.apply("2026306", 2)) == "대기"
    assert first(app.vote("2026302", "2026305")) == "대기"              # 7
    assert first(app.vote("2026308", "2026305")) == "승인"
    assert first(app.vote("2026302", "2026306")) == "대기"              # 8 (학생5도 이제 팀원이라 투표함)
    assert first(app.vote("2026308", "2026306")) == "대기"
    r = app.vote("2026305", "2026306")
    assert first(r) == "거절" and "승인 직전" in r
    assert first(app.apply("2026311", 3)) == "승인"                     # 9
    assert first(app.apply("2026315", 3)) == "대기"                     # 10
    assert first(app.vote("2026311", "2026315")) == "승인"
    r = app.apply("2026314", 3)                                         # 11 막힘(필수 포함 역할 자리)
    assert first(r) == "막힘" and "필수 포함" in r
    assert first(app.apply("2026310", 4)) == "승인"                     # 12
    assert first(app.apply("2026313", 1)) == "대기"                     # 13
    assert first(app.vote("2026301", "2026313")) == "대기"
    assert first(app.close()) == "승인"                                 # 14
    s = app.service.load_state()
    assert "학생10(TT;RW)" in {x.name for x in s.unassigned}
    u13 = app.user("2026313").id
    mine = app.service.load_state(u13).my_latest(u13)
    assert mine.status == "거절" and mine.reason == "모집 마감"
    assert app.assign_and_confirm() == []                              # 15


def test_vote_cannot_change_and_closed_blocks(app):
    app.start("쉬움.csv", 3)
    app.apply("2026101", 1)
    app.apply("2026104", 1)
    assert first(app.vote("2026101", "2026104")) == "승인"
    assert first(app.vote("2026101", "2026104", False)) == "승인"       # 이미 처리됨: 지금 상태
    app.apply("2026107", 1)
    assert first(app.vote("2026101", "2026107")) == "대기"
    assert first(app.vote("2026101", "2026107", False)) == "대기"       # 투표 바꾸기 불가
    app.close()
    assert first(app.apply("2026108", 2)) == "막힘"
    assert first(app.close()) == "막힘"


def test_infeasible_reports_reason_without_ai(app):
    # 쉬움 명단(기획자 가능 3명)으로 팀 4개를 만들면 규칙을 지키는 배정이 없음 -> AI를 부르지 않음
    from tfapp import ai

    app.start("쉬움.csv", 4)
    app.close()
    called = []
    out = ai.run_assignment(app.admin.id, models=["m"], timeout=5,
                            caller=lambda *a, **k: called.append(1) or "")
    assert first(out.result) == "막힘"
    assert "규칙을 지키는 배정이 없음" in out.status_lines[0]
    assert any("기획자" in line for line in out.status_lines)
    assert called == []


def test_ai_success_and_fallback_order(app):
    """1차 빈 응답, 2차 규칙 위반 배정안 -> 걸러 냄, 3차 정상 -> 3차 배정안 채택."""
    import json

    from tfapp import ai, rules

    app.start("쉬움.csv", 3)
    app.apply("2026101", 1); app.apply("2026102", 2); app.apply("2026103", 3)
    app.close()   # 세 명 모두 1명뿐인 팀 -> 모두 팀에서 빠짐
    s = app.service.load_state()
    assert len(s.unassigned) == 8
    good = rules.solve(s.members, s.unassigned).proposal
    bad = [(no, 1) for no, _ in good]
    answers = iter(["", json.dumps({"assignments": [{"student_no": n, "team": t} for n, t in bad]}),
                    "```json\n" + json.dumps({"assignments": [{"student_no": n, "team": f"팀{t}"} for n, t in good]}) + "\n```"])
    seen = []
    out = ai.run_assignment(app.admin.id, models=["m1", "m2", "m3"], timeout=5,
                            caller=lambda model, *a, **k: seen.append(model) or next(answers))
    assert seen == ["m1", "m2", "m3"]
    assert first(out.result) == "승인" and "3차" in out.result
    assert any("빈 응답" in l for l in out.status_lines)
    assert any("검사 실패" in l for l in out.status_lines)
    assert first(app.service.confirm(app.admin.id)) == "승인"
    assert app.final_problems() == []
    logs = app.service.recent_ai_logs()
    assert [r["outcome"] for r in logs][:3] == ["성공", "검사 실패", "빈 응답"]


def test_double_join_same_slot_blocked_by_db(app):
    """마지막 자리에 두 명: DB 유일성 제약이 두 번째를 막는지 직접 확인."""
    app.start("쉬움.csv", 3)
    from psycopg import errors

    from tfapp import db
    a, b = app.user("2026101"), app.user("2026102")
    with db.connection() as conn:
        conn.execute("insert into tf_members (team_id, slot_no, student_id) values (1, 1, %s)", (a.id,))
    with pytest.raises(errors.UniqueViolation):
        with db.connection() as conn:
            conn.execute("insert into tf_members (team_id, slot_no, student_id) values (1, 1, %s)", (b.id,))
