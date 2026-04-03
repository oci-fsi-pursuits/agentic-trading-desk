from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("OCI_GENAI_ENABLE", "0")

import app


def wait_until(predicate, *, timeout_s: float, label: str) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {label}")


def load_events(run_id: str) -> list[dict]:
    path = ROOT / "var" / "runs" / run_id / "event-log.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def main() -> None:
    run_id = app.make_id("itest")
    app._launch_run_job(
        run_id,
        "wayflow",
        "single_name_earnings",
        [],
        "ORCL",
        breaking_news=False,
        debate_depth=1,
    )

    wait_until(
        lambda: app.RUN_JOBS.get(run_id, {}).get("status") == "paused",
        timeout_s=12,
        label="phase-1 pause",
    )

    paused_status = dict(app.RUN_JOBS.get(run_id) or {})
    assert paused_status.get("paused_after_stage") == "quantify", paused_status

    pre_continue_events = load_events(run_id)
    assert pre_continue_events, "run should have written an event log before pausing"
    assert not any(event.get("stage_id") == "debate" for event in pre_continue_events), (
        "debate events were emitted before continue"
    )

    control = app.RUN_CONTROLS.get(run_id)
    assert control is not None, "pause control missing for paused run"
    control.set()

    wait_until(
        lambda: any(
            event.get("stage_id") == "debate" and event.get("event_type") == "stage.started"
            for event in load_events(run_id)
        ),
        timeout_s=8,
        label="phase-2 start after continue",
    )

    post_continue_events = load_events(run_id)
    debate_claims = [
        event for event in post_continue_events
        if event.get("stage_id") == "debate" and event.get("event_type") == "claim.upserted"
    ]
    assert debate_claims, "debate should emit claims after continue"

    print(json.dumps({
        "status": "ok",
        "run_id": run_id,
        "paused_after_stage": paused_status["paused_after_stage"],
        "debate_claim_count_after_continue": len(debate_claims),
    }, indent=2))


if __name__ == "__main__":
    main()
