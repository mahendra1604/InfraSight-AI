from types import SimpleNamespace as A
from unittest.mock import AsyncMock, patch

from app import quantities, site_model
from tests.helpers import make_image

LABELS = "\n".join(['"PLOT BOUNDARY 120 m x 80 m" @ x=10% y=9%', '"BLOCK A" @ x=26% y=29%', '"30 m x 20 m" @ x=26% y=32%',
                    '"G+2" @ x=26% y=35%', '"BLOCK B" @ x=54% y=29%', '"25 m x 20 m" @ x=54% y=32%', '"PARKING" @ x=30% y=68%',
                    '"DRAIN D1 - 150 m" @ x=21% y=44%', '"180 m" @ x=38% y=51%'])
ELEMENTS = [
    {"name": "PLOT BOUNDARY", "category": "boundary", "dimension_type": "length_x_width", "value": 120, "value2": 80, "unit": "m",
     "label_text": "PLOT BOUNDARY 120 m x 80 m"},
    {"name": "BLOCK A", "category": "building", "dimension_type": "length_x_width", "value": 30, "value2": 20, "unit": "m", "label_text": "30 m x 20 m"},
    {"name": "BLOCK B", "category": "building", "dimension_type": "length_x_width", "value": 25, "value2": 20, "unit": "m", "label_text": "25 m x 20 m"},
    {"name": "PARKING", "category": "parking", "dimension_type": "length_x_width", "value": 40, "value2": 15, "unit": "m", "label_text": "40 m x 15 m"},
    {"name": "DRAIN D1", "category": "drainage", "dimension_type": "length", "value": 150, "unit": "m", "label_text": "DRAIN D1 - 150 m"},
    {"name": "ACCESS ROAD", "category": "road", "dimension_type": "length", "value": 180, "unit": "m", "label_text": "180 m"},
]
ACTS = [A(id="A100", name="Site preparation", actual=100, planned=100, duration_days=20, wbs="1.1"),
        A(id="A101", name="Block A excavation", actual=100, planned=100, duration_days=22, wbs="1.2.1"),
        A(id="A102", name="Block A foundation concrete", actual=60, planned=80, duration_days=25, wbs="1.2.2"),
        A(id="A103", name="Ground floor columns", actual=20, planned=35, duration_days=26, wbs="1.3.1"),
        A(id="A109", name="Block B excavation", actual=70, planned=87, duration_days=31, wbs="2.1"),
        A(id="A111", name="Drainage and stormwater lines", actual=45, planned=42, duration_days=40, wbs="3.1"),
        A(id="A112", name="Site access road", actual=30, planned=52, duration_days=42, wbs="3.2"),
        A(id="A116", name="Handover inspection", actual=0, planned=0, duration_days=10, wbs="4.1")]


def plan_result():
    elements = [dict(e) for e in ELEMENTS]
    site_model.locate_elements(elements, LABELS)
    return {"plan": {"elements": elements}, "takeoff": quantities.build_takeoff(elements)}


def test_positions_and_floors_come_from_the_drawing_labels():
    elements = [dict(e) for e in ELEMENTS]
    site_model.locate_elements(elements, LABELS)
    block_a = elements[1]
    assert block_a["position"] == [0.26, 0.29] and block_a["floors"] == 3          # "G+2" -> 3 floors
    assert elements[5]["position"] == [0.38, 0.51]                                 # linear: its own dimension label
    assert "floors" not in elements[2]                                             # no G+n label near Block B


def test_site_model_from_plan_links_activities_and_keeps_footprints_inside():
    model = site_model.build_site_model(ACTS, {"A102": {"risk_level": "high", "risk_score": 80, "delay_probability": 0.9}},
                                        plan_result(), "site_plan.png (analysis #1)")
    assert model["plot"] == {"length": 120.0, "width": 80.0} and model["source"]["type"] == "site_plan"
    by_name = {e["name"]: e for e in model["elements"]}
    assert {a["id"] for a in by_name["Plot"]["activities"]} == {"A100"}
    assert {a["id"] for a in by_name["Block A"]["activities"]} == {"A101", "A102", "A103"}     # A103 via WBS group 1
    assert {a["id"] for a in by_name["Block B"]["activities"]} == {"A109"}
    assert {a["id"] for a in by_name["Drain"]["activities"]} == {"A111"}
    assert {a["id"] for a in by_name["Access Road"]["activities"]} == {"A112"}
    assert by_name["Parking"]["status"] == "unlinked"
    assert [a["id"] for a in model["unlinked_activities"]] == ["A116"]
    block_a = by_name["Block A"]
    assert block_a["status"] == "behind" and block_a["risk_level"] == "high" and block_a["height"] == 3 * 3.5
    assert by_name["Block A"]["position"][0] < by_name["Block B"]["position"][0]                # same order as on the drawing
    for e in model["elements"]:
        if "position" in e and e["kind"] != "plot":
            assert abs(e["position"][0]) + e["length"] / 2 <= 60 and abs(e["position"][1]) + e["width"] / 2 <= 40
    assert len(by_name["Drain"]["path"]) >= 2 and len(by_name["Access Road"]["path"]) >= 2


def test_schematic_model_without_a_site_plan():
    model = site_model.build_site_model(ACTS, {}, None)
    names = {e["name"] for e in model["elements"]}
    assert {"Plot", "Block A", "Block B", "Road", "Drain"} <= names and model["source"]["type"] == "schematic"


def test_site_model_api(client):
    data = client.get("/api/site-model").json()
    assert data["source"]["type"] == "schematic"
    block_a = next(e for e in data["elements"] if e["name"] == "Block A")
    assert {a["id"] for a in block_a["activities"]} >= {"A101", "A102"} and block_a["progress"] is not None
    reading = {"drawing_title": "SITE PLAN", "scale": None, "drawing_date": None, "answer": "", "elements": [dict(e) for e in ELEMENTS],
               "notes": [], "warnings": [], "meta": {"model": "qwen3-vl:4b-instruct", "duration_s": 1}}
    with patch("app.qwen_service.extract_plan", new=AsyncMock(return_value=reading)):
        client.post("/api/plans/upload", files={"file": ("plan.png", make_image("PLAN"), "image/png")})
    data = client.get("/api/site-model").json()
    assert data["source"]["type"] == "site_plan" and data["plot"]["length"] == 120
