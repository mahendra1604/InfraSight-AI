"""Shared test fixtures: sample extraction results and tiny document generators."""
import copy
import io

SAMPLE_TEXT = """INFRASTRUCTURE SITE PROGRESS REPORT
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
Issue: Reinforcement delivery was delayed.
"""

_EXTRACTION = {
    "document_type": "daily_progress_report",
    "report_date": "2026-10-29",
    "answer": "Foundation concrete and columns are behind plan.",
    "entries": [
        {"activity_id": "A102", "activity_text": "Block A foundation concrete", "progress": 60.0, "planned_progress": 80.0,
         "reporting_date": "2026-10-29", "issue": "Rain interrupted the concrete pour for two hours.",
         "evidence": "Cumulative progress: 60%", "needs_review": False, "warnings": []},
        {"activity_id": None, "activity_text": "Ground floor columns", "progress": 20.0, "planned_progress": 35.0,
         "reporting_date": "2026-10-29", "issue": "Reinforcement delivery was delayed.",
         "evidence": "Ground floor columns ... 20%", "needs_review": False, "warnings": []},
        {"activity_id": None, "activity_text": "Ground floor slab", "progress": 0.0, "planned_progress": 10.0,
         "reporting_date": "2026-10-29", "issue": "Waiting for column completion.",
         "evidence": "Ground floor slab 0%", "needs_review": False, "warnings": []},
        {"activity_id": None, "activity_text": "Safety toolbox talk", "progress": None, "planned_progress": None,
         "reporting_date": "2026-10-29", "issue": None, "evidence": "Toolbox talk held", "needs_review": False, "warnings": []},
    ],
    "warnings": [],
    "meta": {"model": "qwen3:4b-instruct-2507-q4_K_M", "duration_s": 1.2, "attempts": 1},
}


def sample_extraction():
    return copy.deepcopy(_EXTRACTION)


def make_text_pdf(lines: list[str]) -> bytes:
    """A minimal single-page PDF with a real text layer (no external libraries)."""
    def esc(value: str) -> str:
        return value.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream = "BT /F1 11 Tf 14 TL 50 780 Td " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def make_image(text: str = "", size=(600, 400), fmt: str = "PNG", font_size: int = 16) -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", size, "white")
    if text:
        try:
            font = ImageFont.truetype("arial.ttf", font_size)
        except OSError:
            font = ImageFont.load_default(size=font_size)
        ImageDraw.Draw(image).multiline_text((20, 20), text, fill="black", font=font, spacing=4)
    buffer = io.BytesIO()
    image.save(buffer, fmt)
    return buffer.getvalue()


def make_docx(paragraphs: list[str]) -> bytes:
    from docx import Document

    doc = Document()
    for paragraph in paragraphs:
        doc.add_paragraph(paragraph)
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "A105 Electrical rough-in"
    table.rows[0].cells[1].text = "5%"
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
