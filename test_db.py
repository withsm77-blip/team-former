"""Supabase DB에 접속되는지 시험합니다. 비밀번호는 출력하지 않습니다.
실행: python test_db.py   (먼저 pip install -r requirements.txt)
.env 의 주소(5432)로 먼저 시도하고, 실패하면 같은 주소의 6543 포트(Transaction pooler)도 시도합니다.
"""
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))
url = os.getenv("DATABASE_URL")
if not url:
    raise SystemExit(".env 에 DATABASE_URL 이 없습니다.")


def try_connect(u: str, label: str) -> bool:
    try:
        # prepare_threshold=None: 6543(Transaction pooler)에서도 오류 없이 쓰기 위함
        with psycopg.connect(u, connect_timeout=10, prepare_threshold=None) as conn:
            ver = conn.execute("select version()").fetchone()[0]
            tables = [r[0] for r in conn.execute(
                "select table_name from information_schema.tables "
                "where table_schema = 'public' order by 1")]
        print(f"[{label}] 접속 성공:", ver.split(",")[0])
        print(f"[{label}] public 테이블:", tables or "(없음)")
        return True
    except Exception as e:  # noqa: BLE001
        msg = str(e).splitlines()[0]
        print(f"[{label}] 접속 실패: {type(e).__name__} {msg}")
        return False


ok = try_connect(url, "5432 Session pooler")
if not ok and ":5432/" in url:
    if try_connect(url.replace(":5432/", ":6543/"), "6543 Transaction pooler"):
        print("\n=> 6543 포트는 됩니다. .env 의 :5432 를 :6543 으로 바꿔 쓰세요.")
    else:
        print("\n=> 두 포트 모두 막혔습니다. 휴대폰 핫스팟 등 다른 인터넷으로 다시 시도해 보세요.")
