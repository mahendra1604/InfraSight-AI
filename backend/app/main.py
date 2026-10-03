"""Local development API. Authentication and AI integration are future modules."""
import csv
import io
import os
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, ForeignKey, String, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./infrasight.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})

class Base(DeclarativeBase):
    pass

class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str]
    planned: Mapped[float]
    actual: Mapped[float]
    predecessor: Mapped[str] = mapped_column(default="")

class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"))
    text: Mapped[str]
    progress: Mapped[float]
    reporting_date: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    source: Mapped[str] = mapped_column(default="Demo fixture")

class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int]
    action: Mapped[str]
    details: Mapped[str]

Base.metadata.create_all(engine)
app = FastAPI(title="InfraSight AI", version="0.1.0", description="Single-project local development starter. No trained AI or authentication yet.")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","), allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

def seed():
    with Session(engine) as session:
        if session.scalar(select(Activity.id).limit(1)):
            return
        sample = Path(__file__).resolve().parents[2] / "data" / "samples" / "schedule.csv"
        for row in csv.DictReader(sample.read_text(encoding="utf-8").splitlines()):
            session.add(Activity(id=row["activity_id"], name=row["name"], planned=float(row["planned_progress"]), actual=float(row["actual_progress"]), predecessor=row["predecessor"]))
        session.flush()
        session.add(Report(activity_id="A102", text="Block A foundation concrete is now 60% complete. Rain interrupted the pour.", progress=60, reporting_date="2026-10-01"))
        session.commit()

seed()

@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0"}

@app.get("/api/project")
def project():
    return {"id": "demo", "name": "Riverside Infrastructure", "description": "Synthetic construction project · baseline as of 1 October 2026", "mode": "demo", "as_of": "2026-10-01"}

@app.get("/api/activities")
def activities():
    with Session(engine) as session:
        return [{"id": a.id, "name": a.name, "planned": a.planned, "actual": a.actual, "variance": round(a.actual-a.planned, 2), "predecessor": a.predecessor} for a in session.scalars(select(Activity).order_by(Activity.id))]

@app.get("/api/summary")
def summary():
    rows = activities()
    return {"activities": len(rows), "planned": round(sum(a["planned"] for a in rows)/len(rows), 1) if rows else 0, "actual": round(sum(a["actual"] for a in rows)/len(rows), 1) if rows else 0, "behind": sum(a["variance"] < 0 for a in rows), "method": "Unweighted mean of activity percentages; demo baseline, not earned value."}

@app.get("/api/reports")
def reports():
    with Session(engine) as session:
        return [{"id": r.id, "activity_id": r.activity_id, "text": r.text, "progress": r.progress, "reporting_date": r.reporting_date, "status": r.status, "source": r.source} for r in session.scalars(select(Report).order_by(Report.id.desc()))]

class ReportInput(BaseModel):
    activity_id: str
    text: str = Field(min_length=1, max_length=5000)
    progress: float = Field(ge=0, le=100, allow_inf_nan=False)
    reporting_date: date

@app.post("/api/reports", status_code=201)
def create_report(body: ReportInput):
    with Session(engine) as session:
        if not session.get(Activity, body.activity_id):
            raise HTTPException(404, "Activity not found")
        report = Report(**body.model_dump(exclude={"reporting_date"}), reporting_date=body.reporting_date.isoformat(), source="Manual entry")
        session.add(report)
        session.commit()
        return {"id": report.id, "status": report.status}

class Decision(BaseModel):
    action: Literal["approve", "reject"]

@app.post("/api/reports/{report_id}/review")
def review(report_id: int, body: Decision):
    with Session(engine) as session:
        report = session.get(Report, report_id)
        if not report:
            raise HTTPException(404, "Report not found")
        if report.status != "pending":
            raise HTTPException(409, "Report already reviewed")
        if body.action == "approve":
            latest = session.scalar(select(Report).where(Report.activity_id == report.activity_id, Report.status == "approved").order_by(Report.reporting_date.desc()).limit(1))
            if latest and report.reporting_date < latest.reporting_date:
                raise HTTPException(409, "A newer approved report exists; historical corrections are not supported yet")
            activity = session.get(Activity, report.activity_id)
            if report.progress < activity.actual:
                raise HTTPException(409, "Cumulative progress cannot decrease in this starter")
            activity.actual = report.progress
        report.status = "approved" if body.action == "approve" else "rejected"
        session.add(Audit(report_id=report.id, action=body.action, details=f"Local demo reviewer; activity {report.activity_id}; cumulative progress {report.progress}%"))
        session.commit()
        return {"status": report.status}

@app.get("/api/audit")
def audit():
    with Session(engine) as session:
        return [{"id": a.id, "report_id": a.report_id, "action": a.action, "details": a.details} for a in session.scalars(select(Audit).order_by(Audit.id.desc()))]

@app.post("/api/schedules/validate")
async def validate_schedule(file: UploadFile = File(...)):
    """Validate a CSV without replacing the shared demo baseline."""
    data = await file.read(1_000_001)
    if len(data) > 1_000_000:
        raise HTTPException(413, "CSV must be at most 1 MB")
    try:
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        required = {"activity_id", "name", "planned_progress", "actual_progress", "predecessor"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError("Required columns: " + ", ".join(sorted(required)))
        rows = list(reader)
        if not rows:
            raise ValueError("Schedule is empty")
        ids = [r["activity_id"].strip() for r in rows]
        if not all(ids) or len(set(ids)) != len(ids):
            raise ValueError("Activity IDs must be nonempty and unique")
        links = {}
        for row, activity_id in zip(rows, ids):
            if not row["name"].strip():
                raise ValueError("Activity names are required")
            for column in ("planned_progress", "actual_progress"):
                if not 0 <= float(row[column]) <= 100:
                    raise ValueError("Progress must be between 0 and 100")
            parent = row["predecessor"].strip()
            if parent and parent not in ids:
                raise ValueError("Unknown predecessor: " + parent)
            links[activity_id] = parent
        for activity_id in ids:
            seen = set()
            current = activity_id
            while current:
                if current in seen:
                    raise ValueError("Dependency cycle detected")
                seen.add(current)
                current = links[current]
        return {"valid": True, "activities": len(rows), "message": "CSV validated. Import and baseline versioning are planned; the demo was not changed."}
    except (ValueError, TypeError, KeyError, AttributeError, csv.Error) as exc:
        raise HTTPException(422, str(exc)) from exc
