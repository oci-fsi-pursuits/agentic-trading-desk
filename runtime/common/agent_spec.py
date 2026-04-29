from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.common.utils import ROOT

SPEC_PATH = ROOT / "spec" / "exported" / "agentic-trading-desk.spec.json"
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


def load_agent_spec(path: Path = SPEC_PATH) -> dict[str, Any]:
    spec = json.loads(path.read_text())
    validate_agent_spec(spec)
    return spec


def agent_components(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        component
        for component in spec.get("components", [])
        if component.get("component_type") == "Agent"
    ]


def flow_component(spec: dict[str, Any]) -> dict[str, Any]:
    for component in spec.get("components", []):
        if component.get("component_type") == "Flow":
            return component
    raise ValueError("agent spec missing Flow component")


def flow_stage_order(spec: dict[str, Any] | None = None) -> list[str]:
    loaded = spec or load_agent_spec()
    flow = flow_component(loaded)
    return [stage["id"] for stage in flow["stages"]]


def validate_agent_spec(spec: dict[str, Any]) -> None:
    if spec.get("export_kind") != "oracle_open_agent_spec":
        raise ValueError("exported spec must use export_kind=oracle_open_agent_spec")
    if not spec.get("agent_spec_version"):
        raise ValueError("exported spec missing agent_spec_version")
    components = spec.get("components")
    if not isinstance(components, list) or not components:
        raise ValueError("exported spec must include Agent and Flow components")

    agents = agent_components(spec)
    if not agents:
        raise ValueError("exported spec must include Agent components")
    agent_ids = {agent.get("id") for agent in agents}
    authored_role_ids = {role.get("id") for role in spec.get("roles", [])}
    if agent_ids != authored_role_ids:
        raise ValueError("Agent components must match authored roles")
    missing_tool_keys = [
        agent.get("id", "")
        for agent in agents
        if "tools" not in agent
    ]
    if missing_tool_keys:
        raise ValueError(f"Agent components missing tools declarations: {sorted(missing_tool_keys)}")

    stage_order = flow_stage_order(spec)
    if stage_order != CANONICAL_STAGE_SEQUENCE:
        raise ValueError(f"Flow stage order must be {CANONICAL_STAGE_SEQUENCE}, got {stage_order}")

    for stage in flow_component(spec)["stages"]:
        stage_agents = [*stage.get("agents", []), *stage.get("optional_agents", [])]
        unknown_agents = sorted(set(stage_agents).difference(agent_ids))
        if unknown_agents:
            raise ValueError(f"Flow stage {stage.get('id')} references unknown agents: {unknown_agents}")
