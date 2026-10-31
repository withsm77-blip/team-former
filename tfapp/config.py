"""설정과 비밀 값 읽기.

비밀 값은 배포한 앱에서는 Streamlit Secrets, 로컬에서는 .env 에서 읽는다.
비밀 값 자체를 화면이나 로그에 출력하는 함수는 두지 않는다.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

APP_DIR = Path(__file__).resolve().parent.parent
load_dotenv(APP_DIR / ".env")

# ---------------------------------------------------------------- 역할과 규칙
ROLES = ("기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자")
PLANNER = "기획자"
WEB_PROGRAMMER = "웹 프로그래머"
REQUIRED_ROLES = (PLANNER, WEB_PROGRAMMER)
TEAM_MIN = 2
TEAM_MAX = 4
STUDENT_DEFAULT_PASSWORD = "1234"
ADMIN_STUDENT_NO = "admin"

# ---------------------------------------------------------------- AI
# 1차, 2차, 3차 모델. Secrets/.env 의 AI_MODELS(쉼표로 구분)로 바꿀 수 있음.
DEFAULT_MODELS = (
    "nvidia/nemotron-3-ultra-550b-a55b:free",   # 10/31 시험: 통과 (25초)
    "nvidia/nemotron-3.5-lightning:free",       # 10/31 시험: 통과 (32초)
    "poolside/laguna-s-2.1:free",               # 10/31 시험: 일시적 429. 제공사가 달라 NVIDIA 장애에 대비
)
AI_TOTAL_TIMEOUT_SEC = 120          # 모델 하나를 부른 때부터 응답을 다 받을 때까지의 전체 제한
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# ---------------------------------------------------------------- 화면
AUTO_REFRESH_SEC = 3                # 다른 사람의 변경을 다시 그리는 간격 (5초 이하)
KST = timezone(timedelta(hours=9))


def get_secret(name: str) -> str | None:
    """Streamlit Secrets → 환경 변수(.env) 순서로 찾는다. 없으면 None."""
    try:
        import streamlit as st

        if name in st.secrets:
            value = st.secrets[name]
            return str(value) if value is not None else None
    except Exception:  # secrets.toml 이 없거나 streamlit 밖에서 실행할 때
        pass
    value = os.getenv(name)
    return value if value else None


def ai_models() -> list[str]:
    raw = get_secret("AI_MODELS")
    if raw:
        models = [m.strip() for m in raw.split(",") if m.strip()]
        if models:
            return models[:3]
    return list(DEFAULT_MODELS)


def ai_timeout_sec() -> float:
    """시험용으로 AI_TIMEOUT_SEC 를 줄일 수 있음(실패 처리 확인용). 기본 120초."""
    raw = get_secret("AI_TIMEOUT_SEC")
    try:
        return float(raw) if raw else float(AI_TOTAL_TIMEOUT_SEC)
    except ValueError:
        return float(AI_TOTAL_TIMEOUT_SEC)


def now_kst() -> datetime:
    return datetime.now(KST)


def fmt_kst(dt: datetime | None, with_date: bool = False) -> str:
    """DB 시각(UTC)을 한국 시각 문자열로."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST).strftime("%m/%d %H:%M:%S" if with_date else "%H:%M:%S")
