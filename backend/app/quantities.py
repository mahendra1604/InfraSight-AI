"""Quantities: units, site-plan take-off, draft schedules and quantity-based progress.

The model only *reads* numbers printed on a drawing or report. All arithmetic
(L x W areas, segment totals, unit conversion, % complete) happens here, in code.
"""
from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------
UNIT_ALIASES: dict[str, set[str]] = {
    "m": {"m", "meter", "meters", "metre", "metres", "mtr", "mtrs", "rm", "rmt", "lm", "rmt.", "running metre", "running meter", "lin m"},
    "km": {"km", "kms", "kilometer", "kilometre", "kilometers", "kilometres"},
    "cm": {"cm", "centimetre", "centimeter"},
    "mm": {"mm", "millimetre", "millimeter"},
    "ft": {"ft", "feet", "foot", "'"},
    "m2": {"m2", "m²", "sqm", "sq m", "sq.m", "sq.m.", "sq. m", "sqmt", "square metre", "square meter", "square metres", "square meters"},
    "ha": {"ha", "hectare", "hectares"},
    "sqft": {"sqft", "sq ft", "sq.ft", "sq. ft", "ft2", "ft²", "square feet", "square foot"},
    "m3": {"m3", "m³", "cum", "cu m", "cu.m", "cu. m", "cubic metre", "cubic meter", "cubic metres", "cubic meters"},
    "nos": {"nos", "no", "no.", "nos.", "number", "numbers", "unit", "units", "pcs", "pc", "piece", "pieces", "each", "ea", "count"},
    "t": {"t", "ton", "tons", "tonne", "tonnes"},
    "kg": {"kg", "kgs", "kilogram", "kilograms"},
}
_LOOKUP = {alias: canonical for canonical, aliases in UNIT_ALIASES.items() for alias in aliases}
# canonical unit -> (base unit, factor)
TO_BASE = {"m": ("m", 1.0), "km": ("m", 1000.0), "cm": ("m", 0.01), "mm": ("m", 0.001), "ft": ("m", 0.3048),
           "m2": ("m2", 1.0), "ha": ("m2", 10000.0), "sqft": ("m2", 0.09290304), "m3": ("m3", 1.0),
           "nos": ("nos", 1.0), "t": ("t", 1.0), "kg": ("t", 0.001)}
LENGTH_TO_AREA = {"m": "m2", "ft": "sqft", "km": "m2", "cm": "m2", "mm": "m2"}


def normalize_unit(unit: Any) -> Optional[str]:
    if unit is None:
        return None
    text = str(unit).strip().lower().rstrip(".")
    if not text or text in {"null", "none", "n/a", "-"}:
        return None
    return _LOOKUP.get(text) or _LOOKUP.get(text + ".") or _LOOKUP.get(text.replace(" ", "")) or text


def convert(value: float, from_unit: Any, to_unit: Any) -> Optional[float]:
    """Convert between compatible units; None when they are not compatible."""
    src, dst = normalize_unit(from_unit), normalize_unit(to_unit)
    if src == dst:
        return float(value)
    if src not in TO_BASE or dst not in TO_BASE:
        return None
    (src_base, src_factor), (dst_base, dst_factor) = TO_BASE[src], TO_BASE[dst]
    if src_base != dst_base:
        return None
    return float(value) * src_factor / dst_factor


def parse_number(value: Any) -> Optional[float]:
    """'180 m' -> 180.0, '1,250' -> 1250.0, None/'' -> None. Negative numbers are rejected."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
        if not match:
            return None
        number = float(match.group().replace(",", ""))
    if number < 0:
        raise ValueError(f"quantity {number} is negative")
    return number


def fmt(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


# ---------------------------------------------------------------------------
# Quantity-based progress
# ---------------------------------------------------------------------------
@dataclass
class QuantityProgress:
    progress: Optional[float] = None
    quantity_done: Optional[float] = None       # cumulative, in the activity's unit
    unit: Optional[str] = None
    notes: list[str] = field(default_factory=list)
    needs_review: bool = False
    reason: Optional[str] = None                 # why no progress could be computed


def progress_from_quantity(total_quantity: Optional[float], total_unit: Optional[str], approved_progress: float,
                           done: Optional[float] = None, today: Optional[float] = None, unit: Optional[str] = None,
                           report_total: Optional[float] = None) -> QuantityProgress:
    """Turn a reported quantity into cumulative % complete.

    done:  cumulative quantity to date ("180 m laid so far", "180 of 450 m")
    today: quantity in this period only ("40 m laid today") -> added to the approved quantity
    """
    result = QuantityProgress()
    reported_unit = normalize_unit(unit)
    total, base_unit = total_quantity, normalize_unit(total_unit)
    if not total or not base_unit:
        if report_total and reported_unit:
            total, base_unit = report_total, reported_unit
            result.notes.append(f"Total {fmt(report_total)} {reported_unit} taken from the report (the schedule has no quantity for this activity).")
            result.needs_review = True
        else:
            result.reason = "Quantity reported, but the activity has no total quantity. Set it on the Site plans or Schedule screen."
            return result
    if reported_unit is None:
        reported_unit = base_unit
        result.notes.append(f"No unit in the report; assumed {base_unit}.")
        result.needs_review = True

    def to_base(value: float) -> Optional[float]:
        return convert(value, reported_unit, base_unit)

    if done is not None and today is not None:
        # Models often copy "laid 45 m today" into both fields. A "cumulative" value equal to today's,
        # or below what is already approved, is really this period's work.
        done_base, today_base = to_base(done), to_base(today)
        if done_base is not None and today_base is not None and (
                abs(done_base - today_base) < 1e-9 or done_base < approved_progress / 100 * total):
            result.notes.append("The reported quantity describes this period's work, so it was added to the approved quantity.")
            done = None
    if done is not None:
        cumulative = to_base(done)
        if cumulative is None:
            result.reason = f"Reported unit '{reported_unit}' cannot be converted to the activity unit '{base_unit}'."
            return result
    elif today is not None:
        increment = to_base(today)
        if increment is None:
            result.reason = f"Reported unit '{reported_unit}' cannot be converted to the activity unit '{base_unit}'."
            return result
        previous = approved_progress / 100 * total
        cumulative = previous + increment
        result.notes.append(f"Cumulative quantity = previously approved {fmt(previous)} {base_unit} + this period's "
                            f"{fmt(increment)} {base_unit}. Check that no other pending report already includes this work.")
        result.needs_review = True
    else:
        result.reason = "No quantity value."
        return result
    pct = cumulative / total * 100
    if pct > 100.5:
        result.notes.append(f"Reported quantity {fmt(cumulative)} {base_unit} exceeds the planned total {fmt(total)} {base_unit}; "
                            "progress capped at 100 %. Check for a quantity overrun.")
        result.needs_review = True
        pct = 100.0
    result.progress = round(pct, 1)
    result.quantity_done = round(cumulative, 3)
    result.unit = base_unit
    result.notes.insert(0, f"Progress calculated from quantity: {fmt(cumulative)} of {fmt(total)} {base_unit} = {result.progress}%.")
    return result


# ---------------------------------------------------------------------------
# Site-plan take-off
# ---------------------------------------------------------------------------
SUMMABLE = {"length", "area", "volume", "count"}
# Labels that mark a point on a drawing; they never own a length or an area.
POINT_MARKERS = {"gate", "entrance", "exit", "north", "n", "notes", "note", "legend", "key", "title", "scale", "date",
                 "drawing", "dwg", "manhole", "mh", "chainage", "ch", "gate house"}
CATEGORIES = ["building", "road", "drainage", "utility", "boundary", "parking", "landscape", "earthworks", "other"]
_SEGMENT_WORDS = {"segment", "section", "part", "seg", "portion", "stretch", "line", "run", "leg"}


def base_name(name: str) -> str:
    """'Drain D1' -> 'Drain', 'Access road segment 2' -> 'Access road', 'Block A' -> 'Block A'."""
    tokens = [t for t in re.split(r"\s+", (name or "").strip()) if t]
    kept = [t for t in tokens if not re.search(r"\d", t) and t.lower().strip("().,-#") not in _SEGMENT_WORDS]
    cleaned = " ".join(kept).strip(" -–:,()") or (name or "").strip()
    if cleaned.isupper() and len(cleaned) > 3:          # drawings are often all caps: "DRAIN" -> "Drain"
        cleaned = " ".join(w if len(w) == 1 else w.capitalize() for w in cleaned.lower().split())
        cleaned = re.sub(r"\b([a-z])\b", lambda m: m.group(1).upper(), cleaned)   # "Block a" -> "Block A"
    return cleaned[:1].upper() + cleaned[1:]


def _printed_labels(source_text: str) -> list[str]:
    """Labels from positioned OCR ('"180 m" @ x=38% y=51%') or plain text-layer lines."""
    quoted = re.findall(r'"([^"]*)"\s*@', source_text or "")
    return quoted if quoted else [line for line in (source_text or "").splitlines() if line.strip()]


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def resolve_double_counting(elements: list[dict[str, Any]], source_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """A printed dimension must not be counted for two elements.

    If a value (e.g. '180 m') is printed fewer times than the number of elements that claim it, keep the
    claims whose label text really appears on the drawing and drop the others; if that cannot decide,
    flag the claims for review."""
    labels = _printed_labels(source_text)
    if not labels:
        return elements, []
    printed = [_squash(label) for label in labels]
    claims: "OrderedDict[tuple, list[dict[str, Any]]]" = OrderedDict()
    for element in elements:
        if element.get("value") is None or element.get("dimension_type") in (None, "other", "length_x_width"):
            continue
        claims.setdefault((float(element["value"]), normalize_unit(element.get("unit"))), []).append(element)
    dropped, notes = set(), []
    for (value, unit), claimed in claims.items():
        names = {base_name(e.get("name", "")).lower() for e in claimed}
        if len(claimed) < 2 or len(names) < 2:
            continue
        number = fmt(value)
        pattern = re.compile(rf"(?<![\d.]){re.escape(number)}(?:\.0+)?(?![\d.])")
        times_printed = sum(len(pattern.findall(label)) for label in labels)
        if times_printed >= len(claimed):
            continue
        verified = [e for e in claimed if _squash(e.get("label_text")) in printed]
        if 0 < len(verified) < len(claimed):
            for e in claimed:
                if e not in verified:
                    dropped.add(id(e))
                    owner = ", ".join(sorted({base_name(v['name']) for v in verified}))
                    notes.append(f"Ignored '{e.get('label_text') or number}' for {base_name(e.get('name', ''))}: {number} {unit or ''} is "
                                 f"printed only {times_printed}× on the drawing and its own label belongs to {owner}.")
        else:
            for e in claimed:
                e["needs_review"] = True
            notes.append(f"{number} {unit or ''} is printed {times_printed}× but was assigned to {len(claimed)} elements "
                         f"({', '.join(sorted(base_name(e.get('name', '')) for e in claimed))}); check which one it belongs to.")
    return [e for e in elements if id(e) not in dropped], notes


def _measure(element: dict[str, Any]) -> tuple[Optional[str], Optional[float], Optional[str]]:
    """Return (measure, value, unit) after code-side arithmetic."""
    kind = (element.get("dimension_type") or "other").lower()
    unit = normalize_unit(element.get("unit"))
    value, value2 = element.get("value"), element.get("value2")
    if value is None:
        return None, None, unit
    if kind == "length_x_width":
        if value2 is None:
            return "length", value, unit
        if unit is None:
            return "area", value * value2, None          # flagged for review by build_takeoff
        if unit in ("km", "cm", "mm"):
            factor = TO_BASE[unit][1]
            return "area", value * factor * value2 * factor, "m2"
        return "area", value * value2, LENGTH_TO_AREA.get(unit, f"{unit}2")
    return kind, value, unit


def build_takeoff(elements: list[dict[str, Any]]) -> dict[str, Any]:
    """Group printed dimensions into quantity totals. Segments of the same element are summed."""
    groups: "OrderedDict[tuple, dict[str, Any]]" = OrderedDict()
    other_dimensions = []
    notes: list[str] = []
    for element in elements:
        measure, value, unit = _measure(element)
        if measure is None:
            continue
        category = element.get("category") if element.get("category") in CATEGORIES else "other"
        name = base_name(element.get("name") or category)
        if measure not in SUMMABLE:
            other_dimensions.append({"name": name, "category": category, "dimension": measure, "value": value, "unit": unit,
                                     "label_text": element.get("label_text", "")})
            continue
        base_unit = TO_BASE.get(unit, (unit, 1.0))[0] if unit else None
        converted = convert(value, unit, base_unit) if unit else value
        key = (category, name.lower(), measure, base_unit)
        point_marker = name.lower() in POINT_MARKERS
        if point_marker and key not in groups:
            notes.append(f"'{element.get('label_text') or value}' was assigned to '{name}', which marks a point on the drawing and has "
                         f"no {measure}. Check which element it belongs to and correct the quantity before applying it.")
        group = groups.setdefault(key, {"name": name, "category": category, "measure": measure, "total": 0.0, "unit": base_unit,
                                        "parts": 0, "labels": [], "needs_review": point_marker, "point_marker": point_marker})
        group["total"] += converted if converted is not None else value
        group["parts"] += 1
        group["labels"].append(element.get("label_text") or f"{value} {unit or ''}".strip())
        group["needs_review"] = group["needs_review"] or bool(element.get("needs_review")) or unit is None
    totals = []
    for group in groups.values():
        group["total"] = round(group["total"], 3)
        totals.append(group)
    by_category: dict[tuple[str, str, str], float] = {}
    for group in totals:
        if group["unit"]:
            k = (group["category"], group["measure"], group["unit"])
            by_category[k] = round(by_category.get(k, 0.0) + group["total"], 3)
    return {
        "totals": totals,
        "category_totals": [{"category": c, "measure": m, "unit": u, "total": t} for (c, m, u), t in by_category.items()],
        "other_dimensions": other_dimensions,
        "notes": notes,
        "method": "The model only read printed labels; L×W areas, segment sums and unit conversions were calculated in code.",
    }


# ---------------------------------------------------------------------------
# Draft schedule from a take-off
# ---------------------------------------------------------------------------
def draft_schedule(takeoff: dict[str, Any], prefix: str = "P") -> list[dict[str, Any]]:
    """A transparent rule-based first draft. Durations and dates are left blank for the team to fill in."""
    rows: list[dict[str, Any]] = []

    def add(name: str, category: str, quantity: Optional[float], unit: Optional[str], predecessors: list[str]) -> str:
        activity_id = f"{prefix}{len(rows) + 1:02d}"
        rows.append({"activity_id": activity_id, "name": name, "planned_progress": 0, "actual_progress": 0,
                     "predecessor": ";".join(predecessors), "planned_start": "", "planned_finish": "", "duration_days": "",
                     "wbs": category, "total_quantity": quantity if quantity is not None else "", "unit": unit or ""})
        return activity_id

    totals = [t for t in takeoff.get("totals", []) if t["measure"] in SUMMABLE and not t.get("point_marker")]
    site = next((t for t in totals if t["category"] in ("boundary",) and t["measure"] == "area"), None)
    start = [add("Site preparation and clearing", "boundary", site["total"] if site else None, site["unit"] if site else "m2", [])]
    earth = [add(f"{t['name']} earthworks", "earthworks", t["total"], t["unit"], start) for t in totals if t["category"] == "earthworks"]
    after_site = earth or start
    networks = [add(f"{t['name']} {'laying' if t['measure'] == 'length' else 'installation'}", t["category"], t["total"], t["unit"], after_site)
                for t in totals if t["category"] in ("drainage", "utility")]
    superstructures = []
    for t in totals:
        if t["category"] == "building" and t["measure"] in ("area", "count", "volume"):
            foundation = add(f"{t['name']} foundations", "building", t["total"], t["unit"], after_site)
            superstructures.append(add(f"{t['name']} superstructure", "building", t["total"], t["unit"], [foundation]))
    pavements = [add(f"{t['name']} construction", t["category"], t["total"], t["unit"], networks or after_site)
                 for t in totals if t["category"] in ("road", "parking")]
    finishing_preds = pavements + superstructures or after_site
    for t in totals:
        if t["category"] in ("landscape", "other"):
            add(f"{t['name']} works", t["category"], t["total"], t["unit"], finishing_preds)
    return rows
