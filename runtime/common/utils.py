from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
VAR_ROOT = ROOT / "var"
RUNS_ROOT = VAR_ROOT / "runs"
DEMO_TICK_SECONDS = float(os.environ.get("ATD_DEMO_TICK_SECONDS", "0.12"))
DEBATE_TURN_SECONDS = float(os.environ.get("ATD_DEBATE_TURN_SECONDS", "1.6"))


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def ensure_runs_root() -> Path:
    RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    return RUNS_ROOT


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


def sleep_tick(seconds: float | None = None) -> None:
    time.sleep(DEMO_TICK_SECONDS if seconds is None else seconds)


def sleep_debate_turn() -> None:
    time.sleep(DEBATE_TURN_SECONDS)
