from __future__ import annotations

import json
import os
import re
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
TICKER_RE = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")


def _build_single_ticker_live_context(
    ticker: str,
    scenario_dataset: dict[str, Any],
    active_seat_ids: list[str] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
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


def _extract_tickers(raw: str | list[str] | None, *, limit: int = 6) -> list[str]:
    if isinstance(raw, list):
        text = ",".join(str(item or "") for item in raw)
    else:
        text = str(raw or "")
    matches = TICKER_RE.findall(text.upper())
    if not matches:
        return []
    unique: list[str] = []
    for item in matches:
        if item not in unique:
            unique.append(item)
    cap = max(1, min(int(limit or 1), 8))
    return unique[:cap]


def _replace_ticker_text(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace_ticker_text(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _replace_ticker_text(item, old, new) for key, item in value.items()}
    return value


def _retarget_dataset_for_ticker(dataset: dict[str, Any], ticker: str) -> dict[str, Any]:
    target = str(ticker or "").strip().upper()
    if not target:
        return json.loads(json.dumps(dataset))
    adjusted = json.loads(json.dumps(dataset))
    source = str(adjusted.get("instrument", "") or "").strip().upper()
    if source and source != target:
        adjusted = _replace_ticker_text(adjusted, source, target)
    adjusted["instrument"] = target
    return adjusted


def _pair_relative_metrics(primary_context: dict[str, Any], peer_contexts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    primary_prices = list(primary_context.get("price_series") or [])
    primary_latest = float(primary_prices[-1]) if primary_prices else 0.0
    primary_momentum = (primary_context.get("market_context") or {}).get("momentum_20d_pct")
    comparisons: dict[str, dict[str, Any]] = {}
    for peer_ticker, peer_context in peer_contexts.items():
        peer_prices = list(peer_context.get("price_series") or [])
        peer_latest = float(peer_prices[-1]) if peer_prices else 0.0
        peer_momentum = (peer_context.get("market_context") or {}).get("momentum_20d_pct")
        relative_ratio = None
        if primary_latest > 0 and peer_latest > 0:
            relative_ratio = round(primary_latest / peer_latest, 4)
        momentum_spread = None
        try:
            momentum_spread = round(float(primary_momentum) - float(peer_momentum), 3)
        except (TypeError, ValueError):
            momentum_spread = None
        comparisons[peer_ticker] = {
            "relative_price_ratio": relative_ratio,
            "momentum_spread_pct": momentum_spread,
        }
    return comparisons


def build_live_context(
    ticker: str,
    scenario_dataset: dict[str, Any],
    pair_peer: str = "",
    active_seat_ids: list[str] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    primary_ticker = str(ticker or "").strip().upper()
    primary_result = _build_single_ticker_live_context(
        primary_ticker,
        scenario_dataset,
        active_seat_ids=active_seat_ids,
        run_id=run_id,
    )
    peer_tickers = [item for item in _extract_tickers(pair_peer, limit=6) if item and item != primary_ticker]
    if not peer_tickers:
        primary_result["pair_mode"] = False
        primary_result["peer_tickers"] = []
        primary_result["ticker_contexts"] = {
            primary_ticker: {
                "coverage": dict(primary_result.get("coverage", {}) or {}),
                "market_context": dict(primary_result.get("market_context", {}) or {}),
                "fundamentals": dict(primary_result.get("fundamentals", {}) or {}),
                "social_context": dict(primary_result.get("social_context", {}) or {}),
                "news_items": list(primary_result.get("news_items", []) or []),
            }
        }
        return primary_result

    ticker_contexts: dict[str, dict[str, Any]] = {
        primary_ticker: {
            "coverage": dict(primary_result.get("coverage", {}) or {}),
            "domain_metadata": dict(primary_result.get("domain_metadata", {}) or {}),
            "market_context": dict(primary_result.get("market_context", {}) or {}),
            "fundamentals": dict(primary_result.get("fundamentals", {}) or {}),
            "social_context": dict(primary_result.get("social_context", {}) or {}),
            "news_items": list(primary_result.get("news_items", []) or []),
            "price_series": list(primary_result.get("price_series", []) or []),
            "volume_millions": list(primary_result.get("volume_millions", []) or []),
            "sentiment_score": primary_result.get("sentiment_score"),
            "ai_basket_correlation": primary_result.get("ai_basket_correlation"),
            "errors": list(primary_result.get("errors", []) or []),
        }
    }

    peer_contexts: dict[str, dict[str, Any]] = {}
    combined_errors = list(primary_result.get("errors", []) or [])
    for peer in peer_tickers:
        peer_dataset = _retarget_dataset_for_ticker(scenario_dataset, peer)
        peer_result = _build_single_ticker_live_context(
            peer,
            peer_dataset,
            active_seat_ids=active_seat_ids,
            run_id=run_id,
        )
        peer_context = {
            "coverage": dict(peer_result.get("coverage", {}) or {}),
            "domain_metadata": dict(peer_result.get("domain_metadata", {}) or {}),
            "market_context": dict(peer_result.get("market_context", {}) or {}),
            "fundamentals": dict(peer_result.get("fundamentals", {}) or {}),
            "social_context": dict(peer_result.get("social_context", {}) or {}),
            "news_items": list(peer_result.get("news_items", []) or []),
            "price_series": list(peer_result.get("price_series", []) or []),
            "volume_millions": list(peer_result.get("volume_millions", []) or []),
            "sentiment_score": peer_result.get("sentiment_score"),
            "ai_basket_correlation": peer_result.get("ai_basket_correlation"),
            "errors": list(peer_result.get("errors", []) or []),
        }
        ticker_contexts[peer] = peer_context
        peer_contexts[peer] = peer_context
        for item in peer_context.get("errors", []):
            value = f"{peer}:{item}"
            if value not in combined_errors:
                combined_errors.append(value)

    primary_result["pair_mode"] = True
    primary_result["peer_tickers"] = peer_tickers
    primary_result["ticker_contexts"] = ticker_contexts
    primary_result["market_context_by_ticker"] = {
        item: dict(ctx.get("market_context", {}) or {}) for item, ctx in ticker_contexts.items()
    }
    primary_result["fundamentals_by_ticker"] = {
        item: dict(ctx.get("fundamentals", {}) or {}) for item, ctx in ticker_contexts.items()
    }
    primary_result["social_context_by_ticker"] = {
        item: dict(ctx.get("social_context", {}) or {}) for item, ctx in ticker_contexts.items()
    }
    primary_result["news_items_by_ticker"] = {
        item: list(ctx.get("news_items", []) or []) for item, ctx in ticker_contexts.items()
    }
    primary_result["pair_analysis"] = _pair_relative_metrics(ticker_contexts[primary_ticker], peer_contexts)
    primary_result["errors"] = combined_errors

    quant_dataset = dict(primary_result.get("quant_dataset", {}) or {})
    quant_dataset["pair_mode"] = True
    quant_dataset["primary_ticker"] = primary_ticker
    quant_dataset["peer_tickers"] = peer_tickers
    quant_dataset["peer_price_series"] = {
        item: list(ctx.get("price_series", []) or []) for item, ctx in peer_contexts.items()
    }
    quant_dataset["peer_sentiment_score"] = {
        item: ctx.get("sentiment_score") for item, ctx in peer_contexts.items()
    }
    quant_dataset["peer_ai_basket_correlation"] = {
        item: ctx.get("ai_basket_correlation") for item, ctx in peer_contexts.items()
    }
    quant_dataset["pair_analysis"] = dict(primary_result.get("pair_analysis", {}) or {})
    primary_result["quant_dataset"] = quant_dataset

    return primary_result
