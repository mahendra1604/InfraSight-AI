"""Generate a synthetic site layout plan (data/samples/site_plan.png) and its expected take-off.

Only labels carry the dimensions (as on real drawings); the pixel geometry is illustrative,
so the model must read the labels instead of measuring.
    .venv\\Scripts\\python.exe ml\\make_site_plan.py
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "samples"

EXPECTED = {
    "totals": [
        {"name": "Plot boundary", "category": "boundary", "measure": "area", "total": 9600, "unit": "m2"},
        {"name": "Block A", "category": "building", "measure": "area", "total": 600, "unit": "m2"},
        {"name": "Block B", "category": "building", "measure": "area", "total": 500, "unit": "m2"},
        {"name": "Parking", "category": "parking", "measure": "area", "total": 600, "unit": "m2"},
        {"name": "Access road", "category": "road", "measure": "length", "total": 300, "unit": "m"},
        {"name": "Drain", "category": "drainage", "measure": "length", "total": 450, "unit": "m"},
    ],
    "drawing": {"title": "SITE LAYOUT PLAN", "scale": "1:500", "date": "15/09/2026"},
}


def font(size: int, bold: bool = False):
    for name in (("arialbd.ttf" if bold else "arial.ttf"), "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def dashed(draw, start, end, color, width=4, dash=18, gap=10):
    (x1, y1), (x2, y2) = start, end
    length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    steps = int(length // (dash + gap)) + 1
    for i in range(steps):
        a = i * (dash + gap) / length
        b = min((i * (dash + gap) + dash) / length, 1)
        draw.line([(x1 + (x2 - x1) * a, y1 + (y2 - y1) * a), (x1 + (x2 - x1) * b, y1 + (y2 - y1) * b)], fill=color, width=width)


def main() -> None:
    image = Image.new("RGB", (2000, 1400), "white")
    d = ImageDraw.Draw(image)
    black, grey, blue, green = (20, 20, 20), (150, 150, 150), (30, 90, 200), (60, 140, 70)

    # Plot boundary
    d.rectangle([100, 100, 1700, 1166], outline=black, width=5)
    d.text((115, 112), "PLOT BOUNDARY  120 m x 80 m", fill=black, font=font(26, True))

    # Buildings
    for box, name, size in [((300, 280, 750, 580), "BLOCK A", "30 m x 20 m"), ((900, 280, 1275, 580), "BLOCK B", "25 m x 20 m")]:
        d.rectangle(box, outline=black, width=4, fill=(235, 235, 235))
        cx, cy = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
        d.text((cx, cy - 22), name, fill=black, font=font(30, True), anchor="mm")
        d.text((cx, cy + 22), size, fill=black, font=font(24), anchor="mm")
        d.text((cx, cy + 58), "G+2", fill=grey, font=font(20), anchor="mm")

    # Parking
    d.rectangle([300, 820, 900, 1045], outline=black, width=3)
    for x in range(330, 900, 50):
        d.line([(x, 820), (x, 880)], fill=grey, width=2)
    d.text((600, 950), "PARKING", fill=black, font=font(26, True), anchor="mm")
    d.text((600, 990), "40 m x 15 m", fill=black, font=font(22), anchor="mm")

    # Access road (L-shaped, two labelled segments)
    d.rectangle([100, 690, 1420, 740], fill=(205, 205, 205))
    d.rectangle([1370, 690, 1420, 1166], fill=(205, 205, 205))
    d.text((140, 750), "ACCESS ROAD (7 m WIDE)", fill=black, font=font(22, True))
    d.text((760, 715), "180 m", fill=black, font=font(22, True), anchor="mm")
    d.text((1430, 930), "120 m", fill=black, font=font(22, True), anchor="lm")
    d.text((110, 700), "GATE", fill=black, font=font(18))

    # Drains (dashed)
    dashed(d, (150, 640), (1320, 640), blue)
    d.text((420, 612), "DRAIN D1 - 150 m", fill=blue, font=font(22, True))
    dashed(d, (1580, 150), (1580, 1120), blue)
    d.text((1590, 360), "DRAIN D2", fill=blue, font=font(19, True))
    d.text((1590, 388), "200 m", fill=blue, font=font(19, True))
    dashed(d, (950, 1110), (1340, 1110), blue)
    d.text((960, 1080), "DRAIN D3 - 100 m", fill=blue, font=font(22, True))

    # Trees / landscape symbols (no dimensions)
    for x, y in [(1100, 860), (1180, 930), (1250, 860)]:
        d.ellipse([x - 22, y - 22, x + 22, y + 22], outline=green, width=3)

    # North arrow
    d.polygon([(1850, 80), (1825, 160), (1875, 160)], outline=black, fill=black)
    d.text((1850, 185), "N", fill=black, font=font(30, True), anchor="mm")

    # Notes and title block
    d.text((1730, 260), "NOTES:", fill=black, font=font(20, True))
    for i, line in enumerate(["1. Drains to be laid", "   before road sub-base.", "2. Levels to be", "   confirmed on site."]):
        d.text((1730, 292 + i * 26), line, fill=black, font=font(18))
    d.rectangle([1720, 1010, 1990, 1390], outline=black, width=3)
    for i, (line, size, bold) in enumerate([("RIVERSIDE", 22, True), ("INFRASTRUCTURE", 22, True), ("SITE LAYOUT PLAN", 20, True),
                                            ("DWG NO: SP-01", 18, False), ("SCALE 1:500", 18, False), ("DATE: 15/09/2026", 18, False),
                                            ("ALL DIMENSIONS", 16, False), ("IN METRES", 16, False)]):
        d.text((1735, 1025 + i * 44), line, fill=black, font=font(size, bold))

    OUT.mkdir(parents=True, exist_ok=True)
    image.save(OUT / "site_plan.png")
    (OUT / "site_plan_expected.json").write_text(json.dumps(EXPECTED, indent=2), encoding="utf-8")
    print("wrote", OUT / "site_plan.png")


if __name__ == "__main__":
    main()
