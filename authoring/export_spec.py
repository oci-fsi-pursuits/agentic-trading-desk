from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_json(path: Path):
    return json.loads(path.read_text())


def main() -> None:
    desk = load_json(ROOT / "authoring/desk/agentic_trading_desk.json")
    roles = load_json(ROOT / "authoring/roles/roles.json")
    flow = load_json(ROOT / "authoring/flows/investment_committee_flow.json")
    export = {
        "schema_version": "v1",
        "export_kind": "agentic_trading_desk_spec",
        "desk": desk,
        "roles": roles["roles"],
        "flow": flow,
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
