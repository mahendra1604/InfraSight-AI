"""Schedule validation and import (report module: Schedule Management — Member 1).

Accepts UTF-8 CSV or Excel (.xlsx/.xlsm, first sheet). Column names are normalised and
common Primavera / MS Project export headers are mapped automatically.

Import policy:
* Activities in the file are inserted or updated (name, planned %, predecessors, dates, duration).
* Actual progress never decreases: an existing activity keeps max(current approved, file value).
* Activities missing from the file are removed only if no report references them.
* Every import is recorded as a baseline version.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import date
from pathlib import Path
from typing import Any

import networkx as nx
from sqlalchemy import delete, select

from app import db
from app.db import Activity, BaselineVersion, Prediction, Report, utcnow
from app.quantities import normalize_unit
from app.qwen_service import parse_date

REQUIRED = ["activity_id", "name", "planned_progress", "actual_progress"]
ALIASES = {
    "activity_id": {"activity_id", "activityid", "id", "task_id", "taskid", "activity_code", "code", "unique_id"},
    "name": {"name", "activity_name", "task_name", "activity", "task", "description"},
    "planned_progress": {"planned_progress", "planned", "planned_%", "planned_percent", "planned_%_complete", "baseline_%"},
    "actual_progress": {"actual_progress", "actual", "%_complete", "percent_complete", "actual_%", "physical_%_complete",
                        "activity_%_complete", "progress"},
    "predecessor": {"predecessor", "predecessors", "pred", "depends_on"},
    "planned_start": {"planned_start", "start", "baseline_start", "start_date", "bl_start"},
    "planned_finish": {"planned_finish", "finish", "baseline_finish", "finish_date", "end", "end_date", "bl_finish"},
    "duration_days": {"duration_days", "duration", "original_duration", "planned_duration", "dur"},
    "wbs": {"wbs", "wbs_code", "level", "outline_number"},
    "total_quantity": {"total_quantity", "quantity", "qty", "total_qty", "planned_quantity", "budgeted_quantity", "boq_quantity",
                       "boq_qty"},
    "unit": {"unit", "units", "uom", "unit_of_measure", "unit_of_measurement"},
}


def _key(header: str) -> str:
    return re.sub(r"[\s\-]+", "_", str(header or "").strip().lower())


def _map_columns(headers: list[str]) -> dict[str, str]:
    mapping = {}
    for header in headers:
        key = _key(header)
        for canonical, names in ALIASES.items():
            if key in names and canonical not in mapping.values():
                mapping[header] = canonical
                break
    return mapping


def read_rows(filename: str | None, data: bytes) -> list[dict[str, Any]]:
    ext = Path(filename or "").suffix.lower()
    if ext in {".xlsx", ".xlsm"} or data[:4] == b"PK\x03\x04":
        from openpyxl import load_workbook

        try:
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as exc:
            raise ValueError(f"The Excel workbook could not be read: {exc}") from exc
        sheet = workbook[workbook.sheetnames[0]]
        values = list(sheet.iter_rows(values_only=True))
        workbook.close()
        if not values:
            raise ValueError("Schedule is empty")
        headers = [str(h) if h is not None else "" for h in values[0]]
        raw_rows = [dict(zip(headers, row)) for row in values[1:] if any(v not in (None, "") for v in row)]
    else:
        try:
            decoded = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("This file is not a UTF-8 CSV. Export your Excel sheet as CSV UTF-8 and upload the .csv file.") from exc
        reader = csv.DictReader(io.StringIO(decoded))
        headers = reader.fieldnames or []
        raw_rows = list(reader)
    mapping = _map_columns(headers)
    missing = [c for c in REQUIRED if c not in mapping.values()]
    if missing:
        raise ValueError("Required columns: " + ", ".join(REQUIRED + ["predecessor"]) + ". Missing: " + ", ".join(missing))
    return [{mapping[h]: v for h, v in row.items() if h in mapping} for row in raw_rows]


def _number(value: Any, column: str, activity_id: str) -> float:
    """Accepts 60, "60", "60%", "25d", "25 days"."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        match = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*(%|d|days?|edays?)?", str(value or "").strip(), re.IGNORECASE)
        if not match:
            raise ValueError(f"{column} must be numeric for {activity_id}")
        number = float(match.group(1))
    if number != number:  # NaN
        raise ValueError(f"{column} must be numeric for {activity_id}")
    return number


def existing_links() -> dict[str, list[str]]:
    """Current activities and their predecessors (used to validate a merge import)."""
    with db.session() as s:
        return {a.id: a.predecessors for a in s.scalars(select(Activity))}


def validate_rows(rows: list[dict[str, Any]], existing: dict[str, list[str]] | None = None) -> list[dict[str, Any]]:
    """existing: when merging, the current activities and links; predecessors may then refer to them."""
    if not rows:
        raise ValueError("Schedule is empty")
    cleaned = []
    for row in rows:
        activity_id = str(row.get("activity_id") or "").strip()
        if isinstance(row.get("activity_id"), float) and row["activity_id"].is_integer():
            activity_id = str(int(row["activity_id"]))
        name = str(row.get("name") or "").strip()
        if not activity_id:
            raise ValueError("Activity IDs must be nonempty and unique")
        if not name:
            raise ValueError("Activity names are required")
        planned = _number(row.get("planned_progress"), "planned_progress", activity_id)
        actual = _number(row.get("actual_progress"), "actual_progress", activity_id)
        if not (0 <= planned <= 100 and 0 <= actual <= 100):
            raise ValueError("Progress must be between 0 and 100")
        preds = [p.strip() for p in re.split(r"[;,]", str(row.get("predecessor") or "")) if p.strip()]
        preds = [re.sub(r"(FS|SS|FF|SF)([+-]\d+\w*)?$", "", p).strip() for p in preds]  # "A101FS+2" -> "A101"
        start = finish = None
        try:
            start = parse_date(row.get("planned_start"))
            finish = parse_date(row.get("planned_finish"))
        except ValueError as exc:
            raise ValueError(f"Invalid date for {activity_id}: {exc}")
        duration = None
        if row.get("duration_days") not in (None, ""):
            duration = _number(row.get("duration_days"), "duration_days", activity_id)
        elif start and finish:
            duration = float((date.fromisoformat(finish) - date.fromisoformat(start)).days + 1)
        if duration is not None and duration <= 0:
            raise ValueError(f"Duration must be positive for {activity_id}")
        total_quantity = None
        if row.get("total_quantity") not in (None, ""):
            total_quantity = _number(row.get("total_quantity"), "total_quantity", activity_id)
            if total_quantity <= 0:
                raise ValueError(f"total_quantity must be positive for {activity_id}")
        unit = normalize_unit(row.get("unit")) if row.get("unit") not in (None, "") else None
        if total_quantity is not None and not unit:
            raise ValueError(f"A unit is required when total_quantity is given ({activity_id})")
        cleaned.append({"id": activity_id, "name": name, "planned": planned, "actual": actual, "predecessors": preds,
                        "planned_start": start, "planned_finish": finish, "duration_days": duration,
                        "wbs": str(row.get("wbs")).strip() if row.get("wbs") not in (None, "") else None,
                        "total_quantity": total_quantity, "unit": unit if total_quantity is not None else None})
    ids = [r["id"] for r in cleaned]
    if len(set(ids)) != len(ids):
        raise ValueError("Activity IDs must be nonempty and unique")
    graph = nx.DiGraph()
    graph.add_nodes_from(ids)
    for activity_id, preds in (existing or {}).items():
        if activity_id in ids:
            continue  # the file's version of this activity replaces its links
        graph.add_node(activity_id)
        for p in preds:
            graph.add_edge(p, activity_id)
    for r in cleaned:
        for p in r["predecessors"]:
            if p not in graph:
                raise ValueError("Unknown predecessor: " + p)
            graph.add_edge(p, r["id"])
    if not nx.is_directed_acyclic_graph(graph):
        cycle = " → ".join(edge[0] for edge in nx.find_cycle(graph))
        raise ValueError(f"Dependency cycle detected ({cycle})")
    return cleaned


def import_schedule(cleaned: list[dict[str, Any]], filename: str, user: str | None, keep_missing: bool = False) -> dict[str, Any]:
    """keep_missing=True merges: activities that are not in the file stay untouched."""
    added = updated = removed = 0
    kept = []
    with db.session() as s:
        existing = {a.id: a for a in s.scalars(select(Activity))}
        for row in cleaned:
            activity = existing.get(row["id"])
            fields = dict(name=row["name"], planned=row["planned"], predecessor=";".join(row["predecessors"]),
                          planned_start=row["planned_start"], planned_finish=row["planned_finish"],
                          duration_days=row["duration_days"], wbs=row["wbs"])
            if row.get("total_quantity") is not None:
                fields.update(total_quantity=row["total_quantity"], unit=row["unit"])
            if activity:
                for key, value in fields.items():
                    setattr(activity, key, value)
                activity.actual = max(activity.actual, row["actual"])
                updated += 1
            else:
                s.add(Activity(id=row["id"], actual=row["actual"], **fields))
                added += 1
        new_ids = {r["id"] for r in cleaned}
        referenced = set(s.scalars(select(Report.activity_id).distinct()))
        s.execute(delete(Prediction))
        for activity_id, activity in existing.items():
            if activity_id in new_ids or keep_missing:
                continue
            if activity_id in referenced:
                kept.append(activity_id)
            else:
                s.delete(activity)
                removed += 1
        version = BaselineVersion(filename=filename or "schedule", activities=len(cleaned), added=added, updated=updated,
                                  removed=removed, imported_by=user, imported_at=utcnow())
        s.add(version)
        s.commit()
        return {"baseline_version": version.id, "activities": len(cleaned), "added": added, "updated": updated,
                "removed": removed, "kept_with_reports": kept,
                "message": f"Imported {len(cleaned)} activities ({added} added, {updated} updated, {removed} removed)."}
