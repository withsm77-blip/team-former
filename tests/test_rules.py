"""rules.py 단위 테스트: 공고의 예시를 그대로 옮김."""
import itertools

from tfapp.rules import (
    REASON_DUP,
    REASON_FULL,
    REASON_SEATS,
    Student,
    check_join,
    required_filled,
    solve,
    validate_proposal,
)

_ids = itertools.count(1)


def S(main, *subs, no=None, name=None):
    i = next(_ids)
    return Student(i, no or f"9{i:05d}", name or f"학생{i}", main, tuple(subs))


PL, WP, FD, TT, RW, PT = "기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자"


def test_required_filled_one_student_both_roles_counts_one():
    # 공고 3장 예: 주 역할 기획자, 보조 역할 웹 프로그래머인 학생 혼자 -> 1
    assert required_filled([S(PL, WP)]) == 1


def test_required_filled_cases():
    assert required_filled([]) == 0
    assert required_filled([S(TT), S(PT)]) == 0
    assert required_filled([S(PL), S(WP)]) == 2
    assert required_filled([S(TT, PL), S(FD, WP)]) == 2   # 보조 역할로 채움
    assert required_filled([S(PL), S(TT, PL)]) == 1        # 기획자만 둘
    assert required_filled([S(PL, WP), S(TT)]) == 1


def test_seat_rule_example_from_notice():
    # 공고 3장 또 다른 예: 테스터, 발표자 팀(2명)에 보고서 작성자 지원 -> 막음
    assert check_join([S(TT), S(PT)], S(RW)) == REASON_SEATS
    # 같은 팀에 웹 프로그래머 지원 -> 남는 자리 1, 못 채운 역할 1 -> 허용
    assert check_join([S(TT), S(PT)], S(WP)) is None


def test_full_and_duplicate():
    team = [S(PL), S(WP), S(FD), S(TT)]
    assert check_join(team, S(RW)) == REASON_FULL
    assert check_join([S(PL)], S(PL, WP)) == REASON_DUP


def test_hard_scenario_step11_blocked():
    # 부록 3 11단계: 팀3 = 학생11(TT;PT), 학생15(PT;FD) 에 학생14(RW;TT) 지원 -> 막힘(자리)
    assert check_join([S(TT, PT), S(PT, FD)], S(RW, TT)) == REASON_SEATS


def test_hard_scenario_step13_allowed():
    # 부록 3 13단계: 팀1 = PL;RW, WP;PL, FD;PT;TT 에 RW;PT;FD 지원 -> 대기(막지 않음)
    assert check_join([S(PL, RW), S(WP, PL), S(FD, PT, TT)], S(RW, PT, FD)) is None


def test_validate_five_checks():
    a, b = S(PL, no="a"), S(WP, no="b")
    c, d = S(FD, no="c"), S(TT, WP, no="d")
    e = S(PL, no="e")
    teams = {1: [a, b], 2: [e]}
    un = [c, d]
    assert validate_proposal(teams, un, [("c", 1), ("d", 2)]) == []
    assert any("배정되지 않은" in x for x in validate_proposal(teams, un, [("c", 1)]))
    assert any("두 팀 이상" in x for x in validate_proposal(teams, un, [("c", 1), ("d", 2), ("c", 2)]))
    assert any("이미 팀이 있는" in x for x in validate_proposal(teams, un, [("c", 1), ("d", 2), ("a", 2)]))
    assert any("없는 팀" in x for x in validate_proposal(teams, un, [("c", 9), ("d", 2)]))
    # 팀2에 웹 프로그래머가 없게 배정
    assert any("필수 포함 역할" in x for x in validate_proposal(teams, un, [("c", 2), ("d", 1)]))
    # 주 역할 중복
    f = S(FD, no="f")
    assert any("중복" in x for x in validate_proposal({1: [a, b, c], 2: [e, d]}, [f], [("f", 1)]))


def test_solve_finds_assignment_with_sub_roles():
    # 팀2(기획자 하나뿐)에 웹 프로그래머 보조 역할을 가진 학생을 넣어야만 풀림
    teams = {1: [S(PL), S(WP)], 2: [S(PL)], 3: []}
    un = [S(FD, WP), S(TT), S(RW, PL), S(PT, WP)]
    res = solve(teams, un)
    assert res.proposal is not None
    assert validate_proposal(teams, un, res.proposal) == []


def test_solve_infeasible_reason_names_team():
    # 팀3: 4명이 다 찼는데 웹 프로그래머가 없음 -> 규칙을 지키는 배정 없음
    teams = {1: [S(PL), S(WP)], 2: [S(PL), S(WP)], 3: [S(PL), S(FD), S(TT), S(RW)]}
    res = solve(teams, [S(PT)])
    assert res.proposal is None
    assert any("팀3" in r for r in res.reasons)


def test_solve_infeasible_no_web_programmer_left():
    teams = {1: [S(PL)], 2: [S(PL)]}
    un = [S(WP), S(TT)]  # 웹 프로그래머 1명뿐인데 두 팀 모두 필요
    res = solve(teams, un)
    assert res.proposal is None
    assert res.reasons


def test_solve_eight_unassigned_four_empty_teams_fast():
    # 기획자 가능 4명, 웹 프로그래머 가능 4명 -> 4팀 모두 채울 수 있음 (보조 역할 필요)
    un = [S(PL), S(TT, PL), S(RW, PL), S(PT, PL), S(WP), S(FD, WP), S(TT, WP), S(RW, WP)]
    res = solve({1: [], 2: [], 3: [], 4: []}, un)
    assert res.proposal is not None
    assert validate_proposal({1: [], 2: [], 3: [], 4: []}, un, res.proposal) == []


def test_solve_four_teams_with_only_two_planners_is_infeasible():
    un = [S(PL), S(PL, TT), S(WP), S(WP, FD), S(FD), S(TT), S(RW), S(PT)]
    res = solve({1: [], 2: [], 3: [], 4: []}, un)
    assert res.proposal is None
    assert any("기획자" in r for r in res.reasons)


def test_solve_sixteen_unassigned_is_bounded():
    roles = [PL, WP, FD, TT, RW, PT]
    un = [S(roles[i % 6], roles[(i + 1) % 6]) for i in range(16)]
    res = solve({1: [], 2: [], 3: [], 4: []}, un)
    # 답이 있든 없든 한도 안에서 끝나야 함
    assert res.nodes <= 3_000_001
