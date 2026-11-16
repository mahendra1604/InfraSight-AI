"""Generate the golden evaluation set in data/eval/ (Phase 5).

15 synthetic site reports in different styles and formats, each with the expected
(activity_id, cumulative progress, reporting date) triples. Activities refer to
data/samples/schedule_extended.csv.

    .venv\\Scripts\\python.exe ml\\make_eval_set.py
"""
import io
import json
import random
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from tests.helpers import make_text_pdf  # noqa: E402

OUT = ROOT / "data" / "eval"

DOCS = [
    ("eval_01_structured.txt", """INFRASTRUCTURE SITE PROGRESS REPORT
Project: Riverside Infrastructure
Report date: 2026-10-29

Activity: Block A foundation concrete
Activity ID: A102
Cumulative progress: 60%
Planned progress: 80%
Issue: Rain interrupted the concrete pour for two hours.

Activity: Ground floor columns
Activity ID: A103
Cumulative progress: 20%
Planned progress: 35%
Issue: Reinforcement delivery was delayed.

Activity: Ground floor slab
Activity ID: A104
Cumulative progress: 0%
Status: Waiting for column completion.
""", [("A102", 60, "2026-10-29"), ("A103", 20, "2026-10-29"), ("A104", 0, "2026-10-29")]),
    ("eval_02_narrative.txt", """Daily site diary - 30 October 2026

The morning pour went well and the foundation concrete for Block A has now reached 65% overall.
Column work on the ground floor is at roughly 25 percent; the rebar truck arrived three hours late, which held up two crews.
Over in Block B the excavation team reports 78% of the dig complete. Weather: cloudy, light drizzle after 4 pm.
""", [("A102", 65, "2026-10-30"), ("A103", 25, "2026-10-30"), ("A109", 78, "2026-10-30")]),
    ("eval_03_table.txt", """WEEKLY PROGRESS TABLE                      Date: 31/10/2026

| ID   | Activity                       | Planned | Actual | Remarks                      |
|------|--------------------------------|---------|--------|------------------------------|
| A111 | Drainage and stormwater lines  | 50%     | 55%    | Ahead, manhole 4 completed   |
| A112 | Site access road               | 60%     | 40%    | Aggregate supply shortage    |
| A109 | Block B excavation             | 95%     | 80%    | Rock encountered at grid C   |
""", [("A111", 55, "2026-10-31"), ("A112", 40, "2026-10-31"), ("A109", 80, "2026-10-31")]),
    ("eval_04_no_ids.txt", """Progress note, 1 Nov 2026

Ground-floor slab: 5% complete (formwork started on bay 1).
Site access road: 45% done, sub-base laid up to chainage 0+300.
No other changes since yesterday.
""", [("A104", 5, "2026-11-01"), ("A112", 45, "2026-11-01")]),
    ("eval_05_planned_actual.txt", """WEEKLY SUMMARY  - week ending 07-11-2026
Block A foundation concrete .......... planned 90%  actual 70%
Ground floor columns ................. planned 50%  actual 30%
Reason for slippage: concrete pump breakdown on Tuesday and late steel delivery.
""", [("A102", 70, "2026-11-07"), ("A103", 30, "2026-11-07")]),
    ("eval_06_issues_only.txt", """SITE DIARY 2026-11-02
Heavy rain stopped all work on the Block B foundation; no progress today.
Drainage and stormwater lines are now 60% complete after laying 40 m of pipe.
Safety: toolbox talk on working near open trenches.
""", [("A111", 60, "2026-11-02")]),
    ("eval_07_ocr_noise.txt", """S1TE PROGRESS REP0RT    Date: 2026-11-05
Actlvity: Block A foundatlon concrete     ID A1O2
Cumulatlve progress: 75 %
Actlvity: Ground fl00r columns   ID: A103
Cumulative progres: 35%
Remarks: shuttering material shortage
""", [("A102", 75, "2026-11-05"), ("A103", 35, "2026-11-05")]),
    ("eval_08_long_multi.txt", """MONTHLY PROGRESS REPORT - RIVERSIDE INFRASTRUCTURE
Reporting date: 8 November 2026

1. Block A foundation concrete (A102): 80% complete. Final pour planned for next week.
2. Ground floor columns (A103): 40% complete. Second crew mobilised.
3. Ground floor slab (A104): 10% complete. Formwork in progress.
4. Block B excavation (A109): 90% complete. Rock breaking continues at grid C.
5. Drainage and stormwater lines (A111): 65% complete.
6. Site access road (A112): 50% complete. Aggregate supply has resumed.

Key risks: monsoon tail-end showers, reinforcement steel lead time of 10 days.
""", [("A102", 80, "2026-11-08"), ("A103", 40, "2026-11-08"), ("A104", 10, "2026-11-08"),
      ("A109", 90, "2026-11-08"), ("A111", 65, "2026-11-08"), ("A112", 50, "2026-11-08")]),
    ("eval_09_email.txt", """From: site.engineer@example.com
Subject: quick update

Hi team, quick update for 09/11/2026 - columns on the ground floor are at 45% and the GF slab has reached 12%.
The slab is moving slowly because the column cycle is still behind.
Regards, Site Engineer
""", [("A103", 45, "2026-11-09"), ("A104", 12, "2026-11-09")]),
    ("eval_10_distractors.txt", """DAILY REPORT 2026-11-10
Crane maintenance completed (2 hours downtime).
Safety audit score: 100%.
Block A foundation concrete: 85% complete.
Visitors: client representative, 11:00-12:00.
""", [("A102", 85, "2026-11-10")]),
]


def render_image(text: str, rotate: float = 0.0, noise: bool = False, fmt: str = "PNG") -> bytes:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    lines = text.splitlines()
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except OSError:
        font = ImageFont.load_default(size=20)
    width = 60 + max(int(font.getlength(line)) for line in lines)
    image = Image.new("RGB", (max(width, 900), 60 + 28 * len(lines)), "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(lines):
        draw.text((30, 30 + 28 * i), line, fill=(25, 25, 25), font=font)
    if noise:
        rng = random.Random(7)
        pixels = image.load()
        for _ in range(image.width * image.height // 40):
            x, y = rng.randrange(image.width), rng.randrange(image.height)
            shade = rng.randrange(150, 230)
            pixels[x, y] = (shade, shade, shade)
        image = image.filter(ImageFilter.GaussianBlur(0.6))
    if rotate:
        image = image.rotate(rotate, expand=True, fillcolor="white")
    buffer = io.BytesIO()
    image.save(buffer, fmt, quality=70) if fmt == "JPEG" else image.save(buffer, fmt)
    return buffer.getvalue()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    texts = {}
    for name, text, expected in DOCS:
        (OUT / name).write_text(text, encoding="utf-8")
        texts[name] = (text, expected)
        manifest.append({"file": name, "kind": "text", "expected": expected})

    def wrapped(text):
        return [part for line in text.splitlines() for part in (textwrap.wrap(line, 95) or [""])]

    for source, target in [("eval_02_narrative.txt", "eval_11_narrative.pdf"), ("eval_08_long_multi.txt", "eval_12_monthly.pdf")]:
        text, expected = texts[source]
        (OUT / target).write_bytes(make_text_pdf(wrapped(text)))
        manifest.append({"file": target, "kind": "pdf", "expected": expected})
    for source, target, kwargs in [("eval_01_structured.txt", "eval_13_scan.png", {}),
                                   ("eval_03_table.txt", "eval_14_phone_photo.jpg", {"rotate": 2.0, "noise": True, "fmt": "JPEG"}),
                                   ("eval_09_email.txt", "eval_15_email_scan.png", {"noise": True})]:
        text, expected = texts[source]
        (OUT / target).write_bytes(render_image(text, **kwargs))
        manifest.append({"file": target, "kind": "image", "expected": expected})
    for item in manifest:
        item["expected"] = [{"activity_id": a, "progress": p, "reporting_date": d} for a, p, d in item["expected"]]
    (OUT / "manifest.json").write_text(json.dumps({"schedule": "data/samples/schedule_extended.csv", "documents": manifest}, indent=2),
                                       encoding="utf-8")
    print(f"Wrote {len(manifest)} documents to {OUT}")


if __name__ == "__main__":
    main()
