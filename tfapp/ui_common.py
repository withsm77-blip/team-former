"""여러 화면이 함께 쓰는 표시 함수."""
from __future__ import annotations

import streamlit as st

from .rules import Student

RESULT_KEY = "result_text"


def set_result(text: str) -> None:
    st.session_state[RESULT_KEY] = text


def show_result() -> None:
    """단추를 누른 결과 안내. key 가 result 인 container 안에 보여 준다 (화면 한 번에 한 번만 호출)."""
    text = st.session_state.get(RESULT_KEY)
    with st.container(key="result"):
        if not text:
            return
        kind = text.split(":", 1)[0]
        if kind == "승인":
            st.success(text)
        elif kind == "대기":
            st.info(text)
        elif kind == "거절":
            st.error(text)
        else:
            st.warning(text)


def roles_text(s: Student) -> str:
    subs = ", ".join(s.sub_roles) if s.sub_roles else "없음"
    return f"{s.main_role} · 보조: {subs}"


def member_line(s: Student, me_id: int | None = None) -> str:
    me = " **(나)**" if me_id == s.id else ""
    return f"- {s.name}{me} — {roles_text(s)}"


def inject_css() -> None:
    st.markdown(
        """
        <style>
        div[class*="st-key-team_"] { min-height: 100%; }
        .tf-badge { display:inline-block; padding:1px 8px; border-radius:10px; font-size:0.8rem;
                    background:#e8f5e9; color:#1b5e20; margin-left:6px; }
        @media (prefers-color-scheme: dark) { .tf-badge { background:#1b5e20; color:#e8f5e9; } }
        </style>
        """,
        unsafe_allow_html=True,
    )
