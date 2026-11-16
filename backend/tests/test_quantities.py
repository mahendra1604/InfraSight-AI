import pytest

from app import quantities as q

PLAN_ELEMENTS = [
    {"name": "Plot boundary", "category": "boundary", "dimension_type": "length_x_width", "value": 120, "value2": 80, "unit": "m",
     "label_text": "PLOT BOUNDARY 120 m x 80 m"},
    {"name": "Block A", "category": "building", "dimension_type": "length_x_width", "value": 30, "value2": 20, "unit": "m",
     "label_text": "30 m x 20 m"},
    {"name": "Access road", "category": "road", "dimension_type": "length", "value": 180, "unit": "m", "label_text": "180 m"},
    {"name": "Access road", "category": "road", "dimension_type": "length", "value": 120, "unit": "m", "label_text": "120 m"},
    {"name": "Access road", "category": "road", "dimension_type": "width", "value": 7, "unit": "m", "label_text": "7 m WIDE"},
    {"name": "Drain D1", "category": "drainage", "dimension_type": "length", "value": 150, "unit": "m", "label_text": "DRAIN D1 - 150 m"},
    {"name": "Drain D2", "category": "drainage", "dimension_type": "length", "value": 0.2, "unit": "km", "label_text": "DRAIN D2 0.2 km"},
    {"name": "Drain D3", "category": "drainage", "dimension_type": "length", "value": 100, "unit": "metres", "label_text": "DRAIN D3 - 100 m"},
    {"name": "Lamp posts", "category": "utility", "dimension_type": "count", "value": 12, "unit": "nos", "label_text": "12 nos"},
]


@pytest.mark.parametrize("raw,expected", [("Metres", "m"), ("sq.m", "m2"), ("SQM", "m2"), ("m³", "m3"), ("Nos.", "nos"),
                                          ("cum", "m3"), (None, None), ("", None), ("bags", "bags")])
def test_normalize_unit(raw, expected):
    assert q.normalize_unit(raw) == expected


def test_convert():
    assert q.convert(1.2, "km", "m") == pytest.approx(1200)
    assert q.convert(1, "ha", "sqm") == pytest.approx(10000)
    assert q.convert(5, "m", "m2") is None
    assert q.convert(3, "bags", "bags") == 3


def test_progress_from_cumulative_quantity_with_unit_conversion():
    result = q.progress_from_quantity(450, "m", 20, done=0.18, unit="km")
    assert (result.progress, result.quantity_done, result.unit) == (40.0, 180.0, "m")
    assert "180 of 450 m = 40.0%" in result.notes[0] and not result.needs_review


def test_progress_from_todays_quantity_adds_to_approved():
    result = q.progress_from_quantity(450, "m", 40, today=45, unit="m")      # 40 % of 450 = 180, + 45 = 225
    assert result.progress == 50.0 and result.needs_review
    assert "previously approved 180 m" in result.notes[1]


def test_quantity_edge_cases():
    assert q.progress_from_quantity(48, "nos", 0, done=10, unit="m").reason.startswith("Reported unit")
    assert "no total quantity" in q.progress_from_quantity(None, None, 0, done=10, unit="m").reason
    from_report = q.progress_from_quantity(None, None, 0, done=180, unit="m", report_total=450)
    assert from_report.progress == 40.0 and from_report.needs_review
    over = q.progress_from_quantity(450, "m", 0, done=500, unit="m")
    assert over.progress == 100.0 and over.needs_review and "exceeds" in over.notes[-1]
    no_unit = q.progress_from_quantity(450, "m", 0, done=90)
    assert no_unit.progress == 20.0 and no_unit.needs_review


def test_takeoff_sums_segments_and_computes_areas_in_code():
    takeoff = q.build_takeoff(PLAN_ELEMENTS)
    totals = {(t["category"], t["name"], t["measure"]): (t["total"], t["unit"], t["parts"]) for t in takeoff["totals"]}
    assert totals[("boundary", "Plot boundary", "area")] == (9600, "m2", 1)
    assert totals[("building", "Block A", "area")] == (600, "m2", 1)
    assert totals[("road", "Access road", "length")] == (300, "m", 2)
    assert totals[("drainage", "Drain", "length")] == (450, "m", 3)           # D1 + 0.2 km + 100 metres
    assert totals[("utility", "Lamp posts", "count")] == (12, "nos", 1)
    assert takeoff["other_dimensions"][0]["dimension"] == "width"             # widths are listed, never summed


def test_takeoff_flags_missing_units():
    takeoff = q.build_takeoff([{"name": "Wall", "category": "other", "dimension_type": "length", "value": 12, "unit": None}])
    assert takeoff["totals"][0]["needs_review"] is True


def test_draft_schedule_order_and_dependencies():
    rows = q.draft_schedule(q.build_takeoff(PLAN_ELEMENTS))
    by_name = {r["name"]: r for r in rows}
    assert rows[0]["name"] == "Site preparation and clearing" and rows[0]["total_quantity"] == 9600
    assert by_name["Drain laying"]["predecessor"] == rows[0]["activity_id"]
    assert by_name["Block A superstructure"]["predecessor"] == by_name["Block A foundations"]["activity_id"]
    road = by_name["Access road construction"]
    assert set(road["predecessor"].split(";")) == {by_name["Drain laying"]["activity_id"], by_name["Lamp posts installation"]["activity_id"]}
    assert road["total_quantity"] == 300 and road["unit"] == "m"
    assert all(r["planned_start"] == "" and r["duration_days"] == "" for r in rows)    # left for the team


@pytest.mark.parametrize("raw,expected", [("Drain D1", "Drain"), ("Access road segment 2", "Access road"), ("Block A", "Block A"),
                                          ("Road (S-3)", "Road")])
def test_base_name(raw, expected):
    assert q.base_name(raw) == expected


DRAWING_LABELS = "\n".join(['"GATE" @ x=6% y=50%', '"180 m" @ x=38% y=51%', '"120 m" @ x=72% y=66%', '"DRAIN D1 - 150 m" @ x=21% y=44%',
                            '"DRAIN D2" @ x=80% y=26%', '"200 m" @ x=80% y=28%', '"DRAIN D3 - 100 m" @ x=48% y=78%'])


def _el(name, category, value, label):
    return {"name": name, "category": category, "dimension_type": "length", "value": value, "unit": "m", "label_text": label}


def test_value_printed_once_is_not_counted_twice():
    # exactly what qwen3-vl:4b-instruct returned for data/samples/site_plan.png
    elements = [_el("GATE", "boundary", 180, "GATE 180 m"), _el("ACCESS ROAD", "road", 180, "180 m"),
                _el("ACCESS ROAD", "road", 120, "120 m"), _el("DRAIN D1", "drainage", 150, "DRAIN D1 - 150 m"),
                _el("DRAIN D2", "drainage", 200, "DRAIN D2 200 m"), _el("DRAIN D3", "drainage", 100, "DRAIN D3 - 100 m")]
    kept, notes = q.resolve_double_counting(elements, DRAWING_LABELS)
    assert [e["name"] for e in kept].count("GATE") == 0 and len(kept) == 5
    assert "printed only 1×" in notes[0] and "Access Road" in notes[0]
    totals = {t["name"]: t["total"] for t in q.build_takeoff(kept)["totals"]}
    assert totals == {"Access Road": 300, "Drain": 450}


def test_repeated_values_and_unresolvable_claims():
    same_twice = [_el("Drain D1", "drainage", 150, "150 m"), _el("Road", "road", 150, "150 m")]
    kept, notes = q.resolve_double_counting(same_twice, '"150 m" @ x=1% y=1%\n"150 m" @ x=9% y=9%')
    assert len(kept) == 2 and notes == []                                   # printed twice: both legitimate
    unclear = [_el("Wall", "other", 75, "wall 75"), _el("Fence", "other", 75, "fence 75")]
    kept, notes = q.resolve_double_counting(unclear, '"75 m" @ x=5% y=5%')
    assert len(kept) == 2 and all(e["needs_review"] for e in kept) and "check which one" in notes[0]


@pytest.mark.parametrize("raw,expected", [("DRAIN", "Drain"), ("BLOCK A", "Block A"), ("ACCESS ROAD", "Access Road")])
def test_all_caps_names_are_tidied(raw, expected):
    assert q.base_name(raw) == expected


def test_today_copied_into_cumulative_is_treated_as_today():
    # real model output for "laid 45 m of pipe today": done=45 and today=45
    result = q.progress_from_quantity(450, "m", 45, done=45, today=45, unit="m")      # approved 202.5 m + 45 m
    assert result.progress == 55.0 and result.needs_review and "this period's work" in result.notes[1]
    genuine = q.progress_from_quantity(450, "m", 45, done=270, today=45, unit="m")     # cumulative 270 > approved 202.5
    assert genuine.progress == 60.0


def test_lengths_on_point_markers_are_flagged_and_kept_out_of_the_draft():
    takeoff = q.build_takeoff([_el("GATE", "boundary", 180, "180 m"), _el("Access road", "road", 120, "120 m")])
    gate = next(t for t in takeoff["totals"] if t["name"] == "Gate")
    assert gate["needs_review"] and gate["point_marker"] and "marks a point" in takeoff["notes"][0]
    assert all("Gate" not in r["name"] for r in q.draft_schedule(takeoff))
