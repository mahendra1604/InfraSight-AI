# Starter architecture

Browser → Vite development proxy → FastAPI → SQLAlchemy → SQLite (default) or PostgreSQL (optional).

The backend seeds a synthetic CSV only when no activities exist. A report is a proposed update. Approval changes cumulative activity progress and inserts an audit record in one transaction. Rejection only records the decision. The frontend reloads API data after mutations.

The initial implementation intentionally keeps the backend in one module for readability; split it before independent backend feature work. Table creation currently uses `create_all`, which does not migrate existing tables. Add Alembic migrations before changing the schema. Add authorization, idempotency, database locking, timestamps/identities, baseline versioning, upload isolation, and project-scoped queries before deploying for real users.

The frontend has no model logic. Future document and matching services should expose deterministic interfaces to the API. The generalist owns schedule/dependency calculations; the ML owner owns extraction/matching and prediction functions; the full-stack owner wires API routes and UI.
