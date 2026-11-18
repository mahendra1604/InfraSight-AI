# InfraSight AI — Project Analysis & LLM Remediation Plan

_Analysis date: 16 Nov 2026 · Scope: the whole repository, measured against the submitted report `InfraSight AI.docx`._

---

## 0. TL;DR

**Measured on this laptop:** the exact request the backend sends took **216.8 s**, which is over the app's 180 s timeout. It finished with **empty output**, because the model spent all 700 output tokens on hidden "thinking". With the current setup, the LLM cannot return a result, even for the clean three-activity sample report.

The LLM is not "broken" in one place. It fails because of **six problems stacking up**:

1. **Wrong model variant.** `qwen3-vl:8b` from the Ollama library is the **thinking** build (`renderer: qwen3-vl-thinking`, thinking on by default), and it **ignores `think: false`**. It is the slowest variant and is the wrong choice for strict JSON extraction.
2. **The laptop has no usable GPU.** Ollama drops the AMD Radeon iGPU and runs **CPU-only** (Ryzen 5 5600H, 15.4 GB RAM, only 3–7 GB free when Ollama starts). A 6.2 GB 8B vision model on this hardware takes minutes per image.
3. **The 180 s timeout is too short. A timeout is then retried and reported as "Ollama not running".** The code retries the *same* server under the alias `localhost`, which doubles the wait to ~6 min. The user then sees a misleading "Cannot connect to Ollama" or "Qwen3-VL is not running".
4. **The context window is 4,096 tokens, but one image can use up to ~4,096 tokens on its own.** This model build upscales every image to at least 1 MP and accepts up to 4 MP. A normal phone photo therefore overflows the context.
5. **Output is truncated and unconstrained.** `num_predict: 700` cuts off multi-activity JSON. `format: "json"` is used, while the carefully written `EXTRACTION_SCHEMA` is **defined but never sent**. `done_reason` is never checked.
6. **Most of the pipeline never reaches the LLM.** `.txt` and `.pdf` reports, the main inputs in your report, are only line-counted or page-counted. Image results from the UI are **never turned into review items**, because the UI calls `/api/ai/analyse-image`, which does not create reports.

**The fix, in short:**
- Switch to `qwen3-vl:4b-instruct` for images and add a small text model for text, PDF and OCR output.
- Downscale images, set `num_ctx`, send the JSON schema, validate the output with Pydantic, and handle timeouts correctly.
- Run analysis as a background job.
- Route every report type through *extract → match (Sentence Transformers) → review queue*. This is what the report's architecture describes.

Section 7 is the step-by-step plan.

---

## ✅ Implementation status — all phases done (16 Nov 2026)

The plan below has been implemented. Sections 1–3 describe the **original** state and are kept as the record of the diagnosis.

### LLM: before vs. after (same laptop, same sample report)

| | Before | After |
|---|---|---|
| Model | `qwen3-vl:8b` (thinking) for everything | `qwen3:4b-instruct-2507` for text/OCR · `qwen3-vl:4b-instruct` for photos |
| Sample report as text | not supported (only line-counted) | **47 s warm / 62 s cold**, 3/3 activities, progress, planned %, date, issues correct |
| Sample report as image | **216.8 s, empty output** (502 or "Ollama not running") | OCR 3.4 s + text model, **61 s** · or vision model **105 s**, 3/3 correct |
| Errors | timeout reported as "not running", duplicate retry | separate *offline / missing model / timeout / truncated / invalid JSON* messages, 1 retry on invalid JSON |
| Reaches review queue | never from the UI | every upload → background job → pending reports with match score |

### Golden-set evaluation (15 documents: 10 text, 2 PDF, 3 scans/photos · 37 expected activity values)

| Metric | v1 (first run) | v2 (after fixes) |
|---|---|---|
| Valid structured output | 100 % | 100 % |
| Activity precision / recall | 0.881 / 0.881 | **1.000 / 1.000** |
| Progress accuracy | 0.919 | **1.000** |
| Date accuracy | 0.946 | **1.000** |
| Latency p50 / p95 — text | 36.6 / 83.4 s | 35.3 / 76.4 s |
| Latency p50 / p95 — PDF | 44.8 / 73.1 s | 42.6 / 71.3 s |
| Latency p50 / p95 — scans/photos (OCR path) | 39.6 / 40.9 s | 48.8 / 49.4 s |

v1 failures came from two causes: (1) when a report had **no** activity codes, the 4B model sometimes numbered the entries itself (A101, A102, …) and the matcher trusted those IDs; (2) the model reformatted `09/11/2026` month-first. The fixes were: a stricter prompt; an ID **grounding check** (an ID is kept only if it is printed in the source text, tolerating OCR's O/0 and l/1); an ID-vs-name cross-check; dates copied verbatim and parsed day-first by code; and hybrid matching (embedding + token overlap, so "Block A" ≠ "Block B").

> **Caveat:** the fixes were designed after seeing the v1 failures on this same set, so 100 % is optimistic. Before quoting accuracy in the final report, build a fresh held-out set (ideally real, anonymised site reports) and evaluate once. Files: `ml/EVAL_RESULTS_v1.md`, `ml/EVAL_RESULTS.md`.

### Other measured results
- **Spark SQL job:** 2,000,004 report rows aggregated in 15.9 s (≈ 126 k rows/s), local mode, existing Spark 3.5.0 + Java 8.
- **Delay model (XGBoost, synthetic hold-out of 1,200 rows):** ROC-AUC 0.946, accuracy 0.869, F1 0.88, delay-days MAE 3.23.
- **Tests:** 112 unit/API tests pass. 3 live tests run against the real models with `pytest -m live` (text report, rendered report image, site plan).

### Site plans and quantity-based progress (added 16 Nov 2026)

When an engineer has only a **site plan** (layout drawing with printed dimensions), the system can now:
1. **Read the drawing:** the vision model plus full-resolution OCR labels, each with its position, return every *printed* dimension and the element it belongs to. The model never measures or calculates.
2. **Build the quantity take-off in code:** "L × W" labels become areas, segments of the same element are summed, and units are converted (km→m, ha→m², sqm→m²). Widths and heights are listed but never summed. A number printed once but claimed by two elements is resolved, or flagged for review.
3. **Apply quantities to schedule activities.** Matching is suggested and the manager confirms; every change is audited.
4. **Produce a draft schedule:** site preparation → drains/utilities and building foundations → superstructure, then roads and parking after the drains. Dates and durations stay blank for the team. It is merged with *Keep existing activities*.
5. **Report progress by quantity:** "180 m laid so far" gives cumulative progress; "laid 45 m today" is added to the approved quantity and flagged for review. "180 of 450 m" uses the report's total when the schedule has none. Unit mismatches are rejected with a reason.

Measured on `data/samples/site_plan.png` (synthetic 2000×1400 layout, `qwen3-vl:4b-instruct`, CPU):

| Run | Time | Totals correct (of 6) | Problem found → fix |
|---|---|---|---|
| Vision v1 | 286 s | 5 (road length assigned to "GATE") | OCR joined "GATE" and "180 m" into one line → labels now passed one per box with x/y position; prompt rule about point markers |
| OCR + text model | 108 s | 4 (D2 misread, road misassigned) | Layout is lost without the image → vision is the default; text mode stays as a fast fallback |
| Vision v2 | 333 s | **6 / 6** (plus one duplicate "GATE 180 m" row) | Duplicate claim of a once-printed value → `resolve_double_counting` drops it |
| Vision, end-to-end via API | 168 s | 5 (the "180 m" segment went *only* to GATE) | Not deterministic between runs → lengths on point markers (GATE, N, notes …) are flagged, left out of the draft, and the manager can **edit the total before applying** |
| Vision, final end-to-end | 271 s | **6 / 6**; the duplicate "GATE 180 m" was dropped automatically with an explanation | — |

**Honest summary:** the model reliably reads the printed labels. Attributing an unnamed dimension ("180 m") to the right element is the weak point of a 4B vision model; it went wrong in 2 of 4 runs on this drawing. That is why quantities are never applied automatically: the manager confirms (and can correct) each total, and every change is audited.

Quantity reports, real text model, final run: "laid 45 m of pipe today" → `quantity_today = 45` → 202.5 m approved + 45 m = 247.5 m = **55 %** (flagged for review, arithmetic shown). Before the prompt fix, the model copied 45 into the cumulative field as well, and code now also guards against that.

Activity suggestions after the matching fixes: road → A112 *Site access road* (0.81), drains → A111 *Drainage and stormwater lines* (0.78), plot → A100 *Site preparation*. Building areas → *needs mapping* (the manager chooses foundation, slab, etc.).

### Phase checklist

| Phase | Delivered | Where |
|---|---|---|
| 0 Environment | Instruct models pulled; Ollama env vars set (ctx 8192, keep-alive 30m, 1 model, flash attention) and Ollama restarted; `.env` loading; stray `backend/.venv` removed | `.env.example`, `backend/app/config.py` |
| 1 Reliable LLM call | JSON-schema output, `num_ctx`/`num_predict`/`keep_alive`, image downscaling, `done_reason` check, Pydantic-style validation (`"60%"`, 150 %, dates), retry, distinct errors | `backend/app/qwen_service.py` |
| 2 Every report type | txt, docx, PDF text layer, scanned PDF → render → OCR, images → OCR or vision, multi-page TIFF, chunking | `backend/app/documents.py` |
| 3 Matching + review | ID → Sentence Transformers top-3 → suggested / needs mapping / unmatched; duplicates and progress decreases skipped; reviewer can correct activity/progress; audit stores model + score | `matching.py`, `pipeline.py`, `main.py` |
| 4 Jobs + UI | `analysis_jobs` table, 202 + polling, one job at a time, restart recovery; new React app (8 screens), live stage + elapsed time, fixed `res.json()` bug | `pipeline.py`, `frontend/src/` |
| 5 Tests + evaluation | 71 unit/API tests (LLM mocked with recorded-style responses), 2 live tests (`pytest -m live`), 15-document golden set, evaluation script with v1/v2 results | `backend/tests/`, `data/eval/`, `ml/` |
| 6 Report modules | JWT auth + roles; schedule import (CSV/XLSX, P6/MSP headers, multi-predecessor, versions); NetworkX CPM + impact; XGBoost delay + risk; Excel/CSV export; Power BI views; Spark SQL job | `auth.py`, `schedules.py`, `analysis.py`, `prediction.py`, `exporting.py`, `pipelines/` |

---

## 1. What the report promises vs. what exists

The report (`InfraSight AI.docx`) defines 12 modules, a 9-step Project Manager user flow and a 6-member team. Current status:

| Report module | Report's technology | Current state in repo | Status |
|---|---|---|---|
| User Management | Login, roles | None. A "local demo reviewer" is hard-coded | ❌ Missing |
| Schedule Management | Primavera/MS Project/Excel, Spark SQL | CSV **validation only**, no import. Excel only opened and sheets counted | 🟡 Partial |
| Report Management | Upload daily reports, PDFs, scans, text | Upload endpoint classifies files. Nothing is persisted | 🟡 Partial |
| OCR Processing | Tesseract / PaddleOCR | None. Qwen3-VL is used in place of OCR, for images only | ❌ Missing |
| NLP Processing | NLP / LLM extraction | Qwen3-VL on images only. Text and PDF never extracted | 🟡 Broken (see §3) |
| Activity Matching | Sentence Transformers, FAISS/pgvector, confidence | `difflib.SequenceMatcher` character similarity, threshold 0.35 | 🟡 Weak |
| Progress Monitoring | Actual vs planned | Works (unweighted mean) | ✅ Works |
| Dependency Analysis | GraphX / NetworkX | One predecessor per activity, cycle check only | 🟡 Minimal |
| Delay Prediction | XGBoost / MLlib | None (UI says "planned") | ❌ Missing |
| Risk Analysis | Risk scoring | None | ❌ Missing |
| Dashboard | React + Power BI | React single component works. No Power BI | 🟡 Partial |
| Audit Trail | Source, confidence, history | Records approve/reject only. No confidence or model version | 🟡 Partial |
| Big-data layer | Apache Spark / Spark SQL | `pipelines/` is an empty README | ❌ Missing |

Other mismatches:
- `README.md` and `docs/team-plan.md` describe a **3-person team**. The report allocates work to **6 members**. Update the docs so they match the report.
- `ml/` and `pipelines/` contain only a README each.

---

## 2. How the LLM is wired today

```
Browser (frontend/src/main.jsx)
 ├─ image file ──► "Ask the project model" panel ──► POST /api/ai/analyse-image
 │                                                     └─ qwen_service.analyse_image()
 │                                                         └─ Ollama /api/chat (qwen3-vl:8b)
 │                                                     ◄─ JSON shown on screen ONLY
 │                                                        (no Report rows created → nothing to review)
 │
 └─ any other file ──► POST /api/uploads/validate
        ├─ .csv  → column/ID/progress checks
        ├─ .xlsx → sheet names + row counts
        ├─ .txt  → line count ("Extract fields and send them for review" — never happens)
        ├─ .pdf  → page count   ("…send rendered pages to Qwen" — never happens)
        └─ image → analyse_image() + SequenceMatcher → pending Reports
                   (UNREACHABLE from the UI: the UI sends all images to the panel above)
```

Files involved: [backend/app/qwen_service.py](backend/app/qwen_service.py), [backend/app/main.py](backend/app/main.py), [frontend/src/main.jsx](frontend/src/main.jsx).

---

## 3. Root-cause diagnosis (with evidence)

All measurements were taken on this machine on 16 Nov 2026. A probe sent the exact request that `qwen_service.analyse_image()` builds, using a 900×760 PNG rendering of `data/samples/qwen_test_report.txt`.

### Measured result: the current configuration cannot succeed

| Metric | Value | Meaning |
|---|---|---|
| Total wall time | **216.8 s** | Over the app's 180 s timeout, so the app gives up (and then retries, see R4) |
| Model load (cold) | 19.0 s | Happens again after every 5 idle minutes (no `keep_alive`) |
| Prompt + image processing | 72.7 s for 1,317 tokens | ~33 s of that is image encoding alone |
| Generation | 124.7 s for 700 tokens (≈ 5.6 tok/s) | CPU-bound |
| `done_reason` | **`length`** | Hit the `num_predict: 700` cap |
| Hidden "thinking" text | **2,680 characters** | `think: False` was **ignored** by this thinking build |
| `message.content` | **empty** | → `_normalise()` raises *"Qwen returned invalid JSON"* → HTTP 502 |

**Conclusion:** even with an unlimited timeout, the installed model spends its whole output budget thinking and returns nothing. Even for the clean three-activity test report, the current setup can only end in a timeout, which is misreported as "Ollama not running", or an empty or invalid JSON error.

### R1 — Wrong model variant (thinking model) · `qwen_service.py:15`
- `ollama show qwen3-vl:8b` reports `renderer qwen3-vl-thinking`, `parser qwen3-vl-thinking`, `thinking default: true`, `temperature 1`.
- The Ollama library tag `qwen3-vl:8b` **is** the thinking build. `qwen3-vl:8b-instruct`, `4b-instruct` and `2b-instruct` are separate tags.
- Thinking models reason before they answer. **Measured:** with `think: False` set, the model still produced 2,680 characters of thinking, used all 700 output tokens, and returned empty content. The flag is not honoured by this build. An instruct model is the correct tool for deterministic extraction.

### R2 — CPU-only inference and low free RAM
- Ollama server log: `dropping integrated GPU; to enable, set OLLAMA_IGPU_ENABLE=1` → `inference compute id=cpu`.
- Free RAM at Ollama start across the last 6 boots: **3.1, 3.9, 5.2, 5.8, 5.9, 7.2 GB**. The 8B model needs about 1.6 GB + 3.2 GB weight buffers + 0.6 GB KV cache ≈ **5.4 GB**. When free RAM is lower than that, Windows pages to disk and inference becomes very slow.
- Image encoding alone took **~33 s** for about 1,050 image tokens (3 batches: 15.3 s + 16.9 s + 1.3 s).

### R3 — Context window overflow for real photos
- Ollama log: `vram-based default context … default_num_ctx=4096`. The code never sets `num_ctx`.
- Model log: `image_min_pixels: 1048576 (custom value)`, `image_max_pixels: 4194304`. Every image becomes **≥ ~1,000 tokens** (32×32 px per token), and a ≥ 4 MP photo becomes **~4,096 tokens**. Add ~270 prompt tokens and the request no longer fits in 4,096.
- Uploads up to 10 MB are accepted and sent **at full resolution, with no resizing**.

### R4 — Timeout handling turns "slow" into "offline" · `qwen_service.py:103-118`
```python
except (httpx.HTTPError, OSError) as exc:   # ReadTimeout is an HTTPError
    last_error = exc                         # → loop tries http://localhost:11434 (same server!)
raise QwenUnavailable("Cannot connect to Ollama …")
```
- A `ReadTimeout` after 180 s is caught, and the **same request is sent again** to `localhost` (the same Ollama server), so the user waits another 180 s.
- The final error says *"Cannot connect to Ollama"*. In `/api/uploads/validate` it becomes *"Image is valid, but Qwen3-VL is not running"*. The message is wrong: Ollama was running but slow.
- Ollama runs with `OLLAMA_NUM_PARALLEL=1`, so the duplicate request queues behind the first one.

### R5 — Truncated, unconstrained output · `qwen_service.py:94-102`
- `num_predict: 700`. One entry with `evidence` + `box_2d` is about 80–120 tokens, so 6+ activities get cut off mid-JSON. The result is `ValueError: Qwen returned invalid JSON` → HTTP 502.
- `EXTRACTION_SCHEMA` (lines 17–41) is **never used**. The request sends `format: "json"`, which only guarantees *some* JSON object, not your fields. Ollama supports `format: <JSON schema>` (structured outputs). That should be used instead.
- `done_reason == "length"` (truncation) is never checked, so truncation is reported as "invalid JSON".
- `keep_alive` is not set. The model unloads after 5 idle minutes, and every new request pays the cold-load cost again.

### R6 — Contradictory prompt · `qwen_service.py:92`
- `PROMPT` asks for an `answer` string field. The appended instruction says *"Put the answer in the warnings array when it is explanatory"*. The UI renders `result.answer` as "MODEL RESPONSE", so answers end up in the wrong place.
- The schema has **no `activity_id` field**, although reports contain "Activity ID: A102". The model is told never to invent IDs but has nowhere to put the real one.

### R7 — Text and PDF reports never reach the LLM · `main.py:253-268`
- `.txt`, `.md` → counts lines. `.pdf` → counts pages. The test file `data/samples/qwen_test_report.txt`, which was clearly written to test Qwen, can **never** be analysed through the UI.
- Text is the cheapest input for an LLM. A text-only request skips the ~30–120 s image encoding completely.

### R8 — AI results never become review items from the UI · `main.jsx:34-35`, `main.py:76-89`
- The UI sends every `image/*` file to `/api/ai/analyse-image`, which **returns JSON but creates no `Report` rows**.
- The code that *does* create pending reports (the image branch of `/api/uploads/validate`) is unreachable from the UI.
- `.tif`/`.bmp` scans are `image/*` → they go to `analyse-image` → are rejected with **415**.

### R9 — Unsafe or weak post-processing · `main.py:274-290`
- Matching uses `SequenceMatcher` (character overlap) with a 0.35 threshold. This produces false matches ("Ground floor slab" vs "Ground floor columns") and misses synonyms. The report specifies **Sentence Transformers + confidence scores**.
- `float(progress)` crashes on `"60%"`. Progress is not range-checked: 150 % or -5 % create pending reports, because the AI path bypasses the `ReportInput` validation.
- `reporting_date` is not validated. A model output like `"29/10/2026"` is stored, and the review logic then compares dates **as strings**.
- No de-duplication: uploading the same file twice creates duplicate pending reports.
- No model name, model version, match score or confidence is stored on `Report` or `Audit`. The report's "Audit Trail: source, confidence and processing history" is not met.

### R10 — Operational gaps
- Tests (13/13 pass) **mock the model entirely**. Nothing ever checks that the real model returns usable output.
- `.env` is never loaded (no `python-dotenv`). Setting `QWEN_MODEL` in a `.env` file has no effect.
- The Docker `api` service cannot reach Ollama: `127.0.0.1` inside a container is the container itself, so it needs `host.docker.internal`.
- Frontend `request()` calls `res.json()` before it checks `res.ok`. A proxy timeout or HTML error therefore shows a cryptic JSON parse error.
- Analysis runs synchronously inside the HTTP request. A multi-minute LLM call keeps the browser request open, shows no progress, and the result is lost on refresh.

---

## 4. Target AI architecture (aligned with the report)

This follows the report's workflow: *Data Collection → Document Extraction → Text Analysis → Schedule-Report Alignment → Validation Decision → Storage → Analysis → Risk → Dashboard*.

```
Upload (txt / pdf / scan / photo / xlsx / csv)
   │
   ▼
[1] Ingest & de-duplicate ── sha256 → analysis_jobs table (queued)
   │
   ▼
[2] Document router (background job)
   ├─ .txt/.md/.docx ───────────────► raw text
   ├─ PDF with text layer (pypdf) ──► raw text
   ├─ scanned PDF / scan image ─────► OCR (Tesseract) ──► raw text   ← report: OCR module
   └─ site photo / plan / OCR failed ► Qwen3-VL (downscaled image)    ← vision fallback
   │
   ▼
[3] LLM extraction (Ollama, structured output = JSON schema, temperature 0)
   text model for text; VL model for images → Pydantic validation → retry once on failure
   │
   ▼
[4] Activity matching ── exact activity_id → Sentence-Transformers cosine top-3
   │                     → confidence = f(id match, cosine, needs_review)        ← report: NLP + Matching
   ▼
[5] Validation decision ── high confidence → "suggested" (pre-ticked)
   │                      low confidence  → "needs manual mapping"
   ▼
[6] Review queue (existing approve/reject) → progress update + audit
   (audit stores model, model version, match score, source file, page/line evidence)
   │
   ▼
[7] Analytics: NetworkX dependency graph → downstream impact
              XGBoost delay-risk model → dashboard (React; Power BI reads PostgreSQL)
```

The key design choice for this hardware: **OCR + a small text LLM is the primary path, and the vision model is the fallback**. Tesseract reads a scanned page in 1–3 s on CPU. A 4B text model extracts fields from that text much faster than a vision model can read the image. This also matches the report's Member 2 (OCR) and Member 3 (NLP) split.

---

## 5. Model selection for this laptop (CPU-only, 16 GB RAM)

Sizes are verified against the Ollama registry on 16 Nov 2026.

| Role | Recommended model | Download | Why |
|---|---|---|---|
| Image / site-plan vision | **`qwen3-vl:4b-instruct`** | 3.34 GB | Non-thinking, about half the RAM and time of 8B, fits in free RAM |
| Vision (if quality is not enough) | `qwen3-vl:8b-instruct` | 6.19 GB | Same family, no thinking. Close other apps first |
| Text / PDF / OCR extraction | **`qwen3:4b-instruct-2507-q4_K_M`** | 2.50 GB | Fast text-only extraction, strong JSON adherence |
| Embeddings (matching) | `sentence-transformers/all-MiniLM-L6-v2` | ~90 MB | The report specifies Sentence Transformers. Fast on CPU |
| Embeddings (no-torch alternative) | `nomic-embed-text` via Ollama | 0.27 GB | Use only if installing PyTorch is a problem |
| Remove | `qwen3-vl:8b` (thinking) | −6.19 GB | Wrong variant for extraction |

> Only one model fits in RAM at a time on this machine. With `OLLAMA_MAX_LOADED_MODELS=1`, Ollama swaps models automatically. Expect a 5–15 s load when it switches between the text and vision models.

---

## 6. Ollama configuration for this machine

Set these as **Windows user environment variables**, then quit and restart Ollama from the tray icon:

| Variable | Value | Purpose |
|---|---|---|
| `OLLAMA_CONTEXT_LENGTH` | `8192` | Room for image tokens (≤ ~2,000 after resize) plus prompt plus output |
| `OLLAMA_KEEP_ALIVE` | `30m` | Avoid cold reloads between demo steps |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | Never hold two models in 16 GB RAM |
| `OLLAMA_FLASH_ATTENTION` | `1` | Smaller KV cache |
| `OLLAMA_IGPU_ENABLE` | `1` (*experiment*) | Tries the Radeon iGPU via Vulkan. **Measure it.** Shared RAM can make it slower; turn it off if it is |

The backend also sends `num_ctx` and `keep_alive` per request, so it does not depend on these settings.

---

## 7. Step-by-step plan

Each phase ends with a check you can demonstrate. Owners map to the report's member allocation.

### Phase 0 — Environment (≈ 1 hour) · Member 1
- [ ] `ollama pull qwen3-vl:4b-instruct` and `ollama pull qwen3:4b-instruct-2507-q4_K_M`. Then `ollama rm qwen3-vl:8b` to free 6 GB of disk.
- [ ] Set the Ollama environment variables from §6 and restart Ollama.
- [ ] Before a demo, close browsers and other heavy apps. Check free RAM in Task Manager (target ≥ 6 GB).
- [ ] Add `python-dotenv`, `pillow` and `pypdfium2` to `backend/requirements.txt`, and load `.env` at startup.
- [ ] Update `.env.example`: `QWEN_MODEL=qwen3-vl:4b-instruct`, `TEXT_MODEL=qwen3:4b-instruct-2507-q4_K_M`, `OLLAMA_NUM_CTX=8192`, `OLLAMA_TIMEOUT=600`.
- [ ] Delete the empty `backend/.venv`. The working venv is the root `.venv`.
- **Done when:** `GET /api/ai/status` shows both models installed.

### Phase 1 — Make the LLM call reliable (≈ 1 day) · Member 3 · `backend/app/qwen_service.py`
- [ ] **Separate error types:** `ConnectError` → `QwenUnavailable` ("Ollama not running"). `TimeoutException` → new `QwenTimeout` ("model too slow; try a smaller image / model"). Show each message accurately in the API and UI.
- [ ] **Remove the localhost retry loop.** Use one host and an `httpx.Timeout(connect=5, read=OLLAMA_TIMEOUT, write=60, pool=5)`.
- [ ] **Send the schema:** `"format": EXTRACTION_SCHEMA`. Add `activity_id: string|null` and an optional `report_date`. Make `box_2d` optional (off by default to save tokens).
- [ ] **Options:** `temperature 0`, `num_ctx 8192`, `num_predict 2048`, `keep_alive "30m"`, `think false`.
- [ ] **Downscale images** with Pillow before sending: apply EXIF orientation, convert to RGB, limit the long side to 1600 px (≤ ~2 MP ≈ ≤ 2,000 tokens), re-encode as JPEG q90. Accept TIFF/BMP by converting them.
- [ ] **Check `done_reason`.** If it is `"length"`, raise a clear "output truncated" error.
- [ ] **Validate with Pydantic** (`ExtractionResult`, `ExtractedEntry`): progress `0–100` (strip `%`), dates parsed to `date` or `None`, unknown fields dropped. On a validation failure, retry once with the error message appended to the prompt.
- [ ] **Fix the prompt:** one clear contract, with `answer` holding the explanation. Delete the "put the answer in warnings" sentence.
- [ ] Add `analyse_text(text)`, which uses `TEXT_MODEL` with the same schema and validation.
- [ ] Return metadata: `model`, `duration_s`, `prompt_tokens`, `output_tokens`.
- **Done when:** the sample report (as text and as a PNG) returns all 3 activities with the correct progress, ID and issue, and no 502/503 errors in 5 consecutive runs.

### Phase 2 — Route every report type to extraction (≈ 1–2 days) · Member 2
- [ ] New module `backend/app/documents.py`: `extract_text(file) -> (text, pages, method)`.
  - txt/md: decode. docx: `python-docx`. PDF: `pypdf` text per page.
  - If a PDF page has fewer than ~50 characters, it is scanned: render it with `pypdfium2` at 200 dpi → OCR.
  - OCR: `pytesseract` + [Tesseract for Windows (UB Mannheim build)](https://github.com/UB-Mannheim/tesseract/wiki). If the OCR text is empty or too short, fall back to `analyse_image()` on the rendered page.
- [ ] Wire `/api/uploads/validate`, or better a new `/api/reports/upload`, so that text and PDF go through `analyse_text()` and photos go through `analyse_image()`.
- [ ] Store the evidence location (`page`, `line` / snippet) on each extracted entry.
- **Done when:** uploading `qwen_test_report.txt`, a PDF of it and a scanned PNG of it all produce the same 3 entries.

### Phase 3 — Matching, validation decision and review queue (≈ 2 days) · Member 3
- [ ] New module `backend/app/matching.py`:
  1. If `activity_id` was extracted and exists in the schedule → match with confidence 1.0.
  2. Otherwise, compute the Sentence-Transformers (`all-MiniLM-L6-v2`) cosine similarity between the extracted text and each `activity.name` (cache the activity embeddings). Return the top 3 candidates with scores.
  3. Confidence = cosine adjusted by `needs_review` and the date/progress sanity checks.
- [ ] **Validation decision** (report §2): `confidence ≥ 0.80` → status `suggested` (pre-selected match). Otherwise → `needs_mapping` (the reviewer picks from the top 3). Progress changes **only** after a human approves.
- [ ] Extend the `Report` model with `match_score`, `match_candidates` (JSON), `model`, `extraction_json`, `file_sha256`, `evidence_page`. Add the same fields to `Audit.details`. Use Alembic, or recreate the dev database, for these schema changes.
- [ ] Reject progress values that decrease or exceed 100 % at creation time. Mark them as warnings rather than silently dropping them.
- [ ] De-duplicate by `file_sha256` + `activity_id` + `reporting_date`.
- [ ] Make `/api/ai/analyse-image` (the "Ask" panel) **create pending reports too**, or offer a "Send to review" button on the result card.
- **Done when:** the uploaded sample creates 3 pending reports (A102 60 %, A103 20 %, A104 0 %) with scores. Approving A102 moves the dashboard to 60 % and records the model and score in the audit log.

### Phase 4 — Background jobs and UI feedback (≈ 1 day) · Member 6
- [ ] Add an `analysis_jobs` table (`id, filename, sha256, status queued|running|done|failed, result_json, error, model, started_at, finished_at`).
- [ ] `POST /api/reports/upload` → saves the file under `uploads/` (gitignored), creates the job, starts `BackgroundTasks` and returns `202 {job_id}`.
- [ ] `GET /api/jobs/{id}` → status and result. Allow one running job at a time, which matches `OLLAMA_NUM_PARALLEL=1`.
- [ ] UI: poll every 2 s and show "Reading document → Extracting with Qwen → Matching activities" with elapsed time. On completion, jump to *Report review*.
- [ ] Fix `request()` in `main.jsx` to check `res.ok` and the content type before calling `res.json()`.
- [ ] Show `AI status` details: whether each model is installed or loaded, and free RAM if possible (`/api/ps`).
- **Done when:** a 3-minute image job survives a page refresh, and the UI never shows a JSON parse error.

### Phase 5 — Testing and evaluation (≈ 1–2 days) · Members 3 + 5
- [ ] Unit tests with **recorded real Ollama responses** (fixtures) for these cases: truncated output, `"60%"` strings, bad dates, extra prose, empty entries, timeout vs connection refused.
- [ ] A live smoke test, `pytest -m live`, skipped when Ollama is down. It runs the sample through the real model.
- [ ] **Golden set:** `data/eval/` with 15–20 synthetic reports (clean text, PDF, scanned image, phone photo, handwritten-style, ambiguous names, missing IDs) and an expected JSON file for each.
- [ ] `ml/eval_extraction.py` reports: JSON-valid rate, activity-match accuracy, progress exact-match, date exact-match, and p50/p95 latency per input type.
- [ ] **Targets:** JSON validity 100 % · match accuracy ≥ 90 % · progress accuracy ≥ 95 % on clean inputs · text path p95 < 60 s · image path p95 < 240 s on this laptop. Put the measured results in the final report. Examiners value real numbers.

### Phase 6 — Remaining report modules (after the LLM path is stable)
| Module | Owner | First deliverable |
|---|---|---|
| Schedule import | M1 | Persist the CSV/XLSX import with `project_id` + `baseline_id`, multiple predecessors with types (FS/SS/FF) and durations |
| Dependency analysis | M4 | NetworkX DAG: critical path, successors of a delayed activity, downstream-impact endpoint, graph view in the UI |
| Delay prediction | M5 | Synthetic historical dataset → features from the report's list → XGBoost classifier (delay yes/no) + regressor (days). `/api/predictions` with model version and evaluation metrics |
| Risk analysis | M5 | Risk score = P(delay) × downstream-impact weight. Top-N risky activities on the dashboard |
| User management | M6 | JWT login, roles (PM / site engineer / admin), reviewer identity in the audit trail |
| Spark layer | M1 | One bounded PySpark batch job (schedule + reports → progress aggregates), with measured runtime |
| Power BI | M6 | Connect to PostgreSQL views (`v_activity_progress`, `v_risk`) |
| Report generation | M6 | Export a PDF/XLSX project report (user-flow step 9) |

---

## 8. Reference implementation sketch — new `analyse_*` core

```python
# backend/app/qwen_service.py (sketch — Phase 1)
import base64, io, os, re
import httpx
from PIL import Image, ImageOps
from pydantic import BaseModel, Field, ValidationError, field_validator
from datetime import date

OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
VISION_MODEL = os.getenv("QWEN_MODEL", "qwen3-vl:4b-instruct")
TEXT_MODEL   = os.getenv("TEXT_MODEL", "qwen3:4b-instruct-2507-q4_K_M")
NUM_CTX      = int(os.getenv("OLLAMA_NUM_CTX", "8192"))
TIMEOUT      = float(os.getenv("OLLAMA_TIMEOUT", "600"))

class QwenUnavailable(RuntimeError): ...
class QwenTimeout(RuntimeError): ...

class Entry(BaseModel):
    activity_id: str | None = None
    activity_text: str
    progress: float | None = Field(default=None, ge=0, le=100)
    reporting_date: date | None = None
    issue: str | None = None
    evidence: str = ""
    needs_review: bool = True

    @field_validator("progress", mode="before")
    @classmethod
    def strip_percent(cls, v):
        if isinstance(v, str):
            m = re.search(r"-?\d+(\.\d+)?", v)
            return float(m.group()) if m else None
        return v

class Extraction(BaseModel):
    document_type: str = "unknown"
    report_date: date | None = None
    answer: str = ""
    entries: list[Entry] = []
    warnings: list[str] = []

SCHEMA = Extraction.model_json_schema()

def prepare_image(data: bytes, max_side: int = 1600) -> str:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()

async def _chat(model: str, content: str, images: list[str] | None = None) -> Extraction:
    msg = {"role": "user", "content": content, **({"images": images} if images else {})}
    req = {"model": model, "stream": False, "think": False, "keep_alive": "30m",
           "format": SCHEMA, "messages": [msg],
           "options": {"temperature": 0, "num_ctx": NUM_CTX, "num_predict": 2048}}
    timeout = httpx.Timeout(connect=5, read=TIMEOUT, write=60, pool=5)
    try:
        async with httpx.AsyncClient(base_url=OLLAMA_URL, timeout=timeout, trust_env=False) as c:
            r = await c.post("/api/chat", json=req)
    except httpx.ConnectError as e:
        raise QwenUnavailable(f"Ollama is not running at {OLLAMA_URL}") from e
    except httpx.TimeoutException as e:
        raise QwenTimeout(f"{model} did not finish within {TIMEOUT:.0f}s") from e
    if r.status_code == 404:
        raise QwenUnavailable(f"Model {model} is not installed: run `ollama pull {model}`")
    r.raise_for_status()
    data = r.json()
    if data.get("done_reason") == "length":
        raise ValueError("Model output was truncated; increase num_predict or reduce input")
    return Extraction.model_validate_json(data["message"]["content"])
```

---

## 9. Verification checklist (demo-day)

- [ ] `ollama ps` shows the expected model. `GET /api/ai/status` → `available: true`, both models installed.
- [ ] Upload `data/samples/qwen_test_report.txt` → 3 pending reports in < 60 s.
- [ ] Upload the same report as a PDF and as a PNG → same 3 entries.
- [ ] Approve A102 → Overview shows 60 % / −20 pp. The audit log shows model, score and source file.
- [ ] Upload the same file again → reported as a duplicate, no new reports.
- [ ] Stop Ollama → the UI says "Ollama not running" (not a JSON error). Restart → it works again.
- [ ] `pytest` passes (unit), and `pytest -m live` passes with Ollama running.
- [ ] Evaluation table from `ml/eval_extraction.py` is pasted into the final report.

---

## 10. Repository housekeeping

- Add a `.gitignore`. **3,842 `frontend/node_modules` files**, `__pycache__/*.pyc`, `backend/.test-tmp/*.db`, `backend/infrasight.db` and `frontend/dist/` are committed. Ignore them and run `git rm -r --cached` on them.
- Remove the test scratch folders `backend/.pytest-qwen-only`, `.pytest-qwen-run` and `.pytest-upload-check`.
- `outputs/…/Music_Genre_Classification_Dashboard*.xlsx` is unrelated to InfraSight. Move it out of the repository.
- Split `backend/app/main.py` (≈ 300 lines, everything in one module) into `models.py`, `routes/`, `services/` before Phase 3, as `docs/architecture.md` already recommends.
- Add `OLLAMA_URL: http://host.docker.internal:11434` to the `api` service in `docker-compose.yml`.
