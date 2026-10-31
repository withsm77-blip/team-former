"""무료 AI 모델 후보를 한 번씩 불러 보고, 쉬움 명단 8명 배정안이 검사를 통과하는지 확인한다.
실행: py test_models.py      (AI 호출 = 후보 수만큼. 무료 한도는 하루 50회)
"""
import time
from concurrent.futures import ThreadPoolExecutor

from tfapp import ai, rules
from tfapp.rules import Student

CANDIDATES = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "qwen/qwen3.8-27b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "poolside/laguna-s-2.1:free",
]

R = {"PL": "기획자", "WP": "웹 프로그래머", "FD": "프론트엔드 디자이너", "TT": "테스터", "RW": "보고서 작성자", "PT": "발표자"}
codes = ["PL;RW", "PL;PT", "PL;TT", "WP;FD", "WP", "WP;TT", "FD;PT", "TT;RW;PT"]
students = []
for i, c in enumerate(codes, 1):
    p = c.split(";")
    students.append(Student(i, str(2026100 + i), f"학생{i}", R[p[0]], tuple(R[x] for x in p[1:])))
teams = {1: [], 2: [], 3: []}
messages = ai.build_messages(teams, students)


def try_model(model):
    t0 = time.monotonic()
    try:
        text = ai.call_openrouter(model, messages, 120)
        proposal = ai.parse_proposal(text)
        problems = rules.validate_proposal(teams, students, proposal)
        verdict = "통과" if not problems else "검사 실패: " + "; ".join(problems[:2])
    except ai.AIFailure as f:
        verdict = f"{f.kind}: {f.detail[:120]}"
    return model, verdict, time.monotonic() - t0


print("후보", len(CANDIDATES), "개를 동시에 시험합니다 (최대 120초)...")
with ThreadPoolExecutor(len(CANDIDATES)) as pool:
    for model, verdict, sec in pool.map(try_model, CANDIDATES):
        print(f"{sec:5.0f}초  {model:45s} {verdict}")
