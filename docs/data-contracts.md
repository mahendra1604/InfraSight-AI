# Data contracts

The live contract is at `http://127.0.0.1:8000/docs` (OpenAPI). All routes are under `/api`. Every route except `/health`, `/auth/config` and `/auth/login` needs `Authorization: Bearer <token>` unless `AUTH_ENABLED=false`.

## Auth
- `POST /auth/login` `{username, password}` → `{token, user}` · `GET /auth/me` · `GET /auth/config`
- Roles: viewer (read) < engineer (upload, manual report, ask model) < manager (review, schedule import) < admin (retrain)

## Project data
- `GET /project`, `GET /summary`, `GET /activities` (adds `predecessors[]`, `planned_start`, `planned_finish`, `duration_days`, `wbs`)
- `GET /reports` — each report includes AI provenance: `issue`, `extracted_activity`, `match_score`, `match_method`, `match_status` (`id_match|suggested|needs_mapping|manual`), `candidates[]`, `model`, `job_id`, `evidence_page`, `needs_review`, `warnings[]`
- `POST /reports` `{activity_id, text, progress, reporting_date}` → pending manual report
- `POST /reports/{id}/review` `{action: approve|reject, activity_id?, progress?, note?}` → `{status, corrections[]}`
- `GET /audit` — `{report_id, action, details, actor, created_at}`

## AI pipeline
- `POST /reports/upload` multipart `file`, `instruction?`, `mode=auto|text|vision`, `force?` → **202** job (or the existing job with `duplicate: true`)
- `GET /jobs`, `GET /jobs/{id}` → `status queued|running|done|failed`, `stage`, `method text|ocr|vision`, `model`, `error`, `result {document, extraction, created_reports[], skipped[]}`
- `POST /ai/analyse-image` — synchronous single-image question (no reports created). 503 Ollama down/model missing · 504 timeout · 502 bad output
- `GET /ai/status` — Ollama availability, installed/loaded models, warnings

### Extraction entry (model output after validation)
```json
{"activity_id": "A102 | null", "activity_text": "Block A foundation concrete", "progress": 60.0,
 "planned_progress": 80.0, "reporting_date": "2026-10-29", "issue": "Rain ...", "evidence": "Cumulative progress: 60%",
 "needs_review": false, "warnings": [], "page": 1,
 "match": {"activity_id": "A102", "score": 1.0, "method": "activity_id", "status": "id_match", "candidates": [...]}}
```
Missing values are `null`, never invented. Percentages outside 0–100 and unreadable dates become `null` with a warning.

## Site plans and quantities
- `POST /plans/upload` multipart `file` (image or PDF), `instruction?`, `mode=vision|text`, `force?` → **202** job with `purpose: "plan"`. Poll `GET /jobs/{id}`; list with `GET /jobs?purpose=plan`.
- Result: `plan {drawing_title, scale, drawing_date, answer, notes[], elements[]}`, `takeoff {totals[], category_totals[], other_dimensions[]}`, `draft_schedule[]`.
  - element (what the model read): `{name, category, dimension_type (length|width|area|volume|count|height|level|length_x_width|other), value, value2, unit, label_text, needs_review}`
  - total (calculated in code): `{name, category, measure, total, unit, parts, labels[], needs_review, suggestion {activity_id, score, status, current_quantity, unit_compatible}, candidates[]}`
- `GET /plans/{job_id}/draft-schedule.csv` (importable; dates/durations blank) · `GET /plans/{job_id}/takeoff.csv`
- `POST /activities/{id}/quantity` `{total_quantity, unit, source?}` (manager) → audited as `set_quantity`
- Reports by quantity: extraction entries carry `quantity_done` (cumulative), `quantity_today` (this period), `quantity_total`, `quantity_unit`. Progress = cumulative ÷ activity total (unit-converted); "today" quantities are added to the approved quantity and flagged for review. `POST /reports` accepts `quantity_done` instead of `progress`.
- Activities expose `total_quantity`, `unit`, `actual_quantity`; reports expose `quantity_done`, `quantity_unit`.

## Schedules
- `POST /schedules/validate` / `POST /schedules/import` — CSV (UTF-8) or XLSX. Required: activity_id, name, planned_progress, actual_progress. Optional: predecessor (`;`-separated), planned_start, planned_finish, duration_days, wbs, total_quantity + unit. MS Project / P6 / BOQ headers are mapped. Form field `keep_missing=true` merges (predecessors may refer to existing activities) instead of replacing the baseline.
- `GET /schedules/versions`

## Analysis, prediction, export
- `GET /analysis/dependencies` — nodes with ES/EF/LS/LF, total float, critical flag, downstream list; `critical_path`, `bottlenecks`
- `GET /analysis/impact/{activity_id}?delay_days=N` — affected successors and project delay
- `GET /predictions` — per activity `delay_probability`, `expected_delay_days`, `risk_score`, `risk_level`, `factors[]`, plus model version and hold-out metrics · `POST /predictions/train` (admin)
- `GET /export/report.xlsx` · `GET /export/{activities|reports|predictions|audit|jobs}.csv`
- Database views for Power BI: `v_activity_progress`, `v_report_pipeline`, `v_risk`

Errors: 401 sign in · 403 role · 404 not found · 409 review conflict · 413 too large · 415 unsupported type · 422 validation.
