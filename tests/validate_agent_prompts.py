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


class _FakeResponsesClient:
    def __init__(self) -> None:
        self.called: dict[str, object] = {}
        self.last_mode = "responses_openai_client"
        self.last_error = ""
        self.last_attempts: list[str] = []

    def complete_with_responses(
        self,
        prompt: str,
        *,
        tools: list[dict[str, object]] | None = None,
        max_tokens: int = 420,
        temperature: float = 0.2,
        local_tool_executor=None,
        max_tool_rounds: int = 3,
        max_tool_calls: int = 6,
    ) -> str:
        self.called = {
            "prompt": prompt,
            "tools": tools or [],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "max_tool_rounds": max_tool_rounds,
            "max_tool_calls": max_tool_calls,
        }
        return "STANCE: neutral CONFIDENCE: 55 TEXT: synthetic response for test."

    def capability_profile(self) -> dict[str, object]:
        return {
            "ready": True,
            "enabled": True,
            "auth_mode": "test",
            "oci_sdk_available": True,
        }


def main() -> None:
    roles = set(role_ids())
    prompt_roles = set(AGENT_SYSTEM_PROMPTS.keys())
    missing = sorted(roles - prompt_roles)
    assert not missing, f"missing system prompts for roles: {missing}"
    news_prompt = AGENT_SYSTEM_PROMPTS["news_analyst"]
    assert "get_news" in news_prompt
    assert "get_global_news" in news_prompt
    assert "never list sources" in news_prompt.lower()
    assert "web_search" not in news_prompt

    for key in ("OCI_GENAI_API_KEY",):
        os.environ.pop(key, None)
    os.environ["OCI_GENAI_ENABLE"] = "0"
    service = AgentTextService()
    text = service.generate(
        role_id="market_analyst",
        phase_id="gather",
        context={"ticker": "NVDA"},
        fallback="fallback text",
    )
    assert text == "fallback text", "expected deterministic fallback when OCI key is not configured"
    os.environ.pop("OCI_GENAI_ENABLE", None)

    # Agent text generation must route through Responses API, including tool-enabled calls.
    fake = _FakeResponsesClient()
    service2 = AgentTextService()
    service2.client = fake  # type: ignore[assignment]
    synthetic = service2.generate(
        role_id="social_analyst",
        phase_id="gather",
        context={
            "ticker": "NVDA",
            "x_search_window": {"from_date": "2026-04-01", "to_date": "2026-04-03"},
            "stocktwits_snapshot": {"ok": True, "provider": "stocktwits", "social_context": {}},
        },
        fallback="fallback social",
        max_words=160,
        tools=[
            {
                "type": "x_search",
                "from_date": "2026-04-01",
                "to_date": "2026-04-03",
                "enable_image_understanding": False,
                "enable_video_understanding": False,
            }
        ],
        temperature=0.1,
    )
    assert "synthetic response for test" in synthetic
    assert fake.called, "expected complete_with_responses to be called"
    assert fake.called["temperature"] == 0.1
    assert isinstance(fake.called["tools"], list) and fake.called["tools"], "expected x_search tool payload"
    first_tool = fake.called["tools"][0]
    assert isinstance(first_tool, dict) and first_tool.get("type") == "x_search"
    prompt_text = str(fake.called["prompt"])
    assert "Context:" in prompt_text
    assert "Runtime context JSON:" not in prompt_text
    assert "ticker: NVDA" in prompt_text
    assert '{"ticker": "NVDA"' not in prompt_text

    print('{"status": "ok"}')


if __name__ == "__main__":
    main()
