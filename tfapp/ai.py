"""AI 배정: OpenRouter 모델을 1차 → 2차 → 3차 순으로 부르고, 모두 실패하면 앱이 스스로 배정한다.

- 모델마다 '부른 때부터 응답을 다 받을 때까지' 전체 120초를 재서 끊는다(응답이 천천히 와도 120초에서 끊음).
- 응답 없음, HTTP 오류(429 등), 빈 응답, 형식 오류, 배정안 검사 실패는 모두 실패로 보고 다음 모델로 넘어간다.
- 무료 모델 자동 라우터(openrouter/free)는 쓰지 않는다.
- API 키는 요청 헤더에만 쓰고 화면, 기록, 오류 메시지에 넣지 않는다.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import requests

from . import rules, service
from .config import OPENROUTER_URL, TEAM_MAX, TEAM_MIN, ai_models, ai_timeout_sec, get_secret
from .rules import Student

StatusFn = Callable[[str], None]


class AIFailure(Exception):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


# ------------------------------------------------------------------ 요청 내용
def build_messages(teams: Mapping[int, Sequence[Student]], unassigned: Sequence[Student]) -> list[dict]:
    def person(s: Student) -> dict:
        return {"student_no": s.student_no, "name": s.name, "main_role": s.main_role, "sub_roles": list(s.sub_roles)}

    data = {
        "teams": [{"team": t, "members": [person(m) for m in teams[t]], "free_seats": TEAM_MAX - len(teams[t])}
                  for t in sorted(teams)],
        "unassigned_students": [person(s) for s in unassigned],
    }
    rules_text = f"""다음 규칙을 모두 지키도록 팀이 없는 학생(unassigned_students)을 팀에 배정하라.
1. 팀이 없는 학생을 한 명도 빠짐없이, 각자 정확히 한 팀에 배정한다.
2. 이미 팀에 있는 학생(members)은 옮기지 않으며 답에 적지 않는다.
3. 배정 뒤 모든 팀의 인원은 {TEAM_MIN}~{TEAM_MAX}명이다(빈 팀 없음).
4. 한 팀 안에서 주 역할(main_role)이 같은 학생이 2명 이상이면 안 된다.
5. 모든 팀에는 '기획자'를 맡을 학생과 '웹 프로그래머'를 맡을 학생이 서로 다른 사람으로 1명씩 있어야 한다.
   주 역할이나 보조 역할(sub_roles) 가운데 하나로 맡을 수 있다. 한 학생이 두 역할을 동시에 맡을 수는 없다.

출력은 설명 없이 아래 형식의 JSON 하나만 낸다.
{{"assignments": [{{"student_no": "학번", "team": 팀번호}}]}}"""
    return [
        {"role": "system", "content": "너는 팀 편성 도우미다. 규칙을 정확히 지키고, 요청한 JSON만 출력한다."},
        {"role": "user", "content": rules_text + "\n\n현재 상태:\n" + json.dumps(data, ensure_ascii=False, indent=1)},
    ]


# ------------------------------------------------------------------ 응답 해석
_FENCE_RE = re.compile(r"```(?:json)?", re.IGNORECASE)


def parse_proposal(text: str) -> list[tuple[str, int]]:
    if not text or not text.strip():
        raise AIFailure("빈 응답", "모델이 내용 없는 응답을 냄")
    cleaned = _FENCE_RE.sub("", text)
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise AIFailure("형식 오류", f"JSON을 찾을 수 없음: {text.strip()[:120]}")
    try:
        obj = json.loads(cleaned[start:end + 1])
    except json.JSONDecodeError as exc:
        raise AIFailure("형식 오류", f"JSON을 읽을 수 없음 ({exc.msg})") from exc
    items = obj.get("assignments") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        raise AIFailure("형식 오류", "assignments 목록이 없음")
    out: list[tuple[str, int]] = []
    for it in items:
        if not isinstance(it, dict) or "student_no" not in it or "team" not in it:
            raise AIFailure("형식 오류", f"항목 형식이 틀림: {str(it)[:80]}")
        digits = re.findall(r"\d+", str(it["team"]))
        if not digits:
            raise AIFailure("형식 오류", f"팀 번호를 읽을 수 없음: {it['team']}")
        out.append((str(it["student_no"]).strip(), int(digits[0])))
    return out


# ------------------------------------------------------------------ OpenRouter 호출 (전체 시간 제한)
def call_openrouter(
    model: str,
    messages: list[dict],
    total_timeout: float,
    tick: Callable[[float], None] | None = None,
    url: str | None = None,
    api_key: str | None = None,
) -> str:
    """모델 응답 본문을 돌려준다. total_timeout 초 안에 응답을 다 받지 못하면 AIFailure('시간 초과')."""
    url = url or get_secret("OPENROUTER_URL") or OPENROUTER_URL   # OPENROUTER_URL 은 시험용(가짜 서버)
    api_key = api_key or get_secret("OPENROUTER_API_KEY")
    if not api_key:
        raise AIFailure("설정 오류", "OPENROUTER_API_KEY 가 설정되지 않음")
    start = time.monotonic()
    cancel = threading.Event()
    holder: dict = {}

    def worker() -> str:
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": 4000,
            "temperature": 0.2,
            "reasoning": {"effort": "low"},
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                   "X-Title": "Team Former"}
        try:
            resp = requests.post(url, json=payload, headers=headers, stream=True,
                                 timeout=(10, total_timeout + 5))
            holder["resp"] = resp
            if cancel.is_set():
                resp.close()
                raise AIFailure("시간 초과", "")
            if resp.status_code != 200:
                body = resp.text[:300].replace("\n", " ")
                raise AIFailure("HTTP 오류", f"HTTP {resp.status_code}: {_error_message(body)}")
            parts: list[str] = []
            for raw in resp.iter_lines():
                if cancel.is_set():
                    raise AIFailure("시간 초과", "")
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace")
                if line.startswith(":") or not line.startswith("data:"):
                    continue          # ": OPENROUTER PROCESSING" 같은 유지 신호
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if obj.get("error"):
                    raise AIFailure("HTTP 오류", _error_message(json.dumps(obj["error"], ensure_ascii=False)))
                for ch in obj.get("choices", []):
                    piece = (ch.get("delta") or {}).get("content")
                    if piece:
                        parts.append(piece)
            return "".join(parts)
        except AIFailure:
            raise
        except requests.Timeout as exc:
            raise AIFailure("시간 초과", "연결이나 읽기 시간이 초과됨") from exc
        except requests.RequestException as exc:
            raise AIFailure("연결 오류", type(exc).__name__) from exc

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(worker)
    try:
        while True:
            elapsed = time.monotonic() - start
            remaining = total_timeout - elapsed
            if remaining <= 0:
                cancel.set()
                # 읽는 중인 연결을 닫는 일은 막힐 수 있으므로 따로 실행하고 바로 다음 모델로 넘어간다
                threading.Thread(target=_close_quietly, args=(holder,), daemon=True).start()
                raise AIFailure("시간 초과", f"{total_timeout:.0f}초 안에 응답을 다 받지 못해 끊음")
            try:
                return future.result(timeout=min(1.0, remaining))
            except FuturesTimeout:
                if tick:
                    tick(time.monotonic() - start)
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _close_quietly(holder: dict) -> None:
    resp = holder.get("resp")
    if resp is None:
        return
    try:
        sock = getattr(getattr(getattr(resp.raw, "_fp", None), "fp", None), "raw", None)
        sock = getattr(sock, "_sock", None)
        if sock is not None:
            import socket

            sock.shutdown(socket.SHUT_RDWR)   # 읽기 대기 중인 스레드를 바로 깨움
    except Exception:
        pass
    try:
        resp.close()
    except Exception:
        pass


def _error_message(body: str) -> str:
    try:
        obj = json.loads(body)
        if isinstance(obj, dict):
            err = obj.get("error", obj)
            if isinstance(err, dict):
                return str(err.get("message") or err.get("code") or err)[:200]
    except (json.JSONDecodeError, TypeError):
        pass
    return body[:200]


# ------------------------------------------------------------------ 전체 배정 흐름
@dataclass
class AssignOutcome:
    result: str                 # result 안내 (승인/막힘으로 시작)
    status_lines: list[str]     # assign_status 에 남길 진행 기록


def run_assignment(
    actor_id: int,
    on_status: StatusFn | None = None,
    models: Sequence[str] | None = None,
    timeout: float | None = None,
    caller: Callable[..., str] = call_openrouter,
) -> AssignOutcome:
    lines: list[str] = []

    def status(line: str, replace_last: bool = False) -> None:
        if replace_last and lines:
            lines[-1] = line
        else:
            lines.append(line)
        if on_status:
            on_status("\n".join(lines))

    from .auth import is_admin

    if not is_admin(actor_id):
        return AssignOutcome(service._msg(service.BLOCKED, "관리자만 할 수 있음"), [])
    teams, unassigned, closed, confirmed = service.assign_context()
    if not teams:
        return AssignOutcome(service._msg(service.BLOCKED, "명단을 먼저 올려야 함"), [])
    if not closed:
        return AssignOutcome(service._msg(service.BLOCKED, "먼저 모집을 마감해야 함"), [])
    if confirmed:
        return AssignOutcome(service._msg(service.BLOCKED, "이미 확정함"), [])

    # 1) 규칙을 지키는 배정이 있는지 먼저 판정 (없으면 AI를 부르지 않음)
    status("규칙을 지키는 배정이 있는지 확인하는 중...")
    solved = rules.solve(teams, unassigned)
    if solved.proposal is None:
        service.clear_proposal()
        status("규칙을 지키는 배정이 없음", replace_last=True)
        for r in solved.reasons:
            status(f"- {r}")
        service.log_ai(0, "가능성 판정", "배정 없음", "; ".join(solved.reasons), None)
        return AssignOutcome(service._msg(service.BLOCKED, "규칙을 지키는 배정이 없음: " + "; ".join(solved.reasons)),
                             lines)
    status(f"규칙을 지키는 배정이 있음 (팀이 없는 학생 {len(unassigned)}명)", replace_last=True)

    if not unassigned:
        service.save_proposal([], "배정할 학생 없음")
        status("팀이 없는 학생이 없어 AI를 부르지 않음")
        return AssignOutcome(service._msg(service.APPROVED, "팀이 없는 학생이 없음. 확정을 누르면 끝남"), lines)

    # 2) AI 1차 → 2차 → 3차
    models = list(models or ai_models())
    timeout = float(timeout or ai_timeout_sec())
    messages = build_messages(teams, unassigned)
    for round_no, model in enumerate(models, start=1):
        head = f"{round_no}차 모델({model})의 응답을 기다리는 중"
        status(f"{head}... 0초 / {timeout:.0f}초")
        started = time.monotonic()

        def tick(sec: float, head=head) -> None:
            status(f"{head}... {sec:.0f}초 / {timeout:.0f}초", replace_last=True)

        try:
            text = caller(model, messages, timeout, tick)
            proposal = parse_proposal(text)
            problems = rules.validate_proposal(teams, unassigned, proposal)
            if problems:
                raise AIFailure("검사 실패", "; ".join(problems[:4]))
        except AIFailure as fail:
            elapsed = time.monotonic() - started
            detail = f" - {fail.detail}" if fail.detail else ""
            status(f"{round_no}차 모델({model}) 실패: {fail.kind}{detail} ({elapsed:.0f}초)", replace_last=True)
            service.log_ai(round_no, model, fail.kind, fail.detail, elapsed)
            continue
        elapsed = time.monotonic() - started
        status(f"{round_no}차 모델({model})의 배정안이 검사 다섯 가지를 통과함 ({elapsed:.0f}초)", replace_last=True)
        service.log_ai(round_no, model, "성공", json.dumps(proposal, ensure_ascii=False), elapsed)
        service.save_proposal(proposal, f"{round_no}차 모델 {model}")
        return AssignOutcome(service._msg(service.APPROVED, f"{round_no}차 모델의 배정안이 검사를 통과함. 확인 후 확정하세요"),
                             lines)

    # 3) 3차까지 실패 -> 앱이 스스로 배정 (백트래킹으로 찾은 배정)
    status(f"{len(models)}차 모델까지 모두 실패하여 앱이 스스로 배정함 (모든 경우를 따져 찾은 배정, 검사 다섯 가지 통과)")
    service.log_ai(0, "앱(백트래킹)", "성공", json.dumps(solved.proposal, ensure_ascii=False), None)
    service.save_proposal(solved.proposal, "앱이 스스로 배정(백트래킹)")
    return AssignOutcome(service._msg(service.APPROVED, "AI가 모두 실패하여 앱이 스스로 배정함. 확인 후 확정하세요"), lines)
