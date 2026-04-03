from __future__ import annotations

from runtime.common.engine import BaseAdapter


class LangGraphAdapter(BaseAdapter):
    runtime_name = "langgraph"

    def stage_order(self) -> list[str]:
        return ["gather", "quantify", "debate", "synthesize", "risk_review", "pm_review", "trade_finalize", "monitor"]
