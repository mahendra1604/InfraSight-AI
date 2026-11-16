"""Delay prediction and risk scoring (report modules: Delay Prediction, Risk Analysis — Member 5).

There is no historical project data yet, so the model is trained on a *synthetic*
dataset generated from an explicit, documented process (see `synthesize`). Replace
`ml/data/synthetic_activity_history.csv` with real history when available and retrain.
Every prediction carries the model version and the evaluation metrics.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Any

import numpy as np

from app.config import settings

log = logging.getLogger("infrasight.prediction")

FEATURES = [
    "planned_duration", "elapsed_days", "planned_progress", "actual_progress", "progress_gap", "spi",
    "previous_delays", "n_predecessors", "downstream_count", "on_critical_path", "total_float_days",
    "resource_availability",
]
LABELS = {
    "planned_duration": "Planned duration", "elapsed_days": "Days elapsed", "planned_progress": "Planned progress",
    "actual_progress": "Actual progress", "progress_gap": "Progress behind plan", "spi": "Schedule performance (actual/planned)",
    "previous_delays": "Previous delays and issues", "n_predecessors": "Number of predecessors",
    "downstream_count": "Downstream activities", "on_critical_path": "On the critical path",
    "total_float_days": "Total float", "resource_availability": "Resource availability",
}
RESOURCE_WORDS = ("delivery", "material", "labour", "labor", "crew", "equipment", "shortage", "supply",
                  "reinforcement", "steel", "cement", "manpower")
MODEL_NAME = "delay-xgb-synthetic"
DELAY_THRESHOLD_DAYS = 2.0


def as_of_date() -> date:
    return date.fromisoformat(os.getenv("PROJECT_AS_OF", "2026-10-01"))


# ---------------------------------------------------------------------------
# Synthetic training data
# ---------------------------------------------------------------------------
def synthesize(n: int = 6000, seed: int = 42) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Generate activity snapshots and their eventual finish delay (days).

    Process: an activity has hidden productivity `perf` and resource availability `res`.
    Its observed progress reflects perf*res; remaining work is completed at that rate,
    so delay = forecast remaining time - planned remaining time, plus knock-on effects
    from earlier issues and many predecessors, minus nothing for float (float protects
    the *project*, not the activity's own finish)."""
    rng = np.random.default_rng(seed)
    duration = rng.integers(5, 61, n).astype(float)
    planned = rng.uniform(0, 100, n)
    perf = np.clip(rng.normal(1.0, 0.25, n), 0.3, 1.4)
    res = np.where(rng.random(n) < 0.6, 1.0, rng.uniform(0.5, 1.0, n))
    actual = np.clip(planned * perf * (0.7 + 0.3 * res) + rng.normal(0, 5, n), 0, 100)
    elapsed = planned / 100 * duration * rng.uniform(0.9, 1.2, n)
    gap = planned - actual
    spi = np.where(planned > 1, actual / np.maximum(planned, 1), 1.0)
    previous = rng.poisson(0.4 + 2.0 * np.clip(1 - perf, 0, None) + 1.0 * (1 - res))
    n_pred = rng.integers(0, 5, n).astype(float)
    downstream = rng.integers(0, 31, n).astype(float)
    critical = (rng.random(n) < 0.3).astype(float)
    total_float = np.where(critical == 1, 0.0, rng.uniform(0, 20, n))
    rate = np.maximum(perf * (0.6 + 0.4 * res), 0.2)
    remaining_work = (100 - actual) / 100 * duration
    remaining_time = np.maximum(duration - elapsed, 0)
    delay = remaining_work / rate - remaining_time + 1.5 * previous + 0.4 * n_pred * (gap > 10) + rng.normal(0, 1.5, n)
    delay = np.where(actual >= 99.5, 0.0, np.maximum(delay, 0.0))
    X = np.column_stack([duration, elapsed, planned, actual, gap, spi, previous, n_pred, downstream, critical,
                         total_float, res])
    return X, (delay >= DELAY_THRESHOLD_DAYS).astype(int), delay


def _paths() -> dict[str, Any]:
    folder = settings.model_dir
    return {"dir": folder, "clf": folder / "delay_classifier.json", "reg": folder / "delay_regressor.json",
            "meta": folder / "delay_model_meta.json", "data": folder.parent / "data" / "synthetic_activity_history.csv"}


def train(n: int = 6000, seed: int = 42) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, roc_auc_score
    from sklearn.model_selection import train_test_split

    X, y, days = synthesize(n, seed)
    paths = _paths()
    paths["dir"].mkdir(parents=True, exist_ok=True)
    paths["data"].parent.mkdir(parents=True, exist_ok=True)
    header = ",".join(FEATURES + ["delayed", "delay_days"])
    np.savetxt(paths["data"], np.column_stack([X, y, days]), delimiter=",", header=header, comments="", fmt="%.4f")
    X_tr, X_te, y_tr, y_te, d_tr, d_te = train_test_split(X, y, days, test_size=0.2, random_state=seed, stratify=y)
    try:
        from xgboost import XGBClassifier, XGBRegressor

        clf = XGBClassifier(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.9, colsample_bytree=0.9,
                            eval_metric="logloss", random_state=seed)
        reg = XGBRegressor(n_estimators=300, max_depth=4, learning_rate=0.05, subsample=0.9, random_state=seed)
        library = "xgboost"
    except ImportError:  # pragma: no cover
        from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor

        clf, reg, library = GradientBoostingClassifier(random_state=seed), GradientBoostingRegressor(random_state=seed), "sklearn"
    clf.fit(X_tr, y_tr)
    reg.fit(X_tr, d_tr)
    proba = clf.predict_proba(X_te)[:, 1]
    metrics = {
        "roc_auc": round(float(roc_auc_score(y_te, proba)), 3),
        "accuracy": round(float(accuracy_score(y_te, proba >= 0.5)), 3),
        "f1": round(float(f1_score(y_te, proba >= 0.5)), 3),
        "delay_days_mae": round(float(mean_absolute_error(d_te, np.maximum(reg.predict(X_te), 0))), 2),
        "positive_rate": round(float(y.mean()), 3), "train_rows": int(len(X_tr)), "test_rows": int(len(X_te)),
    }
    trained_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    meta = {"name": MODEL_NAME, "version": f"{MODEL_NAME}-{trained_at[:10]}", "library": library, "trained_at": trained_at,
            "features": FEATURES, "metrics": metrics, "delay_threshold_days": DELAY_THRESHOLD_DAYS,
            "data": "synthetic (ml/data/synthetic_activity_history.csv) — replace with real project history"}
    if library == "xgboost":
        clf.save_model(paths["clf"])
        reg.save_model(paths["reg"])
    else:  # pragma: no cover
        import joblib

        joblib.dump((clf, reg), paths["dir"] / "delay_models.joblib")
    paths["meta"].write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _cache.clear()
    log.info("trained delay model: %s", metrics)
    return meta


_cache: dict[str, Any] = {}


def load() -> dict[str, Any]:
    if _cache:
        return _cache
    paths = _paths()
    if not paths["meta"].exists():
        train()
    meta = json.loads(paths["meta"].read_text(encoding="utf-8"))
    if meta["library"] == "xgboost":
        from xgboost import XGBClassifier, XGBRegressor

        clf, reg = XGBClassifier(), XGBRegressor()
        clf.load_model(paths["clf"])
        reg.load_model(paths["reg"])
    else:  # pragma: no cover
        import joblib

        clf, reg = joblib.load(paths["dir"] / "delay_models.joblib")
    _cache.update(meta=meta, clf=clf, reg=reg)
    return _cache


# ---------------------------------------------------------------------------
# Live features and predictions
# ---------------------------------------------------------------------------
def build_features(nodes: list[dict[str, Any]], activities: dict[str, Any], issues: dict[str, list[str]]) -> np.ndarray:
    """nodes: output of analysis.dependency_analysis()['nodes']."""
    today = as_of_date()
    by_id = {n["id"]: n for n in nodes}
    rows = []
    for node in nodes:
        activity = activities[node["id"]]
        try:
            elapsed = max(0, (today - date.fromisoformat(activity.planned_start)).days) if activity.planned_start else 0
        except ValueError:
            elapsed = 0
        planned, actual = float(activity.planned), float(activity.actual)
        texts = issues.get(node["id"], [])
        resource_hits = sum(any(word in t.lower() for word in RESOURCE_WORDS) for t in texts)
        preds_behind = sum(1 for p in node["predecessors"] if by_id.get(p, {}).get("variance", 0) < 0)
        rows.append([
            node["duration_days"], min(elapsed, node["duration_days"] * 1.5), planned, actual, planned - actual,
            actual / planned if planned > 1 else 1.0, len(texts) + preds_behind, len(node["predecessors"]),
            node["downstream_count"], 1.0 if node["critical"] else 0.0, max(node["total_float_days"], 0.0),
            max(0.4, 1.0 - 0.2 * resource_hits),
        ])
    return np.array(rows, dtype=float)


def _contributions(clf, X: np.ndarray) -> np.ndarray | None:
    try:
        import xgboost as xgb

        return clf.get_booster().predict(xgb.DMatrix(X, feature_names=None), pred_contribs=True)[:, :-1]
    except Exception:  # pragma: no cover
        return None


def predict(nodes: list[dict[str, Any]], activities: dict[str, Any], issues: dict[str, list[str]]) -> dict[str, Any]:
    model = load()
    if not nodes:
        return {"model": model["meta"], "predictions": []}
    X = build_features(nodes, activities, issues)
    proba = model["clf"].predict_proba(X)[:, 1]
    days = np.maximum(model["reg"].predict(X), 0)
    contribs = _contributions(model["clf"], X)
    total = max(len(nodes) - 1, 1)
    out = []
    for i, node in enumerate(nodes):
        complete = X[i, 3] >= 99.5
        p = 0.0 if complete else float(proba[i])
        d = 0.0 if complete else float(days[i])
        weight = min(1.0, 0.5 + 0.5 * node["downstream_count"] / total + (0.25 if node["critical"] else 0.0))
        risk = round(100 * p * weight, 1)
        factors = []
        if contribs is not None and not complete:
            order = np.argsort(-contribs[i])
            factors = [{"feature": FEATURES[j], "label": LABELS[FEATURES[j]], "value": round(float(X[i, j]), 2),
                        "impact": round(float(contribs[i, j]), 3)} for j in order[:3] if contribs[i, j] > 0]
        out.append({
            "activity_id": node["id"], "name": node["name"], "delay_probability": round(p, 3),
            "expected_delay_days": round(d, 1), "risk_score": risk,
            "risk_level": "high" if risk >= 50 else "medium" if risk >= 25 else "low",
            "downstream_count": node["downstream_count"], "critical": node["critical"], "complete": bool(complete),
            "factors": factors, "features": {f: round(float(X[i, j]), 2) for j, f in enumerate(FEATURES)},
        })
    out.sort(key=lambda r: r["risk_score"], reverse=True)
    return {"model": model["meta"], "as_of": as_of_date().isoformat(), "predictions": out, "risk_formula": RISK_FORMULA,
            "note": "Trained on synthetic data until real project history is available. Use as decision support, not as fact."}


RISK_FORMULA = "risk = 100 × P(delay) × min(1, 0.5 + 0.5 × downstream share + 0.25 if critical)"
