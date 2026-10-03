# Three-person development plan

All members first run the demo and approve a sample report together. Agree on schemas before splitting work. Each member owns tests and documentation for their changes.

## Full-stack owner

Own `frontend/` and API routes. First task: extract the current single React component into pages and reusable components without changing the demonstrated flow. Next add project selection, authentication, proper upload job states, and correction UI. Coordinate shared API changes with the other owners.

## Machine learning owner

Own `ml/` and future document/matching/prediction services. First task: define extraction results and build an offline text-report parser using synthetic fixtures. Next evaluate OCR and semantic matching, preserving source text and candidate scores. Add prediction only after identifying suitable historical data, defining labels, and testing a baseline without future-data leakage. Never label similarity as calibrated confidence without evaluation.

## Generalist owner

Own database, schedule analytics, `pipelines/`, and integration configuration. First task: split backend database/models/services into modules and add migrations. Next implement persistent schedule import with project and baseline IDs, multiple typed dependencies, as-of progress calculation, downstream impact, and concurrency-safe review. Add a bounded Spark batch pipeline with measured results.

## Shared first milestone

All three machines run the same demo. Backend tests and frontend build pass. An approved report updates the dashboard and audit log. API contracts are agreed. Assign the tasks above to their owners and review small changes together.

## Next integrated milestone

Upload a report, extract entries, return candidate matches, correct or accept them, persist verified progress, and explain resulting variance. Add synthetic fixtures for ambiguous matches, OCR errors, duplicate uploads, stale reports, and invalid dependencies.
