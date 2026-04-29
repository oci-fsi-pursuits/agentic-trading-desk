from __future__ import annotations

from runtime.common.agent_spec import flow_stage_order
from runtime.common.engine import BaseAdapter


class LangGraphAdapter(BaseAdapter):
    runtime_name = "langgraph"

    def stage_order(self) -> list[str]:
        return flow_stage_order()
