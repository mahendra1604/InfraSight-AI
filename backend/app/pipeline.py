"""Background analysis jobs: upload -> read/OCR -> LLM extraction -> matching -> pending reports."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import date
from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select

from app import db, quantities, qwen_service
from app.config import settings
from app.db import Activity, AnalysisJob, Report, utcnow
from app.documents import Document, UnsupportedDocument, chunk_text_pages, load_document, load_plan_pages
from app.matching import ActivityMatcher

log = logging.getLogger("infrasight.pipeline")

# Ollama runs one request at a time on this hardware (OLLAMA_NUM_PARALLEL=1),
# so jobs are processed strictly one after another (one lock per event loop).
_job_locks: dict[int, asyncio.Lock] = {}


def _job_lock() -> asyncio.Lock:
    return _job_locks.setdefault(id(asyncio.get_running_loop()), asyncio.Lock())


def store_upload(data: bytes, filename: str) -> tuple[str, Path]:
    sha256 = hashlib.sha256(data).hexdigest()
    folder = settings.upload_dir / sha256[:2]
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{sha256}{Path(filename or '').suffix.lower()[:10]}"
    if not path.exists():
        path.write_bytes(data)
    return sha256, path


def job_to_dict(job: AnalysisJob) -> dict[str, Any]:
    return {
        "id": job.id, "filename": job.filename, "status": job.status, "stage": job.stage, "method": job.method,
        "model": job.model, "mode": job.mode, "instruction": job.instruction, "error": job.error,
        "created_at": job.created_at, "started_at": job.started_at, "finished_at": job.finished_at,
        "duration_s": job.duration_s, "created_by": job.created_by, "sha256": job.sha256,
        "purpose": job.purpose or "report",
        "result": json.loads(job.result_json) if job.result_json else None,
    }


def find_existing_job(sha256: str, instruction: str, mode: str, purpose: str = "report") -> AnalysisJob | None:
    with db.session() as s:
        purpose_filter = AnalysisJob.purpose == purpose if purpose != "report" else \
            (AnalysisJob.purpose == "report") | AnalysisJob.purpose.is_(None)
        return s.scalar(
            select(AnalysisJob)
            .where(AnalysisJob.sha256 == sha256, AnalysisJob.status.in_(["queued", "running", "done"]),
                   AnalysisJob.instruction == instruction, AnalysisJob.mode == mode, purpose_filter)
            .order_by(AnalysisJob.id.desc()).limit(1))


def create_job(data: bytes, filename: str, content_type: str | None, instruction: str, mode: str, user: str | None,
               purpose: str = "report") -> AnalysisJob:
    sha256, path = store_upload(data, filename)
    with db.session() as s:
        job = AnalysisJob(filename=filename or "upload", content_type=content_type, sha256=sha256, stored_path=str(path),
                          instruction=instruction, mode=mode, status="queued", stage="Waiting for the model",
                          created_by=user, created_at=utcnow(), purpose=purpose)
        s.add(job)
        s.commit()
        return job


def _update(job_id: int, **fields: Any) -> None:
    with db.session() as s:
        job = s.get(AnalysisJob, job_id)
        for key, value in fields.items():
            setattr(job, key, value)
        s.commit()


_OCR_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})


def _canonical(text: str) -> str:
    return re.sub(r"[\s\-_./]", "", text.upper()).translate(_OCR_LOOKALIKES)


def ground_activity_ids(entries: list[dict[str, Any]], source_text: str) -> None:
    """Small models sometimes number entries themselves (A101, A102, ...). Keep an ID only
    if it is really printed in the source text (tolerating OCR's O/0 and l/1 confusions)."""
    canonical_source = _canonical(source_text)
    for entry in entries:
        code = entry.get("activity_id")
        if code and _canonical(code) not in canonical_source:
            entry["activity_id"] = None
            entry.setdefault("warnings", []).append(f"The model returned ID {code}, which is not in the document; ignored.")


def _locate_page(entry: dict[str, Any], doc: Document) -> int | None:
    if entry.get("page"):
        return entry["page"]
    needle = (entry.get("evidence") or entry.get("activity_text") or "").strip().lower()[:60]
    if not needle:
        return None
    for page in doc.pages:
        if needle in page.text.lower():
            return page.number
    return None


def persist_reports(job: AnalysisJob, extraction: dict[str, Any], method: str) -> tuple[list[dict], list[dict]]:
    """Match entries to activities and create pending reports. Returns (created, skipped)."""
    created: list[dict] = []
    skipped: list[dict] = []
    model = extraction.get("meta", {}).get("model")
    with db.session() as s:
        activities = list(s.scalars(select(Activity).order_by(Activity.id)))
        by_id = {a.id: a for a in activities}
        matcher = ActivityMatcher([(a.id, a.name) for a in activities])
        for entry in extraction.get("entries", []):
            match = matcher.match(entry)
            entry["match"] = match.to_dict()
            summary = {"activity_text": entry.get("activity_text"), "activity_id": match.activity_id,
                       "progress": entry.get("progress"), "match_status": match.status, "score": match.score}
            progress = entry.get("progress")
            has_quantity = entry.get("quantity_done") is not None or entry.get("quantity_today") is not None
            if progress is None and not has_quantity:
                skipped.append({**summary, "reason": "No progress value in the report"})
                continue
            if match.status == "unmatched" or not match.activity_id:
                skipped.append({**summary, "reason": "No schedule activity is similar enough"})
                continue
            warnings = list(entry.get("warnings", [])) + match.notes
            needs_review = bool(entry.get("needs_review"))
            quantity_done = quantity_unit = None
            if has_quantity:
                activity = by_id[match.activity_id]
                qp = quantities.progress_from_quantity(
                    activity.total_quantity, activity.unit, activity.actual, done=entry.get("quantity_done"),
                    today=entry.get("quantity_today"), unit=entry.get("quantity_unit"), report_total=entry.get("quantity_total"))
                if qp.progress is None:
                    if progress is None:
                        skipped.append({**summary, "reason": qp.reason})
                        continue
                    warnings.append(f"Quantity not used: {qp.reason}")
                elif progress is None:
                    progress = qp.progress
                    quantity_done, quantity_unit = qp.quantity_done, qp.unit
                    warnings.extend(qp.notes)
                    needs_review = needs_review or qp.needs_review
                    summary["progress"] = progress
                    summary["quantity"] = f"{quantities.fmt(qp.quantity_done)} {qp.unit}"
                else:
                    quantity_done, quantity_unit = qp.quantity_done, qp.unit
                    if abs(qp.progress - float(progress)) > 5:
                        warnings.append(f"The report states {progress}% but its quantity gives {qp.progress}%. Check which is right.")
                        needs_review = True
            reporting_date = entry.get("reporting_date")
            if not reporting_date:
                reporting_date = date.today().isoformat()
                needs_review = True
                warnings.append("No date in the report; the upload date was used.")
            duplicate = s.scalar(select(Report).where(
                Report.activity_id == match.activity_id, Report.reporting_date == reporting_date,
                Report.progress == float(progress), Report.status != "rejected").limit(1))
            if duplicate:
                skipped.append({**summary, "reason": f"Duplicate of report {duplicate.id}"})
                continue
            activity = by_id[match.activity_id]
            if match.status in {"id_match", "suggested"} and float(progress) < activity.actual:
                skipped.append({**summary, "reason": f"{progress}% is lower than the approved {activity.actual}% "
                                                     "(cumulative progress cannot decrease)"})
                continue
            text = entry.get("evidence") or f"{entry.get('activity_text')}: {progress}%"
            if entry.get("issue") and entry["issue"].lower() not in text.lower():
                text = f"{text} — Issue: {entry['issue']}"
            report = Report(
                activity_id=match.activity_id, text=text[:5000], progress=float(progress), reporting_date=reporting_date,
                status="pending", source=f"AI ({method}) · {job.filename}", issue=entry.get("issue"),
                extracted_activity=entry.get("activity_text"), match_score=match.score, match_method=match.method,
                match_status=match.status, candidates_json=json.dumps(match.candidates), model=model, job_id=job.id,
                file_sha256=job.sha256, evidence_page=entry.get("page"), needs_review=needs_review,
                warnings_json=json.dumps(warnings), created_at=utcnow(), quantity_done=quantity_done, quantity_unit=quantity_unit)
            s.add(report)
            s.flush()
            created.append({**summary, "report_id": report.id, "activity_name": activity.name,
                            "reporting_date": reporting_date, "needs_review": needs_review})
        s.commit()
    return created, skipped


async def extract_document(filename: str, content_type: str | None, data: bytes, mode: str = "auto",
                           instruction: str = "", on_stage: Callable[[str, str], None] | None = None
                           ) -> tuple[Document, dict[str, Any], list[str]]:
    """Read a document and run LLM extraction on every text chunk / vision page.
    Shared by background jobs and ml/eval_extraction.py so both measure the same path."""
    doc = await asyncio.to_thread(load_document, filename, content_type, data, mode)
    chunks = chunk_text_pages(doc.pages)
    vision_pages = [p for p in doc.pages if p.method == "vision"]
    warnings = list(doc.warnings)
    if len(chunks) + len(vision_pages) > settings.max_chunks:
        warnings.append(f"Only the first {settings.max_chunks} sections were analysed.")
        chunks = chunks[:settings.max_chunks]
        vision_pages = vision_pages[:max(0, settings.max_chunks - len(chunks))]
    if not chunks and not vision_pages:
        raise UnsupportedDocument("No readable text was found in this file.")
    method = "+".join(doc.methods)
    stage = on_stage or (lambda _stage, _method: None)
    models, results = [], []
    total = len(chunks) + len(vision_pages)
    for index, (chunk, pages) in enumerate(chunks, start=1):
        stage(f"Extracting with {settings.text_model} ({index}/{total})", method)
        result = await qwen_service.extract_from_text(chunk, instruction)
        ground_activity_ids(result["entries"], chunk)
        if len(pages) == 1:
            for entry in result["entries"]:
                entry["page"] = pages[0]
        results.append(result)
        models.append(settings.text_model)
    for offset, page in enumerate(vision_pages, start=len(chunks) + 1):
        stage(f"Reading page {page.number} with {settings.vision_model} ({offset}/{total})", method)
        result = await qwen_service.extract_from_image(page.image, instruction)
        for entry in result["entries"]:
            entry["page"] = page.number
        results.append(result)
        models.append(settings.vision_model)
    extraction = qwen_service.merge_results(results)
    extraction["meta"]["model"] = " + ".join(dict.fromkeys(models))
    for entry in extraction["entries"]:
        entry["page"] = _locate_page(entry, doc)
    return doc, extraction, warnings


async def run_job(job_id: int) -> None:
    async with _job_lock():
        started = time.perf_counter()
        _update(job_id, status="running", stage="Reading the document", started_at=utcnow())
        with db.session() as s:
            job = s.get(AnalysisJob, job_id)
        try:
            data = Path(job.stored_path).read_bytes()
            doc, extraction, warnings = await extract_document(
                job.filename, job.content_type, data, job.mode, job.instruction or "",
                on_stage=lambda stage, method: _update(job_id, stage=stage, method=method))
            method = "+".join(doc.methods)
            _update(job_id, stage="Matching activities to the schedule")
            created, skipped = await asyncio.to_thread(persist_reports, job, extraction, method)
            result = {
                "document": {"kind": doc.kind, "pages": len(doc.pages), "methods": doc.methods,
                             "ocr_confidence": [p.ocr_confidence for p in doc.pages if p.ocr_confidence is not None],
                             "warnings": warnings},
                "extraction": extraction, "created_reports": created, "skipped": skipped,
            }
            _update(job_id, status="done", stage="Finished", method=method, model=extraction["meta"]["model"],
                    result_json=json.dumps(result, default=str), finished_at=utcnow(),
                    duration_s=round(time.perf_counter() - started, 1))
        except (qwen_service.QwenUnavailable, qwen_service.QwenTimeout, qwen_service.QwenBadOutput, UnsupportedDocument) as exc:
            log.warning("job %s failed: %s", job_id, exc)
            _update(job_id, status="failed", stage="Failed", error=str(exc), finished_at=utcnow(),
                    duration_s=round(time.perf_counter() - started, 1))
        except Exception as exc:  # pragma: no cover - safety net
            log.exception("job %s crashed", job_id)
            _update(job_id, status="failed", stage="Failed", error=f"Unexpected error: {exc}", finished_at=utcnow(),
                    duration_s=round(time.perf_counter() - started, 1))


# ---------------------------------------------------------------------------
# Site plans: quantity take-off and draft schedule
# ---------------------------------------------------------------------------
CATEGORY_HINTS = {"drainage": "drainage lines", "road": "road", "parking": "parking", "boundary": "site preparation",
                  "earthworks": "earthworks excavation", "landscape": "landscaping", "utility": "utility services"}


def suggest_activities(totals: list[dict[str, Any]]) -> None:
    """Attach the closest existing schedule activity to every take-off total (for 'Apply quantity')."""
    with db.session() as s:
        activities = list(s.scalars(select(Activity).order_by(Activity.id)))
    matcher = ActivityMatcher([(a.id, a.name) for a in activities])
    by_id = {a.id: a for a in activities}
    for total in totals:
        hint = CATEGORY_HINTS.get(total["category"], "")
        query = total["name"] if not hint or hint.split()[0] in total["name"].lower() else f"{total['name']} {hint}"
        match = matcher.match({"activity_text": query})
        candidate = by_id.get(match.activity_id) if match.activity_id else None
        compatible = bool(candidate and (not candidate.unit or quantities.convert(1, total["unit"], candidate.unit) is not None))
        total["suggestion"] = {
            "activity_id": match.activity_id if match.status != "unmatched" else None,
            "name": match.activity_name, "score": match.score, "status": match.status,
            "current_quantity": candidate.total_quantity if candidate else None,
            "current_unit": candidate.unit if candidate else None, "unit_compatible": compatible,
        } if match.activity_id else None
        total["candidates"] = match.candidates


async def analyse_plan(filename: str, content_type: str | None, data: bytes, mode: str = "vision", instruction: str = "",
                       on_stage: Callable[[str, str], None] | None = None) -> dict[str, Any]:
    stage = on_stage or (lambda _stage, _method: None)
    method = "ocr+text" if mode == "text" else "vision+ocr"
    stage("Reading the drawing (OCR at full resolution)", method)
    pages, warnings = await asyncio.to_thread(load_plan_pages, filename, content_type, data)
    readings = []
    for page in pages:
        if mode == "text":
            if not page.text.strip():
                raise UnsupportedDocument("OCR found no text on the drawing. Use the vision mode instead.")
            stage(f"Reading labels with {settings.text_model} (sheet {page.number}/{len(pages)})", method)
            reading = await qwen_service.extract_plan_from_text(page.text, instruction)
        else:
            stage(f"Reading the drawing with {settings.vision_model} (sheet {page.number}/{len(pages)})", method)
            reading = await qwen_service.extract_plan(page.image, page.text, instruction)
        reading["elements"], double_count_notes = quantities.resolve_double_counting(reading["elements"], page.text)
        reading.setdefault("warnings", []).extend(double_count_notes)
        for element in reading["elements"]:
            element["sheet"] = page.number
        readings.append(reading)
    stage("Calculating the quantity take-off", method)
    elements = [e for r in readings for e in r["elements"]]
    takeoff = quantities.build_takeoff(elements)
    await asyncio.to_thread(suggest_activities, takeoff["totals"])
    first = readings[0] if readings else {}
    return {
        "plan": {"drawing_title": first.get("drawing_title"), "scale": first.get("scale"),
                 "drawing_date": first.get("drawing_date"), "answer": " ".join(r["answer"] for r in readings if r.get("answer")),
                 "notes": [n for r in readings for n in r.get("notes", [])], "elements": elements,
                 "sheets": [{"number": p.number, "size": [p.width, p.height], "ocr_characters": len(p.text)} for p in pages]},
        "takeoff": takeoff,
        "draft_schedule": quantities.draft_schedule(takeoff),
        "document": {"kind": "plan", "pages": len(pages), "methods": [method],
                     "warnings": warnings + [w for r in readings for w in r.get("warnings", [])]},
        "model": " + ".join(dict.fromkeys(r["meta"]["model"] for r in readings if r.get("meta"))),
    }


async def run_plan_job(job_id: int) -> None:
    async with _job_lock():
        started = time.perf_counter()
        _update(job_id, status="running", stage="Reading the drawing", started_at=utcnow())
        with db.session() as s:
            job = s.get(AnalysisJob, job_id)
        try:
            result = await analyse_plan(job.filename, job.content_type, Path(job.stored_path).read_bytes(), job.mode,
                                        job.instruction or "", on_stage=lambda stage, method: _update(job_id, stage=stage, method=method))
            _update(job_id, status="done", stage="Finished", model=result["model"], result_json=json.dumps(result, default=str),
                    finished_at=utcnow(), duration_s=round(time.perf_counter() - started, 1))
        except (qwen_service.QwenUnavailable, qwen_service.QwenTimeout, qwen_service.QwenBadOutput, UnsupportedDocument) as exc:
            log.warning("plan job %s failed: %s", job_id, exc)
            _update(job_id, status="failed", stage="Failed", error=str(exc), finished_at=utcnow(),
                    duration_s=round(time.perf_counter() - started, 1))
        except Exception as exc:  # pragma: no cover - safety net
            log.exception("plan job %s crashed", job_id)
            _update(job_id, status="failed", stage="Failed", error=f"Unexpected error: {exc}", finished_at=utcnow(),
                    duration_s=round(time.perf_counter() - started, 1))
