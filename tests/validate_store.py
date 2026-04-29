from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.common.store import RunStore


def write_summary(run_id: str, completed_at: str) -> None:
    store = RunStore(run_id)
    store.write_summary(
        {
            "run_id": run_id,
            "scenario_id": "single_name_earnings",
            "runtime": "wayflow",
            "ticker": "NVDA",
            "stage_sequence": ["completed"],
            "object_counts": {},
            "ticket_id": "tkt_test",
            "decision_id": "dec_test",
            "completed_at": completed_at,
        }
    )


def main() -> None:
    older_id = "run_zzzz_store_order_old"
    newer_id = "run_aaaa_store_order_new"
    write_summary(older_id, "2026-04-29T15:00:00Z")
    time.sleep(0.02)
    write_summary(newer_id, "2026-04-29T15:01:00Z")

    recent = RunStore.list_runs(limit=5)
    ids = [item["run_id"] for item in recent]
    assert newer_id in ids, ids
    assert older_id in ids, ids
    assert ids.index(newer_id) < ids.index(older_id), ids

    print(json.dumps({"status": "ok", "recent_prefix": ids[:5]}, indent=2))
    shutil.rmtree(RunStore(older_id).run_dir, ignore_errors=True)
    shutil.rmtree(RunStore(newer_id).run_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
