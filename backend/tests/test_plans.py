"""Site-plan take-off, quantity-based progress and merge import through the API."""
from unittest.mock import AsyncMock, patch

from tests.helpers import make_image, sample_extraction
from tests.test_quantities import PLAN_ELEMENTS

PLAN_READING = {"drawing_title": "SITE LAYOUT PLAN", "scale": "1:500", "drawing_date": "15/09/2026",
                "answer": "A plot with two blocks, an access road and three drains.", "elements": PLAN_ELEMENTS,
                "notes": ["Drains to be laid before road sub-base."], "warnings": [],
                "meta": {"model": "qwen3-vl:4b-instruct", "duration_s": 3.0}}


def upload_plan(client, data=None, **form):
    return client.post("/api/plans/upload", files={"file": ("plan.png", data or make_image("SITE PLAN"), "image/png")}, data=form)


def test_plan_upload_produces_takeoff_suggestions_and_draft(client):
    with patch("app.qwen_service.extract_plan", new=AsyncMock(return_value=PLAN_READING)) as vision:
        job = client.get(f"/api/jobs/{upload_plan(client, instruction='What is the drain length?').json()['id']}").json()
    assert job["status"] == "done", job["error"]
    assert job["purpose"] == "plan" and vision.await_args.args[2] == "What is the drain length?"
    result = job["result"]
    totals = {t["name"]: t for t in result["takeoff"]["totals"]}
    assert totals["Drain"]["total"] == 450 and totals["Access road"]["total"] == 300
    assert totals["Plot boundary"]["unit"] == "m2" and totals["Plot boundary"]["total"] == 9600
    assert result["plan"]["scale"] == "1:500" and result["draft_schedule"][0]["activity_id"] == "P01"
    assert totals["Block A"]["suggestion"]["activity_id"].startswith("A10")
    # report jobs and plan jobs are listed separately
    assert [j["id"] for j in client.get("/api/jobs?purpose=plan").json()] == [job["id"]]
    assert client.get("/api/jobs?purpose=report").json() == []


def test_plan_csv_downloads_and_errors(client):
    with patch("app.qwen_service.extract_plan", new=AsyncMock(return_value=PLAN_READING)):
        job_id = upload_plan(client).json()["id"]
        assert upload_plan(client).json()["duplicate"] is True
    draft = client.get(f"/api/plans/{job_id}/draft-schedule.csv").text
    assert draft.splitlines()[0].startswith("activity_id,name,planned_progress,actual_progress,predecessor")
    assert "Drain laying" in draft
    assert "450" in client.get(f"/api/plans/{job_id}/takeoff.csv").text
    assert client.get("/api/plans/999/draft-schedule.csv").status_code == 404
    assert client.post("/api/plans/upload", files={"file": ("x.txt", b"hello", "text/plain")}).status_code == 415


def test_text_mode_without_ocr_text_fails_clearly(client):
    job = client.get(f"/api/jobs/{upload_plan(client, mode='text').json()['id']}").json()
    assert job["status"] == "failed" and "OCR found no text" in job["error"]


def test_draft_schedule_can_be_merged_into_the_project(client):
    with patch("app.qwen_service.extract_plan", new=AsyncMock(return_value=PLAN_READING)):
        job_id = upload_plan(client).json()["id"]
    draft = client.get(f"/api/plans/{job_id}/draft-schedule.csv").text
    files = {"file": ("draft.csv", draft, "text/csv")}
    assert client.post("/api/schedules/validate", files=files, data={"keep_missing": "true"}).json()["with_quantities"] >= 6
    result = client.post("/api/schedules/import", files=files, data={"keep_missing": "true"}).json()
    assert result["removed"] == 0 and result["added"] == len(draft.splitlines()) - 1
    ids = {a["id"] for a in client.get("/api/activities").json()}
    assert {"A100", "A105", "P01"} <= ids
    drain = next(a for a in client.get("/api/activities").json() if a["name"] == "Drain laying")
    assert (drain["total_quantity"], drain["unit"]) == (450, "m")


def test_merge_can_reference_existing_activities_but_not_create_cycles(client):
    ok = "activity_id,name,planned_progress,actual_progress,predecessor\nP01,Fencing,0,0,A105\n"
    assert client.post("/api/schedules/validate", files={"file": ("m.csv", ok, "text/csv")}, data={"keep_missing": "true"}).status_code == 200
    assert client.post("/api/schedules/validate", files={"file": ("m.csv", ok, "text/csv")}).status_code == 422   # replace mode
    cycle = "activity_id,name,planned_progress,actual_progress,predecessor\nA100,Site preparation,100,100,A105\n"
    response = client.post("/api/schedules/validate", files={"file": ("m.csv", cycle, "text/csv")}, data={"keep_missing": "true"})
    assert response.status_code == 422 and "cycle" in response.json()["detail"]


def test_set_activity_quantity_is_audited_and_role_checked(client, login):
    response = client.post("/api/activities/A105/quantity", json={"total_quantity": 0.45, "unit": "km", "source": "Plan SP-01"})
    assert response.status_code == 200 and (response.json()["total_quantity"], response.json()["unit"]) == (0.45, "km")
    entry = client.get("/api/audit").json()[0]
    assert entry["action"] == "set_quantity" and "A105" in entry["details"] and "Plan SP-01" in entry["details"]
    assert client.post("/api/activities/A105/quantity", json={"total_quantity": 5}).status_code == 422          # unit required
    engineer = login(client, "engineer", "engineer123")
    assert client.post("/api/activities/A105/quantity", json={"total_quantity": 5, "unit": "m"}, headers=engineer).status_code == 403


def test_manual_report_by_quantity(client):
    body = {"activity_id": "A102", "text": "Poured 252 m3 so far", "quantity_done": 252, "reporting_date": "2026-10-20"}
    response = client.post("/api/reports", json=body)
    assert response.status_code == 201 and response.json()["progress"] == 60.0                      # 252 / 420 m3
    report = next(r for r in client.get("/api/reports").json() if r["id"] == response.json()["id"])
    assert (report["quantity_done"], report["quantity_unit"]) == (252, "m3")
    assert client.post("/api/reports", json={**body, "activity_id": "A105"}).status_code == 422        # no total quantity
    assert client.post("/api/reports", json={k: v for k, v in body.items() if k != "quantity_done"}).status_code == 422


def test_ai_reports_by_quantity_become_progress(client):
    extraction = sample_extraction()
    extraction["entries"] = [
        {"activity_id": None, "activity_text": "Ground floor slab", "progress": None, "quantity_done": 150, "quantity_today": None,
         "quantity_total": None, "quantity_unit": "sqm", "reporting_date": "2026-10-29", "evidence": "150 sqm of slab cast",
         "needs_review": False, "warnings": []},
        {"activity_id": None, "activity_text": "Block A foundation concrete", "progress": None, "quantity_done": None,
         "quantity_today": 42, "quantity_total": None, "quantity_unit": "cum", "reporting_date": "2026-10-29",
         "evidence": "42 cum poured today", "needs_review": False, "warnings": []},
        {"activity_id": None, "activity_text": "Ground floor columns", "progress": None, "quantity_done": 30, "quantity_today": None,
         "quantity_total": None, "quantity_unit": "m", "reporting_date": "2026-10-29", "evidence": "30 m", "needs_review": False,
         "warnings": []},
    ]
    with patch("app.qwen_service.extract_from_text", new=AsyncMock(return_value=extraction)):
        job = client.get(f"/api/jobs/{client.post('/api/reports/upload', files={'file': ('d.txt', b'diary', 'text/plain')}).json()['id']}").json()
    created = {r["activity_id"]: r for r in job["result"]["created_reports"]}
    assert created["A104"]["progress"] == 25.0 and created["A104"]["quantity"] == "150 m2"         # 150 / 600 m2
    assert created["A102"]["progress"] == 50.0 and created["A102"]["needs_review"]                 # (168 + 42) / 420 m3
    assert any("cannot be converted" in s["reason"] for s in job["result"]["skipped"])             # metres vs nos
