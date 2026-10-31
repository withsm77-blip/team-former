"""로그인과 비밀번호 해시 (bcrypt)."""
from __future__ import annotations

from dataclasses import dataclass

import bcrypt

from . import db
from .config import ADMIN_STUDENT_NO, get_secret

BCRYPT_ROUNDS = 10


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("ascii")


def check_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class User:
    id: int
    student_no: str
    name: str
    is_admin: bool
    main_role: str | None


def ensure_admin() -> None:
    """관리자 계정(학번 admin)을 ADMIN_PASSWORD 로 만들거나 비밀번호를 갱신한다."""
    password = get_secret("ADMIN_PASSWORD")
    if not password:
        raise db.DBConfigError("ADMIN_PASSWORD 가 설정되지 않음 (.env 또는 Streamlit Secrets)")
    with db.connection() as conn:
        row = conn.execute(
            "select id, password_hash, is_admin from tf_students where student_no = %s",
            (ADMIN_STUDENT_NO,),
        ).fetchone()
        if row is None:
            conn.execute(
                "insert into tf_students (student_no, name, main_role, password_hash, is_admin) "
                "values (%s, %s, null, %s, true)",
                (ADMIN_STUDENT_NO, "관리자", hash_password(password)),
            )
        elif not row["is_admin"] or not check_password(password, row["password_hash"]):
            conn.execute(
                "update tf_students set password_hash = %s, is_admin = true, main_role = null "
                "where id = %s",
                (hash_password(password), row["id"]),
            )


def login(student_no: str, password: str) -> User | None:
    """학번과 비밀번호가 모두 맞을 때만 User 를 돌려준다."""
    student_no = (student_no or "").strip()
    if not student_no or not password:
        return None
    with db.connection() as conn:
        row = conn.execute(
            "select id, student_no, name, is_admin, main_role, password_hash "
            "from tf_students where student_no = %s",
            (student_no,),
        ).fetchone()
    if row is None or not check_password(password, row["password_hash"]):
        return None
    return User(row["id"], row["student_no"], row["name"], row["is_admin"], row["main_role"])


def is_admin(user_id: int) -> bool:
    """관리자 기능은 화면이 아니라 DB 기준으로 다시 확인한다 (공고 6.3절 11번)."""
    with db.connection() as conn:
        row = conn.execute("select is_admin from tf_students where id = %s", (user_id,)).fetchone()
    return bool(row and row["is_admin"])
