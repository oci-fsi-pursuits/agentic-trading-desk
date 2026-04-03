from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from runtime.common.utils import ROOT


def run_quant(dataset: dict[str, Any], run_id: str) -> dict[str, Any]:
    code = '''import json, statistics, sys
from pathlib import Path

dataset = json.loads(Path(sys.argv[1]).read_text())
prices = dataset["price_series"]
revisions = dataset["estimate_revisions_pct"]
coverage = str(dataset.get("quant_coverage", "full") or "full")
returns = [(b - a) / a for a, b in zip(prices[:-1], prices[1:])]
momentum_20d = round((prices[-1] - prices[0]) / prices[0] * 100, 2)
revision_score = round(sum(revisions[-3:]) / 3, 3)
volatility = round(statistics.pstdev(returns) * 100, 3)
crowding = round(dataset["sentiment_score"] * dataset["ai_basket_correlation"], 3)
signal = round(momentum_20d * 0.35 + revision_score * 10 * 0.40 - crowding * 10 * 0.25, 3)
out = {
  "momentum_20d_pct": momentum_20d,
  "estimate_revision_score": revision_score,
  "return_volatility_pct": volatility,
  "crowding_score": crowding,
  "composite_signal": signal,
  "coverage": coverage
}
print(json.dumps(out))
'''
    artifact_dir = ROOT / "var" / "runs" / run_id / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    code_path = artifact_dir / "quant_notebook.py"
    dataset_path = artifact_dir / "quant_dataset.json"
    code_path.write_text(code)
    dataset_path.write_text(json.dumps(dataset, indent=2))

    completed = subprocess.run(
        [sys.executable, str(code_path), str(dataset_path)],
        capture_output=True,
        text=True,
        timeout=20,
        cwd=artifact_dir,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "quant runner failed")
    result = json.loads(completed.stdout)
    stdout_path = artifact_dir / "quant_stdout.json"
    stdout_path.write_text(json.dumps(result, indent=2) + "\n")
    return {
        "artifact_dir": str(artifact_dir.relative_to(ROOT)),
        "code_path": str(code_path.relative_to(ROOT)),
        "dataset_path": str(dataset_path.relative_to(ROOT)),
        "stdout_path": str(stdout_path.relative_to(ROOT)),
        "result": result,
    }
