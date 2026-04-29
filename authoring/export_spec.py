from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.common.analyst_tools import tool_specs_for_role


def load_json(path: Path):
    return json.loads(path.read_text())


AGENT_SPEC_VERSION = "26.1.0"
CANONICAL_STAGE_SEQUENCE = [
    "gather",
    "quantify",
    "debate",
    "synthesize",
    "risk_review",
    "pm_review",
    "trade_finalize",
    "monitor",
]


def role_component(role: dict) -> dict:
    role_id = role["id"]
    return {
        "component_type": "Agent",
        "id": role_id,
        "name": role["display_name"],
        "description": role["charter"],
        "instructions": role["charter"],
        "tools": tool_specs_for_role(role_id),
        "input_contracts": role.get("required_inputs", []),
        "output_contracts": role.get("allowed_outputs", []),
        "disallowed_outputs": role.get("disallowed_outputs", []),
        "metadata": {
            "role_id": role_id,
            "display_name": role["display_name"],
        },
    }


def flow_component(flow: dict) -> dict:
    return {
        "component_type": "Flow",
        "id": flow["flow_id"],
        "name": flow["name"],
        "stages": [
            {
                "id": stage["id"],
                "name": stage["id"].replace("_", " ").title(),
                "purpose": stage.get("purpose", ""),
                "agents": stage["required_roles"],
                "optional_agents": stage.get("optional_roles", []),
                "parallel": stage.get("parallel", False),
                "required_inputs": stage.get("required_inputs", []),
                "expected_outputs": stage.get("expected_outputs", []),
                "timeout_s": stage.get("timeout_s"),
                "on_seat_failure": stage.get("on_seat_failure", ""),
                "ui_panels": stage.get("ui_panels", []),
            }
            for stage in flow["stages"]
        ],
    }


def validate_flow(flow: dict) -> None:
    stage_sequence = [stage["id"] for stage in flow["stages"]]
    if stage_sequence != CANONICAL_STAGE_SEQUENCE:
        raise ValueError(f"flow stage order must be {CANONICAL_STAGE_SEQUENCE}, got {stage_sequence}")


def main() -> None:
    desk = load_json(ROOT / "authoring/desk/agentic_trading_desk.json")
    roles = load_json(ROOT / "authoring/roles/roles.json")
    flow = load_json(ROOT / "authoring/flows/investment_committee_flow.json")
    validate_flow(flow)
    agent_components = [role_component(role) for role in roles["roles"]]
    flow_spec_component = flow_component(flow)
    export = {
        "schema_version": "v2",
        "agent_spec_version": AGENT_SPEC_VERSION,
        "export_kind": "oracle_open_agent_spec",
        "metadata": {
            "name": desk["name"],
            "description": desk["description"],
            "desk_id": desk["desk_id"],
            "flow_id": desk["flow_id"],
            "runtime_targets": [
                desk["scenario_defaults"]["primary_runtime"],
                desk["scenario_defaults"]["parity_runtime"],
            ],
        },
        "components": [*agent_components, flow_spec_component],
        "desk": desk,
        "roles": roles["roles"],
        "flow": flow,
        "runtime_targets": [
            desk["scenario_defaults"]["primary_runtime"],
            desk["scenario_defaults"]["parity_runtime"],
        ],
        "contracts": {
            "role_registry": "contracts/objects/v1/role-registry.json",
            "stage_registry": "contracts/objects/v1/stage-registry.json",
            "event_registry": "contracts/agui/v1/event-registry.json"
        }
    }
    out = ROOT / "spec/exported/agentic-trading-desk.spec.json"
    out.write_text(json.dumps(export, indent=2) + "\n")
    print(out)


if __name__ == "__main__":
    main()
