import json

import pytest
from fastapi.testclient import TestClient

from thai_shop_agent import api

EVENTS = [
    {"type": "retrieval", "tables": ["orders"], "chunks": [{"id": "relationships", "kind": "relationships", "score": None}]},
    {"type": "tool_call", "step": 1, "id": "c1", "name": "run_sql", "arguments": '{"sql": "SELECT 1"}'},
    {"type": "tool_result", "step": 1, "id": "c1", "name": "run_sql", "ok": True, "error": None, "sql": "SELECT 1",
     "columns": ["1"], "rows": [[1]], "row_count": 1},
    {"type": "answer", "answer": "ยอดขายคือ 1 บาท", "sql": "SELECT 1", "columns": ["1"], "rows": [[1]],
     "citations": {"tables": [], "chunks": ["relationships"]}, "steps": 1, "stopped_reason": "answered",
     "usage": {"llm_calls": 2, "prompt_tokens": 10, "completion_tokens": 2}},
]


def parse_frames(body: str) -> list[dict]:
    assert body.endswith("\n\n")
    frames = body.split("\n\n")[:-1]
    assert all(f.startswith("data: ") and "\n" not in f for f in frames)
    return [json.loads(f[len("data: "):]) for f in frames]


@pytest.fixture
def client_with():
    def make(runner):
        api.app.dependency_overrides[api.get_runner] = lambda: runner
        return TestClient(api.app)

    yield make
    api.app.dependency_overrides.clear()


def test_ask_streams_one_frame_per_event_in_order(client_with):
    seen = []

    def runner(question):
        seen.append(question)
        yield from EVENTS

    resp = client_with(runner).post("/ask", json={"question": "ยอดขายเท่าไหร่"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = parse_frames(resp.text)
    assert events == EVENTS
    assert [e["type"] for e in events] == ["retrieval", "tool_call", "tool_result", "answer"]
    assert seen == ["ยอดขายเท่าไหร่"]
    assert "ยอดขายคือ" in resp.content.decode("utf-8")  # Thai is sent as UTF-8, not \u escapes


def test_runner_failure_becomes_error_frame_and_redacts(client_with):
    def runner(question):
        yield EVENTS[0]
        raise RuntimeError("Rate limit exceeded for api_key: abc123def. try later")

    client = client_with(runner)
    events = parse_frames(client.post("/ask", json={"question": "q"}).text)
    assert [e["type"] for e in events] == ["retrieval", "error"]
    assert "abc123def" not in events[-1]["message"] and "[redacted]" in events[-1]["message"]
    assert client.get("/health").status_code == 200  # server still up


def test_stream_stops_after_error_event(client_with):
    def runner(question):
        yield {"type": "error", "message": "proxy down"}
        yield {"type": "answer", "answer": "should not be sent"}

    events = parse_frames(client_with(runner).post("/ask", json={"question": "q"}).text)
    assert events == [{"type": "error", "message": "proxy down"}]


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "x" * 501}, {"question": 5}, {"q": "hi"}])
def test_invalid_request_is_422(client_with, body):
    def runner(question):
        raise AssertionError("runner must not be called")

    assert client_with(runner).post("/ask", json=body).status_code == 422


def test_question_of_500_chars_is_accepted(client_with):
    resp = client_with(lambda q: iter(EVENTS)).post("/ask", json={"question": "ก" * 500})
    assert resp.status_code == 200


def test_health():
    body = TestClient(api.app).get("/health").json()
    assert body["status"] == "ok" and set(body) == {"status", "database", "index"}


def test_index_serves_web_page(tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text("<!doctype html><title>ร้าน</title>", encoding="utf-8")
    monkeypatch.setattr(api, "WEB_INDEX", page)
    resp = TestClient(api.app).get("/")
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/html")
    assert "ร้าน" in resp.text
    monkeypatch.setattr(api, "WEB_INDEX", tmp_path / "missing.html")
    assert TestClient(api.app).get("/").status_code == 404
