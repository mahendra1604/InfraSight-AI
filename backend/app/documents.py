"""Turn uploaded report files into text pages (or images for the vision model).

Routing (cheapest first, because this laptop is CPU-only):
  .txt/.md/.log ........ decode
  .docx ................ python-docx paragraphs + tables
  PDF with text layer .. pypdf
  scanned PDF page ..... render (pypdfium2) -> OCR (RapidOCR / PaddleOCR models) -> text
  image ................ OCR -> text, or the vision model when OCR finds too little text
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from app.config import settings

log = logging.getLogger("infrasight.documents")

TEXT_EXTENSIONS = {".txt", ".md", ".log", ".text"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
SCHEDULE_EXTENSIONS = {".csv", ".xlsx", ".xlsm"}
MIN_PAGE_TEXT = 40          # fewer characters than this => treat the PDF page as scanned
MIN_OCR_TEXT = 60           # OCR output shorter than this => use the vision model instead
MAX_PDF_PAGES = 20


@dataclass
class Page:
    number: int
    text: str = ""
    image: Optional[bytes] = None      # JPEG/PNG bytes when the vision model must read the page
    method: str = "text"               # text | ocr | vision
    ocr_confidence: Optional[float] = None


@dataclass
class Document:
    kind: str                          # text | docx | pdf | image
    pages: list[Page] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def methods(self) -> list[str]:
        return sorted({p.method for p in self.pages})


class UnsupportedDocument(ValueError):
    pass


def detect_kind(filename: str | None, content_type: str | None, data: bytes) -> str:
    ext = Path(filename or "").suffix.lower()
    mime = (content_type or "").lower()
    if ext in SCHEDULE_EXTENSIONS or mime in {"text/csv", "application/csv"}:
        return "schedule"
    if ext == ".pdf" or data[:5] == b"%PDF-":
        return "pdf"
    if ext == ".docx":
        return "docx"
    if ext in IMAGE_EXTENSIONS or mime.startswith("image/"):
        return "image"
    if ext in TEXT_EXTENSIONS or mime.startswith("text/"):
        return "text"
    if data[:4] == b"PK\x03\x04":
        return "docx"
    return "unknown"


def decode_text(data: bytes) -> str:
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


# ---------------------------------------------------------------------------
# OCR (lazy: the engine loads ONNX models once, on first use)
# ---------------------------------------------------------------------------
_ocr_engine = None
_ocr_failed = False


def _get_ocr():
    global _ocr_engine, _ocr_failed
    if _ocr_engine is not None or _ocr_failed:
        return _ocr_engine
    try:
        from rapidocr import RapidOCR  # PaddleOCR models running on ONNX Runtime

        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        _ocr_engine = RapidOCR()
    except Exception as exc:  # pragma: no cover - depends on local install
        log.warning("OCR unavailable (%s); images will go to the vision model", exc)
        _ocr_failed = True
    return _ocr_engine


def _ocr_items(image) -> tuple[list[tuple[float, float, float, str, float]], Optional[float]]:
    """Raw OCR boxes as (y_centre, height, x_left, text, x_centre) plus mean confidence."""
    if not settings.ocr_enabled:
        return [], None
    engine = _get_ocr()
    if engine is None:
        return [], None
    import numpy as np

    result = engine(np.array(image.convert("RGB")))
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    scores = getattr(result, "scores", None)
    if boxes is None or texts is None or len(texts) == 0:
        return [], None
    items = []
    for box, text in zip(boxes, texts):
        ys = [point[1] for point in box]
        xs = [point[0] for point in box]
        items.append(((min(ys) + max(ys)) / 2, max(ys) - min(ys), min(xs), str(text), (min(xs) + max(xs)) / 2))
    confidence = round(float(sum(scores) / len(scores)), 3) if scores is not None and len(scores) else None
    return items, confidence


def ocr_labels(image) -> str:
    """Drawings: every OCR text box on its own line with its position on the sheet.
    Joining boxes into lines (as for reports) would glue unrelated labels together,
    e.g. a 'GATE' marker and a '180 m' road dimension at the same height."""
    items, _ = _ocr_items(image)
    width, height = image.size
    items.sort(key=lambda item: (round(item[0] / max(height, 1) * 50), item[2]))
    return "\n".join(f'"{text}" @ x={x / width:.0%} y={y / height:.0%}' for y, _, _, text, x in items)


def ocr_image(image) -> tuple[str, Optional[float]]:
    """OCR a PIL image. Returns (text in reading order, mean confidence)."""
    items, confidence = _ocr_items(image)
    if not items:
        return "", None
    items.sort(key=lambda item: (item[0], item[2]))
    lines: list[list[tuple]] = []
    for item in items:
        if lines and abs(item[0] - lines[-1][0][0]) < max(item[1], lines[-1][0][1]) * 0.6:
            lines[-1].append(item)
        else:
            lines.append([item])
    text = "\n".join("  ".join(i[3] for i in sorted(line, key=lambda i: i[2])) for line in lines)
    return text, confidence


def _looks_like_document(text: str) -> bool:
    return len(text.strip()) >= MIN_OCR_TEXT and bool(re.search(r"\d", text))


def _image_page(number: int, image, raw: bytes | None, mode: str, warnings: list[str]) -> Page:
    """OCR first (fast on CPU); fall back to the vision model."""
    if mode != "vision":
        text, confidence = ocr_image(image)
        if _looks_like_document(text) or mode == "text":
            if mode == "text" and not text.strip():
                warnings.append(f"Page {number}: OCR found no text.")
            return Page(number, text=text, method="ocr", ocr_confidence=confidence)
    if raw is None:
        buffer = io.BytesIO()
        image.convert("RGB").save(buffer, "JPEG", quality=92)
        raw = buffer.getvalue()
    return Page(number, image=raw, method="vision")


def load_document(filename: str | None, content_type: str | None, data: bytes, mode: str = "auto") -> Document:
    """mode: auto (OCR first, vision fallback) | text (never use vision) | vision (send images straight to the VL model)."""
    kind = detect_kind(filename, content_type, data)
    warnings: list[str] = []
    if kind == "text":
        return Document("text", [Page(1, text=decode_text(data))])
    if kind == "docx":
        try:
            from docx import Document as Docx
        except ImportError as exc:  # pragma: no cover
            raise UnsupportedDocument("Install python-docx to read .docx files") from exc
        try:
            doc = Docx(io.BytesIO(data))
        except Exception as exc:
            raise UnsupportedDocument(f"The Word file could not be read: {exc}") from exc
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                parts.append(" | ".join(cell.text.strip() for cell in row.cells))
        return Document("docx", [Page(1, text="\n".join(parts))])
    if kind == "pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
        except Exception as exc:
            raise UnsupportedDocument(f"The PDF could not be read: {exc}") from exc
        pages = []
        total = len(reader.pages)
        if total > MAX_PDF_PAGES:
            warnings.append(f"Only the first {MAX_PDF_PAGES} of {total} pages were analysed.")
        pdfium_doc = None
        for index in range(min(total, MAX_PDF_PAGES)):
            try:
                text = reader.pages[index].extract_text() or ""
            except Exception:
                text = ""
            if len(text.strip()) >= MIN_PAGE_TEXT and mode != "vision":
                pages.append(Page(index + 1, text=text, method="text"))
                continue
            try:
                import pypdfium2 as pdfium

                pdfium_doc = pdfium_doc or pdfium.PdfDocument(data)
                image = pdfium_doc[index].render(scale=200 / 72).to_pil()
            except Exception as exc:
                warnings.append(f"Page {index + 1} could not be rendered for OCR: {exc}")
                if text.strip():
                    pages.append(Page(index + 1, text=text, method="text"))
                continue
            pages.append(_image_page(index + 1, image, None, mode, warnings))
        return Document("pdf", pages, warnings)
    if kind == "image":
        from PIL import Image, ImageOps, UnidentifiedImageError

        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise UnsupportedDocument(f"The image could not be opened: {exc}") from exc
        frames = getattr(image, "n_frames", 1)
        if frames > 1:  # multi-page TIFF scans
            pages = []
            for index in range(min(frames, MAX_PDF_PAGES)):
                image.seek(index)
                pages.append(_image_page(index + 1, ImageOps.exif_transpose(image.copy()), None, mode, warnings))
            return Document("image", pages, warnings)
        image = ImageOps.exif_transpose(image)
        raw = data if (content_type or "").lower() in {"image/png", "image/jpeg", "image/webp"} else None
        return Document("image", [_image_page(1, image, raw, mode, warnings)], warnings)
    if kind == "schedule":
        raise UnsupportedDocument("This looks like a schedule (CSV/Excel). Use Schedule import instead of report upload.")
    raise UnsupportedDocument("Unsupported file type. Upload a .txt, .docx, .pdf or an image (PNG, JPEG, WebP, TIFF, BMP).")


MAX_PLAN_PAGES = 3


@dataclass
class PlanPage:
    number: int
    image: bytes          # JPEG for the vision model (downscaled later by qwen_service)
    text: str             # PDF text layer or full-resolution OCR text
    width: int
    height: int


def load_plan_pages(filename: str | None, content_type: str | None, data: bytes) -> tuple[list[PlanPage], list[str]]:
    """Site plans / drawings: every page as an image plus its text (text layer or OCR at full resolution)."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    kind = detect_kind(filename, content_type, data)
    warnings: list[str] = []
    images: list[tuple[int, Any, str]] = []
    if kind == "pdf":
        try:
            import pypdfium2 as pdfium
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data))
            pdf = pdfium.PdfDocument(data)
        except Exception as exc:
            raise UnsupportedDocument(f"The PDF could not be read: {exc}") from exc
        if len(pdf) > MAX_PLAN_PAGES:
            warnings.append(f"Only the first {MAX_PLAN_PAGES} of {len(pdf)} sheets were read.")
        for index in range(min(len(pdf), MAX_PLAN_PAGES)):
            try:
                layer = reader.pages[index].extract_text() or ""
            except Exception:
                layer = ""
            images.append((index + 1, pdf[index].render(scale=200 / 72).to_pil(), layer))
    elif kind == "image":
        try:
            image = Image.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, OSError) as exc:
            raise UnsupportedDocument(f"The image could not be opened: {exc}") from exc
        images.append((1, ImageOps.exif_transpose(image), ""))
    else:
        raise UnsupportedDocument("Upload the site plan as an image (PNG, JPEG, TIFF ...) or a PDF. "
                                  "AutoCAD .dwg/.dxf files must be exported to PDF or PNG first.")
    pages = []
    for number, image, layer in images:
        image = image.convert("RGB")
        text = layer if len(layer.strip()) >= MIN_PAGE_TEXT else ocr_labels(image)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=92)
        pages.append(PlanPage(number, buffer.getvalue(), text, image.width, image.height))
    return pages, warnings


def inspect_document(filename: str | None, content_type: str | None, data: bytes) -> dict:
    """Cheap readability check for /api/uploads/validate: no rendering, no OCR, no model."""
    kind = detect_kind(filename, content_type, data)
    if kind == "pdf":
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            texts = [(page.extract_text() or "") for page in reader.pages]
        except Exception as exc:
            raise UnsupportedDocument(f"The PDF could not be read: {exc}") from exc
        return {"kind": kind, "pages": len(texts), "text_pages": sum(len(t.strip()) >= MIN_PAGE_TEXT for t in texts)}
    if kind == "image":
        from PIL import Image, UnidentifiedImageError

        try:
            image = Image.open(io.BytesIO(data))
            image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise UnsupportedDocument(f"The image could not be opened: {exc}") from exc
        return {"kind": kind, "pages": getattr(image, "n_frames", 1), "text_pages": 0}
    doc = load_document(filename, content_type, data)  # text/docx: cheap; schedule/unknown raise
    return {"kind": kind, "pages": len(doc.pages), "text_pages": sum(bool(p.text.strip()) for p in doc.pages)}


def chunk_text_pages(pages: list[Page], max_chars: int | None = None) -> list[tuple[str, list[int]]]:
    """Group text pages into LLM-sized chunks, marking page numbers."""
    max_chars = max_chars or settings.max_text_chunk_chars
    chunks: list[tuple[str, list[int]]] = []
    current, numbers = "", []
    for page in pages:
        if page.method == "vision" or not page.text.strip():
            continue
        block = f"[Page {page.number}]\n{page.text.strip()}\n" if len(pages) > 1 else page.text.strip() + "\n"
        while len(block) > max_chars:  # very long page: split on line boundaries
            cut = block.rfind("\n", 0, max_chars)
            cut = cut if cut > max_chars // 2 else max_chars
            if current:
                chunks.append((current, numbers))
                current, numbers = "", []
            chunks.append((block[:cut], [page.number]))
            block = block[cut:]
        if len(current) + len(block) > max_chars and current:
            chunks.append((current, numbers))
            current, numbers = "", []
        current += block
        numbers.append(page.number)
    if current.strip():
        chunks.append((current, numbers))
    return chunks
