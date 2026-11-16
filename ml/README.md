# ML workspace (Members 3 and 5)

| File | Purpose |
|---|---|
| `make_eval_set.py` | Generates the golden set in `data/eval/` (10 text, 2 PDF, 3 image reports + expected values) |
| `eval_extraction.py` | Runs the real extraction + matching path on the golden set; writes `eval_results.json` and `EVAL_RESULTS.md` |
| `train_delay_model.py` | Trains the XGBoost delay classifier/regressor; prints hold-out metrics |
| `models/`, `data/` | Generated model files and the synthetic training set (git-ignored, recreated on demand) |

```powershell
.\.venv\Scripts\python.exe ml\make_eval_set.py
.\.venv\Scripts\python.exe ml\eval_extraction.py              # needs Ollama; ~15-25 min on CPU
.\.venv\Scripts\python.exe ml\eval_extraction.py --kind text --limit 3
.\.venv\Scripts\python.exe ml\train_delay_model.py
```

Rules: never call similarity "confidence" without calibration on the golden set; report the model, prompt and hardware with every number; replace the synthetic delay history with real project history before drawing conclusions about real delays.
