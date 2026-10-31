"""비밀 값이 준비되었는지 '있음/없음'만 출력합니다. 값 자체는 절대 출력하지 않습니다."""
import os
from pathlib import Path

from dotenv import load_dotenv

env_path = Path(__file__).with_name(".env")
print(f".env 파일: {'있음' if env_path.exists() else '없음'}")
if Path(__file__).with_name(".env.txt").exists():
    print("경고: .env.txt 파일이 있습니다. 이름을 .env 로 바꾸세요.")

load_dotenv(env_path)
for name in ("OPENROUTER_API_KEY", "DATABASE_URL", "ADMIN_PASSWORD"):
    print(f"{name}: {'있음' if os.getenv(name) else '없음'}")
