"""관리자 화면."""
from __future__ import annotations

import streamlit as st

from . import ai, db, service
from .auth import User
from .config import AUTO_REFRESH_SEC, TEAM_MAX, ai_models, fmt_kst
from .ui_common import member_line, roles_text, set_result, show_result

LINES_KEY = "assign_lines"


def _guard(fn, *args) -> None:
    try:
        set_result(fn(*args))
    except Exception as exc:
        set_result(f"막힘: 처리 중 오류가 남 ({db.safe_error(exc)})")


def _on_start(uid: int) -> None:
    f = st.session_state.get("admin_roster_file")
    data = f.getvalue() if f is not None else None
    st.session_state.pop(LINES_KEY, None)
    _guard(service.reset_with_roster, uid, data, st.session_state.get("admin_team_count", 3))


def _on_close(uid: int) -> None:
    _guard(service.close_recruitment, uid)


def _on_confirm(uid: int) -> None:
    _guard(service.confirm, uid)


def render(user: User) -> None:
    uid = user.id
    show_result()

    # ---------------------------------------------------------- 1. 명단
    st.subheader("1. 명단으로 처음 상태 만들기")
    c1, c2, c3 = st.columns([3, 1, 1.4])
    with c1:
        st.file_uploader("명단 CSV (student_no,name,main_role,sub_roles)", type=["csv"], key="admin_roster_file")
    with c2:
        st.number_input("팀 수", min_value=1, max_value=20, value=3, step=1, key="admin_team_count")
    with c3:
        st.write("")
        st.button("처음 상태 만들기", key="admin_start", on_click=_on_start, args=(uid,), type="primary",
                  use_container_width=True)
    st.caption("지원, 투표, 팀원, 마감 표시, 배정안을 모두 지우고 명단의 학생과 빈 팀을 만듭니다. 관리자 계정은 남습니다.")

    # ---------------------------------------------------------- 2. 마감
    st.subheader("2. 모집 마감")
    st.button("모집 마감", key="admin_close", on_click=_on_close, args=(uid,))

    # ---------------------------------------------------------- 3. 현황 (자동 갱신)
    st.subheader("3. 현황")
    _admin_live()

    # ---------------------------------------------------------- 4. 배정
    st.subheader("4. 배정")
    st.caption("1차 → 2차 → 3차: " + " → ".join(ai_models()) + " · 모델마다 120초 제한 · 3차까지 실패하면 앱이 스스로 배정")
    clicked = st.button("AI 배정", key="admin_assign", type="primary")
    with st.container(key="assign_status", border=True):
        ph = st.empty()
    if clicked:
        def on_status(text: str) -> None:
            ph.info(text.replace("\n", "  \n"))

        try:
            out = ai.run_assignment(uid, on_status)
            set_result(out.result)
            st.session_state[LINES_KEY] = out.status_lines
        except Exception as exc:
            set_result(f"막힘: 배정 중 오류가 남 ({db.safe_error(exc)})")
        st.rerun()
    lines = st.session_state.get(LINES_KEY)
    if lines:
        ph.markdown("  \n".join(lines))
    else:
        ph.caption("AI 배정을 누르면 진행 상황이 여기에 표시됩니다.")

    _proposal_view(uid)

    # ---------------------------------------------------------- 5. AI 호출 기록
    with st.expander("AI 호출 기록 (최근 30건)"):
        try:
            logs = service.recent_ai_logs()
        except Exception as exc:
            st.error(db.safe_error(exc))
            logs = []
        if logs:
            st.dataframe(
                [{"시각": fmt_kst(r["created_at"], True), "차수": f"{r['round']}차" if r["round"] else "앱",
                  "모델": r["model"],
                  "결과": r["outcome"], "걸린 시간(초)": float(r["elapsed_sec"]) if r["elapsed_sec"] is not None else None,
                  "내용": (r["detail"] or "")[:200]} for r in logs],
                hide_index=True, use_container_width=True,
            )
        else:
            st.caption("기록 없음")


@st.fragment(run_every=AUTO_REFRESH_SEC)
def _admin_live() -> None:
    try:
        state = service.load_state()
    except Exception as exc:
        st.error(f"DB에서 상태를 읽지 못함: {db.safe_error(exc)}")
        return
    flags = []
    flags.append("모집 마감됨" if state.closed else "모집 중")
    if state.confirmed:
        flags.append("배정 확정됨")
    st.markdown(f"**상태**: {' · '.join(flags)} · 학생 {len(state.students)}명 · 팀 {len(state.teams)}개")

    team_ids = sorted(state.teams)
    per_row = 4 if len(team_ids) >= 4 else max(1, len(team_ids))
    for start in range(0, len(team_ids), per_row):
        row = st.columns(per_row)
        for col, t in zip(row, team_ids[start:start + per_row]):
            members = state.members.get(t, [])
            with col:
                with st.container(key=f"team_{t}", border=True):
                    st.markdown(f"**{state.teams[t]}** ({len(members)}/{TEAM_MAX}명)")
                    st.markdown("\n".join(member_line(m) for m in members) if members else "_팀원 없음_")
                    for app in state.pending_for_team(t):
                        yes = sum(1 for m in members if app.votes.get(m.id) == "찬성")
                        st.caption(f"대기: {app.student.name} (찬성 {yes}/{len(members)})")

    st.markdown("**팀이 없는 학생**")
    with st.container(key="unassigned", border=True):
        un = state.unassigned
        if un:
            st.markdown("\n".join(f"- {s.name} — {roles_text(s)}" for s in un))
        else:
            st.markdown("없음")


def _proposal_view(uid: int) -> None:
    try:
        state = service.load_state()
    except Exception as exc:
        st.error(db.safe_error(exc))
        return
    prop = state.proposal
    if prop and not state.confirmed:
        by_no = {s.student_no: s for s in state.students.values()}
        rows = []
        for a in prop.get("assignments", []):
            s = by_no.get(str(a["student_no"]))
            if s:
                rows.append({"이름": s.name, "학번": s.student_no, "주 역할": s.main_role,
                             "보조 역할": ", ".join(s.sub_roles) or "없음", "배정 팀": f"팀{a['team']}"})
        st.markdown(f"**배정안** (검사 다섯 가지 통과 · 낸 곳: {prop.get('source', '')})")
        if rows:
            st.dataframe(rows, hide_index=True, use_container_width=True)
        else:
            st.caption("배정할 학생이 없습니다.")
    elif state.confirmed:
        st.success("배정을 확정했습니다. 위 현황이 확정된 팀 구성입니다.")
    st.button("배정안 확정", key="admin_confirm", on_click=_on_confirm, args=(uid,),
              disabled=state.confirmed, type="primary")
