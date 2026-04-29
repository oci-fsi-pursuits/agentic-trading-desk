from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from runtime.common.utils import ROOT

QUANT_TIMEOUT_S = 20
QUANT_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024


def _limit_quant_process() -> None:
    if os.name != "posix":
        return
    try:
        import resource
    except ImportError:
        return
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (QUANT_TIMEOUT_S, QUANT_TIMEOUT_S + 1))
    except (OSError, ValueError):
        pass
    try:
        resource.setrlimit(resource.RLIMIT_AS, (QUANT_MEMORY_LIMIT_BYTES, QUANT_MEMORY_LIMIT_BYTES))
    except (OSError, ValueError):
        pass


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
pair_mode = bool(dataset.get("pair_mode"))
peer_series_map = dataset.get("peer_price_series") or {}
peer_tickers = list(dataset.get("peer_tickers") or [])
pair_metrics = {
  "pair_mode": pair_mode,
  "pair_peer": "",
  "pair_correlation": None,
  "spread_momentum_pct": None,
  "spread_volatility_pct": None,
  "relative_vol_ratio": None
}
if pair_mode and isinstance(peer_series_map, dict):
  selected_peer = ""
  for peer in peer_tickers:
    if peer in peer_series_map:
      selected_peer = peer
      break
  if not selected_peer and peer_series_map:
    selected_peer = list(peer_series_map.keys())[0]
  peer_prices = list(peer_series_map.get(selected_peer) or [])
  pair_metrics["pair_peer"] = selected_peer
  if len(peer_prices) >= 2 and len(prices) >= 2:
    window = min(len(prices), len(peer_prices), 60)
    left = [float(v) for v in prices[-window:]]
    right = [float(v) for v in peer_prices[-window:]]
    left_returns = [(b - a) / a for a, b in zip(left[:-1], left[1:]) if a]
    right_returns = [(b - a) / a for a, b in zip(right[:-1], right[1:]) if a]
    if len(left_returns) == len(right_returns) and len(left_returns) >= 3:
      left_mean = sum(left_returns) / len(left_returns)
      right_mean = sum(right_returns) / len(right_returns)
      cov = sum((a - left_mean) * (b - right_mean) for a, b in zip(left_returns, right_returns))
      left_var = sum((a - left_mean) ** 2 for a in left_returns)
      right_var = sum((b - right_mean) ** 2 for b in right_returns)
      if left_var > 0 and right_var > 0:
        pair_metrics["pair_correlation"] = round(max(-1.0, min(1.0, cov / ((left_var * right_var) ** 0.5))), 4)
      left_vol = statistics.pstdev(left_returns) if len(left_returns) >= 2 else 0.0
      right_vol = statistics.pstdev(right_returns) if len(right_returns) >= 2 else 0.0
      if right_vol > 0:
        pair_metrics["relative_vol_ratio"] = round(left_vol / right_vol, 4)
    spread = [a - b for a, b in zip(left, right)]
    if len(spread) >= 2 and spread[0] != 0:
      pair_metrics["spread_momentum_pct"] = round((spread[-1] - spread[0]) / abs(spread[0]) * 100, 3)
      spread_returns = [(b - a) / abs(a) for a, b in zip(spread[:-1], spread[1:]) if abs(a) > 1e-9]
      if len(spread_returns) >= 2:
        pair_metrics["spread_volatility_pct"] = round(statistics.pstdev(spread_returns) * 100, 3)
out = {
  "momentum_20d_pct": momentum_20d,
  "estimate_revision_score": revision_score,
  "return_volatility_pct": volatility,
  "crowding_score": crowding,
  "composite_signal": signal,
  "coverage": coverage,
  "pair_mode": pair_metrics["pair_mode"],
  "pair_peer": pair_metrics["pair_peer"],
  "pair_correlation": pair_metrics["pair_correlation"],
  "spread_momentum_pct": pair_metrics["spread_momentum_pct"],
  "spread_volatility_pct": pair_metrics["spread_volatility_pct"],
  "relative_vol_ratio": pair_metrics["relative_vol_ratio"]
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
        timeout=QUANT_TIMEOUT_S,
        cwd=artifact_dir,
        preexec_fn=_limit_quant_process if os.name == "posix" else None,
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
