from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.common.agent_spec import CANONICAL_STAGE_SEQUENCE, agent_components, flow_stage_order, load_agent_spec
from runtime.common.service import get_adapter


def main() -> None:
    spec = load_agent_spec()
    stage_order = flow_stage_order(spec)
    assert stage_order == CANONICAL_STAGE_SEQUENCE
    assert get_adapter("wayflow").stage_order() == stage_order
    assert get_adapter("langgraph").stage_order() == stage_order

    agents = {agent["id"]: agent for agent in agent_components(spec)}
    assert agents["market_analyst"]["tools"], "market_analyst tools must be declared in Agent Spec"
    assert agents["news_analyst"]["tools"], "news_analyst tools must be declared in Agent Spec"
    assert agents["fundamentals_analyst"]["tools"], "fundamentals_analyst tools must be declared in Agent Spec"
    assert "tools" in agents["portfolio_manager"], "all agents must carry an explicit tools list"

    try:
        get_adapter("wayflow").execute(
            "single_name_earnings",
            [
                "market_analyst",
                "news_analyst",
                "fundamentals_analyst",
                "bull_researcher",
                "bear_researcher",
                "research_manager",
                "quant_analyst",
                "risk_manager",
                "portfolio_manager",
                "trader",
                "geopolitical_analyst",
            ],
            "NVDA",
        )
    except ValueError as exc:
        assert "scenario-suppressed seats" in str(exc)
    else:
        raise AssertionError("scenario-suppressed seats must be rejected before execution")

    print(json.dumps({"status": "ok", "stage_order": stage_order, "agents": len(agents)}, indent=2))


if __name__ == "__main__":
    main()
