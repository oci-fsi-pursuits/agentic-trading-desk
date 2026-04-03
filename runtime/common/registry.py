from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def load_json(relative_path: str) -> Any:
    path = ROOT / relative_path
    return json.loads(path.read_text())


def role_ids() -> list[str]:
    data = load_json("contracts/objects/v1/role-registry.json")
    return [item["id"] for item in data["roles"]]


def stage_ids() -> list[str]:
    data = load_json("contracts/objects/v1/stage-registry.json")
    return [item["id"] for item in data["stages"]]


def event_types() -> list[str]:
    data = load_json("contracts/agui/v1/event-registry.json")
    return [item["type"] for item in data["events"]]
