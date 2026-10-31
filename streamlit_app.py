"""팀 편성 웹 앱 (Team Former) - 진입점.

실행: streamlit run streamlit_app.py
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from tfapp import auth, db
from tfapp.config import APP_DIR
from tfapp.ui_common import inject_css, set_result, show_result

st.set_page_config(page_title="팀 편성", page_icon="👥", layout="wide")
inject_css()


@st.cache_resource(show_spinner="DB를 준비하는 중...")
def bootstrap() -> bool:
    """앱이 시작할 때 한 번: 테이블이 없으면 만들고(schema.sql), 관리자 계정을 만들거나 비밀번호를 갱신한다."""
    schema = Path(APP_DIR / "schema.sql").read_text(encoding="utf-8")
    with db.connection() as conn:
        conn.execute(schema)
    auth.ensure_admin()
    return True


def _on_login() -> None:
    no = st.session_state.get("login_student_no", "")
    pw = st.session_state.get("login_password", "")
    try:
        user = auth.login(no, pw)
    except Exception as exc:
        set_result(f"막힘: 로그인 중 오류가 남 ({db.safe_error(exc)})")
        return
    if user is None:
        set_result("막힘: 학번 또는 비밀번호가 틀림")
        return
    st.session_state["user"] = user
    set_result(f"승인: {user.name}(으)로 로그인함")


def _on_logout() -> None:
    for k in list(st.session_state.keys()):
        del st.session_state[k]


def login_page() -> None:
    st.title("👥 팀 편성")
    st.caption("학번과 비밀번호로 로그인하세요.")
    show_result()
    with st.form("login_form"):
        st.text_input("학번", key="login_student_no")
        st.text_input("비밀번호", type="password", key="login_password")
        st.form_submit_button("로그인", key="login_submit", on_click=_on_login, type="primary")


def main() -> None:
    try:
        bootstrap()
    except Exception as exc:
        st.error(f"앱을 시작하지 못함: {db.safe_error(exc)}")
        st.info("DATABASE_URL, ADMIN_PASSWORD 가 .env 또는 Streamlit Secrets 에 있는지 확인하세요.")
        st.stop()

    user = st.session_state.get("user")
    if user is None:
        login_page()
        return

    head, out = st.columns([5, 1])
    with head:
        with st.container(key="whoami"):
            who = "관리자" if user.is_admin else f"학생 · {user.main_role}"
            st.markdown(f"### 👤 {user.name} ({user.student_no}) · {who}")
    with out:
        st.button("로그아웃", on_click=_on_logout, use_container_width=True)

    if user.is_admin:
        from tfapp import ui_admin
        ui_admin.render(user)
    else:
        from tfapp import ui_student
        ui_student.render(user)


main()
