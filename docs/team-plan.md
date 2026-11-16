# Team plan — six members (matches the submitted report)

Everyone first runs the app, analyses `data/samples/qwen_test_report.txt`, approves a report and opens Dependencies and Delay risk. Run `pytest` before every merge. Agree API changes in `docs/data-contracts.md` before implementing them.

| Member | Module (report) | Code you own | Next tasks |
|---|---|---|---|
| 1 | Project manager + Schedule intelligence | `backend/app/schedules.py`, `db.py`, `pipelines/` | Primavera XER import; multiple projects (`project_id` on every table); Alembic migrations; scale the Spark job and record timings |
| 2 | OCR + Document processing | `backend/app/documents.py` | Table-aware OCR for scanned forms; handwriting samples; image pre-processing (deskew, contrast) with before/after accuracy |
| 3 | NLP + Activity matching | `backend/app/qwen_service.py`, `matching.py`, `ml/eval_extraction.py` | Grow the golden set to 50+ real-style reports; calibrate match thresholds on it; prompt and model comparison table |
| 4 | Dependency + Graph analysis | `backend/app/analysis.py` | SS/FF/SF links and lags; forecast dates from actual progress; bottleneck metrics comparison |
| 5 | Machine learning + Delay prediction | `backend/app/prediction.py`, `ml/` | Replace synthetic history with a public or anonymised dataset; time-based split without leakage; feature importance study |
| 6 | Frontend + Dashboard + Deployment | `frontend/`, `docker-compose.yml`, Power BI | Power BI report on the `v_*` views; mobile layout pass; Docker validation with PostgreSQL + Ollama |

## Definition of done for any change

- Unit tests added or updated; `pytest` passes (`pytest -m live` too when the change touches the LLM path).
- No AI output changes progress without review.
- Measured numbers (accuracy, latency) recorded when a model, prompt or threshold changes.
