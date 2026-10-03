# InfraSight AI

A shared starter for infrastructure progress monitoring, built for three teammates. React provides the dashboard; FastAPI and SQLAlchemy provide the API and persistent storage.

## Run locally on Windows

Requirements: Python 3.13 and Node.js 24. Run the following from the project folder in PowerShell.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.txt
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal, from the project folder:

```powershell
cd frontend
npm.cmd ci
npm.cmd run dev
```

Open http://localhost:5173. API documentation: http://127.0.0.1:8000/docs. On macOS/Linux use `.venv/bin/python` instead of `.venv\Scripts\python.exe` and `npm` instead of `npm.cmd`.

SQLite is created at `backend/infrasight.db` on first startup when following these commands. Sample data is seeded only into an empty database. Changes persist across restarts. `.env.example` documents available settings; this starter reads process environment variables, it does not automatically load `.env`.

## What works

- Six synthetic activities with a fixed 1 October 2026 baseline.
- Planned/actual dashboard and activity register.
- Manual report creation and approval/rejection queue.
- Approved cumulative progress updates and persisted audit entries.
- Rejection leaves progress unchanged. Duplicate decisions and older approved-data overwrites are blocked.
- CSV schedule validation including duplicate IDs, missing predecessors, invalid percentages, and dependency cycles. Validation does not import or replace the baseline.
- Readable dependency sequence, API documentation, and tests.

## Try the complete flow

1. Observe foundation concrete at 40% actual versus 80% planned.
2. Open Report review and approve the seeded 60% report.
3. Return to Overview: foundation concrete is 60%, variance is -20 percentage points.
4. Open Audit trail to see the recorded decision.
5. Add another manual report or validate `data/samples/schedule.csv`.

## Development scope

This is a local single-project demo. No authentication, file persistence, trained AI, OCR, automatic matching, delay predictions, critical-path calculation, Spark jobs, production migration system, or report export is implemented yet. Do not expose it publicly or use confidential data. Report review identifies a local demo reviewer, not a signed-in user. Multi-user simultaneous decision locking and baseline/date versioning must be implemented before shared production use.

Progress is cumulative (0–100). Overall progress is an unweighted mean, not earned value. Planned values are fixed fixture values rather than a time-based schedule calculation. Dates are ISO dates. The starter permits one predecessor per activity and does not model dependency type or lag. Reject and resubmit incorrect manual entries; editing reviewed reports is not supported yet.

## Tests

```powershell
cd backend
..\.venv\Scripts\python.exe -m pytest tests -q
cd ../frontend
npm.cmd run build
```

## Optional PostgreSQL

With Docker installed, run `docker compose up --build` from the root, then run the frontend normally. This starts PostgreSQL and the API instead of the local SQLite API. Development credentials are in the compose file; ports are bound to localhost. Do not run both API alternatives on port 8000. Docker execution must be validated on a machine with Docker installed.

## Work together

Read `docs/team-plan.md`, `docs/data-contracts.md`, and `docs/architecture.md`. Agree on shared interfaces before changing them, review changes together, and keep the application runnable on all three machines.
