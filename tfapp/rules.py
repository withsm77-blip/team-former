"""팀 규칙 판정 (DB와 화면에 의존하지 않는 순수 함수).

공고 2.3절(팀 규칙), 2.4절(지원과 투표), 2.5절(배정안 검사 다섯 가지), 3장(필수 포함 역할 자리 계산)을
코드로 옮긴 것이다. 학생 이름이나 시나리오 순서는 보지 않고 규칙으로만 판정한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from .config import PLANNER, REQUIRED_ROLES, TEAM_MAX, TEAM_MIN, WEB_PROGRAMMER


@dataclass(frozen=True)
class Student:
    id: int
    student_no: str
    name: str
    main_role: str
    sub_roles: tuple[str, ...] = field(default_factory=tuple)

    @property
    def abilities(self) -> frozenset[str]:
        """맡을 수 있는 역할(주 역할 + 보조 역할)."""
        return frozenset((self.main_role, *self.sub_roles))

    def can(self, role: str) -> bool:
        return role in self.abilities

    def label(self) -> str:
        subs = ", ".join(self.sub_roles) if self.sub_roles else "없음"
        return f"{self.name} ({self.main_role} / 보조: {subs})"


# ------------------------------------------------------------------ 필수 포함 역할
def required_filled(members: Iterable[Student]) -> int:
    """기획자와 웹 프로그래머를 '서로 다른' 학생에게 맡길 때 채울 수 있는 최대 수(0~2).

    예: 주 역할 기획자, 보조 역할 웹 프로그래머인 학생 혼자면 1.
    """
    members = list(members)
    planners = {s.id for s in members if s.can(PLANNER)}
    webs = {s.id for s in members if s.can(WEB_PROGRAMMER)}
    if planners and webs and len(planners | webs) >= 2:
        return 2
    if planners or webs:
        return 1
    return 0


def missing_required(members: Iterable[Student]) -> list[str]:
    """아직 채우지 못한 필수 포함 역할 이름(설명용)."""
    members = list(members)
    filled = required_filled(members)
    if filled == 2:
        return []
    has_p = any(s.can(PLANNER) for s in members)
    has_w = any(s.can(WEB_PROGRAMMER) for s in members)
    if filled == 0:
        return list(REQUIRED_ROLES)
    # filled == 1: 한쪽만 있거나, 한 학생이 둘 다 할 수 있는 경우
    if has_p and not has_w:
        return [WEB_PROGRAMMER]
    if has_w and not has_p:
        return [PLANNER]
    return ["기획자 또는 웹 프로그래머(서로 다른 학생)"]


# ------------------------------------------------------------------ 지원 / 승인 직전 검사
REASON_FULL = "팀이 꽉 참"
REASON_DUP = "같은 주 역할의 팀원이 있음"
REASON_SEATS = "필수 포함 역할을 맡을 학생의 자리가 모자람"


def check_join(members: Sequence[Student], applicant: Student) -> str | None:
    """applicant 가 members 팀에 들어갈 수 없으면 이유를, 들어갈 수 있으면 None 을 돌려준다.

    지원할 때(결과: 막힘)와 승인 직전 재검사(결과: 거절)에 같은 함수를 쓴다.
    """
    if len(members) >= TEAM_MAX:
        return REASON_FULL
    if any(m.main_role == applicant.main_role for m in members):
        return REASON_DUP
    after = [*members, applicant]
    seats_left = TEAM_MAX - len(after)
    need = len(REQUIRED_ROLES) - required_filled(after)
    if seats_left < need:
        return REASON_SEATS
    return None


# ------------------------------------------------------------------ 배정안 검사 (2.5절 4항)
def team_problems(team_no: int, members: Sequence[Student]) -> list[str]:
    """확정할 팀 하나가 규칙을 어기는 점."""
    problems: list[str] = []
    if not (TEAM_MIN <= len(members) <= TEAM_MAX):
        problems.append(f"팀{team_no} 인원이 {len(members)}명임 ({TEAM_MIN}~{TEAM_MAX}명이어야 함)")
    seen: dict[str, str] = {}
    for m in members:
        if m.main_role in seen:
            problems.append(f"팀{team_no}에 주 역할 '{m.main_role}'이 중복됨 ({seen[m.main_role]}, {m.name})")
        else:
            seen[m.main_role] = m.name
    if members and required_filled(members) < 2:
        problems.append(f"팀{team_no}에 필수 포함 역할이 없음: {', '.join(missing_required(members))}")
    return problems


def validate_proposal(
    teams: Mapping[int, Sequence[Student]],
    unassigned: Sequence[Student],
    proposal: Sequence[tuple[str, int]],
) -> list[str]:
    """배정안(학번, 팀 번호 목록)을 다섯 가지로 검사한다. 빈 목록이면 통과.

    1. 팀이 없는 학생이 빠짐없이 배정됨
    2. 한 학생은 한 팀에만 배정됨
    3. 모든 팀이 2~4명
    4. 팀 안에서 주 역할 중복 없음
    5. 필수 포함 역할 충족
    """
    errors: list[str] = []
    by_no = {s.student_no: s for s in unassigned}
    member_nos = {m.student_no for ms in teams.values() for m in ms}

    counts: dict[str, int] = {}
    for no, team_no in proposal:
        counts[no] = counts.get(no, 0) + 1
        if no in member_nos:
            errors.append(f"이미 팀이 있는 학생({no})을 다시 배정함")
        elif no not in by_no:
            errors.append(f"명단에 없는 학생({no})을 배정함")
        if team_no not in teams:
            errors.append(f"없는 팀(팀{team_no})에 배정함")

    missing = [s.name for s in unassigned if s.student_no not in counts]
    if missing:
        errors.append(f"배정되지 않은 학생이 있음: {', '.join(missing)}")
    dups = [by_no[no].name if no in by_no else no for no, c in counts.items() if c > 1]
    if dups:
        errors.append(f"두 팀 이상에 배정된 학생이 있음: {', '.join(dups)}")
    if errors:
        return errors

    result = {t: list(ms) for t, ms in teams.items()}
    for no, team_no in proposal:
        result[team_no].append(by_no[no])
    for team_no in sorted(result):
        errors.extend(team_problems(team_no, result[team_no]))
    return errors


def apply_proposal(
    teams: Mapping[int, Sequence[Student]],
    unassigned: Sequence[Student],
    proposal: Sequence[tuple[str, int]],
) -> dict[int, list[Student]]:
    by_no = {s.student_no: s for s in unassigned}
    result = {t: list(ms) for t, ms in teams.items()}
    for no, team_no in proposal:
        result[team_no].append(by_no[no])
    return result


# ------------------------------------------------------------------ 앱이 스스로 배정 (백트래킹)
@dataclass
class SolveResult:
    proposal: list[tuple[str, int]] | None    # None 이면 규칙을 지키는 배정이 없음
    reasons: list[str]                        # 배정이 없을 때의 이유
    nodes: int = 0                            # 탐색한 경우의 수 (설명용)


MAX_NODES = 3_000_000


def solve(teams: Mapping[int, Sequence[Student]], unassigned: Sequence[Student]) -> SolveResult:
    """기존 팀원은 옮기지 않고, 팀이 없는 학생만 넣어 다섯 가지 검사를 모두 통과하는 배정을
    모든 경우를 따져(백트래킹) 끝까지 찾는다."""
    team_nos = sorted(teams)
    state = {t: list(teams[t]) for t in team_nos}

    # 기존 팀 자체가 이미 규칙을 어기면(5명 이상, 주 역할 중복) 어떤 배정도 불가능
    for t in team_nos:
        if len(state[t]) > TEAM_MAX:
            return SolveResult(None, [f"팀{t} 인원이 이미 {len(state[t])}명임"])
        mains = [m.main_role for m in state[t]]
        if len(mains) != len(set(mains)):
            return SolveResult(None, [f"팀{t}에 이미 주 역할이 중복된 팀원이 있음"])

    # 놓을 수 있는 팀이 적은 학생, 맡을 수 있는 필수 역할이 적은 학생부터 놓는다
    def options(s: Student) -> int:
        return sum(1 for t in team_nos if all(m.main_role != s.main_role for m in state[t]))

    order = sorted(unassigned, key=lambda s: (options(s), -len(s.abilities & set(REQUIRED_ROLES)), s.student_no))
    n = len(order)
    assignment: list[tuple[str, int]] = []
    nodes = 0
    aborted = False

    # 남은 학생(index i 이후)이 맡을 수 있는 필수 역할을 미리 계산해 가지치기에 씀
    suffix_p = [0] * (n + 1)
    suffix_w = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        suffix_p[i] = suffix_p[i + 1] + (1 if order[i].can(PLANNER) else 0)
        suffix_w[i] = suffix_w[i + 1] + (1 if order[i].can(WEB_PROGRAMMER) else 0)

    def feasible_bound(i: int) -> bool:
        remaining = n - i
        need_people = 0
        need_p = 0
        need_w = 0
        for t in team_nos:
            ms = state[t]
            seats = TEAM_MAX - len(ms)
            lack = len(REQUIRED_ROLES) - required_filled(ms)
            if seats < lack:
                return False
            need_people += max(0, TEAM_MIN - len(ms), lack)
            if not any(m.can(PLANNER) for m in ms):
                need_p += 1
            if not any(m.can(WEB_PROGRAMMER) for m in ms):
                need_w += 1
        return need_people <= remaining and need_p <= suffix_p[i] and need_w <= suffix_w[i]

    def rec(i: int) -> bool:
        nonlocal nodes, aborted
        nodes += 1
        if nodes > MAX_NODES:
            aborted = True
            return False
        if not feasible_bound(i):
            return False
        if i == n:
            return all(not team_problems(t, state[t]) for t in team_nos)
        s = order[i]
        tried_empty = False
        for t in team_nos:
            ms = state[t]
            if len(ms) >= TEAM_MAX or any(m.main_role == s.main_role for m in ms):
                continue
            if not ms:  # 빈 팀끼리는 서로 바꿔도 같은 경우이므로 첫 빈 팀만 시도
                if tried_empty:
                    continue
                tried_empty = True
            ms.append(s)
            assignment.append((s.student_no, t))
            if rec(i + 1):
                return True
            assignment.pop()
            ms.pop()
        return False

    if rec(0):
        return SolveResult(sorted(assignment, key=lambda x: (x[1], x[0])), [], nodes)
    reasons = explain_infeasible(teams, unassigned)
    if aborted:
        reasons.append("경우의 수가 너무 많아 탐색을 끝내지 못함")
    return SolveResult(None, reasons, nodes)


def _team_completable(members: Sequence[Student], candidates: Sequence[Student]) -> bool:
    """팀 하나만 따로 볼 때, 후보 학생 일부를 더해 규칙을 지키는 팀을 만들 수 있는가."""
    seats = TEAM_MAX - len(members)
    best = False

    def rec(i: int, chosen: list[Student], mains: set[str]) -> bool:
        team = [*members, *chosen]
        if len(team) >= TEAM_MIN and required_filled(team) == 2:
            return True
        if len(chosen) >= seats or i >= len(candidates):
            return False
        for j in range(i, len(candidates)):
            c = candidates[j]
            if c.main_role in mains:
                continue
            chosen.append(c)
            mains.add(c.main_role)
            if rec(j + 1, chosen, mains):
                return True
            chosen.pop()
            mains.discard(c.main_role)
        return False

    best = rec(0, [], {m.main_role for m in members})
    return best


def explain_infeasible(teams: Mapping[int, Sequence[Student]], unassigned: Sequence[Student]) -> list[str]:
    """규칙을 지키는 배정이 없을 때 관리자에게 보여 줄 이유."""
    reasons: list[str] = []
    total = sum(len(ms) for ms in teams.values()) + len(unassigned)
    if total < TEAM_MIN * len(teams):
        reasons.append(
            f"학생이 모자라 모든 팀을 {TEAM_MIN}명 이상으로 만들 수 없음 (학생 {total}명, 팀 {len(teams)}개)"
        )
    if total > TEAM_MAX * len(teams):
        reasons.append(f"학생이 너무 많아 모든 팀을 {TEAM_MAX}명 이하로 만들 수 없음 (학생 {total}명, 팀 {len(teams)}개)")

    for t in sorted(teams):
        ms = list(teams[t])
        mains = {m.main_role for m in ms}
        cands = [s for s in unassigned if s.main_role not in mains]
        if _team_completable(ms, cands):
            continue
        pool = [*ms, *cands]
        if not any(s.can(WEB_PROGRAMMER) for s in pool):
            reasons.append(f"팀{t}에 웹 프로그래머를 맡을 학생이 없음")
        elif not any(s.can(PLANNER) for s in pool):
            reasons.append(f"팀{t}에 기획자를 맡을 학생이 없음")
        elif len(ms) >= TEAM_MAX:
            reasons.append(f"팀{t}은 4명이 다 찼는데 필수 포함 역할({', '.join(missing_required(ms))})이 없음")
        else:
            reasons.append(
                f"팀{t}의 남은 자리 {TEAM_MAX - len(ms)}개로 필수 포함 역할"
                f"({', '.join(missing_required(ms))})을 채울 수 없음"
            )

    if not reasons:
        need_w = sum(1 for ms in teams.values() if not any(m.can(WEB_PROGRAMMER) for m in ms))
        have_w = sum(1 for s in unassigned if s.can(WEB_PROGRAMMER))
        need_p = sum(1 for ms in teams.values() if not any(m.can(PLANNER) for m in ms))
        have_p = sum(1 for s in unassigned if s.can(PLANNER))
        if need_w > have_w:
            reasons.append(f"웹 프로그래머가 없는 팀이 {need_w}개인데, 맡을 수 있는 팀이 없는 학생은 {have_w}명뿐임")
        if need_p > have_p:
            reasons.append(f"기획자가 없는 팀이 {need_p}개인데, 맡을 수 있는 팀이 없는 학생은 {have_p}명뿐임")
    if not reasons:
        reasons.append("인원, 주 역할 중복, 필수 포함 역할 조건을 모두 함께 지키는 배정이 없음")
    return reasons
