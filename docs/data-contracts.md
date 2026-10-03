# Starter data contracts

The live contract is available at `/docs` and `/openapi.json` when the API runs. Current routes are under `/api`. This is one fixed demo project; project-scoped resources are the next schema change.

- `GET /health`: API status and version.
- `GET /project`: demo metadata and baseline date.
- `GET /activities`: ID, name, planned/actual percentages, variance in percentage points, one predecessor ID.
- `GET /summary`: counts and unweighted planned/actual means.
- `GET /reports`: proposed cumulative progress with source, ISO reporting date, and pending/approved/rejected state.
- `POST /reports`: `{activity_id, text, progress, reporting_date}` creates a pending manual report.
- `POST /reports/{id}/review`: `{action: "approve" | "reject"}` performs a single review and writes an audit entry.
- `GET /audit`: recorded review decisions.
- `POST /schedules/validate`: multipart `file`, UTF-8 CSV up to 1 MB, validation only. Columns match `data/samples/schedule.csv`.

Errors use HTTP 404 for absent resources, 409 for review conflicts, 413 for oversized files, and 422 for validation failures.

## Proposed ML integration contract

Agree before implementation: extraction should return report ID, source page/span, raw text, normalized activity text, as-of date, cumulative progress or quantity with units, issues, and extraction warnings. Missing values must be null rather than invented. Matching should return ranked activity IDs, raw similarity scores, model/version, and review-needed status. Keep these separate from approved progress. Predictions must include target date/horizon, model/version, and evaluation context.
