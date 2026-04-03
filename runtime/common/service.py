from __future__ import annotations

from runtime.langgraph.adapter import LangGraphAdapter
from runtime.wayflow.adapter import WayflowAdapter


def get_adapter(runtime_name: str):
    if runtime_name == "wayflow":
        return WayflowAdapter()
    if runtime_name == "langgraph":
        return LangGraphAdapter()
    raise KeyError(f"Unsupported runtime: {runtime_name}")
