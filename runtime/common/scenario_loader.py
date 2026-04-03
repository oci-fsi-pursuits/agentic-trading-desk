from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from runtime.common.scenario_validation import validate_scenario

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = ROOT / "scenarios"

COMMON_REQUIRED_SEATS = [
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
]

COMMON_OPTIONAL_SEATS = [
    "social_analyst",
    "macro_economist",
    "geopolitical_analyst",
    "aggressive_analyst",
    "conservative_analyst",
    "neutral_analyst",
]

SCENARIO_FILE_NAMES = [
    "single-name-earnings.json",
    "single-name-breaking-news.json",
    "sector-pair-trade-committee.json",
    "thesis-break-monitoring.json",
]


def _policy_preview(pm_decision_policy: dict[str, Any]) -> dict[str, Any]:
    preview = dict(pm_decision_policy.get("preview", {}) or {})
    if preview:
        return preview
    return {
        "outcome": "approved_with_changes",
        "position_size_bps": "variable",
        "approval_notes": "PM decision varies by vote balance, quant signal, and scenario-specific risk gates.",
    }


def _read_scenario_file(path: Path) -> dict[str, Any]:
    scenario = json.loads(path.read_text())
    validate_scenario(scenario)
    return scenario


def _normalize_scenario(raw: dict[str, Any]) -> dict[str, Any]:
    scenario = copy.deepcopy(raw)
    seat_plan = dict(scenario.get("seat_plan", {}) or {})
    required = list(seat_plan.get("required", scenario.get("required_seat_ids", COMMON_REQUIRED_SEATS)) or COMMON_REQUIRED_SEATS)
    optional = list(seat_plan.get("optional", scenario.get("optional_seat_ids", COMMON_OPTIONAL_SEATS)) or COMMON_OPTIONAL_SEATS)
    scenario["required_seat_ids"] = required
    scenario["optional_seat_ids"] = optional
    scenario["seat_plan"] = {
        "required": required,
        "optional": optional,
        "scenario_overrides": copy.deepcopy(seat_plan.get("scenario_overrides", {}) or {}),
    }

    scenario.setdefault("primary_runtime", scenario.get("runtime_goal", {}).get("primary_runtime", "wayflow"))
    scenario.setdefault("parity_runtime", scenario.get("runtime_goal", {}).get("parity_runtime", "langgraph"))
    instrument_universe = list(scenario.get("instrument_universe", []) or [])
    instrument = str(scenario.get("instrument", instrument_universe[0] if instrument_universe else "") or "").strip().upper()
    scenario["instrument"] = instrument
    scenario["instrument_universe"] = instrument_universe or ([instrument] if instrument else [])

    demo_mode = dict(scenario.get("demo_mode", {}) or {})
    demo_mode.setdefault("scripted_pm_default", _policy_preview(dict(scenario.get("pm_decision_policy", {}) or {})))
    if "instrument_label" not in scenario:
        scenario["instrument_label"] = demo_mode.get("instrument_label") or (" / ".join(scenario["instrument_universe"]) if len(scenario["instrument_universe"]) > 1 else instrument)
    scenario["demo_mode"] = demo_mode
    validate_scenario(scenario)
    return scenario


def _load_all_scenarios() -> list[dict[str, Any]]:
    scenarios = []
    for filename in SCENARIO_FILE_NAMES:
        path = SCENARIO_DIR / filename
        scenarios.append(_normalize_scenario(_read_scenario_file(path)))
    return scenarios

DATASET_FILES = {
    "single_name_earnings": "single_name_earnings.json",
    "single_name_breaking_news": "single_name_breaking_news.json",
    "sector_pair_trade_committee": "sector_pair_trade_committee.json",
    "thesis_break_monitoring": "thesis_break_monitoring.json",
}


def _load_dataset_file(dataset_name: str) -> dict[str, Any]:
    path = ROOT / "data" / "demo" / dataset_name
    return json.loads(path.read_text())


def _replace_ticker_text(value: Any, old: str, new: str) -> Any:
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [_replace_ticker_text(item, old, new) for item in value]
    if isinstance(value, dict):
        return {key: _replace_ticker_text(item, old, new) for key, item in value.items()}
    return value


def _retarget_demo_dataset(dataset: dict[str, Any], ticker: str) -> dict[str, Any]:
    source_ticker = str(dataset.get("instrument", "") or "").strip().upper()
    target_ticker = str(ticker or "").strip().upper()
    if not target_ticker or source_ticker == target_ticker:
      return dataset

    adjusted = copy.deepcopy(dataset)
    digest = hashlib.sha256(target_ticker.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big") / float(2**64 - 1)
    drift_bias = (seed - 0.5) * 0.018
    revision_bias = (seed - 0.5) * 0.5
    sentiment_bias = (seed - 0.5) * 0.35
    correlation_bias = (seed - 0.5) * 0.25
    volume_scale = 0.75 + seed * 0.9
    base_price = round(35 + seed * 225, 2)

    source_prices = adjusted.get("price_series", [])
    if len(source_prices) >= 2:
        base_returns = [(b - a) / a for a, b in zip(source_prices[:-1], source_prices[1:])]
        next_prices = [base_price]
        for base_return in base_returns:
            next_return = max(min(base_return + drift_bias, 0.12), -0.12)
            next_prices.append(round(next_prices[-1] * (1 + next_return), 2))
        adjusted["price_series"] = next_prices

    revisions = adjusted.get("estimate_revisions_pct", [])
    adjusted["estimate_revisions_pct"] = [
        round(max(min(value + revision_bias, 1.5), -1.5), 3)
        for value in revisions
    ]

    volumes = adjusted.get("volume_millions", [])
    adjusted["volume_millions"] = [round(max(value * volume_scale, 0.2), 2) for value in volumes]

    adjusted["sentiment_score"] = round(max(min(adjusted.get("sentiment_score", 0.5) + sentiment_bias, 0.98), 0.02), 3)
    adjusted["ai_basket_correlation"] = round(max(min(adjusted.get("ai_basket_correlation", 0.5) + correlation_bias, 0.98), 0.05), 3)

    prices = adjusted.get("price_series", [])
    if len(prices) >= 2:
        momentum = round((prices[-1] - prices[0]) / prices[0] * 100, 2)
        adjusted.setdefault("market_context", {})["momentum_20d_pct"] = momentum

    adjusted["instrument"] = target_ticker
    adjusted["dataset_id"] = f"{adjusted.get('dataset_id', 'demo')}_{target_ticker.lower()}_synthetic"
    return _replace_ticker_text(adjusted, source_ticker, target_ticker)


def load_demo_dataset(scenario_id: str = "single_name_earnings", ticker: str | None = None) -> dict[str, Any]:
    dataset_name = DATASET_FILES.get(scenario_id)
    if not dataset_name:
        raise KeyError(f"No demo dataset configured for scenario: {scenario_id}")
    requested_ticker = str(ticker or "").strip().upper()
    if requested_ticker:
        for candidate_name in DATASET_FILES.values():
            candidate = _load_dataset_file(candidate_name)
            if str(candidate.get("instrument", "") or "").strip().upper() == requested_ticker:
                return candidate
    base_dataset = _load_dataset_file(dataset_name)
    if requested_ticker:
        return _retarget_demo_dataset(base_dataset, requested_ticker)
    return base_dataset


def load_scenario_catalog() -> list[dict[str, Any]]:
    return _load_all_scenarios()


def load_scenario(scenario_id: str) -> dict[str, Any]:
    for item in _load_all_scenarios():
        if item["scenario_id"] == scenario_id:
            return item
    raise KeyError(f"Unknown scenario: {scenario_id}")
