"""Schedules, dependency analysis, delay prediction, export and auth."""
import io

from openpyxl import Workbook, load_workbook

EXTENDED = """activity_id,name,planned_progress,actual_progress,predecessor,planned_start,planned_finish
A100,Site preparation,100,100,,2026-08-01,2026-08-20
A101,Block A excavation,100,100,A100,2026-08-21,2026-09-11
A102,Block A foundation concrete,80,40,A101,2026-09-12,2026-10-06
A103,Ground floor columns,35,20,A102,2026-09-22,2026-10-17
A104,Ground floor slab,10,0,A103,2026-09-28,2026-10-27
A106,Plumbing rough-in,0,0,A104,2026-10-20,2026-11-12
A107,Drainage,40,30,A100,2026-09-01,2026-10-30
A108,Handover,0,0,A106;A107,2026-11-13,2026-11-20
"""


def test_schedule_import_upserts_and_keeps_referenced_activities(client):
    files = {"file": ("schedule.csv", EXTENDED, "text/csv")}
    assert client.post("/api/schedules/validate", files=files).json()["links"] == 8
    result = client.post("/api/schedules/import", files=files).json()
    assert (result["added"], result["updated"], result["removed"]) == (3, 5, 1)    # A105 removed (no reports)
    ids = [a["id"] for a in client.get("/api/activities").json()]
    assert "A108" in ids and "A105" not in ids
    a108 = next(a for a in client.get("/api/activities").json() if a["id"] == "A108")
    assert a108["predecessors"] == ["A106", "A107"] and a108["duration_days"] == 8
    assert client.get("/api/schedules/versions").json()[0]["activities"] == 8


def test_schedule_import_never_decreases_actual_progress(client):
    client.post("/api/reports/1/review", json={"action": "approve"})                 # A102 -> 60 %
    client.post("/api/schedules/import", files={"file": ("s.csv", EXTENDED, "text/csv")})
    assert next(a for a in client.get("/api/activities").json() if a["id"] == "A102")["actual"] == 60


def test_excel_schedule_with_ms_project_headers(client):
    wb = Workbook()
    ws = wb.active
    ws.append(["Task ID", "Task Name", "Planned %", "% Complete", "Predecessors", "Start", "Finish", "Duration"])
    ws.append(["T1", "Mobilisation", 100, 100, None, "2026-08-01", "2026-08-05", "5d"])
    ws.append(["T2", "Piling", 50, 30, "T1FS+2", "2026-08-06", "2026-08-30", "25 days"])
    buffer = io.BytesIO()
    wb.save(buffer)
    response = client.post("/api/schedules/validate", files={"file": ("plan.xlsx", buffer.getvalue(), "application/octet-stream")})
    assert response.status_code == 200, response.text
    assert response.json()["links"] == 1


def test_schedule_errors(client):
    bad = "activity_id,name,planned_progress,actual_progress,predecessor\nA,One,10,5,B\nB,Two,10,5,A\n"
    response = client.post("/api/schedules/validate", files={"file": ("s.csv", bad, "text/csv")})
    assert response.status_code == 422 and "cycle" in response.json()["detail"]
    missing = client.post("/api/schedules/validate", files={"file": ("s.csv", "id,foo\n1,2\n", "text/csv")})
    assert missing.status_code == 422 and "Missing" in missing.json()["detail"]


def test_dependency_analysis_finds_critical_path_and_impact(client):
    data = client.get("/api/analysis/dependencies").json()
    assert data["valid"] and data["critical_path"] == ["A100", "A101", "A102", "A103", "A104", "A105"]
    a102 = next(n for n in data["nodes"] if n["id"] == "A102")
    assert a102["downstream"] == ["A103", "A104", "A105"] and a102["critical"]
    impact = client.get("/api/analysis/impact/A102?delay_days=5").json()
    assert impact["project_delay_days"] == 5 and len(impact["affected"]) == 3
    assert client.get("/api/analysis/impact/NOPE").status_code == 404


def test_float_absorbs_delay_off_the_critical_path(client):
    client.post("/api/schedules/import", files={"file": ("s.csv", EXTENDED, "text/csv")})
    nodes = {n["id"]: n for n in client.get("/api/analysis/dependencies").json()["nodes"]}
    assert nodes["A107"]["total_float_days"] > 0 and not nodes["A107"]["critical"]
    impact = client.get(f"/api/analysis/impact/A107?delay_days={nodes['A107']['total_float_days']}").json()
    assert impact["project_delay_days"] == 0


def test_predictions_are_probabilities_with_factors(client):
    data = client.get("/api/predictions").json()
    assert data["model"]["metrics"]["roc_auc"] > 0.8
    preds = {p["activity_id"]: p for p in data["predictions"]}
    assert preds["A100"]["delay_probability"] == 0 and preds["A100"]["complete"]
    assert all(0 <= p["delay_probability"] <= 1 for p in preds.values())
    assert preds["A102"]["delay_probability"] > preds["A105"]["delay_probability"]
    assert preds["A102"]["factors"] and preds["A102"]["risk_level"] in {"low", "medium", "high"}


def test_exports(client):
    response = client.get("/api/export/report.xlsx")
    assert response.status_code == 200
    workbook = load_workbook(io.BytesIO(response.content))
    assert workbook.sheetnames == ["Summary", "Activities", "Delay risk", "Reports", "Audit trail"]
    csv_text = client.get("/api/export/activities.csv").text
    assert csv_text.startswith("id,name,planned,actual") and "A102" in csv_text


def test_auth_is_required_and_roles_are_enforced(anon_client, login):
    assert anon_client.get("/api/health").status_code == 200
    assert anon_client.get("/api/activities").status_code == 401
    assert anon_client.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    viewer = login(anon_client, "viewer", "viewer123")
    assert anon_client.get("/api/activities", headers=viewer).status_code == 200
    assert anon_client.post("/api/reports/1/review", json={"action": "approve"}, headers=viewer).status_code == 403
    engineer = login(anon_client, "engineer", "engineer123")
    assert anon_client.post("/api/reports/1/review", json={"action": "approve"}, headers=engineer).status_code == 403
    manager = login(anon_client, "manager", "manager123")
    assert anon_client.post("/api/reports/1/review", json={"action": "approve"}, headers=manager).status_code == 200
    assert anon_client.get("/api/auth/me", headers=manager).json()["role"] == "manager"
    assert anon_client.get("/api/audit", headers=manager).json()[0]["actor"] == "manager"


def test_auth_can_be_disabled(anon_client, monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "false")
    assert anon_client.get("/api/activities").status_code == 200


def test_upload_validation_is_side_effect_free(client):
    response = client.post("/api/uploads/validate", files={"file": ("r.txt", b"A102 60%", "text/plain")}).json()
    assert response["valid"] and response["kind"] == "text"
    assert client.get("/api/jobs").json() == []


def test_upload_validation_counts_pdf_text_pages(client):
    from tests.helpers import make_text_pdf

    pdf = make_text_pdf(["A102 Block A foundation concrete is 60 percent complete today"])
    response = client.post("/api/uploads/validate", files={"file": ("r.pdf", pdf, "application/pdf")}).json()
    assert response["valid"] and (response["pages"], response["text_pages"]) == (1, 1)
    assert "1 of 1 pages have a text layer" in response["message"]
