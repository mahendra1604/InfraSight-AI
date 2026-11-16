import pytest

from app import documents
from app.matching import ActivityMatcher, lexical_similarity
from tests.helpers import make_docx, make_image, make_text_pdf

ACTS = [("A100", "Site preparation"), ("A101", "Block A excavation"), ("A102", "Block A foundation concrete"),
        ("A103", "Ground floor columns"), ("A104", "Ground floor slab"), ("A105", "Electrical rough-in")]


@pytest.fixture(autouse=True)
def no_ocr(monkeypatch):
    monkeypatch.setenv("OCR_ENABLED", "false")
    monkeypatch.setenv("MATCHER_BACKEND", "lexical")


def test_text_files_decode_with_bom_and_cp1252():
    assert documents.load_document("r.txt", "text/plain", "﻿Progress 60%".encode("utf-8")).pages[0].text == "Progress 60%"
    assert "°" in documents.load_document("r.txt", "text/plain", "Temp 30°C".encode("cp1252")).pages[0].text


def test_docx_paragraphs_and_tables_are_read():
    doc = documents.load_document("r.docx", None, make_docx(["Block A foundation concrete 60%"]))
    assert doc.kind == "docx" and "60%" in doc.pages[0].text and "Electrical rough-in | 5%" in doc.pages[0].text


def test_pdf_text_layer_is_used():
    doc = documents.load_document("r.pdf", "application/pdf", make_text_pdf(["Activity A102 Block A foundation concrete",
                                                                             "Cumulative progress 60 percent on 2026-10-29"]))
    assert doc.pages[0].method == "text" and "A102" in doc.pages[0].text


def test_scanned_pdf_without_ocr_goes_to_vision():
    from PIL import Image
    import io

    buffer = io.BytesIO()
    Image.open(io.BytesIO(make_image("scanned"))).save(buffer, "PDF")
    doc = documents.load_document("scan.pdf", "application/pdf", buffer.getvalue())
    assert doc.pages[0].method == "vision" and doc.pages[0].image


def test_images_without_ocr_go_to_vision_and_tiff_is_accepted():
    assert documents.load_document("p.png", "image/png", make_image("x")).pages[0].method == "vision"
    assert documents.load_document("p.tif", "image/tiff", make_image("x", fmt="TIFF")).pages[0].method == "vision"


def test_schedules_and_unknown_files_are_rejected():
    with pytest.raises(documents.UnsupportedDocument, match="Schedule"):
        documents.load_document("s.csv", "text/csv", b"a,b")
    with pytest.raises(documents.UnsupportedDocument):
        documents.load_document("x.bin", "application/octet-stream", b"\x00\x01")


def test_chunking_respects_limit_and_page_markers():
    pages = [documents.Page(i, text=("line of text\n" * 300)) for i in range(1, 4)]
    chunks = documents.chunk_text_pages(pages, max_chars=2000)
    assert all(len(text) <= 2000 for text, _ in chunks)
    assert chunks[0][0].startswith("[Page 1]") and len(chunks) > 3


def test_evidence_is_located_on_the_right_page():
    from app.pipeline import _locate_page
    from app.qwen_service import merge_results

    doc = documents.Document("pdf", [documents.Page(1, text="Cover page"), documents.Page(2, text="Ground floor columns at 20%")])
    merged = merge_results([{"entries": [{"activity_text": "Ground floor columns", "evidence": "Ground floor columns at 20%"}],
                             "meta": {"duration_s": 1}}])
    assert "page" not in merged["entries"][0]                  # merge must not invent page numbers
    assert _locate_page(merged["entries"][0], doc) == 2
    assert _locate_page({"evidence": "not in the document"}, doc) is None


def test_plan_ocr_labels_keep_boxes_separate_with_positions(monkeypatch):
    from PIL import Image

    boxes = [(715, 22, 730, "180 m", 760), (700, 20, 100, "GATE", 120)]
    monkeypatch.setattr(documents, "_ocr_items", lambda image: (boxes, 0.99))
    text = documents.ocr_labels(Image.new("RGB", (2000, 1400)))
    assert text.splitlines() == ['"GATE" @ x=6% y=50%', '"180 m" @ x=38% y=51%']      # not glued into "GATE 180 m"


def test_prefix_words_match():
    assert lexical_similarity("Drain", "Drainage and stormwater lines") > lexical_similarity("Drain", "Site access road")
    assert ActivityMatcher(ACTS + [("A111", "Drainage and stormwater lines")]).match({"activity_text": "Drain drainage lines"}).activity_id == "A111"


def test_activity_id_wins_over_text():
    result = ActivityMatcher(ACTS).match({"activity_id": "a102", "activity_text": "something else"})
    assert (result.activity_id, result.status, result.score) == ("A102", "id_match", 1.0)


def test_id_inside_text_is_found_and_unknown_id_is_reported():
    matcher = ActivityMatcher(ACTS)
    assert matcher.match({"activity_text": "Columns (A103) progressing"}).activity_id == "A103"
    result = matcher.match({"activity_id": "Z999", "activity_text": "Ground floor slab"})
    assert result.activity_id == "A104" and any("Z999" in n for n in result.notes)


def test_semantic_match_statuses():
    matcher = ActivityMatcher(ACTS)
    assert matcher.match({"activity_text": "Ground floor slab"}).status == "suggested"
    assert matcher.match({"activity_text": "Crane hire invoice"}).status == "unmatched"
    candidates = matcher.match({"activity_text": "ground floor"}).candidates
    assert {c["activity_id"] for c in candidates[:2]} == {"A103", "A104"}


def test_invented_ids_are_dropped_but_ocr_lookalikes_kept():
    from app.pipeline import ground_activity_ids

    entries = [{"activity_id": "A101", "activity_text": "foundation concrete"},
               {"activity_id": "A102", "activity_text": "Block A foundation concrete"}]
    ground_activity_ids(entries, "Actlvity: Block A foundatlon concrete  ID A1O2  progress 75 %")
    assert entries[0]["activity_id"] is None and "not in the document" in entries[0]["warnings"][0]
    assert entries[1]["activity_id"] == "A102"                    # "A1O2" (letter O) in the OCR text


def test_id_that_contradicts_the_name_needs_mapping():
    result = ActivityMatcher(ACTS).match({"activity_id": "A101", "activity_text": "Ground floor slab"})
    assert (result.activity_id, result.status) == ("A104", "needs_mapping")
    assert "does not fit" in result.notes[-1]


def test_block_letters_are_distinguished():
    acts = ACTS + [("A110", "Block B foundation concrete")]
    result = ActivityMatcher(acts).match({"activity_text": "foundation concrete for Block A"})
    assert result.activity_id == "A102"
    assert lexical_similarity("Ground floor columns", "column work on the ground floor") > 0.8   # plural handling


def test_lexical_similarity_is_symmetric_and_bounded():
    assert lexical_similarity("Ground floor slab", "ground-floor SLAB") == 1.0
    assert 0 <= lexical_similarity("abc", "xyz") < 0.3
