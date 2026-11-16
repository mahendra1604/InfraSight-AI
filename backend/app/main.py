"""InfraSight AI API.

Pipeline: upload -> document reading/OCR -> local LLM extraction (Ollama) -> semantic activity
matching -> human review -> progress, dependency analysis, delay/risk prediction, dashboard, export.
"""
from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Query, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app import analysis, db, documents, exporting, pipeline, prediction, quantities, schedules
from app.auth import CurrentUser, authenticate, create_token, current_user, require
from app.config import settings
from app.db import Activity, AnalysisJob, Audit, BaselineVersion, Prediction, Report, utcnow
from app.matching import backend_name
from app.qwen_service import QwenBadOutput, QwenTimeout, QwenUnavailable, analyse_image
from app.qwen_service import status as qwen_status

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

VERSION = "0.3.0"


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init()
    yield


app = FastAPI(title="InfraSight AI", version=VERSION, lifespan=lifespan,
              description="Infrastructure progress monitoring: local LLM extraction, semantic matching, "
                          "dependency analysis and delay-risk prediction. AI output always requires human review.")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                   allow_headers=["Content-Type", "Authorization"])

viewer = Depends(require("viewer"))


# ---------------------------------------------------------------------------
# Health, auth, AI status
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "version": VERSION, "auth_enabled": settings.auth_enabled}


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=200)


@app.get("/api/auth/config")
def auth_config():
    demo = os.getenv("DEMO_MODE", "true").lower() in {"1", "true", "yes"}
    return {"auth_enabled": settings.auth_enabled,
            "demo_accounts": [{"username": u, "role": r, "password": p} for u, _, r, p in db.DEMO_USERS] if demo else []}


@app.post("/api/auth/login")
def login(body: LoginInput):
    user = authenticate(body.username.strip(), body.password)
    if not user:
        raise HTTPException(401, "Wrong username or password")
    return {"token": create_token(user), "user": user.to_dict()}


@app.get("/api/auth/me")
def me(user: CurrentUser = Depends(current_user)):
    return user.to_dict()


@app.get("/api/ai/status", dependencies=[viewer])
async def ai_status():
    result = await qwen_status()
    result["matcher"] = settings.matcher_backend if settings.matcher_backend != "auto" else "auto (sentence-transformers → ollama → lexical)"
    result["ocr_enabled"] = settings.ocr_enabled
    return result


@app.post("/api/ai/analyse-image")
async def analyse_uploaded_image(file: UploadFile = File(...), instruction: str = Form(default=""),
                                 user: CurrentUser = Depends(require("engineer"))):
    """Synchronous single-image question to the vision model. Creates no reports
    (use POST /api/reports/upload for the full pipeline)."""
    if not (file.content_type or "").startswith("image/"):
        raise HTTPException(415, "Use an image file (PNG, JPEG, WebP, TIFF or BMP)")
    image = await file.read(10_000_001)
    if len(image) > 10_000_000:
        raise HTTPException(413, "Image must be at most 10 MB")
    try:
        result = await analyse_image(image, file.content_type, instruction)
    except QwenUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except QwenTimeout as exc:
        raise HTTPException(504, str(exc)) from exc
    except QwenBadOutput as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"model": (result.get("meta") or {}).get("model", settings.vision_model), "status": "pending_review",
            "instruction": instruction, "source_filename": file.filename, "result": result}


# ---------------------------------------------------------------------------
# Project, activities, reports, review, audit
# ---------------------------------------------------------------------------
@app.get("/api/project", dependencies=[viewer])
def project():
    return {"id": "demo", "name": "Riverside Infrastructure", "mode": "demo", "as_of": prediction.as_of_date().isoformat(),
            "description": f"Synthetic construction project · baseline as of {prediction.as_of_date():%d %B %Y}"}


def _activity_dict(a: Activity) -> dict:
    return {"id": a.id, "name": a.name, "planned": a.planned, "actual": a.actual, "variance": round(a.actual - a.planned, 2),
            "predecessor": a.predecessor, "predecessors": a.predecessors, "planned_start": a.planned_start,
            "planned_finish": a.planned_finish, "duration_days": a.duration_days, "wbs": a.wbs,
            "total_quantity": a.total_quantity, "unit": a.unit,
            "actual_quantity": round(a.actual / 100 * a.total_quantity, 3) if a.total_quantity else None}


def _activities() -> list[Activity]:
    with db.session() as s:
        return list(s.scalars(select(Activity).order_by(Activity.id)))


@app.get("/api/activities", dependencies=[viewer])
def activities():
    return [_activity_dict(a) for a in _activities()]


@app.get("/api/summary", dependencies=[viewer])
def summary():
    rows = activities()
    return {"activities": len(rows), "planned": round(sum(a["planned"] for a in rows) / len(rows), 1) if rows else 0,
            "actual": round(sum(a["actual"] for a in rows) / len(rows), 1) if rows else 0,
            "behind": sum(a["variance"] < 0 for a in rows),
            "method": "Unweighted mean of activity percentages; demo baseline, not earned value."}


def _report_dict(r: Report) -> dict:
    return {"id": r.id, "activity_id": r.activity_id, "text": r.text, "progress": r.progress,
            "reporting_date": r.reporting_date, "status": r.status, "source": r.source, "issue": r.issue,
            "extracted_activity": r.extracted_activity, "match_score": r.match_score, "match_method": r.match_method,
            "match_status": r.match_status, "candidates": json.loads(r.candidates_json) if r.candidates_json else [],
            "model": r.model, "job_id": r.job_id, "evidence_page": r.evidence_page, "needs_review": r.needs_review,
            "warnings": json.loads(r.warnings_json) if r.warnings_json else [], "created_at": r.created_at,
            "quantity_done": r.quantity_done, "quantity_unit": r.quantity_unit}


@app.get("/api/reports", dependencies=[viewer])
def reports():
    with db.session() as s:
        return [_report_dict(r) for r in s.scalars(select(Report).order_by(Report.id.desc()))]


class ReportInput(BaseModel):
    activity_id: str
    text: str = Field(min_length=1, max_length=5000)
    progress: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False)
    quantity_done: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False,
                                           description="Cumulative quantity in the activity's unit (alternative to progress)")
    reporting_date: date

    @model_validator(mode="after")
    def _one_measure(self):
        if self.progress is None and self.quantity_done is None:
            raise ValueError("Give either progress (%) or quantity_done")
        return self


@app.post("/api/reports", status_code=201)
def create_report(body: ReportInput, user: CurrentUser = Depends(require("engineer"))):
    with db.session() as s:
        activity = s.get(Activity, body.activity_id)
        if not activity:
            raise HTTPException(404, "Activity not found")
        progress, quantity_done, notes = body.progress, None, []
        if body.quantity_done is not None:
            qp = quantities.progress_from_quantity(activity.total_quantity, activity.unit, activity.actual, done=body.quantity_done,
                                                   unit=activity.unit)
            if qp.progress is None:
                raise HTTPException(422, qp.reason)
            quantity_done, notes = qp.quantity_done, qp.notes
            if progress is None:
                progress = qp.progress
        report = Report(activity_id=body.activity_id, text=body.text, progress=progress, reporting_date=body.reporting_date.isoformat(),
                        source=f"Manual entry · {user.display_name}", match_status="manual", created_at=utcnow(),
                        quantity_done=quantity_done, quantity_unit=activity.unit if quantity_done is not None else None,
                        warnings_json=json.dumps(notes) if notes else None)
        s.add(report)
        s.commit()
        return {"id": report.id, "status": report.status, "progress": progress}


class QuantityInput(BaseModel):
    total_quantity: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False, description="null clears the quantity")
    unit: Optional[str] = Field(default=None, max_length=20)
    source: Optional[str] = Field(default=None, max_length=300, description="e.g. 'Site plan SP-01, analysis #12'")


@app.post("/api/activities/{activity_id}/quantity")
def set_activity_quantity(activity_id: str, body: QuantityInput, user: CurrentUser = Depends(require("manager"))):
    """Set (or clear) an activity's planned total quantity, e.g. from a site-plan take-off."""
    unit = quantities.normalize_unit(body.unit)
    if body.total_quantity is not None and not unit:
        raise HTTPException(422, "A unit is required with a quantity")
    with db.session() as s:
        activity = s.get(Activity, activity_id)
        if not activity:
            raise HTTPException(404, "Activity not found")
        before = f"{quantities.fmt(activity.total_quantity)} {activity.unit or ''}".strip()
        activity.total_quantity = body.total_quantity
        activity.unit = unit if body.total_quantity is not None else None
        after = f"{quantities.fmt(activity.total_quantity)} {activity.unit or ''}".strip()
        details = f"{user.display_name} ({user.role}); activity {activity_id}; total quantity {before} → {after}"
        if body.source:
            details += f"; source {body.source}"
        s.add(Audit(report_id=0, action="set_quantity", details=details, actor=user.username, created_at=utcnow()))
        s.execute(Prediction.__table__.delete())
        s.commit()
        return _activity_dict(activity)


class Decision(BaseModel):
    action: Literal["approve", "reject"]
    activity_id: Optional[str] = Field(default=None, description="Reviewer correction of the matched activity")
    progress: Optional[float] = Field(default=None, ge=0, le=100, allow_inf_nan=False, description="Reviewer correction")
    note: Optional[str] = Field(default=None, max_length=1000)


@app.post("/api/reports/{report_id}/review")
def review(report_id: int, body: Decision, user: CurrentUser = Depends(require("manager"))):
    with db.session() as s:
        report = s.get(Report, report_id)
        if not report:
            raise HTTPException(404, "Report not found")
        if report.status != "pending":
            raise HTTPException(409, "Report already reviewed")
        corrections = []
        if body.activity_id and body.activity_id != report.activity_id:
            if not s.get(Activity, body.activity_id):
                raise HTTPException(404, "Activity not found")
            corrections.append(f"activity {report.activity_id}→{body.activity_id}")
            report.activity_id = body.activity_id
        if body.progress is not None and body.progress != report.progress:
            corrections.append(f"progress {report.progress}%→{body.progress}%")
            report.progress = body.progress
        if body.action == "approve":
            latest = s.scalar(select(Report).where(Report.activity_id == report.activity_id, Report.status == "approved")
                              .order_by(Report.reporting_date.desc()).limit(1))
            if latest and report.reporting_date < latest.reporting_date:
                raise HTTPException(409, "A newer approved report exists; historical corrections are not supported yet")
            activity = s.get(Activity, report.activity_id)
            if report.progress < activity.actual:
                raise HTTPException(409, "Cumulative progress cannot decrease")
            activity.actual = report.progress
            s.execute(Prediction.__table__.delete())  # predictions are stale once progress changes
        report.status = "approved" if body.action == "approve" else "rejected"
        details = [f"{user.display_name} ({user.role})", f"activity {report.activity_id}", f"cumulative progress {report.progress}%",
                   f"source {report.source}"]
        if report.model:
            details.append(f"model {report.model}")
        if report.match_status and report.match_status != "manual":
            details.append(f"match {report.match_status} via {report.match_method}, score {report.match_score}")
        if corrections:
            details.append("corrected: " + ", ".join(corrections))
        if body.note:
            details.append(f"note: {body.note}")
        s.add(Audit(report_id=report.id, action=body.action, details="; ".join(details), actor=user.username, created_at=utcnow()))
        s.commit()
        return {"status": report.status, "corrections": corrections}


@app.get("/api/audit", dependencies=[viewer])
def audit():
    with db.session() as s:
        return [{"id": a.id, "report_id": a.report_id, "action": a.action, "details": a.details, "actor": a.actor,
                 "created_at": a.created_at} for a in s.scalars(select(Audit).order_by(Audit.id.desc()))]


# ---------------------------------------------------------------------------
# Report upload -> background AI analysis job
# ---------------------------------------------------------------------------
@app.post("/api/reports/upload", status_code=202)
async def upload_report(background: BackgroundTasks, file: UploadFile = File(...), instruction: str = Form(default=""),
                        mode: Literal["auto", "text", "vision"] = Form(default="auto"), force: bool = Form(default=False),
                        user: CurrentUser = Depends(require("engineer"))):
    """Store the file and start extraction in the background. Poll GET /api/jobs/{id}."""
    data = await file.read(25_000_001)
    if len(data) > 25_000_000:
        raise HTTPException(413, "Files must be at most 25 MB")
    if not data:
        raise HTTPException(422, "The file is empty")
    kind = documents.detect_kind(file.filename, file.content_type, data)
    if kind == "schedule":
        raise HTTPException(422, "This looks like a schedule (CSV/Excel). Use Schedule import instead.")
    if kind == "unknown":
        raise HTTPException(415, "Upload a .txt, .docx, .pdf or an image (PNG, JPEG, WebP, TIFF, BMP)")
    instruction = instruction.strip()[:2000]
    if not force:
        sha256 = hashlib.sha256(data).hexdigest()
        existing = pipeline.find_existing_job(sha256, instruction, mode)
        if existing:
            return {**pipeline.job_to_dict(existing), "duplicate": True,
                    "message": f"This file was already analysed (job {existing.id}). Send force=true to analyse it again."}
    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0]
    job = pipeline.create_job(data, file.filename or "upload", content_type, instruction, mode, user.username)
    background.add_task(pipeline.run_job, job.id)
    return {**pipeline.job_to_dict(job), "duplicate": False}


@app.get("/api/jobs", dependencies=[viewer])
def jobs(limit: int = Query(default=20, ge=1, le=100), purpose: Optional[Literal["report", "plan"]] = None):
    with db.session() as s:
        query = select(AnalysisJob).order_by(AnalysisJob.id.desc()).limit(limit)
        if purpose == "plan":
            query = query.where(AnalysisJob.purpose == "plan")
        elif purpose == "report":
            query = query.where((AnalysisJob.purpose == "report") | AnalysisJob.purpose.is_(None))
        return [{k: v for k, v in pipeline.job_to_dict(j).items() if k != "result"} for j in s.scalars(query)]


# ---------------------------------------------------------------------------
# Site plans: quantity take-off + draft schedule
# ---------------------------------------------------------------------------
@app.post("/api/plans/upload", status_code=202)
async def upload_plan(background: BackgroundTasks, file: UploadFile = File(...), instruction: str = Form(default=""),
                      mode: Literal["vision", "text"] = Form(default="vision"), force: bool = Form(default=False),
                      user: CurrentUser = Depends(require("engineer"))):
    """Read the printed dimensions of a site plan / drawing (image or PDF) in the background."""
    data = await file.read(25_000_001)
    if len(data) > 25_000_000:
        raise HTTPException(413, "Files must be at most 25 MB")
    if documents.detect_kind(file.filename, file.content_type, data) not in {"image", "pdf"}:
        raise HTTPException(415, "Upload the site plan as an image (PNG, JPEG, TIFF, BMP, WebP) or PDF. "
                                 "Export AutoCAD .dwg/.dxf drawings to PDF or PNG first.")
    instruction = instruction.strip()[:2000]
    if not force:
        existing = pipeline.find_existing_job(hashlib.sha256(data).hexdigest(), instruction, mode, purpose="plan")
        if existing:
            return {**pipeline.job_to_dict(existing), "duplicate": True,
                    "message": f"This drawing was already read (analysis {existing.id}). Send force=true to read it again."}
    content_type = file.content_type or mimetypes.guess_type(file.filename or "")[0]
    job = pipeline.create_job(data, file.filename or "plan", content_type, instruction, mode, user.username, purpose="plan")
    background.add_task(pipeline.run_plan_job, job.id)
    return {**pipeline.job_to_dict(job), "duplicate": False}


def _plan_result(job_id: int) -> dict:
    with db.session() as s:
        found = s.get(AnalysisJob, job_id)
        if not found or found.purpose != "plan":
            raise HTTPException(404, "Site-plan analysis not found")
        if found.status != "done":
            raise HTTPException(409, f"The analysis is {found.status}")
        return json.loads(found.result_json)


@app.get("/api/plans/{job_id}/draft-schedule.csv", dependencies=[viewer])
def plan_draft_schedule(job_id: int):
    rows = _plan_result(job_id)["draft_schedule"]
    return Response(exporting.to_csv(rows), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="draft-schedule-plan-{job_id}.csv"'})


@app.get("/api/plans/{job_id}/takeoff.csv", dependencies=[viewer])
def plan_takeoff(job_id: int):
    totals = [{k: v for k, v in t.items() if k not in ("labels", "candidates", "suggestion")} |
              {"labels": " | ".join(t["labels"]), "suggested_activity": (t.get("suggestion") or {}).get("activity_id")}
              for t in _plan_result(job_id)["takeoff"]["totals"]]
    return Response(exporting.to_csv(totals), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="takeoff-plan-{job_id}.csv"'})


@app.get("/api/jobs/{job_id}", dependencies=[viewer])
def job(job_id: int):
    with db.session() as s:
        found = s.get(AnalysisJob, job_id)
        if not found:
            raise HTTPException(404, "Job not found")
        return pipeline.job_to_dict(found)


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------
@app.post("/api/schedules/validate", dependencies=[viewer])
async def validate_schedule(file: UploadFile = File(...), keep_missing: bool = Form(default=False)):
    """Validate a CSV/XLSX schedule without changing project data. keep_missing=true validates a merge."""
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        raise HTTPException(413, "Schedule files must be at most 5 MB")
    try:
        cleaned = schedules.validate_rows(schedules.read_rows(file.filename, data),
                                          schedules.existing_links() if keep_missing else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"valid": True, "activities": len(cleaned),
            "with_dates": sum(1 for r in cleaned if r["planned_start"]),
            "with_quantities": sum(1 for r in cleaned if r["total_quantity"] is not None),
            "links": sum(len(r["predecessors"]) for r in cleaned),
            "message": "Schedule is valid. Use Import to " + ("add it to the project." if keep_missing else "make it the project baseline.")}


@app.post("/api/schedules/import")
async def import_schedule(file: UploadFile = File(...), keep_missing: bool = Form(default=False),
                          user: CurrentUser = Depends(require("manager"))):
    """keep_missing=false replaces the baseline (unreferenced activities not in the file are removed);
    keep_missing=true merges the file into the current schedule."""
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        raise HTTPException(413, "Schedule files must be at most 5 MB")
    try:
        cleaned = schedules.validate_rows(schedules.read_rows(file.filename, data),
                                          schedules.existing_links() if keep_missing else None)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return schedules.import_schedule(cleaned, file.filename or "schedule", user.username, keep_missing=keep_missing)


@app.get("/api/schedules/versions", dependencies=[viewer])
def schedule_versions():
    with db.session() as s:
        return [{"id": v.id, "filename": v.filename, "activities": v.activities, "added": v.added, "updated": v.updated,
                 "removed": v.removed, "imported_by": v.imported_by, "imported_at": v.imported_at}
                for v in s.scalars(select(BaselineVersion).order_by(BaselineVersion.id.desc()))]


@app.post("/api/uploads/validate", dependencies=[viewer])
async def validate_any_upload(file: UploadFile = File(...)):
    """Classify a file and check that it is readable. Never changes project data and never calls the model."""
    data = await file.read(25_000_001)
    if len(data) > 25_000_000:
        raise HTTPException(413, "Files must be at most 25 MB")
    ext = Path(file.filename or "").suffix.lower()
    mime = file.content_type or mimetypes.guess_type(file.filename or "")[0] or "application/octet-stream"
    base = {"filename": file.filename, "size_bytes": len(data), "extension": ext, "content_type": mime}
    kind = documents.detect_kind(file.filename, mime, data)
    if kind == "schedule":
        try:
            cleaned = schedules.validate_rows(schedules.read_rows(file.filename, data))
            return {**base, "kind": "schedule", "valid": True, "rows": len(cleaned), "message": "Schedule is valid",
                    "next_step": "Import it as the project baseline"}
        except ValueError as exc:
            return {**base, "kind": "schedule", "valid": False, "message": str(exc), "next_step": "Fix the file and retry"}
    if kind == "unknown":
        return {**base, "kind": "unknown", "valid": False, "status": "unsupported", "message": "File type is not recognized",
                "next_step": "Use CSV/Excel for schedules, or TXT, DOCX, PDF, images for site reports"}
    try:
        info = documents.inspect_document(file.filename, mime, data)
    except documents.UnsupportedDocument as exc:
        return {**base, "kind": kind, "valid": False, "message": str(exc), "next_step": "Export or scan the document again"}
    scanned = info["pages"] - info["text_pages"]
    return {**base, **info, "valid": True,
            "message": f"{kind.upper()} is readable" + (f" ({info['text_pages']} of {info['pages']} pages have a text layer; "
                                                         f"{scanned} will be OCR'd)" if kind == "pdf" else ""),
            "next_step": "Upload it as a site report to extract progress"}


# ---------------------------------------------------------------------------
# Dependency analysis, prediction, export
# ---------------------------------------------------------------------------
@app.get("/api/analysis/dependencies", dependencies=[viewer])
def dependencies():
    return analysis.dependency_analysis(_activities())


@app.get("/api/analysis/impact/{activity_id}", dependencies=[viewer])
def impact(activity_id: str, delay_days: float = Query(default=5, ge=0, le=365)):
    try:
        return analysis.downstream_impact(_activities(), activity_id, delay_days)
    except KeyError:
        raise HTTPException(404, "Activity not found")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def _issues_by_activity() -> dict[str, list[str]]:
    issues: dict[str, list[str]] = {}
    with db.session() as s:
        for r in s.scalars(select(Report).where(Report.status != "rejected")):
            text = r.issue or (r.text if any(w in r.text.lower() for w in ("delay", "issue", "interrupt", "waiting", "shortage")) else "")
            if text:
                issues.setdefault(r.activity_id, []).append(text)
    return issues


def compute_predictions() -> dict:
    acts = _activities()
    dep = analysis.dependency_analysis(acts)
    if not dep["valid"]:
        raise HTTPException(422, dep["message"])
    result = prediction.predict(dep["nodes"], {a.id: a for a in acts}, _issues_by_activity())
    now = utcnow()
    with db.session() as s:
        s.execute(Prediction.__table__.delete())
        for p in result["predictions"]:
            s.add(Prediction(activity_id=p["activity_id"], delay_probability=p["delay_probability"],
                             expected_delay_days=p["expected_delay_days"], downstream_count=p["downstream_count"],
                             risk_score=p["risk_score"], risk_level=p["risk_level"], factors_json=json.dumps(p["factors"]),
                             model_version=result["model"]["version"], computed_at=now))
        s.commit()
    result["computed_at"] = now
    return result


@app.get("/api/predictions", dependencies=[viewer])
def predictions():
    return compute_predictions()


@app.post("/api/predictions/train", dependencies=[Depends(require("admin"))])
def train_predictions():
    meta = prediction.train()
    return {"model": meta, "message": "Delay model retrained on the synthetic dataset."}


@app.get("/api/export/report.xlsx")
def export_report(user: CurrentUser = Depends(require("viewer"))):
    content = exporting.project_workbook(project(), summary(), activities(), dependencies(), compute_predictions(),
                                         reports(), audit(), user.display_name)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="infrasight-report-{date.today()}.xlsx"'})


@app.get("/api/export/{dataset}.csv", dependencies=[viewer])
def export_csv(dataset: Literal["activities", "reports", "predictions", "audit", "jobs"]):
    rows = {"activities": activities, "reports": reports, "audit": audit, "jobs": lambda: jobs(100),
            "predictions": lambda: compute_predictions()["predictions"]}[dataset]()
    return Response(exporting.to_csv(rows), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{dataset}.csv"'})


@app.get("/api/system", dependencies=[viewer])
def system_info():
    return {"version": VERSION, "matcher_backend": backend_name(), "text_model": settings.text_model,
            "vision_model": settings.vision_model, "num_ctx": settings.num_ctx, "timeout_s": settings.llm_timeout,
            "auth_enabled": settings.auth_enabled}
