"""call_openrouter 를 가짜 HTTP 서버로 시험: 정상 스트림, 429, 천천히 오는 응답(전체 제한으로 끊김), 빈 응답."""
from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tfapp.ai import AIFailure, call_openrouter


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        mode = body["model"]
        assert self.headers["Authorization"] == "Bearer test-key"
        assert body["stream"] is True and body["reasoning"] == {"effort": "low"}
        if mode == "429":
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": {"message": "Rate limit exceeded", "code": 429}}).encode())
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

        def send(line: str):
            self.wfile.write((line + "\n\n").encode())
            self.wfile.flush()

        if mode == "ok":
            for piece in ['{"assign', 'ments": []}']:
                send("data: " + json.dumps({"choices": [{"delta": {"content": piece}}]}))
            send("data: [DONE]")
        elif mode == "slow":       # 1초마다 유지 신호만 보내고 끝나지 않음 -> 전체 제한으로 끊겨야 함
            for _ in range(30):
                send(": OPENROUTER PROCESSING")
                time.sleep(1)
        elif mode == "trickle":    # 내용이 아주 천천히 옴 (조각마다는 빨라도 전체는 오래 걸림)
            for _ in range(30):
                send("data: " + json.dumps({"choices": [{"delta": {"content": "x"}}]}))
                time.sleep(0.5)
        elif mode == "empty":
            send("data: " + json.dumps({"choices": [{"delta": {"reasoning": "thinking..."}}]}))
            send("data: [DONE]")
        elif mode == "midstream_error":
            send("data: " + json.dumps({"error": {"message": "Provider returned error", "code": 502}}))


@pytest.fixture(scope="module")
def url():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/v1/chat/completions"
    srv.shutdown()


def call(url, model, timeout=3.0, ticks=None):
    return call_openrouter(model, [{"role": "user", "content": "hi"}], timeout,
                           tick=(ticks.append if ticks is not None else None), url=url, api_key="test-key")


def test_ok_stream(url):
    assert call(url, "ok") == '{"assignments": []}'


def test_http_429(url):
    with pytest.raises(AIFailure) as e:
        call(url, "429")
    assert e.value.kind == "HTTP 오류" and "429" in e.value.detail


@pytest.mark.parametrize("mode", ["slow", "trickle"])
def test_total_timeout_cuts(url, mode):
    ticks: list[float] = []
    t0 = time.monotonic()
    with pytest.raises(AIFailure) as e:
        call(url, mode, timeout=3.0, ticks=ticks)
    took = time.monotonic() - t0
    assert e.value.kind == "시간 초과"
    assert 2.9 <= took < 4.5, took          # 응답이 계속 와도 전체 3초에서 끊김
    assert ticks and ticks[-1] >= 1.5        # 기다리는 동안 경과 시간을 알려 줌


def test_empty_content(url):
    assert call(url, "empty") == ""          # 빈 응답 판정은 parse_proposal 이 함


def test_midstream_error(url):
    with pytest.raises(AIFailure) as e:
        call(url, "midstream_error")
    assert e.value.kind == "HTTP 오류"


def test_connection_refused():
    with pytest.raises(AIFailure) as e:
        call_openrouter("m", [], 3, url="http://127.0.0.1:9/x", api_key="k")
    assert e.value.kind in ("연결 오류", "시간 초과")


def test_missing_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(AIFailure) as e:
        call_openrouter("m", [], 3, url="http://127.0.0.1:9/x")
    assert e.value.kind == "설정 오류"
