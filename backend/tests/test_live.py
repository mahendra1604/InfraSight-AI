"""Live smoke tests against a real Ollama. Run with:  pytest -m live
Skipped automatically when Ollama or the configured models are missing."""
import asyncio

import pytest

from app import qwen_service as q
from app.matching import ActivityMatcher
from tests.helpers import make_image

pytestmark = pytest.mark.live

SAMPLE = (__import__("pathlib").Path(__file__).resolve().parents[2] / "data" / "samples" / "qwen_test_report.txt").read_text(encoding="utf-8")
ACTS = [("A100", "Site preparation"), ("A101", "Block A excavation"), ("A102", "Block A foundation concrete"),
        ("A103", "Ground floor columns"), ("A104", "Ground floor slab"), ("A105", "Electrical rough-in")]
EXPECTED = {"A102": 60.0, "A103": 20.0, "A104": 0.0}


@pytest.fixture(scope="module")
def ollama_status():
    status = asyncio.run(q.status())
    if not status["available"]:
        pytest.skip("Ollama is not running")
    return status


def _check(result):
    matcher = ActivityMatcher(ACTS)
    found = {matcher.match(e).activity_id: e["progress"] for e in result["entries"] if e["progress"] is not None}
    assert {k: found.get(k) for k in EXPECTED} == EXPECTED, result
    assert result["report_date"] == "2026-10-29"


def test_text_model_extracts_sample_report(ollama_status, monkeypatch):
    if not ollama_status["text_installed"]:
        pytest.skip(f"{ollama_status['text_model']} is not installed")
    monkeypatch.setenv("MATCHER_BACKEND", "lexical")
    _check(asyncio.run(q.extract_from_text(SAMPLE)))


def test_vision_model_reads_rendered_report(ollama_status, monkeypatch):
    if not ollama_status["vision_installed"]:
        pytest.skip(f"{ollama_status['vision_model']} is not installed")
    monkeypatch.setenv("MATCHER_BACKEND", "lexical")
    image = make_image(SAMPLE, size=(900, 760), font_size=18)
    _check(asyncio.run(q.extract_from_image(image)))


def test_vision_model_reads_site_plan(ollama_status):
    if not ollama_status["vision_installed"]:
        pytest.skip(f"{ollama_status['vision_model']} is not installed")
    from app.documents import load_plan_pages
    from app.quantities import build_takeoff

    root = __import__("pathlib").Path(__file__).resolve().parents[2]
    pages, _ = load_plan_pages("site_plan.png", "image/png", (root / "data" / "samples" / "site_plan.png").read_bytes())
    reading = asyncio.run(q.extract_plan(pages[0].image, pages[0].text))
    totals = build_takeoff(reading["elements"])["totals"]
    found = {(t["category"], t["measure"], round(t["total"])) for t in totals}
    assert ("drainage", "length", 450) in found, totals          # three drain labels summed in code
    assert ("building", "area", 600) in found and ("building", "area", 500) in found, totals
    assert ("boundary", "area", 9600) in found, totals
