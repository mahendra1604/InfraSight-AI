# Architecture

```
Browser (React + Vite, frontend/src)
   │  /api (Vite dev proxy → 127.0.0.1:8000), JWT bearer token
   ▼
FastAPI (backend/app/main.py)
   ├─ auth.py         login, JWT, roles (viewer < engineer < manager < admin)
   ├─ schedules.py    CSV/XLSX schedule validation + import (baseline versions)
   ├─ pipeline.py     background analysis jobs (one at a time)
   │    ├─ documents.py   txt/docx/pdf/image → text pages; scanned pages → OCR (RapidOCR, PaddleOCR models)
   │    ├─ qwen_service.py Ollama client: text model for text/OCR, vision model for photos; JSON schema output
   │    └─ matching.py    activity ID → Sentence-Transformers cosine → top-3 + validation decision
   ├─ analysis.py     NetworkX dependency graph, CPM (float, critical path), downstream impact
   ├─ prediction.py   XGBoost delay classifier + regressor, risk score, per-prediction factors
   ├─ exporting.py    Excel project report, CSV feeds
   └─ db.py           SQLAlchemy models, forward-only column migration, Power BI views, seed data
   ▼
SQLite (default, backend/infrasight.db) or PostgreSQL (docker compose)

Ollama (local, 127.0.0.1:11434)
   qwen3:4b-instruct-2507-q4_K_M  — text / OCR extraction
   qwen3-vl:4b-instruct           — images, site photos, drawings

pipelines/spark_progress_job.py — Spark SQL batch KPIs from the same database
ml/                             — golden-set generator, extraction evaluation, delay-model training
```

## Report pipeline

1. **Upload** (`POST /api/reports/upload`) stores the file under `backend/uploads/<sha256>` and creates an `analysis_jobs` row. The same file with the same question is not processed twice.
2. **Read** — text layer first; a PDF page with < 40 characters is rendered and OCR'd; an image is OCR'd and goes to the vision model only when OCR finds < 60 characters (or when the user picks *Vision model*).
3. **Extract** — Ollama with `format: <JSON schema>`, `temperature 0`, `num_ctx 8192`, `num_predict 2048`, `keep_alive 30m`. Output is validated (percentages 0–100, ISO dates, IDs never invented). Invalid JSON is retried once; truncation, timeouts, missing models and an offline Ollama are reported as different errors.
4. **Match** — written activity ID → exact match; otherwise Sentence-Transformers cosine similarity against activity names. ≥ 0.80 and unambiguous → *suggested*; ≥ 0.40 → *needs mapping*; lower → not turned into a report.
5. **Guard** — no progress value, duplicates (same activity, date, progress) and decreasing cumulative progress are skipped with a reason.
6. **Review** — a manager approves, corrects (activity / progress) or rejects. Approval updates actual progress and writes an audit entry with the reviewer, model, match method and score.

## Design decisions

- **CPU-first.** The development laptop has no usable GPU. OCR + a 4B text model is much faster than a vision model, so vision is the fallback, not the default.
- **Human in the loop.** AI output never changes progress directly.
- **Instruct models only.** Thinking variants spend the output budget on hidden reasoning and return empty JSON (measured: 216 s, empty content).
- **Synthetic ML data is labelled as such** everywhere it appears (API, UI, export).
- **Schema changes** use a small forward-only migration (`db._add_missing_columns`). Adopt Alembic before renaming or dropping columns.

## Known limits

Single demo project; finish-to-start links without lag; planned progress values are baseline fixtures, not computed from dates; delay model trained on synthetic data; Spark job is a bounded local batch, not a cluster deployment.
