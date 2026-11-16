"""Database engine, ORM models, lightweight migrations and demo seed data."""
from __future__ import annotations

import csv
import hashlib
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

from sqlalchemy import Engine, ForeignKey, String, Text, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from app.config import ROOT, settings


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Base(DeclarativeBase):
    pass


class Activity(Base):
    __tablename__ = "activities"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str]
    planned: Mapped[float]
    actual: Mapped[float]
    # One or more predecessor IDs separated by ";" (the original starter allowed one).
    predecessor: Mapped[str] = mapped_column(default="")
    planned_start: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    planned_finish: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    duration_days: Mapped[Optional[float]] = mapped_column(nullable=True)
    wbs: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # Planned total quantity (e.g. 450 m of drainage) for quantity-based progress.
    total_quantity: Mapped[Optional[float]] = mapped_column(nullable=True)
    unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    @property
    def predecessors(self) -> list[str]:
        return [p.strip() for p in (self.predecessor or "").replace(",", ";").split(";") if p.strip()]


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"))
    text: Mapped[str]
    progress: Mapped[float]
    reporting_date: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    source: Mapped[str] = mapped_column(default="Demo fixture")
    # --- AI provenance (all nullable so old databases migrate in place) ---
    issue: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extracted_activity: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    match_score: Mapped[Optional[float]] = mapped_column(nullable=True)
    match_method: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    match_status: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    candidates_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    job_id: Mapped[Optional[int]] = mapped_column(nullable=True)
    file_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    evidence_page: Mapped[Optional[int]] = mapped_column(nullable=True)
    needs_review: Mapped[Optional[bool]] = mapped_column(nullable=True)
    warnings_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    # Cumulative quantity behind the progress value (in the activity's unit), when reported by quantity.
    quantity_done: Mapped[Optional[float]] = mapped_column(nullable=True)
    quantity_unit: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)


class Audit(Base):
    __tablename__ = "audit"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int]
    action: Mapped[str]
    details: Mapped[str]
    actor: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)


class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str]
    content_type: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    stored_path: Mapped[str]
    instruction: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mode: Mapped[str] = mapped_column(String(20), default="auto")
    purpose: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, default="report")  # report | plan
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|failed
    stage: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    method: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)  # text|ocr|vision
    model: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[str] = mapped_column(String(32), default=utcnow)
    started_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    finished_at: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    duration_s: Mapped[Optional[float]] = mapped_column(nullable=True)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str]
    role: Mapped[str] = mapped_column(String(20))  # admin|manager|engineer|viewer
    password_hash: Mapped[str]


class BaselineVersion(Base):
    __tablename__ = "baseline_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str]
    activities: Mapped[int]
    added: Mapped[int] = mapped_column(default=0)
    updated: Mapped[int] = mapped_column(default=0)
    removed: Mapped[int] = mapped_column(default=0)
    imported_by: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    imported_at: Mapped[str] = mapped_column(String(32), default=utcnow)


class Prediction(Base):
    __tablename__ = "predictions"
    activity_id: Mapped[str] = mapped_column(ForeignKey("activities.id"), primary_key=True)
    delay_probability: Mapped[float]
    expected_delay_days: Mapped[float]
    downstream_count: Mapped[int]
    risk_score: Mapped[float]
    risk_level: Mapped[str] = mapped_column(String(10))
    factors_json: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(String(80))
    computed_at: Mapped[str] = mapped_column(String(32))


# ---------------------------------------------------------------------------
# Engine management (reconfigurable so tests can point at a temporary DB)
# ---------------------------------------------------------------------------
engine: Engine = None  # type: ignore[assignment]


def configure(url: str | None = None) -> Engine:
    global engine
    if engine is not None:
        engine.dispose()
    url = url or settings.database_url
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    return engine


@contextmanager
def session() -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as s:
        yield s


def _add_missing_columns() -> None:
    """Tiny forward-only migration: add nullable columns that exist in the
    models but not in an older database. (Use Alembic for anything more.)"""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                ddl_type = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {ddl_type}'))


VIEWS = {
    # Power BI / reporting views. Kept dialect-neutral.
    "v_activity_progress": """
        SELECT a.id AS activity_id, a.name, a.planned AS planned_progress, a.actual AS actual_progress,
               a.actual - a.planned AS variance_pp, a.predecessor, a.planned_start, a.planned_finish, a.duration_days,
               a.total_quantity, a.unit, a.actual * a.total_quantity / 100.0 AS actual_quantity
        FROM activities a""",
    "v_report_pipeline": """
        SELECT r.id AS report_id, r.activity_id, r.progress, r.quantity_done, r.quantity_unit, r.reporting_date, r.status,
               r.source, r.match_score, r.match_method, r.match_status, r.model, r.job_id, r.created_at
        FROM reports r""",
    "v_risk": """
        SELECT p.activity_id, a.name, a.planned AS planned_progress, a.actual AS actual_progress,
               p.delay_probability, p.expected_delay_days, p.downstream_count, p.risk_score, p.risk_level,
               p.model_version, p.computed_at
        FROM predictions p JOIN activities a ON a.id = p.activity_id""",
}


def _create_views() -> None:
    with engine.begin() as conn:
        for name, sql in VIEWS.items():
            if engine.dialect.name == "postgresql":
                conn.execute(text(f"CREATE OR REPLACE VIEW {name} AS {sql}"))
            else:
                conn.execute(text(f"DROP VIEW IF EXISTS {name}"))
                conn.execute(text(f"CREATE VIEW {name} AS {sql}"))


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2_sha256$200000${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations))
        return digest.hex() == digest_hex
    except (ValueError, TypeError):
        return False


DEMO_USERS = [
    ("admin", "Admin", "admin", "admin123"),
    ("manager", "Project Manager", "manager", "manager123"),
    ("engineer", "Site Engineer", "engineer", "engineer123"),
    ("viewer", "Viewer", "viewer", "viewer123"),
]

SAMPLE_SCHEDULE = ROOT / "data" / "samples" / "schedule.csv"


def _float_or_none(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def seed() -> None:
    with session() as s:
        if not s.scalar(select(User.id).limit(1)):
            for username, name, role, password in DEMO_USERS:
                s.add(User(username=username, display_name=name, role=role, password_hash=hash_password(password)))
        sample = {r["activity_id"]: r for r in csv.DictReader(SAMPLE_SCHEDULE.read_text(encoding="utf-8-sig").splitlines())}
        if not s.scalar(select(Activity.id).limit(1)):
            for row in sample.values():
                s.add(Activity(
                    id=row["activity_id"], name=row["name"], planned=float(row["planned_progress"]),
                    actual=float(row["actual_progress"]), predecessor=row.get("predecessor", "") or "",
                    planned_start=row.get("planned_start") or None, planned_finish=row.get("planned_finish") or None,
                    duration_days=_float_or_none(row.get("duration_days")), wbs=row.get("wbs") or None,
                    total_quantity=_float_or_none(row.get("total_quantity")), unit=row.get("unit") or None,
                ))
            s.flush()
            s.add(Report(activity_id="A102", text="Block A foundation concrete is now 60% complete. Rain interrupted the pour.",
                         progress=60, reporting_date="2026-10-01", source="Demo fixture", match_status="manual",
                         created_at=utcnow()))
        else:
            # Backfill fields that databases created by older versions do not have yet (never overwrite values).
            for activity in s.scalars(select(Activity)):
                row = sample.get(activity.id)
                if not row or row["name"] != activity.name:
                    continue
                activity.planned_start = activity.planned_start or row.get("planned_start") or None
                activity.planned_finish = activity.planned_finish or row.get("planned_finish") or None
                if activity.duration_days is None:
                    activity.duration_days = _float_or_none(row.get("duration_days"))
                if activity.total_quantity is None and row.get("total_quantity"):
                    activity.total_quantity = _float_or_none(row.get("total_quantity"))
                    activity.unit = activity.unit or row.get("unit") or None
        s.commit()


def recover_interrupted_jobs() -> None:
    with session() as s:
        for job in s.scalars(select(AnalysisJob).where(AnalysisJob.status.in_(["queued", "running"]))):
            job.status = "failed"
            job.error = "Interrupted by a server restart. Upload the file again to re-run the analysis."
            job.finished_at = utcnow()
        s.commit()


def init() -> None:
    if engine is None:
        configure()
    Base.metadata.create_all(engine)
    _add_missing_columns()
    _create_views()
    seed()
    recover_interrupted_jobs()


configure()
