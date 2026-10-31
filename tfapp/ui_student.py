"""학생 화면. st.fragment 로 3초마다 바뀐 부분만 다시 그린다 (다른 사람의 변경 보기: 어려운 해결)."""
from __future__ import annotations

import streamlit as st

from . import db, service
from .auth import User
from .config import AUTO_REFRESH_SEC, TEAM_MAX, fmt_kst
from .ui_common import member_line, roles_text, set_result, show_result


def _on_apply(student_id: int, team_id: int) -> None:
    try:
        set_result(service.apply(student_id, team_id))
    except Exception as exc:  # DB 오류는 주소를 가리고 보여 줌
        set_result(f"막힘: 처리 중 오류가 남 ({db.safe_error(exc)})")


def _on_vote(voter_id: int, applicant_no: str, agree: bool) -> None:
    try:
        set_result(service.vote(voter_id, applicant_no, agree))
    except Exception as exc:
        set_result(f"막힘: 처리 중 오류가 남 ({db.safe_error(exc)})")


def render(user: User) -> None:
    _student_live(user.id)


@st.fragment(run_every=AUTO_REFRESH_SEC)
def _student_live(uid: int) -> None:
    show_result()
    try:
        state = service.load_state(uid)
    except Exception as exc:
        st.error(f"DB에서 상태를 읽지 못함: {db.safe_error(exc)}")
        return
    me = state.students.get(uid)
    if me is None:
        st.warning("명단이 새로 올라와 내 계정이 없어졌습니다. 로그아웃 후 다시 로그인하세요.")
        return

    my_team = state.team_of.get(uid)
    my_pending = state.my_pending(uid)
    can_apply = my_team is None and my_pending is None and not state.closed

    # ---------------------------------------------------------- 내 상태
    if state.confirmed:
        st.success("팀 편성이 확정되었습니다.")
    elif state.closed:
        st.warning("모집이 마감되었습니다. 지원과 투표를 할 수 없습니다.")
    cols = st.columns(2)
    with cols[0]:
        st.markdown(f"**내 역할**: {roles_text(me)}")
        st.markdown(f"**내 팀**: {state.teams[my_team]}" if my_team else "**내 팀**: 아직 팀이 없음")
    with cols[1]:
        latest = state.my_latest(uid)
        if latest:
            team_name = state.teams.get(latest.team_id, f"팀{latest.team_id}")
            if latest.status == "대기":
                total = len(state.members.get(latest.team_id, []))
                yes = sum(1 for v in latest.votes.values() if v == "찬성")
                st.markdown(f"**내 지원**: {team_name} · 대기 (찬성 {yes}/{total})")
            else:
                why = f" — {latest.reason}" if latest.reason else ""
                st.markdown(f"**내 지원**: {team_name} · {latest.status}{why}")
        else:
            st.markdown("**내 지원**: 없음")

    # ---------------------------------------------------------- 내 팀에 들어온 지원 (투표)
    if my_team is not None:
        pending = state.pending_for_team(my_team)
        st.subheader("내 팀에 들어온 지원")
        if not pending:
            st.caption("기다리는 지원이 없습니다.")
        members = state.members.get(my_team, [])
        for app in pending:
            yes = sum(1 for m in members if app.votes.get(m.id) == "찬성")
            with st.container(border=True):
                c1, c2, c3 = st.columns([5, 1.3, 1.3])
                c1.markdown(f"**{app.student.name}** ({app.student.student_no}) — {roles_text(app.student)}  \n"
                            f"찬성 {yes}/{len(members)} · 지원 시각 {fmt_kst(app.created_at)}")
                my_vote = app.votes.get(uid)
                if my_vote:
                    c2.markdown(f"내 투표: **{my_vote}**")
                elif not state.closed:
                    no = app.student.student_no
                    c2.button("찬성", key=f"vote_yes_{no}", on_click=_on_vote, args=(uid, no, True),
                              type="primary", use_container_width=True)
                    c3.button("거부", key=f"vote_no_{no}", on_click=_on_vote, args=(uid, no, False),
                              use_container_width=True)

    # ---------------------------------------------------------- 팀 목록
    st.subheader("팀 목록")
    team_ids = sorted(state.teams)
    per_row = 4 if len(team_ids) >= 4 else max(1, len(team_ids))
    for start in range(0, len(team_ids), per_row):
        row = st.columns(per_row)
        for col, t in zip(row, team_ids[start:start + per_row]):
            members = state.members.get(t, [])
            with col:
                with st.container(key=f"team_{t}", border=True):
                    badge = " <span class='tf-badge'>내 팀</span>" if t == my_team else ""
                    st.markdown(f"**{state.teams[t]}** ({len(members)}/{TEAM_MAX}명){badge}",
                                unsafe_allow_html=True)
                    if members:
                        st.markdown("\n".join(member_line(m, uid) for m in members))
                    else:
                        st.caption("아직 팀원이 없음")
                    waiting = len(state.pending_for_team(t))
                    if waiting:
                        st.caption(f"대기 중인 지원 {waiting}건")
                    st.button(f"{state.teams[t]}에 지원", key=f"apply_{t}", on_click=_on_apply, args=(uid, t),
                              disabled=not can_apply, use_container_width=True)
    if not can_apply and not state.closed:
        st.caption("이미 팀이 있거나 결과를 기다리는 지원이 있으면 지원 단추가 비활성화됩니다.")
