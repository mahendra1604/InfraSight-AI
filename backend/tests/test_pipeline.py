"""End-to-end API tests for upload -> background job -> pending reports -> review."""
from unittest.mock import AsyncMock, patch

from app.qwen_service import QwenTimeout, QwenUnavailable
from tests.helpers import SAMPLE_TEXT, make_text_pdf, sample_extraction


def upload(client, name="report.txt", data=SAMPLE_TEXT.encode(), mime="text/plain", **form):
    return client.post("/api/reports/upload", files={"file": (name, data, mime)}, data=form)


def test_text_report_creates_matched_pending_reports(client):
    with patch("app.qwen_service.extract_from_text", new=AsyncMock(return_value=sample_extraction())) as llm:
        response = upload(client)
    assert response.status_code == 202
    job = client.get(f"/api/jobs/{response.json()['id']}").json()
    assert job["status"] == "done", job["error"]
    assert job["method"] == "text" and llm.await_count == 1
    created = {r["activity_id"]: r for r in job["result"]["created_reports"]}
    assert set(created) == {"A102", "A103", "A104"}
    assert created["A102"]["match_status"] == "id_match"
    assert created["A103"]["match_status"] == "suggested"
    assert [s["reason"] for s in job["result"]["skipped"]] == ["No progress value in the report"]
    report = next(r for r in client.get("/api/reports").json() if r["activity_id"] == "A103" and r["job_id"])
    assert report["status"] == "pending" and report["model"].startswith("qwen3") and report["candidates"]
    assert "Reinforcement delivery" in report["issue"]


def test_same_file_twice_is_detected_as_duplicate(client):
    with patch("app.qwen_service.extract_from_text", new=AsyncMock(return_value=sample_extraction())) as llm:
        first = upload(client).json()
        second = upload(client).json()
        forced = upload(client, force="true").json()
    assert second["duplicate"] is True and second["id"] == first["id"]
    assert llm.await_count == 2
    job = client.get(f"/api/jobs/{forced['id']}").json()
    assert job["result"]["created_reports"] == []              # same values -> no duplicate reports
    assert all("Duplicate of report" in s["reason"] for s in job["result"]["skipped"][:3])


def test_progress_decrease_is_skipped(client):
    extraction = sample_extraction()
    extraction["entries"][0]["progress"] = 30.0                  # A102 is already 40 %
    with patch("app.qwen_service.extract_from_text", new=AsyncMock(return_value=extraction)):
        job = client.get(f"/api/jobs/{upload(client).json()['id']}").json()
    assert any("cannot decrease" in s["reason"] for s in job["result"]["skipped"])


def test_ollama_down_and_timeout_fail_the_job_with_clear_messages(client):
    with patch("app.qwen_service.extract_from_text", new=AsyncMock(side_effect=QwenUnavailable("Ollama is not running"))):
        job = client.get(f"/api/jobs/{upload(client).json()['id']}").json()
    assert job["status"] == "failed" and "not running" in job["error"]
    with patch("app.qwen_service.extract_from_text", new=AsyncMock(side_effect=QwenTimeout("too slow"))):
        job = client.get(f"/api/jobs/{upload(client, force='true').json()['id']}").json()
    assert job["status"] == "failed" and "too slow" in job["error"]


def test_pdf_goes_through_text_model(client):
    pdf = make_text_pdf(["Report date 2026-10-29", "A102 Block A foundation concrete 60 percent"])
    with patch("app.qwen_service.extract_from_text", new=AsyncMock(return_value=sample_extraction())) as llm:
        job = client.get(f"/api/jobs/{upload(client, 'r.pdf', pdf, 'application/pdf').json()['id']}").json()
    assert job["status"] == "done" and "A102" in llm.await_args.args[0]


def test_scanned_image_without_ocr_uses_vision_model(client):
    from tests.helpers import make_image

    with patch("app.qwen_service.extract_from_image", new=AsyncMock(return_value=sample_extraction())) as vision:
        job = client.get(f"/api/jobs/{upload(client, 'site.jpg', make_image('x', fmt='JPEG'), 'image/jpeg', instruction='Read the plan').json()['id']}").json()
    assert job["status"] == "done" and job["method"] == "vision"
    assert vision.await_args.args[1] == "Read the plan"


def test_schedule_and_unknown_uploads_are_refused(client):
    assert upload(client, "s.csv", b"activity_id,name\n", "text/csv").status_code == 422
    assert upload(client, "x.bin", b"\x00\x01\x02", "application/octet-stream").status_code == 415


def test_review_with_correction_updates_progress_and_audit(client):
    with patch("app.qwen_service.extract_from_text", new=AsyncMock(return_value=sample_extraction())):
        job = client.get(f"/api/jobs/{upload(client).json()['id']}").json()
    report_id = next(r["report_id"] for r in job["result"]["created_reports"] if r["activity_id"] == "A103")
    response = client.post(f"/api/reports/{report_id}/review", json={"action": "approve", "progress": 25, "note": "checked on site"})
    assert response.status_code == 200 and response.json()["corrections"] == ["progress 20.0%→25.0%"]
    assert next(a for a in client.get("/api/activities").json() if a["id"] == "A103")["actual"] == 25
    entry = client.get("/api/audit").json()[0]
    assert entry["actor"] == "admin" and "model qwen3" in entry["details"] and "score" in entry["details"]
    assert "corrected: progress" in entry["details"] and "checked on site" in entry["details"]


def test_jobs_list_and_unknown_job(client):
    with patch("app.qwen_service.extract_from_text", new=AsyncMock(return_value=sample_extraction())):
        upload(client)
    listing = client.get("/api/jobs").json()
    assert listing and "result" not in listing[0]
    assert client.get("/api/jobs/999").status_code == 404
