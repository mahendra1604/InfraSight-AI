# Batch pipelines (Member 1)

`spark_progress_job.py` — bounded Spark SQL batch job over the InfraSight database:
extract (activities, reports → staging CSV) → Spark DataFrames → Spark SQL KPIs → `outputs/spark/*.csv` + `summary.json`.

Requirements: Java 8/11/17 and PySpark 3.5 (`pip install -r pipelines/requirements-spark.txt`). PySpark 4.x needs Java 17+.

```powershell
.\.venv\Scripts\python.exe pipelines\spark_progress_job.py                       # your database
.\.venv\Scripts\python.exe pipelines\spark_progress_job.py --synthetic 2000000   # throughput test
```

Measured: 2,000,004 report rows in 15.9 s (≈ 126 k rows/s), local mode, Spark 3.5.0 + Java 8. If `SPARK_HOME` is set, PySpark uses that installation. On Windows, a `ShutdownHookManager … temp dir` error at exit is harmless.

Outputs: `activity_progress.csv` (per-activity variance, report counts, issues, last approved date, mean match score), `report_pipeline.csv` (status mix, AI share, needs-mapping count), `weekly_reports.csv`, `summary.json` (KPIs, timings, reports/second). Results are written by Python after `collect()`, so Windows does not need `winutils.exe`.
