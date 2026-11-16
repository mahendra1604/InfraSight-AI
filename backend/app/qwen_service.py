"""Ollama client for document extraction (Qwen3 text + Qwen3-VL vision).

Design rules (see PROJECT_ANALYSIS_AND_LLM_PLAN.md, Phase 1):
* Instruct (non-thinking) models; temperature 0; structured output via a JSON schema.
* Explicit `num_ctx`, `num_predict` and `keep_alive` on every request.
* Images are orientation-fixed and downscaled before they are sent.
* "Ollama is down", "model missing", "too slow" and "bad output" are different errors.
* Output is validated with Pydantic. Bad output is retried once with the error message.
* Results are *untrusted suggestions*: they only become progress after human review.
"""
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
from datetime import date, datetime
from typing import Any, Callable, Optional

import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.config import settings
from app.quantities import CATEGORIES, parse_number

log = logging.getLogger("infrasight.llm")

# Tests can set this to an `httpx.MockTransport`.
TRANSPORT: httpx.AsyncBaseTransport | None = None


class QwenUnavailable(RuntimeError):
    """Ollama is not reachable, or the configured model is not installed."""


class QwenTimeout(RuntimeError):
    """Ollama is reachable but the model did not answer in time."""


class QwenBadOutput(ValueError):
    """The model answered, but the answer was empty, truncated or not valid JSON."""


# ---------------------------------------------------------------------------
# Output contract
# ---------------------------------------------------------------------------
_TEXT_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y",
                      "%d %b, %Y", "%d %B, %Y", "%b %d %Y", "%B %d %Y")
_DATE_PATTERN = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.\-]\d{1,2}[/.\-]\d{4}|\d{1,2}\s+[A-Za-z]{3,9},?\s+\d{4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}")


def _numeric_formats() -> tuple[str, ...]:
    """Day-first by default (India/UK). Set DATE_ORDER=MDY for US-style reports."""
    if os.getenv("DATE_ORDER", "DMY").upper() == "MDY":
        return ("%m/%d/%Y", "%m-%d-%Y", "%m.%d.%Y")
    return ("%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y")


def parse_date(value: Any) -> Optional[str]:
    """Return an ISO date string or None. The model copies dates as written; the code decides the order."""
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(value).strip())
    if not text or text.lower() in {"null", "none", "n/a", "unknown"}:
        return None
    candidates = [text.split("T")[0]] + [m.group(0) for m in _DATE_PATTERN.finditer(text)]
    for candidate in candidates:
        for fmt in _TEXT_DATE_FORMATS + _numeric_formats():
            try:
                return datetime.strptime(candidate.strip(), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    raise ValueError(f"unrecognised date {value!r}")


def parse_percent(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if not match:
            return None
        number = float(match.group())
    if not 0 <= number <= 100:
        raise ValueError(f"percentage {number} is outside 0-100")
    return round(number, 2)


class ExtractedEntry(BaseModel):
    activity_id: Optional[str] = None
    activity_text: str = ""
    progress: Optional[float] = Field(default=None, ge=0, le=100)
    planned_progress: Optional[float] = Field(default=None, ge=0, le=100)
    reporting_date: Optional[str] = None
    issue: Optional[str] = None
    evidence: str = ""
    needs_review: bool = True
    quantity_done: Optional[float] = Field(default=None, ge=0)
    quantity_today: Optional[float] = Field(default=None, ge=0)
    quantity_total: Optional[float] = Field(default=None, ge=0)
    quantity_unit: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("activity_id", "issue", "quantity_unit", mode="before")
    @classmethod
    def _blank_to_none(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return None if value.lower() in {"", "null", "none", "n/a", "-"} else value

    @field_validator("activity_text", "evidence", mode="before")
    @classmethod
    def _to_text(cls, value):
        return "" if value is None else str(value).strip()

    @field_validator("needs_review", mode="before")
    @classmethod
    def _to_bool(cls, value):
        if isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1"}
        return bool(value) if value is not None else True


class ExtractionResult(BaseModel):
    document_type: str = "unknown"
    report_date: Optional[str] = None
    answer: str = ""
    entries: list[ExtractedEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# Hand-written (no $ref) so every Ollama/llama.cpp grammar converter accepts it.
_NULLABLE_STRING = {"type": ["string", "null"]}
_NULLABLE_NUMBER = {"type": ["number", "null"]}
EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "document_type": {"type": "string"},
        "report_date": _NULLABLE_STRING,
        "answer": {"type": "string"},
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "activity_id": _NULLABLE_STRING,
                    "activity_text": {"type": "string"},
                    "progress": _NULLABLE_NUMBER,
                    "planned_progress": _NULLABLE_NUMBER,
                    "reporting_date": _NULLABLE_STRING,
                    "issue": _NULLABLE_STRING,
                    "quantity_done": _NULLABLE_NUMBER,
                    "quantity_today": _NULLABLE_NUMBER,
                    "quantity_total": _NULLABLE_NUMBER,
                    "quantity_unit": _NULLABLE_STRING,
                    "evidence": {"type": "string"},
                    "needs_review": {"type": "boolean"},
                },
                "required": ["activity_id", "activity_text", "progress", "planned_progress", "reporting_date", "issue",
                             "quantity_done", "quantity_today", "quantity_total", "quantity_unit", "evidence", "needs_review"],
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["document_type", "report_date", "answer", "entries", "warnings"],
}

SYSTEM_PROMPT = """You are the document-intelligence component of InfraSight AI, an infrastructure project progress monitoring system.
You read daily progress reports, site diaries, scanned forms and site photos, and you return ONLY one JSON object that matches the provided schema.

Rules:
- Create one entry per construction activity that has a progress value, a status or an issue.
- activity_id: copy an activity/task code only when that exact code is printed in the document next to the activity (for example "A102"). Most reports have no codes: then use null. Never number the entries yourself.
- activity_text: the activity name as written in the document.
- progress: CUMULATIVE ACTUAL percent complete (0-100) as a number. Use null when it is not stated. Never calculate or guess it, and never use the planned value here.
- planned_progress: the planned or target percent when stated, otherwise null.
- reporting_date: the date the progress applies to, copied exactly as written (for example "09/11/2026" or "8 November 2026"); do not reformat it. Use the report date when the entry has none. Use null when no date is visible.
- issue: the delay reason, problem or blocker for that activity, otherwise null.
- quantity_done: the CUMULATIVE quantity completed to date when the report states one (180 for "180 m laid so far" or "180 of 450 m"), otherwise null.
- quantity_today: the quantity done only today or in this period when that is what the report says (40 for "laid 40 m today"), otherwise null. Do not add it to anything.
  When the report only says what was done today, quantity_done must be null (never copy today's quantity into quantity_done).
- quantity_total: the total planned quantity when stated (450 for "180 of 450 m"), otherwise null.
- quantity_unit: the unit of those quantities as printed (m, m2, m3, km, nos ...), otherwise null.
- An activity reported only by quantity is still an entry: keep progress null in that case; it is calculated later.
- evidence: a short exact quote from the document that supports the values.
- needs_review: true when any value is unclear, partly legible, inferred or ambiguous.
- document_type: one of daily_progress_report, site_diary, inspection_report, site_photo, drawing, other.
- report_date: the document date copied exactly as written, otherwise null.
- answer: 1-3 sentences that answer the user's question (or summarise the document when there is no question), using only the document.
- warnings: anything a reviewer should know, such as illegible parts or contradictions.
Return JSON only. No markdown, no explanations outside the JSON."""

DEFAULT_QUESTION = "Extract every activity with its cumulative progress, date and issues. Identify delayed activities."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _client(timeout: httpx.Timeout | float) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=settings.ollama_url, timeout=timeout, trust_env=False, transport=TRANSPORT)


def prepare_image(data: bytes, max_side: int | None = None) -> tuple[bytes, dict[str, Any]]:
    """Fix EXIF orientation, flatten transparency, downscale and re-encode as JPEG."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    max_side = max_side or settings.image_max_side
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise QwenBadOutput(f"The image could not be opened: {exc}") from exc
    original = image.size
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.split()[-1])
        image = background
    elif image.mode != "RGB":
        image = image.convert("RGB")
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=90)
    w, h = image.size
    # Qwen3-VL: one visual token per 32x32 px, with a 1 MP minimum in Ollama's build.
    est_tokens = max(w * h, 1_048_576) // 1024
    return buffer.getvalue(), {"original_size": list(original), "sent_size": [w, h], "estimated_image_tokens": est_tokens}


def _load_json(content: str) -> dict[str, Any]:
    """Tolerant JSON loading: strips code fences and surrounding prose."""
    text = (content or "").strip()
    if not text:
        raise QwenBadOutput("The model returned an empty answer")
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text).strip()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise QwenBadOutput(f"The model did not return JSON: {text[:200]}")
        try:
            raw = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise QwenBadOutput(f"The model returned malformed JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise QwenBadOutput("The model returned JSON that is not an object")
    return raw


def parse_extraction(content: str) -> tuple[ExtractionResult, list[str]]:
    """Parse model text into a validated ExtractionResult.

    Values that are present but invalid (e.g. 150 %, unreadable dates) are set to
    null and flagged for review instead of failing the whole document."""
    raw = _load_json(content)
    notes: list[str] = []
    entries = []
    raw_entries = raw.get("entries") if isinstance(raw.get("entries"), list) else []
    if not isinstance(raw.get("entries"), list):
        notes.append("The model returned no structured entries.")
    try:
        report_date = parse_date(raw.get("report_date"))
    except ValueError as exc:
        report_date = None
        notes.append(f"Report date ignored: {exc}")
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            continue
        item = dict(item)
        entry_notes = []
        for key in ("progress", "planned_progress"):
            try:
                item[key] = parse_percent(item.get(key))
            except ValueError as exc:
                item[key] = None
                item["needs_review"] = True
                entry_notes.append(f"{key.replace('_', ' ')} ignored: {exc}")
        for key in ("quantity_done", "quantity_today", "quantity_total"):
            try:
                item[key] = parse_number(item.get(key))
            except ValueError as exc:
                item[key] = None
                item["needs_review"] = True
                entry_notes.append(f"{key.replace('_', ' ')} ignored: {exc}")
        try:
            item["reporting_date"] = parse_date(item.get("reporting_date")) or report_date
        except ValueError as exc:
            item["reporting_date"] = report_date
            item["needs_review"] = True
            entry_notes.append(f"Date ignored: {exc}")
        try:
            entry = ExtractedEntry.model_validate(item)
        except ValidationError as exc:
            notes.append(f"Entry {index + 1} skipped: {exc.errors()[0]['msg']}")
            continue
        if not entry.activity_text and not entry.activity_id:
            notes.append(f"Entry {index + 1} skipped: no activity name or ID")
            continue
        entry.warnings.extend(entry_notes)
        entries.append(entry)
    warnings = [str(w) for w in raw.get("warnings", []) if w] if isinstance(raw.get("warnings"), list) else []
    result = ExtractionResult(document_type=str(raw.get("document_type") or "unknown"), report_date=report_date,
                              answer=str(raw.get("answer") or ""), entries=entries, warnings=warnings + notes)
    return result, notes


_capabilities_cache: dict[str, list[str]] = {}


async def _capabilities(model: str) -> list[str]:
    if model in _capabilities_cache:
        return _capabilities_cache[model]
    try:
        async with _client(10) as client:
            response = await client.post("/api/show", json={"model": model})
    except (httpx.HTTPError, ValueError):
        return []  # not cached: Ollama may simply be down right now
    if response.status_code != 200:
        return []
    caps = response.json().get("capabilities", [])
    _capabilities_cache[model] = caps
    return caps


async def _chat(model: str, messages: list[dict[str, Any]], schema: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
    caps = await _capabilities(model)
    request: dict[str, Any] = {
        "model": model,
        "stream": False,
        "format": schema or EXTRACTION_SCHEMA,
        "messages": messages,
        "keep_alive": settings.keep_alive,
        "options": {"temperature": 0, "num_ctx": settings.num_ctx, "num_predict": settings.num_predict},
    }
    if "thinking" in caps:
        request["think"] = False
    timeout = httpx.Timeout(connect=5.0, read=settings.llm_timeout, write=60.0, pool=5.0)
    started = time.perf_counter()
    try:
        async with _client(timeout) as client:
            response = await client.post("/api/chat", json=request)
    except httpx.ConnectError as exc:
        raise QwenUnavailable(f"Ollama is not running at {settings.ollama_url}. Start the Ollama app, then retry.") from exc
    except httpx.TimeoutException as exc:
        raise QwenTimeout(
            f"{model} did not finish within {settings.llm_timeout:.0f} s. The model is running but too slow on this "
            "computer: close other applications, use a smaller image, or raise OLLAMA_TIMEOUT.") from exc
    except httpx.HTTPError as exc:
        raise QwenUnavailable(f"Could not talk to Ollama at {settings.ollama_url}: {exc}") from exc
    if response.status_code == 404:
        raise QwenUnavailable(f"Model '{model}' is not installed. Run: ollama pull {model}")
    if response.status_code >= 400:
        raise QwenUnavailable(f"Ollama returned HTTP {response.status_code}: {response.text[:300]}")
    data = response.json()
    message = data.get("message") or {}
    meta = {
        "model": model,
        "duration_s": round(time.perf_counter() - started, 1),
        "load_s": round((data.get("load_duration") or 0) / 1e9, 1),
        "prompt_tokens": data.get("prompt_eval_count"),
        "output_tokens": data.get("eval_count"),
        "done_reason": data.get("done_reason"),
    }
    content = message.get("content") or ""
    if data.get("done_reason") == "length":
        raise QwenBadOutput(
            f"The model output was cut off after {data.get('eval_count')} tokens. "
            "Split the document or raise OLLAMA_NUM_PREDICT.")
    if not content.strip() and message.get("thinking"):
        raise QwenBadOutput(f"{model} produced only hidden reasoning. Configure an '-instruct' model instead of a thinking model.")
    log.info("ollama %s: %ss, %s prompt tokens, %s output tokens", model, meta["duration_s"], meta["prompt_tokens"], meta["output_tokens"])
    return content, meta


def _report_parser(content: str) -> dict[str, Any]:
    return parse_extraction(content)[0].model_dump()


async def _extract(model: str, user_message: dict[str, Any], system: str = SYSTEM_PROMPT,
                   schema: dict[str, Any] | None = None, parser: Callable[[str], dict[str, Any]] = _report_parser) -> dict[str, Any]:
    messages = [{"role": "system", "content": system}, user_message]
    total = 0.0
    last_error: QwenBadOutput | None = None
    for attempt in (1, 2):
        content, meta = await _chat(model, messages, schema)
        total += meta["duration_s"]
        try:
            output = parser(content)
        except QwenBadOutput as exc:
            last_error = exc
            log.warning("attempt %s returned bad output: %s", attempt, exc)
            messages = messages + [
                {"role": "assistant", "content": content[:2000]},
                {"role": "user", "content": f"That reply was invalid ({exc}). Reply again with only the JSON object."},
            ]
            continue
        output["meta"] = {**meta, "duration_s": round(total, 1), "attempts": attempt}
        return output
    assert last_error is not None
    raise last_error


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
async def extract_from_text(text: str, instruction: str = "") -> dict[str, Any]:
    question = instruction.strip() or DEFAULT_QUESTION
    content = f"Question: {question}\n\nDocument text (page markers such as [Page 2] may appear):\n<<<\n{text.strip()}\n>>>"
    return await _extract(settings.text_model, {"role": "user", "content": content})


async def extract_from_image(image: bytes, instruction: str = "") -> dict[str, Any]:
    prepared, info = prepare_image(image)
    question = instruction.strip() or DEFAULT_QUESTION
    content = f"Question: {question}\n\nThe document is the attached image. Read every visible label, number and date carefully."
    result = await _extract(settings.vision_model, {
        "role": "user", "content": content, "images": [base64.b64encode(prepared).decode("ascii")]})
    result["meta"]["image"] = info
    return result


async def analyse_image(image: bytes, mime_type: str = "", user_instruction: str = "") -> dict[str, Any]:
    """Backward-compatible name used by `/api/ai/analyse-image`."""
    return await extract_from_image(image, user_instruction)


# ---------------------------------------------------------------------------
# Site plans / drawings: read printed dimensions (quantity take-off)
# ---------------------------------------------------------------------------
DIMENSION_TYPES = ["length", "width", "area", "volume", "count", "height", "level", "length_x_width", "other"]
PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "drawing_title": _NULLABLE_STRING,
        "scale": _NULLABLE_STRING,
        "drawing_date": _NULLABLE_STRING,
        "answer": {"type": "string"},
        "elements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "dimension_type": {"type": "string", "enum": DIMENSION_TYPES},
                    "value": _NULLABLE_NUMBER,
                    "value2": _NULLABLE_NUMBER,
                    "unit": _NULLABLE_STRING,
                    "label_text": {"type": "string"},
                    "needs_review": {"type": "boolean"},
                },
                "required": ["name", "category", "dimension_type", "value", "value2", "unit", "label_text", "needs_review"],
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["drawing_title", "scale", "drawing_date", "answer", "elements", "notes"],
}

PLAN_PROMPT = """You are the drawing-reading component of InfraSight AI. You read construction site plans, layout drawings and
sketches and return ONLY one JSON object that matches the provided schema.

Rules:
- Create one element for every dimension, length, area, size or count that is PRINTED on the drawing.
- Never measure, estimate, scale or calculate anything yourself. If a value is not printed, leave it out.
- name: the element the label belongs to, as named on the drawing (for example "Access road", "Block A", "Drain D1").
  A dimension written on or along an element (inside a road, beside a drain line, inside a building outline) belongs to
  that element, even when the element's name is printed elsewhere. Point markers such as GATE, N (north arrow), notes and
  the title block are not elements and never own a length.
- category: building, road, drainage, utility, boundary (plot or site boundary), parking, landscape, earthworks or other.
- dimension_type: length, width, area, volume, count, height, level, length_x_width (for labels such as "30 m x 20 m") or other.
- value: the printed number. value2: the second number, only for length_x_width labels; otherwise null.
- unit: the unit as printed (m, m2, sqm, km, nos ...). If the drawing says "all dimensions in metres" and a label has no unit,
  use "m". Otherwise null.
- label_text: the exact printed text of the label.
- needs_review: true when the text is hard to read or it is unclear which element the label belongs to.
- drawing_title, scale, drawing_date: copied exactly as written, otherwise null.
- notes: general notes printed on the drawing.
- answer: 1-3 sentences that answer the user's question, or describe the drawing when there is no question.
Return JSON only."""

DEFAULT_PLAN_QUESTION = "List every element with its printed dimensions and describe the site layout."


class PlanElement(BaseModel):
    name: str = ""
    category: str = "other"
    dimension_type: str = "other"
    value: Optional[float] = None
    value2: Optional[float] = None
    unit: Optional[str] = None
    label_text: str = ""
    needs_review: bool = True

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, value):
        value = str(value or "other").strip().lower()
        return value if value in CATEGORIES else "other"

    @field_validator("dimension_type", mode="before")
    @classmethod
    def _dimension(cls, value):
        value = str(value or "other").strip().lower().replace(" ", "_")
        return value if value in DIMENSION_TYPES else "other"

    @field_validator("value", "value2", mode="before")
    @classmethod
    def _number(cls, value):
        try:
            return parse_number(value)
        except ValueError:
            return None

    @field_validator("unit", mode="before")
    @classmethod
    def _unit(cls, value):
        if value is None:
            return None
        value = str(value).strip()
        return None if value.lower() in {"", "null", "none", "n/a"} else value

    @field_validator("name", "label_text", mode="before")
    @classmethod
    def _text(cls, value):
        return "" if value is None else str(value).strip()


def parse_plan(content: str) -> dict[str, Any]:
    raw = _load_json(content)
    elements, notes = [], []
    for index, item in enumerate(raw.get("elements") or []):
        if not isinstance(item, dict):
            continue
        try:
            element = PlanElement.model_validate(item)
        except ValidationError as exc:
            notes.append(f"Label {index + 1} skipped: {exc.errors()[0]['msg']}")
            continue
        if element.value is None and not element.label_text:
            continue
        elements.append(element.model_dump())
    printed_notes = [str(n) for n in raw.get("notes", []) if n] if isinstance(raw.get("notes"), list) else []
    return {"drawing_title": raw.get("drawing_title"), "scale": raw.get("scale"), "drawing_date": raw.get("drawing_date"),
            "answer": str(raw.get("answer") or ""), "elements": elements, "notes": printed_notes, "warnings": notes}


def _ocr_block(ocr_text: str) -> str:
    text = (ocr_text or "").strip()
    return f"\n\nText labels found on the drawing by OCR, each with its position (x from the left, y from the top). " \
           f"OCR may contain recognition errors; use it to read small labels exactly, and use the image and the " \
           f"positions to decide which element each label belongs to:\n<<<\n{text[:6000]}\n>>>" if text else ""


async def extract_plan(image: bytes, ocr_text: str = "", instruction: str = "") -> dict[str, Any]:
    """Read a site plan with the vision model; OCR text (full resolution) is passed as a reading aid."""
    prepared, info = prepare_image(image)
    content = f"Question: {instruction.strip() or DEFAULT_PLAN_QUESTION}\n\nThe drawing is the attached image.{_ocr_block(ocr_text)}"
    result = await _extract(settings.vision_model, {"role": "user", "content": content,
                                                    "images": [base64.b64encode(prepared).decode("ascii")]},
                            system=PLAN_PROMPT, schema=PLAN_SCHEMA, parser=parse_plan)
    result["meta"]["image"] = info
    return result


async def extract_plan_from_text(ocr_text: str, instruction: str = "") -> dict[str, Any]:
    """Faster, text-only reading of a drawing's OCR text (layout is lost, so attributions are less reliable)."""
    content = (f"Question: {instruction.strip() or DEFAULT_PLAN_QUESTION}\n\nThe drawing is not attached. Only its OCR text "
               f"labels are available, each with its position (x from the left, y from the top). Labels close to each other "
               f"usually belong together; mark uncertain attributions with needs_review:\n<<<\n{ocr_text.strip()[:8000]}\n>>>")
    return await _extract(settings.text_model, {"role": "user", "content": content},
                          system=PLAN_PROMPT, schema=PLAN_SCHEMA, parser=parse_plan)


def merge_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine chunk/page results into one extraction."""
    if not results:
        return {"document_type": "unknown", "report_date": None, "answer": "", "entries": [], "warnings": [], "meta": {}}
    merged = {
        "document_type": results[0].get("document_type", "unknown"),
        "report_date": next((r.get("report_date") for r in results if r.get("report_date")), None),
        "answer": " ".join(r.get("answer", "") for r in results if r.get("answer")).strip(),
        "entries": [],
        "warnings": [],
        "meta": {
            "model": results[0].get("meta", {}).get("model"),
            "duration_s": round(sum(r.get("meta", {}).get("duration_s", 0) for r in results), 1),
            "calls": len(results),
        },
    }
    for result in results:
        for entry in result.get("entries", []):
            entry = dict(entry)  # "page" is set by the caller, which knows the real page numbers
            if not entry.get("reporting_date"):
                entry["reporting_date"] = merged["report_date"]
            merged["entries"].append(entry)
        merged["warnings"].extend(result.get("warnings", []))
    merged["warnings"] = list(dict.fromkeys(merged["warnings"]))
    return merged


async def status(timeout: float = 5.0) -> dict[str, Any]:
    vision, text_model = settings.vision_model, settings.text_model
    base = {"ollama_url": settings.ollama_url, "vision_model": vision, "text_model": text_model, "model": vision}
    try:
        async with _client(timeout) as client:
            tags = await client.get("/api/tags")
            tags.raise_for_status()
            names = [m.get("name") for m in tags.json().get("models", [])]
            try:
                ps = await client.get("/api/ps")
                loaded = [m.get("name") for m in ps.json().get("models", [])]
            except (httpx.HTTPError, ValueError):
                loaded = []
    except (httpx.HTTPError, OSError, ValueError):
        return {**base, "available": False, "model_installed": False, "vision_installed": False, "text_installed": False,
                "models": [], "loaded": [], "warnings": ["Ollama is not running. Start the Ollama app."]}

    def installed(name: str) -> bool:
        return name in names or (":" not in name and f"{name}:latest" in names)

    warnings = []
    for name in (vision, text_model):
        if not installed(name):
            warnings.append(f"Model {name} is not installed. Run: ollama pull {name}")
        elif "thinking" in await _capabilities(name) and "instruct" not in name:
            warnings.append(f"{name} is a thinking model; it is slow and may return empty JSON. Use an -instruct model.")
    return {**base, "available": True, "model_installed": installed(vision), "vision_installed": installed(vision),
            "text_installed": installed(text_model), "models": names, "loaded": loaded, "warnings": warnings}
