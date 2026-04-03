from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.common.agent_prompts import AGENT_SYSTEM_PROMPTS
from runtime.common.oci_genai import AgentTextService
from runtime.common.registry import role_ids


def main() -> None:
    roles = set(role_ids())
    prompt_roles = set(AGENT_SYSTEM_PROMPTS.keys())
    missing = sorted(roles - prompt_roles)
    assert not missing, f"missing system prompts for roles: {missing}"

    for key in ("OCI_GENAI_API_KEY",):
        os.environ.pop(key, None)
    os.environ["OCI_GENAI_ENABLE"] = "0"
    service = AgentTextService()
    text = service.generate(
        role_id="market_analyst",
        phase_id="gather",
        task="test task",
        context={"ticker": "NVDA"},
        fallback="fallback text",
    )
    assert text == "fallback text", "expected deterministic fallback when OCI key is not configured"
    os.environ.pop("OCI_GENAI_ENABLE", None)

    print('{"status": "ok"}')


if __name__ == "__main__":
    main()
