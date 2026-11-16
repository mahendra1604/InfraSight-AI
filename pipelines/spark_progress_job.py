"""Bounded Spark batch job: schedule + site reports -> progress KPIs (report: Apache Spark / Spark SQL).

Steps
  1. Extract   activities and reports from the InfraSight database to staging CSV (Python sqlite3/SQLAlchemy)
  2. Load      staging CSV into Spark DataFrames
  3. Transform Spark SQL: per-activity progress, project KPIs, weekly report volume, AI pipeline quality
  4. Publish   results collected to the driver and written to outputs/spark/*.csv + summary.json
               (written with Python, so Windows needs no winutils.exe)

Optional --synthetic N appends N generated report rows (inside Spark) to measure throughput.

Requirements: Java 8/11/17 and `pip install pyspark==3.5.9` (PySpark 4.x needs Java 17+).
    .venv\\Scripts\\python.exe pipelines\\spark_progress_job.py
    .venv\\Scripts\\python.exe pipelines\\spark_progress_job.py --synthetic 2000000
"""
import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "backend" / "infrasight.db"
STAGING = ROOT / "outputs" / "spark" / "staging"
OUT = ROOT / "outputs" / "spark"


def extract(database_url: str) -> dict[str, int]:
    """Step 1: export tables to staging CSV with SQLAlchemy (works for SQLite and PostgreSQL)."""
    from sqlalchemy import create_engine, text

    STAGING.mkdir(parents=True, exist_ok=True)
    engine = create_engine(database_url)
    counts = {}
    queries = {
        "activities": "SELECT id, name, planned, actual, predecessor, planned_start, planned_finish, duration_days FROM activities",
        "reports": "SELECT id, activity_id, progress, reporting_date, status, source, model, match_score, match_status, issue FROM reports",
    }
    with engine.connect() as conn:
        for name, sql in queries.items():
            result = conn.execute(text(sql))
            rows = result.fetchall()
            with (STAGING / f"{name}.csv").open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(result.keys())
                writer.writerows(rows)
            counts[name] = len(rows)
    engine.dispose()
    return counts


def write_rows(name: str, rows: list) -> None:
    path = OUT / f"{name}.csv"
    with path.open("w", newline="", encoding="utf-8") as fh:
        if rows:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].asDict().keys()))
            writer.writeheader()
            writer.writerows(r.asDict() for r in rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", f"sqlite:///{DEFAULT_DB.as_posix()}"))
    parser.add_argument("--synthetic", type=int, default=0, help="extra generated report rows for a throughput test")
    args = parser.parse_args()

    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    timings = {}
    t0 = time.perf_counter()
    counts = extract(args.database_url)
    timings["extract_s"] = round(time.perf_counter() - t0, 2)

    from pyspark.sql import SparkSession

    t0 = time.perf_counter()
    spark = (SparkSession.builder.appName("InfraSightProgressJob").master("local[*]")
             .config("spark.sql.shuffle.partitions", "8").config("spark.ui.enabled", "false")
             .config("spark.driver.memory", "2g").getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    timings["spark_start_s"] = round(time.perf_counter() - t0, 2)

    t0 = time.perf_counter()
    read = spark.read.option("header", True).option("inferSchema", True)
    activities = read.csv(str(STAGING / "activities.csv"))
    reports = read.csv(str(STAGING / "reports.csv"))
    if args.synthetic:
        ids = [r["id"] for r in activities.select("id").collect()]
        id_array = ",".join(f"'{i}'" for i in ids)
        synthetic = spark.range(args.synthetic).selectExpr(
            "id + 1000000 AS id",
            f"element_at(array({id_array}), CAST(pmod(id, {len(ids)}) + 1 AS INT)) AS activity_id",
            "CAST(pmod(id * 7, 101) AS DOUBLE) AS progress",
            "date_format(date_add(DATE'2026-08-01', CAST(pmod(id, 120) AS INT)), 'yyyy-MM-dd') AS reporting_date",
            "CASE WHEN pmod(id, 10) < 6 THEN 'approved' WHEN pmod(id, 10) < 9 THEN 'pending' ELSE 'rejected' END AS status",
            "'synthetic' AS source",
            "CASE WHEN pmod(id, 3) = 0 THEN NULL ELSE 'qwen3:4b-instruct' END AS model",
            "CASE WHEN pmod(id, 3) = 0 THEN NULL ELSE 0.4 + pmod(id, 60) / 100.0 END AS match_score",
            "CASE WHEN pmod(id, 3) = 0 THEN 'manual' WHEN pmod(id, 5) = 0 THEN 'needs_mapping' ELSE 'suggested' END AS match_status",
            "CASE WHEN pmod(id, 4) = 0 THEN 'material delivery delayed' ELSE NULL END AS issue")
        reports = reports.unionByName(synthetic, allowMissingColumns=True)
    activities.createOrReplaceTempView("activities")
    reports.createOrReplaceTempView("reports")

    activity_progress = spark.sql("""
        SELECT a.id AS activity_id, a.name, a.planned, a.actual, ROUND(a.actual - a.planned, 2) AS variance_pp,
               CASE WHEN a.actual < a.planned THEN 1 ELSE 0 END AS behind,
               COUNT(r.id) AS reports, SUM(CASE WHEN r.status = 'approved' THEN 1 ELSE 0 END) AS approved,
               SUM(CASE WHEN r.status = 'pending' THEN 1 ELSE 0 END) AS pending,
               SUM(CASE WHEN r.issue IS NOT NULL AND r.issue <> '' THEN 1 ELSE 0 END) AS issues,
               MAX(CASE WHEN r.status = 'approved' THEN r.reporting_date END) AS last_approved_date,
               ROUND(AVG(r.match_score), 3) AS avg_match_score
        FROM activities a LEFT JOIN reports r ON r.activity_id = a.id
        GROUP BY a.id, a.name, a.planned, a.actual ORDER BY a.id""").collect()
    kpis = spark.sql("""
        SELECT COUNT(*) AS activities, ROUND(AVG(planned), 1) AS mean_planned, ROUND(AVG(actual), 1) AS mean_actual,
               SUM(CASE WHEN actual < planned THEN 1 ELSE 0 END) AS behind,
               ROUND(SUM(actual) / NULLIF(SUM(planned), 0), 3) AS progress_performance_index
        FROM activities""").collect()[0].asDict()
    pipeline = spark.sql("""
        SELECT status, COUNT(*) AS reports, SUM(CASE WHEN model IS NOT NULL THEN 1 ELSE 0 END) AS ai_reports,
               ROUND(AVG(match_score), 3) AS avg_match_score,
               SUM(CASE WHEN match_status = 'needs_mapping' THEN 1 ELSE 0 END) AS needs_mapping
        FROM reports GROUP BY status ORDER BY status""").collect()
    weekly = spark.sql("""
        SELECT date_format(to_date(reporting_date), 'yyyy') AS year, weekofyear(to_date(reporting_date)) AS week,
               COUNT(*) AS reports, SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved
        FROM reports WHERE reporting_date IS NOT NULL GROUP BY 1, 2 ORDER BY 1, 2""").collect()
    timings["transform_s"] = round(time.perf_counter() - t0, 2)
    total_reports = sum(r["reports"] for r in pipeline)

    OUT.mkdir(parents=True, exist_ok=True)
    write_rows("activity_progress", activity_progress)
    write_rows("report_pipeline", pipeline)
    write_rows("weekly_reports", weekly)
    summary = {"spark_version": spark.version, "source_rows": counts, "synthetic_rows": args.synthetic,
               "reports_processed": total_reports, "project_kpis": kpis, "timings": timings,
               "throughput_reports_per_s": round(total_reports / max(timings["transform_s"], 0.001))}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    spark.stop()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
