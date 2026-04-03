from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.common.scenario_loader import SCENARIO_FILE_NAMES
from runtime.common.scenario_validation import validate_scenario_catalog


SCENARIO_FILES = [f"scenarios/{filename}" for filename in SCENARIO_FILE_NAMES]


def load_json(path: str) -> dict:
    return json.loads((ROOT / path).read_text())


def main() -> None:
    roles = load_json("contracts/objects/v1/role-registry.json")["roles"]
    role_ids = {role["id"] for role in roles}

    stages = load_json("contracts/objects/v1/stage-registry.json")["stages"]
    stage_ids = {stage["id"] for stage in stages}

    events = load_json("contracts/agui/v1/event-registry.json")["events"]
    event_types = {event["type"] for event in events}

    authored_roles = load_json("authoring/roles/roles.json")["roles"]
    authored_role_ids = {role["id"] for role in authored_roles}
    assert role_ids == authored_role_ids, "contract and authored role registries diverge"

    flow = load_json("authoring/flows/investment_committee_flow.json")
    for stage in flow["stages"]:
        assert stage["id"] in stage_ids, f"unknown stage id {stage['id']}"
        for role_id in stage["required_roles"]:
            assert role_id in role_ids, f"unknown role id {role_id}"

    scenario_catalog = [load_json(path) for path in SCENARIO_FILES]
    assert len(scenario_catalog) == 4, "expected four canonical scenario artifacts"
    validate_scenario_catalog(scenario_catalog)

    scenario_schema = load_json("contracts/scenarios/v1/scenario.schema.json")
    assert scenario_schema["title"] == "Scenario Definition"
    assert scenario_schema["properties"]["scenario_type"]["$ref"] == "#/$defs/scenarioType"

    required_events = {
        "run.started",
        "stage.started",
        "evidence.upserted",
        "claim.upserted",
        "metric.upserted",
        "approval.requested",
        "approval.resolved",
        "ticket.updated",
        "run.completed",
    }
    assert required_events.issubset(event_types), "event registry missing required events"

    spec = load_json("spec/exported/agentic-trading-desk.spec.json")
    assert spec["desk"]["desk_id"] == "agentic_trading_desk"
    assert len(spec["roles"]) == len(role_ids)
    assert len(spec["flow"]["stages"]) == 8

    print(json.dumps({
        "status": "ok",
        "roles": len(role_ids),
        "stages": len(stage_ids),
        "events": len(event_types),
        "scenarios": len(scenario_catalog),
    }, indent=2))


if __name__ == "__main__":
    main()
