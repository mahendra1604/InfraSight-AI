"""Data for the 3D site model ("digital twin lite").

Sources, in order of preference:
1. the latest finished site-plan analysis: element sizes from the take-off, positions from where
   their labels sit on the drawing (OCR), building floors from "G+n" labels;
2. otherwise a schematic layout derived from activity names ("Block A ...", "... road", "drain ...").

Every element is linked to schedule activities, so its colour and fill show approved progress
and predicted delay risk. Roads and drains are drawn schematically along the plot edges because
a take-off has lengths but not routes.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable, Optional

from app.quantities import SUMMABLE, base_name

LABEL_RE = re.compile(r'"([^"]*)"\s*@\s*x=(\d+)%\s*y=(\d+)%')
FLOORS_RE = re.compile(r"\bG\s*\+\s*(\d{1,2})\b", re.IGNORECASE)
DEFAULT_FLOORS = 3
FLOOR_HEIGHT = 3.5
KIND = {"building": "building", "parking": "parking", "road": "road", "drainage": "drain", "utility": "utility",
        "landscape": "landscape", "earthworks": "earthworks", "other": "area"}
GENERIC_WORDS = {"construction", "works", "work", "laying", "installation", "area", "zone"}
SITE_WORDS = ("site preparation", "clearing", "site clearance", "mobilisation", "mobilization")


def _squash(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def parse_labels(ocr_text: str) -> list[tuple[str, float, float]]:
    return [(t, int(x) / 100, int(y) / 100) for t, x, y in LABEL_RE.findall(ocr_text or "")]


def locate_elements(elements: list[dict[str, Any]], ocr_text: str) -> None:
    """Attach a sheet position (0-1) to each element from its OCR label, and floors to buildings."""
    labels = parse_labels(ocr_text)
    if not labels:
        return
    for element in elements:
        name, label_text = _squash(element.get("name")), _squash(element.get("label_text"))
        by_name = [(x, y) for t, x, y in labels if name and (_squash(t) == name or _squash(t).startswith(name))]
        by_label = [(x, y) for t, x, y in labels if label_text and _squash(t) == label_text]
        area_like = element.get("category") in ("building", "parking", "boundary", "landscape")
        ordered = (by_name + by_label) if area_like else (by_label + by_name)
        if ordered:
            element["position"] = list(ordered[0])
        if element.get("category") == "building" and element.get("position"):
            ex, ey = element["position"]
            near = [(math.dist((ex, ey), (x, y)), int(m.group(1))) for t, x, y in labels if (m := FLOORS_RE.search(t))]
            near = [n for n in near if n[0] <= 0.15]
            if near:
                element["floors"] = min(near)[1] + 1


# ---------------------------------------------------------------------------
# Activity linking
# ---------------------------------------------------------------------------
def _tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words]


def _contains_all(activity_name: str, element_name: str) -> bool:
    wanted = [t for t in _tokens(element_name) if t not in GENERIC_WORDS]
    have = _tokens(activity_name)
    if not wanted:
        return False

    def hit(token: str) -> bool:
        return any(token == h or (min(len(token), len(h)) >= 4 and (h.startswith(token) or token.startswith(h))) for h in have)

    return all(hit(t) for t in wanted)


def link_activities(elements: list[dict[str, Any]], activities: list[Any]) -> list[Any]:
    """Assign activities to elements; returns the activities left unlinked.

    1. plot <- site preparation / clearing
    2. element name contained in the activity name ("Block A" -> "Block A excavation", "Drain" -> "Drainage lines")
    3. buildings also take unassigned activities in the same top-level WBS group as their linked ones
    """
    taken: set[str] = set()
    for element in elements:
        element["_acts"] = []
    plot = next((e for e in elements if e["kind"] == "plot"), None)
    if plot:
        for a in activities:
            if any(w in a.name.lower() for w in SITE_WORDS):
                plot["_acts"].append(a)
                taken.add(a.id)
    for element in elements:
        if element["kind"] == "plot":
            continue
        for a in activities:
            if a.id not in taken and _contains_all(a.name, element["name"]):
                element["_acts"].append(a)
                taken.add(a.id)
    for element in elements:
        if element["kind"] != "building" or not element["_acts"]:
            continue
        groups = {a.wbs.split(".")[0] for a in element["_acts"] if a.wbs}
        for a in activities:
            if a.id not in taken and a.wbs and a.wbs.split(".")[0] in groups:
                element["_acts"].append(a)
                taken.add(a.id)
    return [a for a in activities if a.id not in taken]


def _summarise(element: dict[str, Any], predictions: dict[str, dict[str, Any]]) -> None:
    acts = element.pop("_acts", [])
    rows = []
    for a in acts:
        p = predictions.get(a.id, {})
        rows.append({"id": a.id, "name": a.name, "actual": a.actual, "planned": a.planned, "variance": round(a.actual - a.planned, 2),
                     "delay_probability": p.get("delay_probability"), "risk_level": p.get("risk_level"),
                     "risk_score": p.get("risk_score"), "expected_delay_days": p.get("expected_delay_days")})
    element["activities"] = rows
    if not acts:
        element.update(progress=None, planned=None, status="unlinked", risk_level=None, risk_score=None)
        return
    weights = [float(a.duration_days or 10) for a in acts]
    total = sum(weights)
    element["progress"] = round(sum(a.actual * w for a, w in zip(acts, weights)) / total, 1)
    element["planned"] = round(sum(a.planned * w for a, w in zip(acts, weights)) / total, 1)
    element["status"] = ("complete" if all(a.actual >= 100 for a in acts)
                         else "behind" if element["progress"] < element["planned"] - 0.5 else "on_plan")
    scored = [r for r in rows if r["risk_score"] is not None]
    worst = max(scored, key=lambda r: r["risk_score"], default=None)
    element["risk_level"] = worst["risk_level"] if worst else None
    element["risk_score"] = worst["risk_score"] if worst else None


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
def _edge_paths(length: float, width: float) -> dict[str, list[list[float]]]:
    hl, hw = length / 2, width / 2
    return {
        "road": [[-hl, hw - 6], [hl - 6, hw - 6], [hl - 6, -hw + 4]],          # front edge, then up the right side
        "drain": [[hl - 2, hw - 2], [hl - 2, -hw + 2], [-hl + 2, -hw + 2]],     # right and back edges
        "utility": [[-hl + 3, hw - 3], [-hl + 3, -hw + 3]],                     # left edge
    }


def _offset(path: list[list[float]], amount: float) -> list[list[float]]:
    return [[round(x - amount, 2), round(z + amount, 2)] for x, z in path]


def _place(elements: list[dict[str, Any]], length: float, width: float) -> None:
    """Map sheet positions to plot metres, or lay out schematically; keep footprints inside the plot."""
    areas = [e for e in elements if e["kind"] in ("building", "parking", "landscape", "earthworks", "area")]
    positioned = [e for e in areas if e.get("sheet_position")]
    if len(positioned) >= 2:
        xs = [e["sheet_position"][0] for e in positioned]
        ys = [e["sheet_position"][1] for e in positioned]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        margin_x, margin_z = length * 0.22, width * 0.22
        for e in positioned:
            fx = (e["sheet_position"][0] - x0) / (x1 - x0) if x1 > x0 else 0.5
            fy = (e["sheet_position"][1] - y0) / (y1 - y0) if y1 > y0 else 0.5
            e["position"] = [-length / 2 + margin_x + fx * (length - 2 * margin_x), -width / 2 + margin_z + fy * (width - 2 * margin_z)]
    row = [e for e in areas if "position" not in e]
    for index, e in enumerate(row):  # schematic: buildings along the back, others along the front
        back = e["kind"] == "building"
        slots = max(1, sum(1 for r in row if (r["kind"] == "building") == back))
        slot = sum(1 for r in row[:index] if (r["kind"] == "building") == back)
        e["position"] = [-length / 2 + (slot + 0.5) * length / slots * 0.9 + length * 0.05, -width * 0.18 if back else width * 0.18]
    for e in areas:  # clamp footprint inside the plot
        hl, hw = e["length"] / 2, e["width"] / 2
        e["position"] = [round(min(max(e["position"][0], -length / 2 + hl + 2), length / 2 - hl - 2), 2),
                         round(min(max(e["position"][1], -width / 2 + hw + 2), width / 2 - hw - 2), 2)]
    paths = _edge_paths(length, width)
    counts: dict[str, int] = {}
    for e in elements:
        if e["kind"] in ("road", "drain", "utility"):
            n = counts.get(e["kind"], 0)
            e["path"] = _offset(paths[e["kind"]], n * 2.5)
            counts[e["kind"]] = n + 1
    for e in elements:
        e.pop("sheet_position", None)


def _from_plan(plan: dict[str, Any]) -> tuple[float, float, list[dict[str, Any]]]:
    raw = plan.get("plan", {}).get("elements", [])
    plot_label = next((r for r in raw if r.get("category") == "boundary" and r.get("dimension_type") == "length_x_width"
                       and r.get("value") and r.get("value2")), None)
    length, width = (float(plot_label["value"]), float(plot_label["value2"])) if plot_label else (120.0, 80.0)
    if width > length:
        length, width = width, length
    elements: list[dict[str, Any]] = [{"id": "plot", "name": "Plot", "kind": "plot", "length": length, "width": width}]
    for index, total in enumerate(plan.get("takeoff", {}).get("totals", [])):
        if total.get("point_marker") or total.get("measure") not in SUMMABLE or total.get("category") == "boundary":
            continue
        members = [r for r in raw if r.get("category") == total["category"] and base_name(r.get("name", "")).lower() == total["name"].lower()]
        kind = KIND.get(total["category"], "area")
        element = {"id": f"e{index}", "name": total["name"], "kind": kind,
                   "quantity": {"total": total["total"], "unit": total["unit"], "measure": total["measure"]}}
        if kind in ("road", "drain", "utility"):
            element["length"] = total["total"] if total["measure"] == "length" else None
        else:
            lw = next((m for m in members if m.get("dimension_type") == "length_x_width" and m.get("value") and m.get("value2")), None)
            if lw:
                element["length"], element["width"] = float(lw["value"]), float(lw["value2"])
            else:
                side = math.sqrt(total["total"]) if total["measure"] == "area" and total["total"] else 15.0
                element["length"] = element["width"] = round(side, 2)
            element["length"] = min(element["length"], length * 0.9)
            element["width"] = min(element["width"], width * 0.9)
        positions = [m["position"] for m in members if m.get("position")]
        if positions:
            element["sheet_position"] = [sum(p[0] for p in positions) / len(positions), sum(p[1] for p in positions) / len(positions)]
        if kind == "building":
            element["floors"] = next((m["floors"] for m in members if m.get("floors")), DEFAULT_FLOORS)
        elements.append(element)
    return length, width, elements


def _from_activities(activities: Iterable[Any]) -> tuple[float, float, list[dict[str, Any]]]:
    length, width = 120.0, 80.0
    elements: list[dict[str, Any]] = [{"id": "plot", "name": "Plot", "kind": "plot", "length": length, "width": width}]
    names = [a.name for a in activities]
    blocks = sorted({m.group(1).upper() for n in names if (m := re.search(r"\bblock\s+([a-z0-9]+)\b", n, re.IGNORECASE))})
    for letter in blocks:
        elements.append({"id": f"b{letter}", "name": f"Block {letter}", "kind": "building", "length": 30.0, "width": 20.0,
                         "floors": DEFAULT_FLOORS})
    if any(re.search(r"\broads?\b", n, re.IGNORECASE) for n in names):
        elements.append({"id": "road", "name": "Road", "kind": "road", "length": None})
    if any(re.search(r"\bdrain", n, re.IGNORECASE) for n in names):
        elements.append({"id": "drain", "name": "Drain", "kind": "drain", "length": None})
    return length, width, elements


def build_site_model(activities: list[Any], predictions: dict[str, dict[str, Any]], plan: Optional[dict[str, Any]],
                     plan_label: Optional[str] = None) -> dict[str, Any]:
    if plan:
        length, width, elements = _from_plan(plan)
        source = {"type": "site_plan", "label": plan_label,
                  "note": "Sizes from the site-plan take-off; buildings placed where their labels are on the drawing; "
                          "roads and drains drawn schematically along the plot edges."}
    else:
        length, width, elements = _from_activities(activities)
        source = {"type": "schematic", "label": None,
                  "note": "Schematic layout from activity names. Read a site plan (Site plans screen) for real sizes."}
    _place(elements, length, width)
    unlinked = link_activities(elements, activities)
    for element in elements:
        _summarise(element, predictions)
        if element["kind"] == "building":
            element["height"] = element.get("floors", DEFAULT_FLOORS) * FLOOR_HEIGHT
    return {"plot": {"length": length, "width": width}, "elements": elements, "source": source,
            "unlinked_activities": [{"id": a.id, "name": a.name, "actual": a.actual} for a in unlinked],
            "linking": "Activities are linked by name (e.g. 'Block A …', 'Drainage …'); buildings also take activities in the same "
                       "top-level WBS group."}
