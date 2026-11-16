"""LLM client tests using recorded-style Ollama responses (no model needed)."""
import asyncio
import json

import httpx
import pytest

from app import qwen_service as q
from tests.helpers import make_image


def ollama_reply(content, done_reason="stop", thinking=None, eval_count=120):
    message = {"role": "assistant", "content": content}
    if thinking:
        message["thinking"] = thinking
    return {"model": "m", "message": message, "done": True, "done_reason": done_reason, "prompt_eval_count": 300,
            "eval_count": eval_count, "total_duration": 9_000_000_000, "load_duration": 1_000_000_000}


GOOD = {
    "document_type": "daily_progress_report", "report_date": "29/10/2026", "answer": "Two activities are behind.",
    "entries": [
        {"activity_id": "A102", "activity_text": "Block A foundation concrete", "progress": "60%", "planned_progress": 80,
         "reporting_date": None, "issue": "Rain", "evidence": "Cumulative progress: 60%", "needs_review": False},
        {"activity_id": "null", "activity_text": "Ground floor columns", "progress": 150, "planned_progress": None,
         "reporting_date": "sometime soon", "issue": "", "evidence": "", "needs_review": "false"},
        {"activity_id": None, "activity_text": "", "progress": 5, "planned_progress": None,
         "reporting_date": None, "issue": None, "evidence": "", "needs_review": False},
    ],
    "warnings": [],
}


@pytest.fixture
def ollama(monkeypatch):
    """Install a fake Ollama. `replies` is a list of (status, json | exception) consumed in order."""
    state = {"replies": [], "requests": []}

    def handler(request: httpx.Request):
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"capabilities": ["completion", "vision"]})
        state["requests"].append(json.loads(request.content))
        status, payload = state["replies"].pop(0)
        if isinstance(payload, Exception):
            raise payload
        return httpx.Response(status, json=payload)

    monkeypatch.setattr(q, "TRANSPORT", httpx.MockTransport(handler))
    q._capabilities_cache.clear()
    return state


def run(coro):
    return asyncio.run(coro)


def test_valid_output_is_normalised(ollama):
    ollama["replies"] = [(200, ollama_reply(json.dumps(GOOD)))]
    result = run(q.extract_from_text("report text"))
    first, second = result["entries"][0], result["entries"][1]
    assert first["progress"] == 60.0                       # "60%" -> 60.0
    assert first["reporting_date"] == "2026-10-29"         # falls back to report date (day-first)
    assert second["activity_id"] is None                   # "null" string -> None
    assert second["progress"] is None and second["needs_review"] is True   # 150 % rejected, flagged
    assert any("outside 0-100" in w for w in second["warnings"])
    assert len(result["entries"]) == 2                     # entry without name or ID dropped
    assert result["meta"]["attempts"] == 1


def test_request_uses_schema_context_and_keep_alive(ollama):
    ollama["replies"] = [(200, ollama_reply(json.dumps(GOOD)))]
    run(q.extract_from_text("report text"))
    sent = ollama["requests"][0]
    assert sent["format"] == q.EXTRACTION_SCHEMA
    assert sent["options"]["num_ctx"] >= 8192 and sent["options"]["num_predict"] >= 2048
    assert sent["options"]["temperature"] == 0 and sent["keep_alive"]
    assert "think" not in sent                             # model has no thinking capability


def test_prose_around_json_is_recovered(ollama):
    ollama["replies"] = [(200, ollama_reply("Here you go:\n```json\n" + json.dumps(GOOD) + "\n```\nDone."))]
    assert len(run(q.extract_from_text("x"))["entries"]) == 2


def test_invalid_json_is_retried_once(ollama):
    ollama["replies"] = [(200, ollama_reply("not json at all")), (200, ollama_reply(json.dumps(GOOD)))]
    result = run(q.extract_from_text("x"))
    assert result["meta"]["attempts"] == 2
    assert "invalid" in ollama["requests"][1]["messages"][-1]["content"]


def test_truncated_output_is_a_clear_error(ollama):
    ollama["replies"] = [(200, ollama_reply('{"entries": [', done_reason="length", eval_count=2048))]
    with pytest.raises(q.QwenBadOutput, match="cut off"):
        run(q.extract_from_text("x"))


def test_thinking_only_output_is_explained(ollama):
    ollama["replies"] = [(200, ollama_reply("", thinking="Let me think..." * 50))]
    with pytest.raises(q.QwenBadOutput, match="instruct"):
        run(q.extract_from_text("x"))


def test_connection_refused_means_unavailable(ollama):
    ollama["replies"] = [(0, httpx.ConnectError("refused"))]
    with pytest.raises(q.QwenUnavailable, match="not running"):
        run(q.extract_from_text("x"))


def test_timeout_is_not_reported_as_offline(ollama):
    ollama["replies"] = [(0, httpx.ReadTimeout("slow"))]
    with pytest.raises(q.QwenTimeout, match="too slow"):
        run(q.extract_from_text("x"))
    assert len(ollama["requests"]) == 1                   # no duplicate retry against "localhost"


def test_missing_model_gives_pull_command(ollama):
    ollama["replies"] = [(404, {"error": "model not found"})]
    with pytest.raises(q.QwenUnavailable, match="ollama pull"):
        run(q.extract_from_text("x"))


def test_images_are_downscaled_before_sending(ollama):
    ollama["replies"] = [(200, ollama_reply(json.dumps(GOOD)))]
    big = make_image(size=(4000, 3000), fmt="PNG")
    result = run(q.extract_from_image(big))
    assert result["meta"]["image"]["sent_size"] == [1600, 1200]
    assert ollama["requests"][0]["messages"][-1]["images"]


@pytest.mark.parametrize("value,expected", [
    ("2026-10-29", "2026-10-29"), ("29/10/2026", "2026-10-29"), ("29 Oct 2026", "2026-10-29"),
    ("October 29, 2026", "2026-10-29"), ("29th October 2026", "2026-10-29"), (None, None), ("null", None),
    ("09/11/2026", "2026-11-09"), ("week ending 07-11-2026", "2026-11-07"), ("Report date: 2026-10-29", "2026-10-29"),
])
def test_parse_date(value, expected):
    assert q.parse_date(value) == expected


def test_parse_date_month_first_option(monkeypatch):
    monkeypatch.setenv("DATE_ORDER", "MDY")
    assert q.parse_date("09/11/2026") == "2026-09-11"


def test_status_reports_installed_models(ollama, monkeypatch):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "qwen3-vl:4b-instruct"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, json={"capabilities": []})

    monkeypatch.setattr(q, "TRANSPORT", httpx.MockTransport(handler))
    result = run(q.status())
    assert result["available"] and result["vision_installed"] and not result["text_installed"]
    assert any("ollama pull" in w for w in result["warnings"])
