from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from runtime.common.data_providers import (
    fetch_fundamentals_domain,
    fetch_geopolitical_domain,
    fetch_macro_domain,
    fetch_market_domain,
    fetch_news_domain,
    fetch_social_domain,
)
from runtime.common.env_validation import parse_bool
from runtime.common.utils import now_iso

LIVE_CONTEXT_TIMEOUT_S = 8.0


def build_live_context(
    ticker: str,
    scenario_dataset: dict[str, Any],
    pair_peer: str = "",
    active_seat_ids: list[str] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    del pair_peer  # reserved for future pair-aware provider enrichment
    ticker_value = str(ticker or "").strip().upper()
    enabled = parse_bool(os.environ.get("ATD_ENABLE_LIVE_CONTEXT"), default=True)
    baseline = json.loads(json.dumps(scenario_dataset))
    baseline_as_of = baseline.get("as_of", now_iso())

    if not enabled or not ticker_value:
        return {
            "enabled": False,
            "ticker": ticker_value,
            "as_of": baseline_as_of,
            "coverage": {
                "market": "fallback",
                "news": "fallback",
                "macro": "fallback",
                "geopolitical": "fallback",
                "fundamentals": "fallback",
                "social": "fallback",
                "quant_inputs": "fallback",
            },
            "domain_metadata": {},
            "market_context": baseline.get("market_context", {}),
            "news_items": baseline.get("news_items", []),
            "fundamentals": baseline.get("fundamentals", {}),
            "macro_context": {"macro_risk": baseline.get("macro_risk", "moderate")},
            "geopolitical_context": {},
            "social_context": {"sentiment_score": baseline.get("sentiment_score", 0.5), "sentiment_label": "neutral"},
            "sentiment_score": baseline.get("sentiment_score", 0.5),
            "ai_basket_correlation": baseline.get("ai_basket_correlation", 0.5),
            "price_series": baseline.get("price_series", []),
            "volume_millions": baseline.get("volume_millions", []),
            "quant_dataset": baseline,
            "errors": [],
            "provider_debug": {},
        }

    active_seats = set(active_seat_ids or [])
    optional_fetch_enabled = bool(active_seat_ids)

    work: dict[str, Any] = {
        "market": lambda: fetch_market_domain(ticker_value),
        "news": lambda: fetch_news_domain(ticker_value),
        "fundamentals": lambda: fetch_fundamentals_domain(ticker_value),
    }
    if not optional_fetch_enabled or "social_analyst" in active_seats:
        work["social"] = lambda: fetch_social_domain(ticker_value, run_id=run_id)
    if not optional_fetch_enabled or "macro_economist" in active_seats:
        work["macro"] = fetch_macro_domain
    if not optional_fetch_enabled or "geopolitical_analyst" in active_seats:
        work["geopolitical"] = lambda: fetch_geopolitical_domain(ticker_value)

    results: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=len(work), thread_name_prefix="live_ctx") as pool:
        futures = {name: pool.submit(fn) for name, fn in work.items()}
        for name, future in futures.items():
            try:
                results[name] = future.result(timeout=LIVE_CONTEXT_TIMEOUT_S)
            except Exception:  # noqa: BLE001
                results[name] = {
                    "ok": False,
                    "provider": "",
                    "error": f"{name}_timeout_or_failure",
                    "errors": [f"{name}:timeout_or_failure"],
                    "freshness": "fallback",
                    "source_count": 0,
                    "fallback_used": False,
                    "degraded": True,
                    "degraded_reason": "timeout_or_failure",
                    "as_of": now_iso(),
                }

    market = results.get("market", {})
    news = results.get("news", {})
    macro = results.get("macro", {})
    geopolitical = results.get("geopolitical", {})
    fundamentals = results.get("fundamentals", {})
    social = results.get("social", {})

    coverage = {
        "market": "live" if market.get("ok") else "fallback",
        "news": "live" if news.get("ok") else "fallback",
        "macro": "live" if macro.get("ok") else "fallback",
        "geopolitical": "live" if geopolitical.get("ok") else "fallback",
        "fundamentals": "live" if fundamentals.get("ok") else "fallback",
        "social": "live" if social.get("ok") else "fallback",
    }

    market_context = market.get("market_context") or baseline.get("market_context", {})
    news_items = news.get("news_items") or baseline.get("news_items", [])
    fundamentals_context = fundamentals.get("fundamentals") or baseline.get("fundamentals", {})
    macro_context = macro.get("macro_context") or {"macro_risk": baseline.get("macro_risk", "moderate")}
    geopolitical_context = geopolitical.get("geopolitical_context") or {}
    social_context = social.get("social_context") or {
        "sentiment_score": baseline.get("sentiment_score", 0.5),
        "sentiment_label": "neutral",
    }
    sentiment_score = social_context.get("sentiment_score", baseline.get("sentiment_score", 0.5))
    ai_corr = market.get("ai_basket_correlation")
    if ai_corr is None:
        ai_corr = baseline.get("ai_basket_correlation", 0.5)

    quant_dataset = json.loads(json.dumps(baseline))
    quant_dataset["instrument"] = ticker_value
    quant_dataset["as_of"] = market.get("as_of") or now_iso()
    quant_dataset["price_series"] = market.get("price_series") or baseline.get("price_series", [])
    quant_dataset["volume_millions"] = market.get("volume_millions") or baseline.get("volume_millions", [])
    quant_dataset["sentiment_score"] = sentiment_score
    quant_dataset["ai_basket_correlation"] = ai_corr
    quant_dataset["news_items"] = news_items
    quant_dataset["market_context"] = market_context
    quant_dataset["fundamentals"] = fundamentals_context
    quant_dataset["macro_risk"] = macro_context.get("macro_risk", baseline.get("macro_risk", "moderate"))
    quant_dataset["quant_coverage"] = "full" if coverage["market"] == "live" else "partial"
    quant_dataset["quant_input_source"] = "live_context_builder" if coverage["market"] == "live" else "scenario_fallback"
    coverage["quant_inputs"] = "live" if coverage["market"] == "live" else "fallback"

    errors: list[str] = []
    domain_metadata: dict[str, dict[str, Any]] = {}
    for domain_name, result in results.items():
        if not result.get("ok"):
            errors.append(f"{domain_name}:{result.get('error', 'unavailable')}")
        for item in result.get("errors", []):
            if item and item not in errors:
                errors.append(str(item))
        domain_metadata[domain_name] = {
            "provider": result.get("provider", ""),
            "as_of": result.get("as_of", baseline_as_of),
            "freshness": result.get("freshness", "fallback"),
            "source_count": int(result.get("source_count", 0) or 0),
            "fallback_used": bool(result.get("fallback_used", False)),
            "degraded": bool(result.get("degraded", False)),
            "degraded_reason": result.get("degraded_reason", ""),
            "ok": bool(result.get("ok", False)),
        }

    return {
        "enabled": True,
        "ticker": ticker_value,
        "as_of": now_iso(),
        "coverage": coverage,
        "domain_metadata": domain_metadata,
        "market_context": market_context,
        "news_items": news_items,
        "fundamentals": fundamentals_context,
        "macro_context": macro_context,
        "geopolitical_context": geopolitical_context,
        "social_context": social_context,
        "sentiment_score": sentiment_score,
        "ai_basket_correlation": ai_corr,
        "price_series": quant_dataset.get("price_series", []),
        "volume_millions": quant_dataset.get("volume_millions", []),
        "quant_dataset": quant_dataset,
        "errors": errors,
        "provider_debug": {domain_name: meta["provider"] for domain_name, meta in domain_metadata.items()},
    }
