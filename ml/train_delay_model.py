"""Train the delay classifier + regressor and print hold-out metrics.

    .venv\\Scripts\\python.exe ml\\train_delay_model.py
Writes ml/models/delay_*.json and ml/data/synthetic_activity_history.csv.
The API trains automatically on first use if these files are missing.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import prediction  # noqa: E402

if __name__ == "__main__":
    meta = prediction.train()
    print(json.dumps(meta, indent=2))
