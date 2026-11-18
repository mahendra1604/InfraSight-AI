# InfraSight AI

Infrastructure project progress monitoring: daily site reports (text, Word, PDF, scans, photos) are read by a **local LLM**, matched to schedule activities with **Sentence Transformers**, verified by a project manager, and turned into progress, **critical-path / downstream-impact analysis** and **XGBoost delay-risk predictions**. Everything runs on a laptop; no data leaves the machine.

See `InfraSight AI.docx` for the project report, `PROJECT_ANALYSIS_AND_LLM_PLAN.md` for the LLM diagnosis and plan, and `docs/` for architecture, API contracts and the six-member team plan.

## Setup (Windows, PowerShell, from the project folder)

Requirements: Python 3.13, Node.js 24, [Ollama](https://ollama.com/download/windows). Optional: Java 8+ for the Spark job.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt

# Local models (instruct = no hidden "thinking"; ~5.8 GB total)
ollama pull qwen3:4b-instruct-2507-q4_K_M
ollama pull qwen3-vl:4b-instruct
```

Recommended Ollama settings for a CPU-only laptop. Set them once, then quit and restart Ollama from the tray:

```powershell
setx OLLAMA_CONTEXT_LENGTH 8192; setx OLLAMA_KEEP_ALIVE 30m; setx OLLAMA_MAX_LOADED_MODELS 1; setx OLLAMA_FLASH_ATTENTION 1
```

Start the API and the web app in two terminals:

```powershell
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```
```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Open http://localhost:5173 and sign in with a demo account shown on the login page: `admin/admin123`, `manager/manager123`, `engineer/engineer123` or `viewer/viewer123`. API docs: http://127.0.0.1:8000/docs. Copy `.env.example` to `.env` to change settings (loaded automatically). `AUTH_ENABLED=false` skips login.

The first start migrates an existing `backend/infrasight.db` in place (new columns and tables, existing data kept). It also downloads the 90 MB matching model on the first analysis and trains the delay model on the first risk request (a few seconds).

## Try the complete flow

1. **Analyse reports** → upload `data/samples/qwen_test_report.txt` → watch *Reading → Extracting → Matching* (about 50–70 s on CPU) → 3 suggestions are created.
2. Upload the same report as an image (any screenshot of it) → OCR reads it in ~3 s; the values are recognised as duplicates.
3. **Report review** → approve A102 at 60 % (or correct the activity/progress first). The audit trail records the reviewer, model, match method and score.
4. **Dependencies** → critical path and float; simulate a 7-day slip of A103 and see the project finish move.
5. **Delay risk** → probability of delay, expected slip and main factors per activity; download the Excel project report.
6. **Schedule** → validate and import `data/samples/schedule_extended.csv` (17 activities, multiple predecessors, quantities).
7. **Site plans** → upload `data/samples/site_plan.png` → the model reads the printed dimensions; code builds the quantity take-off (drains 450 m, road 300 m, Block A 600 m² …). Apply a quantity to an activity, download the draft schedule, and import it with *Keep existing activities* ticked.
8. **Report by quantity** → in *Report review → Add a manual report* choose *By quantity* (e.g. 252 m³ of 420 m³ → 60 %), or upload a diary saying "laid 45 m of drain today": the system converts it to cumulative progress.
9. **3D views** → the Overview shows the site in 3D: buildings rise floor by floor to their approved progress, roads and drains fill along their length, high-risk elements pulse red; click an element for its activities. On **Dependencies**, the 3D graph glows along the critical path; *Simulate* spreads a delay through it in red. Share a what-if with a link such as `#/dependencies?simulate=A102&days=10`. The header's **Motion on/off** switch turns all animation off (it also follows the Windows "reduce animations" setting), and 3D pauses automatically while an AI job is running.

## What is implemented (report modules)

| Module (report) | Implementation | Status |
|---|---|---|
| User management | JWT login, 4 roles, reviewer identity in the audit trail | ✅ |
| Schedule management | CSV/XLSX import, P6/MS Project header mapping, multiple predecessors, baseline versions | ✅ (single project) |
| Report management | Upload txt/docx/pdf/png/jpg/webp/tiff/bmp, stored by SHA-256, background jobs, duplicate detection | ✅ |
| OCR processing | RapidOCR (PaddleOCR models, ONNX Runtime) for scans and scanned PDF pages | ✅ |
| NLP processing | Ollama `qwen3:4b-instruct` (text/OCR) and `qwen3-vl:4b-instruct` (photos), JSON-schema output, validation, retry | ✅ |
| Activity matching | ID match → Sentence Transformers (all-MiniLM-L6-v2) top-3, validation decision | ✅ |
| Progress monitoring | Planned vs actual, variance, human-approved cumulative progress; progress by % **or by quantity** (m, m², m³, nos … with unit conversion) | ✅ |
| Site plans (drawings) | Vision + OCR reads printed dimensions; code computes the quantity take-off; apply quantities to activities; draft schedule CSV | ✅ |
| Dependency analysis | NetworkX DAG, CPM (ES/EF/LS/LF, float), critical path, bottlenecks, downstream impact simulation | ✅ (FS links) |
| Delay prediction / Risk | XGBoost classifier + regressor, per-prediction factors, risk score | ✅ (synthetic training data) |
| Dashboard | React app (9 screens), one design system (dark/light), shared 3D background world, fully animated (Motion) · **3D site model** ("digital twin lite") and **3D dependency graph** (React Three Fiber) · Power BI via `v_activity_progress`, `v_report_pipeline`, `v_risk` views and CSV exports | ✅ / Power BI file to build |
| Audit trail | Reviewer, time, model, match method/score, corrections, notes | ✅ |
| Big data | Spark SQL batch KPI job with throughput test (`pipelines/`) | ✅ (local mode) |
| Report generation | Excel project report (summary, activities, risk, reports, audit) | ✅ |

## Look and feel

- **One design system** (`frontend/src/design.css`): dark *Blueprint Command Center* by default, plus a light theme. Switch with the **Dark/Light** button in the header (or open the app with `?theme=light`).
- **One 3D world behind every screen**: the camera moves per screen (aerial on Overview, top-down blueprint on Site plans, along a time tunnel on Schedule, above a network on Dependencies, red-tinted on Delay risk, a scanning beam on Analyse reports). Hovering a report in *Report review* lights up its building.
- **In-page 3D**: digital twin (Overview, Site plans "Plan → 3D"), progress skyline (Activities), 3D dependency graph and what-if (Dependencies), risk landscape (Delay risk), 3D Gantt (Schedule).
- **Present** button: hides the panels for an automatic camera tour of the project, or free camera; Esc to exit. Good for the examiners' demo.
- **Motion on/off** turns every animation off (also follows the Windows "reduce animations" setting). The background renders at 30 fps and lower resolution, and all 3D pauses while an AI job is running.

## Measured results

All measured on a CPU-only laptop (Ryzen 5 5600H, 16 GB RAM):

- **LLM before vs. after:** 216.8 s with empty output → 35–55 s per typical report with all fields correct (`PROJECT_ANALYSIS_AND_LLM_PLAN.md`).
- **Golden set (15 docs, 37 values):** v1 precision/recall 0.881, progress accuracy 0.919. v2 after fixes: 1.0 on all metrics, p50 ≈ 35–49 s (`ml/EVAL_RESULTS.md`). v2 is optimistic because the fixes were tuned on the same set; use a fresh held-out set for the final report.
- **Delay model (synthetic hold-out):** ROC-AUC 0.946, F1 0.88, MAE 3.2 days.
- **Spark SQL:** 2 million report rows aggregated in 15.9 s (≈ 126 k rows/s, local mode).
- **Site plan take-off** (`data/samples/site_plan.png`, vision model, 3–5 min on CPU): printed labels read correctly in every run; 6/6 totals in 2 of 4 runs. The weak point is assigning an unnamed dimension ("180 m") to the right element, so quantities are always confirmed (and editable) by a manager before they are applied.

## Tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest            # 112 unit + API tests (LLM mocked), ~40 s
..\.venv\Scripts\python.exe -m pytest -m live    # 3 tests with the real Ollama models, ~8 min
cd ..\frontend
npm.cmd run build
```

Evaluation and training: see `ml/README.md`. Spark: see `pipelines/README.md`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Ollama is not running" | Start the Ollama app; check `http://127.0.0.1:11434/api/version`. |
| "Model … is not installed" | Run the `ollama pull` command shown in the message. |
| "did not finish within 600 s" | The model is running but too slow: close other apps (needs ~5 GB free RAM), use *Automatic* mode, raise `OLLAMA_TIMEOUT`. |
| Empty answer / "only hidden reasoning" | A thinking model is configured. Use the `-instruct` tags above (`qwen3-vl:8b` is a thinking model). |
| "output was cut off" | Very long page: split the document or raise `OLLAMA_NUM_PREDICT`. |
| pytest `PermissionError` on Temp | Already handled by `backend/pytest.ini` (`--basetemp=.pytest-tmp`). |
| 3D view is slow or blank | Turn **Motion off** in the header (static 3D), update the graphics driver, or use the 2D graph button. 3D needs WebGL (any modern browser). |
| Backend on another port | `$env:API_URL = 'http://127.0.0.1:8010'; npm.cmd run dev -- --port 5180` |

## Docker (optional)

`docker compose up --build` starts PostgreSQL and the API. Ollama keeps running on the host and is reached at `host.docker.internal:11434`. Run the frontend normally. Do not run both API alternatives on port 8000.

## Limits

One demo project; finish-to-start links without lag; planned percentages are baseline fixtures; the delay model is trained on a documented synthetic dataset until real project history is available. AI output is decision support and always requires review.
