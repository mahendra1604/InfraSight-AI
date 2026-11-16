"""Evaluate the full extraction + matching path on the golden set (Phase 5).

Uses exactly the code the API uses (app.pipeline.extract_document + app.matching),
against real Ollama models. Writes ml/eval_results.json and ml/EVAL_RESULTS.md.

    .venv\\Scripts\\python.exe ml\\make_eval_set.py          # once
    .venv\\Scripts\\python.exe ml\\eval_extraction.py        # all 15 documents (~15-25 min on CPU)
    .venv\\Scripts\\python.exe ml\\eval_extraction.py --kind text --limit 3
"""
import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import pipeline, qwen_service  # noqa: E402
from app.config import settings  # noqa: E402
from app.matching import ActivityMatcher, backend_name  # noqa: E402

MIME = {".txt": "text/plain", ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg"}


def load_schedule(path: Path) -> list[tuple[str, str]]:
    with path.open(encoding="utf-8-sig") as fh:
        return [(row["activity_id"], row["name"]) for row in csv.DictReader(fh)]


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))], 1)


async def evaluate(args) -> dict:
    manifest = json.loads((ROOT / "data" / "eval" / "manifest.json").read_text(encoding="utf-8"))
    matcher = ActivityMatcher(load_schedule(ROOT / manifest["schedule"]))
    docs = [d for d in manifest["documents"] if not args.kind or d["kind"] == args.kind][: args.limit or None]
    rows = []
    for doc in docs:
        path = ROOT / "data" / "eval" / doc["file"]
        expected = {e["activity_id"]: e for e in doc["expected"]}
        started = time.perf_counter()
        error = None
        predicted = {}
        trace = []
        method = model = None
        try:
            parsed, extraction, _ = await pipeline.extract_document(doc["file"], MIME[path.suffix], path.read_bytes(), args.mode)
            method, model = "+".join(parsed.methods), extraction["meta"].get("model")
            for entry in extraction["entries"]:
                match = matcher.match(entry)
                trace.append({"text": entry.get("activity_text"), "model_id": entry.get("activity_id"), "progress": entry.get("progress"),
                              "date": entry.get("reporting_date"), "matched": match.activity_id, "status": match.status,
                              "score": match.score, "warnings": entry.get("warnings", []) + match.notes})
                if entry.get("progress") is None:
                    continue
                if match.status != "unmatched" and match.activity_id:
                    predicted.setdefault(match.activity_id, {"progress": entry["progress"], "date": entry.get("reporting_date"),
                                                             "status": match.status, "score": match.score})
        except (qwen_service.QwenUnavailable, qwen_service.QwenTimeout, qwen_service.QwenBadOutput, ValueError) as exc:
            error = str(exc)
        seconds = round(time.perf_counter() - started, 1)
        tp = [a for a in predicted if a in expected]
        row = {
            "file": doc["file"], "kind": doc["kind"], "method": method, "model": model, "seconds": seconds, "error": error,
            "expected": len(expected), "predicted": len(predicted), "true_positive": len(tp),
            "false_positive": sorted(a for a in predicted if a not in expected),
            "missed": sorted(a for a in expected if a not in predicted),
            "progress_correct": sum(abs(predicted[a]["progress"] - expected[a]["progress"]) < 0.5 for a in tp),
            "date_correct": sum(predicted[a]["date"] == expected[a]["reporting_date"] for a in tp),
            "wrong_values": {a: {"got": predicted[a]["progress"], "want": expected[a]["progress"], "date": predicted[a]["date"]}
                             for a in tp if abs(predicted[a]["progress"] - expected[a]["progress"]) >= 0.5
                             or predicted[a]["date"] != expected[a]["reporting_date"]},
            "entries": trace,
        }
        rows.append(row)
        print(f"{doc['file']:<28} {seconds:>6}s  {method or '-':<8} TP {row['true_positive']}/{row['expected']}  FP {row['false_positive']}  "
              f"missed {row['missed']}  progress {row['progress_correct']}/{len(tp)}  date {row['date_correct']}/{len(tp)}"
              + (f"  ERROR {error}" if error else ""), flush=True)

    tp = sum(r["true_positive"] for r in rows)
    pred = sum(r["predicted"] for r in rows)
    exp = sum(r["expected"] for r in rows)
    summary = {
        "documents": len(rows), "errors": sum(1 for r in rows if r["error"]),
        "valid_output_rate": round(1 - sum(1 for r in rows if r["error"]) / max(len(rows), 1), 3),
        "precision": round(tp / pred, 3) if pred else None, "recall": round(tp / exp, 3) if exp else None,
        "progress_accuracy": round(sum(r["progress_correct"] for r in rows) / tp, 3) if tp else None,
        "date_accuracy": round(sum(r["date_correct"] for r in rows) / tp, 3) if tp else None,
        "latency_by_kind": {kind: {"p50_s": percentile([r["seconds"] for r in rows if r["kind"] == kind and not r["error"]], 0.5),
                                   "p95_s": percentile([r["seconds"] for r in rows if r["kind"] == kind and not r["error"]], 0.95),
                                   "n": sum(1 for r in rows if r["kind"] == kind)}
                            for kind in sorted({r["kind"] for r in rows})},
    }
    return {"run_at": datetime.now().isoformat(timespec="seconds"), "mode": args.mode, "text_model": settings.text_model,
            "vision_model": settings.vision_model, "matcher": backend_name(), "summary": summary, "documents": rows}


def write_markdown(result: dict, path: Path) -> None:
    s = result["summary"]
    lines = [
        "# Extraction evaluation results", "",
        f"Run {result['run_at']} · text model `{result['text_model']}` · vision model `{result['vision_model']}` · "
        f"matcher `{result['matcher']}` · mode `{result['mode']}` · CPU-only laptop (Ryzen 5 5600H, 16 GB RAM).", "",
        "| Metric | Value |", "|---|---|",
        f"| Documents | {s['documents']} |", f"| Valid structured output | {s['valid_output_rate']:.0%} |",
        f"| Activity precision | {s['precision']} |", f"| Activity recall | {s['recall']} |",
        f"| Progress accuracy (matched activities) | {s['progress_accuracy']} |", f"| Date accuracy (matched activities) | {s['date_accuracy']} |",
    ]
    for kind, lat in s["latency_by_kind"].items():
        lines.append(f"| Latency {kind} (p50 / p95, n={lat['n']}) | {lat['p50_s']} s / {lat['p95_s']} s |")
    lines += ["", "| Document | Type | Method | Time | Found | False positives | Missed | Wrong values |", "|---|---|---|---|---|---|---|---|"]
    for r in result["documents"]:
        lines.append(f"| {r['file']} | {r['kind']} | {r['method'] or '—'} | {r['seconds']} s | {r['true_positive']}/{r['expected']} | "
                     f"{', '.join(r['false_positive']) or '—'} | {', '.join(r['missed']) or '—'} | "
                     f"{json.dumps(r['wrong_values']) if r['wrong_values'] else '—'}{' · ERROR ' + r['error'] if r['error'] else ''} |")
    lines += ["", "Precision = predicted activities that are in the expected set; recall = expected activities that were found. "
              "Progress/date accuracy are measured on correctly found activities. Golden set: `data/eval/` (generated by `ml/make_eval_set.py`)."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kind", choices=["text", "pdf", "image"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--mode", choices=["auto", "text", "vision"], default="auto")
    parser.add_argument("--out", default=str(ROOT / "ml" / "eval_results.json"))
    args = parser.parse_args()
    result = asyncio.run(evaluate(args))
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    write_markdown(result, Path(args.out).with_name("EVAL_RESULTS.md"))
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    main()
