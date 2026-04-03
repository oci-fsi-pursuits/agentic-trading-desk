from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.common.contract_validation import validate_run_payload
from runtime.common.scenario_loader import load_scenario, load_scenario_catalog
from runtime.common.service import get_adapter


def run_and_validate(runtime_name: str, scenario_id: str, ticker: str, seat_ids: list[str] | None) -> dict:
    adapter = get_adapter(runtime_name)
    result = adapter.execute(scenario_id, seat_ids, ticker)
    validate_run_payload(result)
    llm = result.get("summary", {}).get("llm", {})
    assert isinstance(llm, dict), "summary.llm must be present"
    for key in ("live_count", "fallback_count", "last_mode", "auth_mode"):
        assert key in llm, f"summary.llm missing '{key}'"
    return result


def main() -> None:
    matrix = []
    scenarios = load_scenario_catalog()
    for runtime_name in ("wayflow", "langgraph"):
        for scenario_entry in scenarios:
            scenario_id = scenario_entry["scenario_id"]
            scenario = load_scenario(scenario_id)
            required_only = scenario["required_seat_ids"]
            ticker = scenario["instrument"]
            result = run_and_validate(runtime_name, scenario_id, ticker, required_only)
            matrix.append(
                {
                    "runtime": runtime_name,
                    "scenario_id": scenario_id,
                    "run_id": result["summary"]["run_id"],
                    "claim_count": len(result["objects"].get("claim", {})),
                    "ticker": result["summary"]["ticker"],
                    "ticket_id": result["summary"]["ticket_id"],
                }
            )

    print(json.dumps({"status": "ok", "runs": matrix}, indent=2))


if __name__ == "__main__":
    main()
