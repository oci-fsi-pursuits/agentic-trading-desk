from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Iterable

from runtime.common.contract_validation import validate_event, validate_run_payload
from runtime.common.analyst_tools import tool_specs_for_role
from runtime.common.data_providers import X_SEARCH_LOOKBACK_DAYS, fetch_news_domain
from runtime.common.live_context import build_live_context
from runtime.common.oci_genai import AgentTextService
from runtime.common.quant_runner import run_quant
from runtime.common.scenario_loader import load_demo_dataset, load_scenario
from runtime.common.store import RunStore
from runtime.common.types import EventEnvelope, RunArtifacts, RunContext
from runtime.common.utils import make_id, now_iso, sleep_debate_turn, sleep_tick

STANCE_RE = re.compile(r"\bSTANCE\s*[:=]\s*(bull|bear|neutral|dissent|long|short)\b", re.IGNORECASE)
CONFIDENCE_RE = re.compile(r"\bCONFIDENCE\s*[:=]\s*(100|[0-9]{1,2})(?:\s*%?)\b", re.IGNORECASE)
TEXT_LABEL_RE = re.compile(r"^\s*TEXT\s*[:=]\s*", re.IGNORECASE)
TICKER_RE = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")
STANCE_ALIASES = {
    "bull": "long",
    "bear": "short",
    "dissent": "short",
    "long": "long",
    "short": "short",
    "neutral": "neutral",
}
DEFAULT_ROLE_STANCE = {
    "market_analyst": "long",
    "news_analyst": "neutral",
    "fundamentals_analyst": "long",
    "social_analyst": "long",
    "macro_economist": "neutral",
    "geopolitical_analyst": "short",
    "bull_researcher": "long",
    "bear_researcher": "short",
    "aggressive_analyst": "long",
    "conservative_analyst": "short",
    "neutral_analyst": "neutral",
    "quant_analyst": "neutral",
    "research_manager": "neutral",
    "risk_manager": "neutral",
    "portfolio_manager": "neutral",
    "trader": "neutral",
}
BREAKING_NEWS_AUTO_DELAY_S = 1.2


class BaseAdapter(ABC):
    runtime_name: str

    def __init__(self) -> None:
        self.dataset = load_demo_dataset()
        self.agent_text = AgentTextService()

    def agent_narrative(
        self,
        role_id: str,
        phase_id: str,
        context: dict[str, Any],
        fallback: str,
        max_words: int = 200,
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ) -> str:
        return self.agent_text.generate(
            role_id,
            phase_id,
            context,
            fallback,
            max_words=max_words,
            tools=tools,
            temperature=temperature,
        )

    @staticmethod
    def compact_label(text: str, fallback: str) -> str:
        value = " ".join((text or "").split()).strip()
        if not value:
            return fallback
        return value[:120]

    @staticmethod
    def normalize_stance(value: str | None, default: str = "neutral") -> str:
        candidate = str(value or "").strip().lower()
        return STANCE_ALIASES.get(candidate, STANCE_ALIASES.get(default, "neutral"))

    @staticmethod
    def normalize_confidence(value: int | float | str | None, default: int = 70) -> int:
        try:
            numeric = int(round(float(value)))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            numeric = int(default)
        return max(0, min(100, numeric))

    @staticmethod
    def deterministic_ratio(*parts: Any) -> float:
        raw = "||".join(str(part) for part in parts)
        digest = hashlib.sha256(raw.encode("utf-8")).digest()
        return int.from_bytes(digest[:8], "big") / float(2**64 - 1)

    def deterministic_int(self, low: int, high: int, *parts: Any) -> int:
        lo = int(low)
        hi = int(high)
        if hi <= lo:
            return lo
        ratio = self.deterministic_ratio(*parts)
        return lo + int(round((hi - lo) * ratio))

    @staticmethod
    def metric_value(metrics: Iterable[dict[str, Any]], name: str, default: float = 0.0) -> float:
        for metric in metrics:
            if metric.get("name") == name:
                try:
                    return float(metric.get("value", default))
                except (TypeError, ValueError):
                    return default
        return default

    def vote_breakdown(self, claims: Iterable[dict[str, Any]]) -> dict[str, int]:
        counts = {"long": 0, "short": 0, "neutral": 0}
        seat_votes: dict[str, str] = {}
        unscoped_votes: list[str] = []
        for claim in claims:
            stance = self.normalize_stance(claim.get("stance"), "neutral")
            role_id = str((claim.get("provenance", {}) or {}).get("producer_role", "") or "").strip().lower()
            if role_id:
                # One vote per seat: later turns overwrite earlier turns for that seat.
                seat_votes[role_id] = stance
            else:
                unscoped_votes.append(stance)
        if seat_votes:
            for stance in seat_votes.values():
                counts[stance] = counts.get(stance, 0) + 1
            return counts
        for stance in unscoped_votes:
            counts[stance] = counts.get(stance, 0) + 1
        return counts

    @staticmethod
    def stage_objective(scenario: dict[str, Any], stage_id: str, fallback: str) -> str:
        scenario_type = str(scenario.get("scenario_type", "") or "")
        objectives: dict[str, dict[str, str]] = {
            "pre_event_initiation": {
                "gather": "Collect pre-event technical, news, and fundamentals inputs before the desk debates an earnings setup.",
                "debate": "Run structured long-short committee debate on the pre-event setup using analyst outputs and quant validation.",
                "risk_review": "Apply event-risk, liquidity, and correlation limits before any pre-earnings position can go forward.",
                "trade_finalize": "Prepare a starter-position execution plan with tight entry discipline ahead of earnings.",
                "monitor": "Track earnings release triggers, add conditions, and exit conditions after the pre-event decision.",
            },
            "breaking_news_reunderwrite": {
                "gather": "Collect headline quality, second-source confirmation, and immediate market-reaction inputs after the reroute.",
                "debate": "Run a fast long-short debate on whether the desk should act now, defer, or stay neutral on the headline.",
                "risk_review": "Apply headline-confirmation, liquidity, and implementation-risk controls before any action is approved.",
                "trade_finalize": "Prepare urgent execution guidance with explicit spread discipline, timing, and abort conditions.",
                "monitor": "Track follow-up headlines, confirmation signals, and reversal risk after the breaking-news decision.",
            },
            "relative_value_pair": {
                "gather": "Collect relative-value inputs on both legs so the desk can evaluate a spread trade rather than an outright view.",
                "debate": "Run structured debate on whether the proposed pair offers a clean relative-value edge.",
                "risk_review": "Apply pair-specific constraints including borrow, leg balance, beta awareness, and joint liquidity.",
                "trade_finalize": "Prepare coordinated execution guidance for the pair with hedge discipline and leg sequencing.",
                "monitor": "Track spread drift, factor divergence, and borrow conditions after the pair decision.",
            },
            "thesis_break_review": {
                "gather": "Collect forensic evidence on what broke in the original thesis and whether deterioration is temporary or structural.",
                "debate": "Run hold-trim-exit debate on the existing position using current evidence and quant deterioration signals.",
                "risk_review": "Decide whether remaining exposure is justified or whether the desk should reduce or fully exit.",
                "trade_finalize": "Prepare unwind or reduction guidance for the existing position with liquidity-aware execution.",
                "monitor": "Track residual downside risk and define any re-entry gates after the thesis-break decision.",
            },
        }
        return objectives.get(scenario_type, {}).get(stage_id, fallback)

    @staticmethod
    def normalize_breaking_news_mode(value: str | None) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"manual", "manual_now", "manual-now", "immediate", "force"}:
            return "manual"
        if raw in {"auto_after_gather", "auto-after-gather", "auto", "timer", "timed", "delayed"}:
            return "auto_after_gather"
        return "off"

    @staticmethod
    def _is_blocking_liquidity_regime(liquidity: str) -> bool:
        text = str(liquidity or "").strip().lower()
        if not text:
            return False
        return any(token in text for token in ("halt", "suspended", "closed", "illiquid"))

    @staticmethod
    def projected_remaining_exposure_bps(current_size_bps: int, position_action: str, position_size_bps: int) -> int:
        current = max(0, int(current_size_bps or 0))
        proposed = max(0, int(position_size_bps or 0))
        action = str(position_action or "").strip().lower()
        if action == "exit":
            return 0
        if action == "trim":
            return max(0, current - proposed)
        if action == "hold":
            return max(current, proposed or current)
        if action in {"initiate", "add"}:
            return proposed
        if action == "defer":
            return current
        return proposed or current

    def evaluate_constraint_gates(
        self,
        scenario: dict[str, Any],
        constraints: list[dict[str, Any]],
        *,
        position_action: str,
        position_size_bps: int,
        effective_breaking_news: bool,
    ) -> tuple[list[str], list[str]]:
        scenario_type = str(scenario.get("scenario_type", "") or "")
        starting_state = dict(scenario.get("starting_position_state", {}) or {})
        current_size = int(starting_state.get("size_bps", 0) or 0)
        projected_remaining_bps = self.projected_remaining_exposure_bps(
            current_size,
            position_action,
            position_size_bps,
        )
        demo_mode = dict(scenario.get("demo_mode", {}) or {})
        hedge_leg_note = str(demo_mode.get("hedge_leg_note", "") or "").strip().lower()
        market_context = dict(self.dataset.get("market_context", {}) or {})
        fundamentals_context = dict(self.dataset.get("fundamentals", {}) or {})
        liquidity_state = str(market_context.get("liquidity", "") or "")
        correlation_value = self.dataset.get("ai_basket_correlation")
        try:
            correlation = float(correlation_value)
        except (TypeError, ValueError):
            correlation = None

        blocking_reasons: list[str] = []
        warning_reasons: list[str] = []

        for constraint in constraints:
            if not isinstance(constraint, dict):
                continue
            severity = str(constraint.get("severity", "") or "").strip().lower()
            constraint_id = str(constraint.get("constraint_id", "") or "").strip()
            constraint_type = str(constraint.get("constraint_type", "") or "").strip().lower()
            label = str(constraint.get("label", "") or constraint_id or "constraint").strip()
            value = constraint.get("value")
            reason = ""

            if constraint_type == "position_limit" and isinstance(value, (int, float)):
                cap = int(round(float(value)))
                if scenario_type == "thesis_break_review":
                    if projected_remaining_bps > cap:
                        reason = (
                            f"{label} caps remaining exposure at {cap} bps, "
                            f"projected remaining exposure is {projected_remaining_bps} bps."
                        )
                elif position_size_bps > cap:
                    reason = f"{label} caps size at {cap} bps, proposed size is {position_size_bps} bps."

            if (
                not reason
                and constraint_type == "liquidity"
                and str(value or "").strip().lower() == "required"
                and position_action in {"initiate", "add", "hold", "trim", "exit"}
                and self._is_blocking_liquidity_regime(liquidity_state)
            ):
                reason = f"{label} failed because live liquidity is '{liquidity_state}'."

            if (
                not reason
                and constraint_id == "borrow_check"
                and str(value or "").strip().lower() == "required"
                and position_action in {"initiate", "add"}
                and "short" not in hedge_leg_note
            ):
                reason = f"{label} failed because short-leg borrow/locate was not confirmed."

            if (
                not reason
                and constraint_id == "pair_beta_neutrality"
                and str(value or "").strip().lower() == "required"
                and position_action in {"initiate", "add"}
                and correlation is not None
                and correlation >= 0.95
            ):
                reason = (
                    f"{label} failed because AI-basket correlation is {correlation:.2f}, "
                    "too high for beta-aware spread expression."
                )

            if (
                not reason
                and constraint_id == "headline_confirmation"
                and str(value or "").strip().lower() == "required"
                and effective_breaking_news
                and position_action in {"initiate", "add"}
            ):
                reason = f"{label} is still outstanding, position should wait for second-source confirmation."

            if (
                not reason
                and scenario_type == "thesis_break_review"
                and constraint_id == "max_drawdown_response"
                and str(value or "").strip().lower() == "required"
                and position_action == "hold"
            ):
                deterioration_signals: list[str] = []
                try:
                    momentum_value = float(market_context.get("momentum_20d_pct"))
                except (TypeError, ValueError):
                    momentum_value = None
                if momentum_value is not None and momentum_value <= 0:
                    deterioration_signals.append("negative_20d_momentum")
                revision_trend = str(fundamentals_context.get("estimate_revision_trend", "") or "").strip().lower()
                if revision_trend in {"negative", "deteriorating", "down", "weakening"}:
                    deterioration_signals.append("negative_revision_trend")
                if deterioration_signals:
                    reason = (
                        f"{label} requires an explicit de-risk response while deterioration persists "
                        f"({', '.join(deterioration_signals[:2])})."
                    )

            if (
                not reason
                and scenario_type == "thesis_break_review"
                and constraint_id == "software_factor_correlation"
                and str(value or "").strip().lower() == "elevated_watch"
                and correlation is not None
                and correlation >= 0.75
                and position_action in {"hold", "trim"}
            ):
                reason = (
                    f"{label} warning: factor correlation is {correlation:.2f}; "
                    "prefer smaller residual exposure and tighter re-entry gates."
                )

            if not reason:
                continue

            if severity == "blocking":
                blocking_reasons.append(reason)
            elif severity == "warning":
                warning_reasons.append(reason)

        return blocking_reasons, warning_reasons

    def resolve_pm_policy(
        self,
        ctx: RunContext,
        scenario: dict[str, Any],
        claims: list[dict[str, Any]],
        metrics: list[dict[str, Any]],
        constraints: list[dict[str, Any]],
        effective_breaking_news: bool,
    ) -> dict[str, Any]:
        policy = dict(scenario.get("pm_decision_policy", {}) or {})
        scenario_type = str(scenario.get("scenario_type", "") or "")
        branch_conditions = dict(scenario.get("branch_conditions", {}) or {})
        votes = self.vote_breakdown(claims)
        net_votes = votes["long"] - votes["short"]
        composite_signal = self.metric_value(metrics, "composite signal")
        revision_signal = self.metric_value(metrics, "estimate revision score")
        warning_count = sum(1 for item in constraints if str(item.get("severity", "")).lower() == "warning")
        noise = (self.deterministic_ratio(scenario.get("scenario_id", ""), ctx.ticker, "pm-policy") - 0.5) * 0.8
        adjusted_signal = composite_signal + revision_signal * 0.25 + net_votes * 0.55 + noise + float(policy.get("signal_bias", 0.0) or 0.0)
        neutral_vote_band = int(policy.get("neutral_vote_band", 1) or 1)
        starting_state = dict(scenario.get("starting_position_state", {}) or {})
        current_size = int(starting_state.get("size_bps", 0) or 0)
        action = "defer"
        stance = "neutral"
        outcome = "approved_with_changes"

        if scenario_type == "thesis_break_review":
            exit_threshold = float(policy.get("exit_signal_threshold", -2.5) or -2.5)
            trim_threshold = float(policy.get("trim_signal_threshold", -1.0) or -1.0)
            if adjusted_signal <= exit_threshold or votes["short"] > votes["long"]:
                action = "exit"
                stance = "neutral"
                outcome = "approved"
            elif adjusted_signal <= trim_threshold or votes["short"] >= votes["long"]:
                action = "trim"
                stance = "long"
                outcome = "approved_with_changes"
            else:
                action = "hold"
                stance = "long"
                outcome = "approved"
        elif scenario_type == "relative_value_pair":
            long_threshold = float(policy.get("long_signal_threshold", 3.8) or 3.8)
            allow_short = bool(policy.get("allow_short"))
            if adjusted_signal >= long_threshold and votes["long"] >= max(votes["short"], 1):
                stance = "long"
                action = "initiate"
                outcome = "approved_with_changes" if warning_count else "approved"
            elif allow_short and adjusted_signal <= float(policy.get("short_signal_threshold", -3.0) or -3.0):
                stance = "short"
                action = "initiate"
                outcome = "approved_with_changes"
            else:
                stance = "neutral"
                action = "defer"
                outcome = "approved_with_changes"
        elif scenario_type == "breaking_news_reunderwrite":
            long_threshold = float(policy.get("long_signal_threshold", 2.0) or 2.0)
            short_threshold = float(policy.get("short_signal_threshold", -1.15) or -1.15)
            if "requires_news_confirmation" in branch_conditions:
                requires_confirmation = bool(branch_conditions.get("requires_news_confirmation"))
            else:
                requires_confirmation = bool(policy.get("confirmation_requires_strong_edge"))
            if requires_confirmation and effective_breaking_news and (
                abs(adjusted_signal) < max(abs(long_threshold), abs(short_threshold)) or abs(net_votes) <= neutral_vote_band
            ):
                stance = "neutral"
                action = "defer"
                outcome = "approved_with_changes"
            elif adjusted_signal <= short_threshold and votes["short"] >= votes["long"]:
                stance = "short"
                action = "initiate"
                outcome = "approved_with_changes"
            elif adjusted_signal >= long_threshold and votes["long"] >= votes["short"] + 1:
                stance = "long"
                action = "initiate"
                outcome = "approved_with_changes" if warning_count else "approved"
            else:
                stance = "neutral"
                action = "defer"
                outcome = "approved_with_changes"
        else:
            long_threshold = float(policy.get("long_signal_threshold", 2.8) or 2.8)
            short_threshold = float(policy.get("short_signal_threshold", -2.6) or -2.6)
            if adjusted_signal >= long_threshold and net_votes >= 0:
                stance = "long"
                action = "initiate"
                outcome = "approved_with_changes" if warning_count else "approved"
            elif adjusted_signal <= short_threshold and net_votes <= 0:
                stance = "short"
                action = "initiate"
                outcome = "approved_with_changes"
            else:
                stance = "neutral"
                action = "defer"
                outcome = "approved_with_changes"

        action_map = dict(policy.get("action_map", {}) or {})
        if scenario_type != "thesis_break_review" and stance in action_map:
            mapped_action = str(action_map.get(stance, action) or action).strip().lower()
            if mapped_action:
                action = mapped_action

        size_ranges = dict(policy.get("size_ranges_bps", {}) or {})
        if scenario_type == "thesis_break_review":
            size_key = action
        else:
            size_key = stance
        low, high = size_ranges.get(size_key, [current_size, current_size])[:2]
        size_bps = self.deterministic_int(int(low or 0), int(high or low or 0), scenario.get("scenario_id", ""), ctx.ticker, action, stance)
        if warning_count and stance in {"long", "short"}:
            size_bps = max(0, int(round(size_bps * float(policy.get("warning_size_multiplier", 1.0) or 1.0))))
        if action == "defer":
            size_bps = 0
        if action == "hold":
            size_bps = max(size_bps, current_size or size_bps)
        if action == "exit":
            size_bps = current_size or size_bps

        if action == "exit":
            trade_side = "SELL"
        elif action == "trim":
            trade_side = "SELL"
        elif action == "hold":
            trade_side = "HOLD"
        elif stance == "short":
            trade_side = "SELL"
        elif stance == "long":
            trade_side = str(scenario.get("demo_mode", {}).get("trade_side", "BUY") or "BUY").upper()
        else:
            trade_side = "HOLD"

        if action == "defer":
            note = "Stay neutral until evidence and implementation conditions improve."
        elif action == "exit":
            note = "Exit the remaining position and require fresh underwriting before any re-entry."
        elif action == "trim":
            note = "Reduce exposure materially, preserve flexibility, and reassess only after stabilization."
        elif action == "hold":
            note = "Maintain the current position with tighter monitoring and no add until conditions improve."
        elif scenario_type == "breaking_news_reunderwrite":
            note = "Act only with reduced size and clear confirmation because the headline path remains fragile."
        elif scenario_type == "relative_value_pair":
            note = "Run the pair only with balanced gross legs, borrow confirmed, and spread discipline."
        else:
            note = "Start with measured size and add only if the scenario confirms."

        blocking_reasons, warning_reasons = self.evaluate_constraint_gates(
            scenario,
            constraints,
            position_action=action,
            position_size_bps=int(size_bps),
            effective_breaking_news=effective_breaking_news,
        )
        if blocking_reasons:
            if scenario_type == "thesis_break_review":
                action = "exit"
                stance = "neutral"
                size_bps = max(int(current_size or 0), int(size_bps or 0))
                trade_side = "SELL"
                outcome = "approved_with_changes"
                note = (
                    "Constraint gate forced a full exit: "
                    f"{blocking_reasons[0]}"
                )
            else:
                action = "defer"
                stance = "neutral"
                size_bps = 0
                trade_side = "HOLD"
                outcome = "rejected"
                note = (
                    "Trade blocked by mandatory constraint: "
                    f"{blocking_reasons[0]}"
                )
        elif warning_reasons:
            if outcome == "approved":
                outcome = "approved_with_changes"
            note = f"{note} Warning gate: {warning_reasons[0]}"

        return {
            "outcome": outcome,
            "stance": stance,
            "position_action": action,
            "position_size_bps": int(size_bps),
            "trade_side": trade_side,
            "approval_notes": note,
            "votes": votes,
            "adjusted_signal": round(adjusted_signal, 3),
            "warning_count": warning_count,
            "constraint_gate_blockers": blocking_reasons,
            "constraint_gate_warnings": warning_reasons,
            "requires_news_confirmation": (
                bool(branch_conditions.get("requires_news_confirmation"))
                if "requires_news_confirmation" in branch_conditions
                else bool(policy.get("confirmation_requires_strong_edge"))
            ),
        }

    def parse_stance_confidence_and_text(
        self,
        role_id: str,
        narrative: str,
        default_stance: str | None = None,
        default_confidence: int = 70,
    ) -> tuple[str, int, str]:
        default = self.normalize_stance(default_stance or DEFAULT_ROLE_STANCE.get(role_id, "neutral"))
        text = str(narrative or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            return default, self.normalize_confidence(default_confidence), text
        stance_match = STANCE_RE.search(text)
        confidence_match = CONFIDENCE_RE.search(text)
        stance = default
        if stance_match:
            stance = self.normalize_stance(stance_match.group(1).lower(), default)
        confidence = self.normalize_confidence(confidence_match.group(1) if confidence_match else default_confidence)
        cleaned = STANCE_RE.sub("", text)
        cleaned = CONFIDENCE_RE.sub("", cleaned)
        cleaned = TEXT_LABEL_RE.sub("", cleaned).strip(" -:;")
        cleaned = "\n".join(line.rstrip() for line in cleaned.splitlines())
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
        return stance, confidence, cleaned or text

    def parse_stance_and_text(self, role_id: str, narrative: str, default_stance: str | None = None) -> tuple[str, str]:
        stance, _, text = self.parse_stance_confidence_and_text(role_id, narrative, default_stance)
        return stance, text

    def split_breaking_news_outputs(self, text: str, display_instrument: str) -> tuple[str, str]:
        cleaned = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not cleaned:
            source_note = (
                f"BREAKING NEWS - Simulated severe development for {display_instrument} "
                "requires immediate desk reroute."
            )
            risk_note = (
                "Risk asymmetry shifted sharply: downside-tail risk and execution uncertainty "
                "rose versus prior assumptions."
            )
            return source_note, risk_note

        if not re.match(r"^\s*breaking news\s*-\s*", cleaned, flags=re.IGNORECASE):
            cleaned = f"BREAKING NEWS - {cleaned.lstrip('-: ')}"

        risk_note = ""
        for line in cleaned.splitlines():
            match = re.match(r"^\s*risk asymmetry\s*[-:]\s*(.+)$", line.strip(), flags=re.IGNORECASE)
            if match:
                risk_note = match.group(1).strip()
                break

        if not risk_note:
            first_line = next((line.strip() for line in cleaned.splitlines() if line.strip()), cleaned)
            first_line = re.sub(r"^\s*breaking news\s*-\s*", "", first_line, flags=re.IGNORECASE).strip()
            sentence = re.split(r"(?<=[.!?])\s+", first_line, maxsplit=1)[0].strip()
            if not sentence:
                sentence = "The simulated development materially increased downside-tail and execution risk."
            risk_note = f"Risk asymmetry shifted: {sentence}"

        return cleaned, risk_note

    def role_stance_from_decision(self, outcome: str) -> str:
        if outcome == "rejected":
            return "neutral"
        if outcome in {"approved", "approved_with_changes"}:
            return "long"
        return "neutral"

    @abstractmethod
    def stage_order(self) -> list[str]:
        raise NotImplementedError

    def build_context(
        self,
        scenario_id: str,
        active_seat_ids: list[str] | None = None,
        ticker: str | None = None,
        run_id: str | None = None,
    ) -> tuple[RunContext, dict[str, Any]]:
        scenario = load_scenario(scenario_id)
        seats = active_seat_ids or [*scenario["required_seat_ids"], *scenario["optional_seat_ids"]]
        requested_tokens = self.extract_tickers(ticker, limit=4)
        requested_ticker = requested_tokens[0] if requested_tokens else (ticker or scenario.get("instrument", "NVDA")).strip().upper()
        if not requested_ticker:
            requested_ticker = "NVDA"
        self.dataset = load_demo_dataset(scenario_id, requested_ticker)
        requested_tickers = requested_tokens or [requested_ticker]
        return (
            RunContext(
                run_id=run_id or make_id("run"),
                scenario_id=scenario_id,
                runtime=self.runtime_name,
                active_seat_ids=seats,
                ticker=requested_ticker,
                tickers=requested_tickers,
                demo_mode=True,
            ),
            scenario,
        )

    @staticmethod
    def extract_tickers(raw: str | None, limit: int = 2) -> list[str]:
        matches = TICKER_RE.findall((raw or "").upper())
        if not matches:
            return []
        cap = max(1, min(int(limit or 1), 6))
        return matches[:cap]

    def make_event(self, ctx: RunContext, stage_id: str, producer: str, event_type: str, payload: dict[str, Any]) -> EventEnvelope:
        return EventEnvelope(
            schema_version="v1",
            event_id=make_id("evt"),
            event_type=event_type,
            run_id=ctx.run_id,
            stage_id=stage_id,
            emitted_at=now_iso(),
            producer=producer,
            payload=payload,
        )

    def emit(self, artifacts: RunArtifacts, store: RunStore, envelope: EventEnvelope) -> dict[str, Any]:
        event = envelope.as_dict()
        validate_event(event)
        artifacts.event_log.append(event)
        store.append_event(event)
        # Persist object-store snapshots throughout execution so the UI can render live state.
        store.write_objects(artifacts)
        return event

    def source_object(self, ctx: RunContext, stage_id: str, role_id: str, source_id: str, source_type: str, title: str, content: str, freshness: str = "snapshot") -> dict[str, Any]:
        return {
            "schema_version": "v1",
            "source_id": source_id,
            "source_type": source_type,
            "title": title,
            "content": content,
            "freshness": freshness,
            "provenance": {
                "run_id": ctx.run_id,
                "stage_id": stage_id,
                "producer_role": role_id,
                "dataset_refs": [self.dataset["dataset_id"]],
                "emitted_at": now_iso(),
            },
        }

    def evidence_object(self, ctx: RunContext, stage_id: str, role_id: str, evidence_id: str, evidence_type: str, title: str, summary: str, source_ids: list[str], confidence: float, tags: list[str]) -> dict[str, Any]:
        return {
            "schema_version": "v1",
            "evidence_id": evidence_id,
            "evidence_type": evidence_type,
            "title": title,
            "summary": summary,
            "source_ids": source_ids,
            "confidence": confidence,
            "tags": tags,
            "provenance": {
                "run_id": ctx.run_id,
                "stage_id": stage_id,
                "producer_role": role_id,
                "dataset_refs": [self.dataset["dataset_id"]],
                "emitted_at": now_iso(),
            },
        }

    def claim_object(self, ctx: RunContext, role_id: str, statement: str, stance: str, supporting_evidence_ids: list[str], counter_evidence_ids: list[str], confidence: float) -> dict[str, Any]:
        return {
            "schema_version": "v1",
            "claim_id": make_id("clm"),
            "stance": stance,
            "statement": statement,
            "supporting_evidence_ids": supporting_evidence_ids,
            "counter_evidence_ids": counter_evidence_ids,
            "confidence": confidence,
            "provenance": {
                "run_id": ctx.run_id,
                "stage_id": "debate",
                "producer_role": role_id,
                "dataset_refs": [self.dataset["dataset_id"]],
                "emitted_at": now_iso(),
            },
        }

    def metric_object(self, ctx: RunContext, metric_id: str, name: str, value: Any, unit: str, code_artifact_id: str, confidence: float, coverage: str = "full") -> dict[str, Any]:
        return {
            "schema_version": "v1",
            "metric_id": metric_id,
            "name": name,
            "value": value,
            "unit": unit,
            "code_artifact_id": code_artifact_id,
            "confidence": confidence,
            "coverage": coverage,
            "provenance": {
                "run_id": ctx.run_id,
                "stage_id": "quantify",
                "producer_role": "quant_analyst",
                "dataset_refs": [self.dataset["dataset_id"]],
                "emitted_at": now_iso(),
            },
        }

    def artifact_object(self, ctx: RunContext, stage_id: str, role_id: str, artifact_type: str, label: str, storage_uri: str, content_type: str = "application/json") -> dict[str, Any]:
        return {
            "schema_version": "v1",
            "artifact_id": make_id("art"),
            "artifact_type": artifact_type,
            "label": label,
            "storage_uri": storage_uri,
            "content_type": content_type,
            "provenance": {
                "run_id": ctx.run_id,
                "stage_id": stage_id,
                "producer_role": role_id,
                "dataset_refs": [self.dataset["dataset_id"]],
                "emitted_at": now_iso(),
            },
        }

    def constraint_object(self, ctx: RunContext, constraint_id: str, constraint_type: str, label: str, value: Any, severity: str) -> dict[str, Any]:
        return {
            "schema_version": "v1",
            "constraint_id": constraint_id,
            "constraint_type": constraint_type,
            "label": label,
            "value": value,
            "severity": severity,
            "provenance": {
                "run_id": ctx.run_id,
                "stage_id": "risk_review",
                "producer_role": "risk_manager",
                "dataset_refs": [self.dataset["dataset_id"]],
                "emitted_at": now_iso(),
            },
        }

    def decision_object(
        self,
        ctx: RunContext,
        stage_id: str,
        role_id: str,
        decision_type: str,
        outcome: str,
        linked_claim_ids: list[str],
        linked_constraint_ids: list[str],
        position_size_bps: int | None = None,
        dissent_claim_ids: list[str] | None = None,
        requires_risk_recheck: bool = False,
        stance: str | None = None,
        position_action: str | None = None,
    ) -> dict[str, Any]:
        payload = {
            "schema_version": "v1",
            "decision_id": make_id("dec"),
            "decision_type": decision_type,
            "outcome": outcome,
            "linked_claim_ids": linked_claim_ids,
            "linked_constraint_ids": linked_constraint_ids,
            "dissent_claim_ids": dissent_claim_ids or [],
            "requires_risk_recheck": requires_risk_recheck,
            "provenance": {
                "run_id": ctx.run_id,
                "stage_id": stage_id,
                "producer_role": role_id,
                "dataset_refs": [self.dataset["dataset_id"]],
                "emitted_at": now_iso(),
            },
        }
        if position_size_bps is not None:
            payload["position_size_bps"] = position_size_bps
        if stance is not None:
            payload["stance"] = self.normalize_stance(stance)
        if position_action is not None:
            payload["position_action"] = position_action
        return payload

    def ticket_object(
        self,
        ctx: RunContext,
        ticket_type: str,
        display_instrument: str,
        legs: list[dict[str, Any]],
        constraint_ids: list[str],
        approved_by: str,
        entry_conditions: list[str],
        exit_conditions: list[str],
        time_horizon: str,
    ) -> dict[str, Any]:
        normalized_legs: list[dict[str, Any]] = []
        gross_bps = 0
        net_bps = 0
        for index, raw_leg in enumerate(legs):
            side = str(raw_leg.get("side", "HOLD") or "HOLD").strip().upper()
            if side not in {"BUY", "SELL", "HOLD"}:
                side = "HOLD"
            size_bps = max(0, int(raw_leg.get("size_bps", 0) or 0))
            role = str(raw_leg.get("role", "primary" if index == 0 else "hedge") or "").strip().lower()
            if role not in {"primary", "hedge"}:
                role = "primary" if index == 0 else "hedge"
            leg = {
                "leg_id": make_id("leg"),
                "instrument": str(raw_leg.get("instrument", "") or "").strip().upper(),
                "side": side,
                "size_bps": size_bps,
                "role": role,
            }
            normalized_legs.append(leg)
            if side in {"BUY", "SELL"}:
                gross_bps += size_bps
            if side == "BUY":
                net_bps += size_bps
            elif side == "SELL":
                net_bps -= size_bps

        display_label = str(display_instrument or "").strip()
        if not display_label:
            display_label = " / ".join(leg["instrument"] for leg in normalized_legs if leg["instrument"]) or ctx.ticker

        normalized_ticket_type = str(ticket_type or "").strip().lower()
        if normalized_ticket_type not in {"single_leg", "pair_trade"}:
            normalized_ticket_type = "pair_trade" if len(normalized_legs) > 1 else "single_leg"

        return {
            "schema_version": "v2",
            "ticket_id": make_id("tkt"),
            "ticket_type": normalized_ticket_type,
            "display_instrument": display_label,
            "legs": normalized_legs,
            "exposure": {
                "gross_bps": gross_bps,
                "net_bps": net_bps,
            },
            "time_horizon": time_horizon,
            "entry_conditions": entry_conditions,
            "exit_conditions": exit_conditions,
            "constraint_ids": constraint_ids,
            "approved_by": approved_by,
            "provenance": {
                "run_id": ctx.run_id,
                "stage_id": "trade_finalize",
                "producer_role": "trader",
                "dataset_refs": [self.dataset["dataset_id"]],
                "emitted_at": now_iso(),
            },
        }

    def _upsert_and_emit(
        self,
        artifacts: RunArtifacts,
        store: RunStore,
        ctx: RunContext,
        stage_id: str,
        producer: str,
        object_type: str,
        object_id: str,
        obj: dict[str, Any],
        extra_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifacts.upsert(object_type, object_id, obj)
        payload = {"object_id": object_id, "object_type": object_type, "object": obj}
        if extra_payload:
            payload.update(extra_payload)
        return self.emit(
            artifacts,
            store,
            self.make_event(ctx, stage_id, producer, f"{object_type}.upserted", payload),
        )

    def _stage_started(
        self,
        artifacts: RunArtifacts,
        store: RunStore,
        ctx: RunContext,
        stage_id: str,
        producer: str,
        timeout_s: int,
        objective: str | None = None,
        depends_on: list[str] | None = None,
        active_seat_ids: list[str] | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        artifacts.stage_sequence.append(stage_id)
        payload = {"timeout_s": timeout_s}
        if objective:
            payload["objective"] = objective
        if depends_on:
            payload["depends_on"] = depends_on
        if active_seat_ids:
            payload["active_seat_ids"] = active_seat_ids
        if reason:
            payload["reason"] = reason
        return self.emit(artifacts, store, self.make_event(ctx, stage_id, producer, "stage.started", payload))

    def _stage_completed(
        self,
        artifacts: RunArtifacts,
        store: RunStore,
        ctx: RunContext,
        stage_id: str,
        producer: str,
        status: str = "success",
        output_summary: str | None = None,
    ) -> dict[str, Any]:
        payload = {"status": status}
        if output_summary:
            payload["output_summary"] = output_summary
        return self.emit(artifacts, store, self.make_event(ctx, stage_id, producer, "stage.completed", payload))

    def execute(
        self,
        scenario_id: str,
        active_seat_ids: list[str] | None = None,
        ticker: str | None = None,
        run_id: str | None = None,
        breaking_news: bool = False,
        breaking_news_mode: str | None = None,
        debate_depth: int = 1,
        phase_pause: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        ctx, scenario = self.build_context(scenario_id, active_seat_ids, ticker, run_id=run_id)
        requested_tickers = list(ctx.tickers or self.extract_tickers(ticker, limit=4))
        if not requested_tickers:
            requested_tickers = [ctx.ticker]
        peer_override = requested_tickers[1] if len(requested_tickers) > 1 else ""
        artifacts = RunArtifacts()
        store = RunStore(ctx.run_id)
        primary_ticker = ctx.ticker
        requested_peer_tickers = [
            item
            for item in requested_tickers[1:]
            if item and item != primary_ticker
        ]
        scenario_primary_ticker = str(scenario.get("instrument", "") or "").strip().upper()
        scenario_pair_peer = str(scenario.get("pair_peer", "") or "").strip().upper()
        is_pair_scenario = len(scenario.get("instrument_universe", [])) > 1
        pair_peer = peer_override if (is_pair_scenario and peer_override) else scenario_pair_peer
        pair_peer_tickers: list[str] = []
        if is_pair_scenario:
            if pair_peer:
                pair_peer_tickers.append(pair_peer)
            for item in requested_peer_tickers:
                if item not in pair_peer_tickers:
                    pair_peer_tickers.append(item)
        single_name_override = bool(
            primary_ticker
            and scenario_primary_ticker
            and primary_ticker != scenario_primary_ticker
            and len(scenario.get("instrument_universe", [])) <= 1
        )
        display_instrument = (
            f"{primary_ticker} / {pair_peer}" if is_pair_scenario and pair_peer else (
                primary_ticker
                if single_name_override
                else (
                    scenario.get("instrument_label")
                    or scenario.get("display_instrument")
                    or scenario.get("demo_mode", {}).get("instrument_label")
                    or primary_ticker
                )
            )
        )
        scenario_summary = str(scenario.get("summary", "") or "")
        thesis_prompt = str(scenario.get("thesis_prompt", "") or "")
        decision_question = str(scenario.get("decision_question", "") or "")
        scenario_type = str(scenario.get("scenario_type", "") or "")
        branch_conditions = dict(scenario.get("branch_conditions", {}) or {})
        requires_news_confirmation = bool(branch_conditions.get("requires_news_confirmation"))
        time_sensitive_debate = bool(branch_conditions.get("time_sensitive_debate"))
        if single_name_override:
            scenario_summary = scenario_summary.replace(scenario_primary_ticker, primary_ticker)
            thesis_prompt = thesis_prompt.replace(scenario_primary_ticker, primary_ticker)
        if is_pair_scenario:
            if scenario_primary_ticker and primary_ticker and scenario_primary_ticker != primary_ticker:
                scenario_summary = scenario_summary.replace(scenario_primary_ticker, primary_ticker)
                thesis_prompt = thesis_prompt.replace(scenario_primary_ticker, primary_ticker)
            if scenario_pair_peer and pair_peer and scenario_pair_peer != pair_peer:
                scenario_summary = scenario_summary.replace(scenario_pair_peer, pair_peer)
                thesis_prompt = thesis_prompt.replace(scenario_pair_peer, pair_peer)
        time_horizon = scenario.get("demo_mode", {}).get("time_horizon", "event_tactical")
        scenario_forced_breaking_news = (
            bool(scenario.get("branch_conditions", {}).get("force_breaking_news"))
            or bool(scenario.get("demo_mode", {}).get("force_breaking_news"))
        )
        breaking_news_mode_requested = self.normalize_breaking_news_mode(breaking_news_mode)
        if breaking_news and breaking_news_mode_requested == "off":
            breaking_news_mode_requested = "manual"
        breaking_news_mode_effective = breaking_news_mode_requested
        if breaking_news_mode_effective == "off" and scenario_forced_breaking_news:
            breaking_news_mode_effective = "auto_after_gather"
        effective_breaking_news = breaking_news_mode_effective != "off"
        breaking_news_trigger = (
            "manual"
            if breaking_news_mode_effective == "manual"
            else ("timer" if breaking_news_mode_effective == "auto_after_gather" else "none")
        )
        breaking_news_delay_s = BREAKING_NEWS_AUTO_DELAY_S if breaking_news_mode_effective == "auto_after_gather" else 0.0
        debate_depth = max(1, min(int(debate_depth or 1), 8))
        if time_sensitive_debate:
            debate_depth = min(debate_depth, 2)
        dataset_for_narrative = self.dataset
        if is_pair_scenario and scenario_pair_peer and pair_peer and scenario_pair_peer != pair_peer:
            dataset_for_narrative = json.loads(
                json.dumps(self.dataset).replace(scenario_pair_peer, pair_peer)
            )
        live_context = build_live_context(
            primary_ticker,
            dataset_for_narrative,
            pair_peer=",".join(pair_peer_tickers),
            active_seat_ids=ctx.active_seat_ids,
            run_id=ctx.run_id,
        )
        pair_mode = bool(live_context.get("pair_mode", False) or (is_pair_scenario and pair_peer_tickers))
        peer_tickers = list(live_context.get("peer_tickers", pair_peer_tickers) or [])
        self.emit(
            artifacts,
            store,
            self.make_event(
                ctx,
                "gather",
                "research_manager",
                "run.started",
                {
                    "scenario_id": scenario_id,
                    "scenario_type": scenario_type,
                    "decision_question": decision_question,
                    "runtime": self.runtime_name,
                    "active_seat_ids": ctx.active_seat_ids,
                    "ticker": ctx.ticker,
                    "tickers": [primary_ticker, *peer_tickers],
                    "pair_mode": pair_mode,
                    "peer_tickers": peer_tickers,
                    "breaking_news_mode": breaking_news_mode_effective,
                    "breaking_news_mode_requested": breaking_news_mode_requested,
                    "breaking_news_mode_effective": breaking_news_mode_effective,
                    "breaking_news_forced_by_scenario": scenario_forced_breaking_news,
                    "breaking_news_trigger": breaking_news_trigger,
                    "breaking_news_delay_s": breaking_news_delay_s,
                    "requires_news_confirmation": requires_news_confirmation,
                    "time_sensitive_debate": time_sensitive_debate,
                    "debate_depth": debate_depth,
                },
            ),
        )
        for seat_id in ctx.active_seat_ids:
            self.emit(artifacts, store, self.make_event(ctx, "gather", seat_id, "seat.activated", {"seat_id": seat_id, "activation_mode": "required" if seat_id in scenario["required_seat_ids"] else "optional"}))
        sleep_tick()

        # gather
        self._stage_started(
            artifacts,
            store,
            ctx,
            "gather",
            "research_manager",
            60,
            objective=self.stage_objective(
                scenario,
                "gather",
                "Collect multi-source context (technical, news, fundamentals, social, macro, geopolitical).",
            ),
            active_seat_ids=[
                seat
                for seat in (
                    "market_analyst",
                    "news_analyst",
                    "fundamentals_analyst",
                    "social_analyst",
                    "macro_economist",
                    "geopolitical_analyst",
                )
                if seat in ctx.active_seat_ids
            ],
        )
        sources = []
        evidences = []
        market_source = None
        news_source = None
        fundamentals_source = None
        social_source = None
        macro_source = None
        geopolitical_source = None
        breaking_source_note = ""
        breaking_risk_note = ""
        market_context = live_context.get("market_context", dataset_for_narrative.get("market_context", {}))
        fundamentals = live_context.get("fundamentals", dataset_for_narrative.get("fundamentals", {}))
        news_items = live_context.get("news_items", dataset_for_narrative.get("news_items", []))
        social_context = live_context.get("social_context", {})
        macro_context = live_context.get("macro_context", {})
        geopolitical_context = live_context.get("geopolitical_context", {})
        live_coverage = dict(live_context.get("coverage", {}) or {})
        live_errors = list(live_context.get("errors", []) or [])
        live_domain_metadata = dict(live_context.get("domain_metadata", {}) or {})
        ticker_contexts = dict(live_context.get("ticker_contexts", {}) or {})
        if primary_ticker not in ticker_contexts:
            ticker_contexts[primary_ticker] = {
                "coverage": dict(live_coverage or {}),
                "market_context": dict(live_context.get("market_context", {}) or {}),
                "fundamentals": dict(live_context.get("fundamentals", {}) or {}),
                "social_context": dict(live_context.get("social_context", {}) or {}),
                "news_items": list(live_context.get("news_items", []) or []),
            }
        pair_analysis = dict(live_context.get("pair_analysis", {}) or {})
        market_context_by_ticker = dict(live_context.get("market_context_by_ticker", {}) or {})
        fundamentals_by_ticker = dict(live_context.get("fundamentals_by_ticker", {}) or {})
        social_context_by_ticker = dict(live_context.get("social_context_by_ticker", {}) or {})
        news_items_by_ticker = dict(live_context.get("news_items_by_ticker", {}) or {})
        if not market_context_by_ticker:
            market_context_by_ticker = {
                item: dict((ctx.get("market_context", {}) or {}))
                for item, ctx in ticker_contexts.items()
            }
        if not fundamentals_by_ticker:
            fundamentals_by_ticker = {
                item: dict((ctx.get("fundamentals", {}) or {}))
                for item, ctx in ticker_contexts.items()
            }
        if not social_context_by_ticker:
            social_context_by_ticker = {
                item: dict((ctx.get("social_context", {}) or {}))
                for item, ctx in ticker_contexts.items()
            }
        if not news_items_by_ticker:
            news_items_by_ticker = {
                item: list((ctx.get("news_items", []) or []))
                for item, ctx in ticker_contexts.items()
            }
        analysis_tickers = [primary_ticker, *peer_tickers]
        news_headlines_by_ticker: dict[str, list[str]] = {}
        pair_ticker_contexts: list[dict[str, Any]] = []
        for ticker_item in analysis_tickers:
            ticker_ctx = dict(ticker_contexts.get(ticker_item, {}) or {})
            market_ctx = dict(ticker_ctx.get("market_context", {}) or {})
            fundamentals_ctx = dict(ticker_ctx.get("fundamentals", {}) or {})
            social_ctx = dict(ticker_ctx.get("social_context", {}) or {})
            news_rows = list(ticker_ctx.get("news_items", []) or [])
            news_headlines = [
                str(item.get("title", "") or "").strip()
                for item in news_rows[:3]
                if str(item.get("title", "") or "").strip()
            ]
            news_headlines_by_ticker[ticker_item] = list(news_headlines)
            pair_ticker_contexts.append(
                {
                    "ticker": ticker_item,
                    "coverage": dict(ticker_ctx.get("coverage", {}) or {}),
                    "market": {
                        "momentum_20d_pct": market_ctx.get("momentum_20d_pct"),
                        "event_risk": market_ctx.get("event_risk"),
                        "liquidity": market_ctx.get("liquidity"),
                    },
                    "fundamentals": {
                        "estimate_revision_trend": fundamentals_ctx.get("estimate_revision_trend"),
                        "valuation_state": fundamentals_ctx.get("valuation_state"),
                    },
                    "social": {
                        "sentiment_score": social_ctx.get("sentiment_score"),
                        "sentiment_label": social_ctx.get("sentiment_label"),
                    },
                    "news_headlines": news_headlines,
                }
            )
        pair_prompt_context = {
            "pair_mode": pair_mode,
            "primary_ticker": primary_ticker,
            "peer_tickers": peer_tickers,
            "all_tickers": analysis_tickers,
            "pair_trade_context": "relative_value_spread" if pair_mode else "single_name",
            "pair_ticker_contexts": pair_ticker_contexts,
            "market_context_by_ticker": market_context_by_ticker,
            "fundamentals_by_ticker": fundamentals_by_ticker,
            "social_context_by_ticker": social_context_by_ticker,
            "news_headlines_by_ticker": news_headlines_by_ticker,
            "pair_analysis": pair_analysis,
        }
        news_meta = dict(live_domain_metadata.get("news", {}) or {})
        if news_meta.get("provider") == "yfinance" and news_meta.get("ok"):
            news_analyst_items = list(news_items)
            news_analyst_freshness = "live" if live_coverage.get("news") == "live" else "snapshot"
        else:
            yfinance_news_result = fetch_news_domain(primary_ticker, providers=["yfinance"])
            news_analyst_items = list(yfinance_news_result.get("news_items") or [])
            news_analyst_freshness = (
                str(yfinance_news_result.get("freshness", "snapshot") or "snapshot")
                if yfinance_news_result.get("ok")
                else "snapshot"
            )
        market_freshness = "live" if live_coverage.get("market") == "live" else "snapshot"
        fundamentals_freshness = "live" if live_coverage.get("fundamentals") == "live" else "snapshot"
        social_freshness = "live" if live_coverage.get("social") == "live" else "snapshot"
        macro_freshness = "live" if live_coverage.get("macro") == "live" else "snapshot"
        geopolitical_freshness = "live" if live_coverage.get("geopolitical") == "live" else "snapshot"
        def compact_data_status(meta: dict[str, Any], *, default_provider: str = "", default_freshness: str = "") -> dict[str, Any]:
            status: dict[str, Any] = {}
            provider = str(default_provider or meta.get("provider", "") or "").strip()
            freshness = str(default_freshness or meta.get("freshness", "") or "").strip()
            as_of = str(meta.get("as_of", "") or "").strip()
            if provider:
                status["provider"] = provider
            if freshness:
                status["freshness"] = freshness
            if as_of:
                status["as_of"] = as_of
            if meta.get("fallback_used"):
                status["fallback_used"] = True
            if meta.get("degraded"):
                status["degraded"] = True
            return status

        trade_date = datetime.now(UTC).date().isoformat()

        market_prompt_context = {
            "ticker": primary_ticker,
            "trade_date": trade_date,
            "analysis_window_days": 120,
            **pair_prompt_context,
            "market_data_status": compact_data_status(
                dict(live_domain_metadata.get("market", {}) or {}),
                default_freshness=market_freshness,
            ),
        }

        news_prompt_context = {
            "ticker": primary_ticker,
            "trade_date": trade_date,
            "news_mode": "live_news",
            "analysis_window_days": 7,
            **pair_prompt_context,
            "news_data_status": compact_data_status(
                news_meta,
                default_provider="yfinance",
                default_freshness=news_analyst_freshness,
            ),
        }

        fundamentals_prompt_context = {
            "ticker": primary_ticker,
            "trade_date": trade_date,
            "statement_period": "quarterly",
            **pair_prompt_context,
            "fundamentals_data_status": compact_data_status(
                dict(live_domain_metadata.get("fundamentals", {}) or {}),
                default_freshness=fundamentals_freshness,
            ),
        }

        social_prompt_context = {
            "ticker": primary_ticker,
            **pair_prompt_context,
            "social_context": social_context,
            "analysis_tickers": analysis_tickers,
            "social_data_status": compact_data_status(
                dict(live_domain_metadata.get("social", {}) or {}),
                default_freshness=social_freshness,
            ),
        }
        sentiment_score = live_context.get("sentiment_score", dataset_for_narrative.get("sentiment_score"))
        if sentiment_score is not None:
            social_prompt_context["sentiment_score"] = sentiment_score
        ai_basket_correlation = live_context.get("ai_basket_correlation", dataset_for_narrative.get("ai_basket_correlation"))
        if ai_basket_correlation is not None:
            social_prompt_context["ai_basket_correlation"] = ai_basket_correlation

        macro_prompt_context = {
            "ticker": primary_ticker,
            **pair_prompt_context,
            "macro_context": macro_context,
            "macro_data_status": compact_data_status(
                dict(live_domain_metadata.get("macro", {}) or {}),
                default_freshness=macro_freshness,
            ),
        }

        geopolitical_prompt_context = {
            "ticker": primary_ticker,
            **pair_prompt_context,
            "geopolitical_context": geopolitical_context,
            "geopolitical_data_status": compact_data_status(
                dict(live_domain_metadata.get("geopolitical", {}) or {}),
                default_freshness=geopolitical_freshness,
            ),
        }
        quant_input_dataset = live_context.get("quant_dataset", dataset_for_narrative)
        role_stances: dict[str, str] = dict(DEFAULT_ROLE_STANCE)
        role_confidences: dict[str, int] = {}
        # Start quant compute in parallel with gather analyst narratives so quant no longer waits
        # for every gather seat to finish before beginning heavy work.
        quant_prefetch_executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quant_prefetch")
        quant_prefetch_future: Future[dict[str, Any]] = quant_prefetch_executor.submit(run_quant, quant_input_dataset, ctx.run_id)
        quant_prefetch_executor.shutdown(wait=False)
        peer_ticker_label = ", ".join(peer_tickers)
        pair_relative_bits: list[str] = []
        for peer_ticker in peer_tickers:
            rel = dict(pair_analysis.get(peer_ticker, {}) or {})
            ratio = rel.get("relative_price_ratio")
            momentum_spread = rel.get("momentum_spread_pct")
            fragments: list[str] = []
            if ratio is not None:
                fragments.append(f"ratio {primary_ticker}/{peer_ticker}={ratio}")
            if momentum_spread is not None:
                fragments.append(f"momentum_spread={momentum_spread}%")
            if fragments:
                pair_relative_bits.append(f"vs {peer_ticker} ({', '.join(fragments)})")
        pair_instruction_suffix = ""
        if pair_mode and peer_ticker_label:
            pair_instruction_suffix = (
                f" Compare primary {primary_ticker} against peers {peer_ticker_label} and frame conclusions as relative-value spread logic."
            )
            if pair_relative_bits:
                pair_instruction_suffix += " Current spread diagnostics: " + "; ".join(pair_relative_bits) + "."
        self._stage_started(
            artifacts,
            store,
            ctx,
            "quantify",
            "research_manager",
            45,
            objective=self.stage_objective(
                scenario,
                "quantify",
                "Quant Analyst validates the Phase 1 setup with indicators and risk metrics before debate.",
            ),
            active_seat_ids=["quant_analyst"],
            reason="parallel_prefetch",
        )

        def emit_gather_source(source_obj: dict[str, Any], producer: str) -> None:
            artifacts.upsert("source", source_obj["source_id"], source_obj)
            self.emit(
                artifacts,
                store,
                self.make_event(
                    ctx,
                    "gather",
                    producer,
                    "source.ingested",
                    {
                        "source_id": source_obj["source_id"],
                        "source": source_obj,
                        "stance": role_stances.get(producer, DEFAULT_ROLE_STANCE.get(producer, "neutral")),
                        "confidence": role_confidences.get(producer, 70),
                    },
                ),
            )

        gather_requests: dict[str, dict[str, Any]] = {
            "market_analyst": {
                "role_id": "market_analyst",
                "context": market_prompt_context,
                "fallback": (
                    (
                        " | ".join(
                            [
                                (
                                    f"{ticker_item}: 20D momentum "
                                    f"{(market_context_by_ticker.get(ticker_item) or {}).get('momentum_20d_pct', 'n/a')}% "
                                    f"event risk {(market_context_by_ticker.get(ticker_item) or {}).get('event_risk', 'n/a')} "
                                    f"liquidity {(market_context_by_ticker.get(ticker_item) or {}).get('liquidity', 'n/a')}"
                                )
                                for ticker_item in analysis_tickers
                            ]
                        )
                        if pair_mode
                        else (
                            f"{primary_ticker} has 20D momentum of {market_context.get('momentum_20d_pct', 'n/a')}% "
                            f"with event risk marked {market_context.get('event_risk', 'n/a')}."
                        )
                    )
                    +
                    f"{pair_instruction_suffix}"
                ),
                "tools": tool_specs_for_role("market_analyst"),
            },
            "news_analyst": {
                "role_id": "news_analyst",
                "context": news_prompt_context,
                "fallback": (
                    (
                        " | ".join(
                            [
                                (
                                    f"{ticker_item}: "
                                    f"{str(((news_items_by_ticker.get(ticker_item) or [{}])[0]).get('summary') or ((news_items_by_ticker.get(ticker_item) or [{}])[0]).get('title') or 'No current headline').strip()}"
                                )
                                for ticker_item in analysis_tickers
                            ]
                        )
                        if pair_mode
                        else (
                            news_analyst_items[0].get("summary", "")
                            if news_analyst_items
                            else f"No current yfinance news items provided for {display_instrument}."
                        )
                    )
                    +
                    f"{pair_instruction_suffix}"
                ),
                "tools": tool_specs_for_role("news_analyst"),
            },
            "fundamentals_analyst": {
                "role_id": "fundamentals_analyst",
                "context": fundamentals_prompt_context,
                "fallback": (
                    (
                        " | ".join(
                            [
                                (
                                    f"{ticker_item}: estimate revisions "
                                    f"{(fundamentals_by_ticker.get(ticker_item) or {}).get('estimate_revision_trend', 'mixed')}, "
                                    f"valuation {(fundamentals_by_ticker.get(ticker_item) or {}).get('valuation_state', 'uncertain')}"
                                )
                                for ticker_item in analysis_tickers
                            ]
                        )
                        if pair_mode
                        else (
                            f"{primary_ticker} estimate revisions remain {fundamentals.get('estimate_revision_trend', 'mixed')} "
                            f"while valuation is {fundamentals.get('valuation_state', 'uncertain')}."
                        )
                    )
                    +
                    f"{pair_instruction_suffix}"
                ),
                "tools": tool_specs_for_role("fundamentals_analyst"),
            },
        }
        if "social_analyst" in ctx.active_seat_ids:
            social_meta = dict((live_domain_metadata or {}).get("social", {}) or {})
            social_errors = [str(item) for item in live_errors if str(item).startswith("social:")]
            social_end_date = datetime.now(UTC).date()
            social_start_date = social_end_date - timedelta(days=max(1, int(X_SEARCH_LOOKBACK_DAYS)))
            stocktwits_snapshot = {
                "ok": bool(social_meta.get("ok", False)),
                "provider": str(social_meta.get("provider", "stocktwits") or "stocktwits"),
                "error": social_errors[0].split(":", 1)[1] if social_errors else "",
                "source_count": int(social_meta.get("source_count", 0) or 0),
                "freshness": str(social_meta.get("freshness", "snapshot") or "snapshot"),
                "as_of": str(social_meta.get("as_of", now_iso()) or now_iso()),
                "social_context": dict(social_context or {}),
            }
            social_context_payload = dict(social_prompt_context)
            social_context_payload["stocktwits_snapshot"] = stocktwits_snapshot
            social_context_payload["x_search_window"] = {
                "from_date": social_start_date.isoformat(),
                "to_date": social_end_date.isoformat(),
            }
            social_context_payload["x_search_tickers"] = analysis_tickers
            if pair_mode and peer_tickers:
                social_context_payload["x_search_query_hint"] = " OR ".join(analysis_tickers)
            gather_requests["social_analyst"] = {
                "role_id": "social_analyst",
                "context": social_context_payload,
                "fallback": (
                    (
                        " | ".join(
                            [
                                (
                                    f"{ticker_item}: sentiment "
                                    f"{(social_context_by_ticker.get(ticker_item) or {}).get('sentiment_label', 'neutral')} "
                                    f"({(social_context_by_ticker.get(ticker_item) or {}).get('sentiment_score', 'n/a')})"
                                )
                                for ticker_item in analysis_tickers
                            ]
                        )
                        if pair_mode
                        else f"{display_instrument} social coverage is limited; maintain neutral stance until broader confirmation arrives."
                    )
                    +
                    f"{pair_instruction_suffix}"
                ),
                "max_words": 160,
                "tools": [
                    {
                        "type": "x_search",
                        "from_date": social_start_date.isoformat(),
                        "to_date": social_end_date.isoformat(),
                        "enable_image_understanding": False,
                        "enable_video_understanding": False,
                    }
                ],
                "temperature": 0.1,
            }
        if "macro_economist" in ctx.active_seat_ids:
            gather_requests["macro_economist"] = {
                "role_id": "macro_economist",
                "context": macro_prompt_context,
                "fallback": (
                    f"Macro regime is {macro_context.get('regime', 'mixed')}, VIX is {macro_context.get('vix_level', 'n/a')}, "
                    f"US 10Y is {macro_context.get('us10y_yield_pct', 'n/a')}%, and SPX daily change is {macro_context.get('spx_change_pct', 'n/a')}%."
                ),
                "tools": tool_specs_for_role("macro_economist"),
            }
        if "geopolitical_analyst" in ctx.active_seat_ids:
            gather_requests["geopolitical_analyst"] = {
                "role_id": "geopolitical_analyst",
                "context": geopolitical_prompt_context,
                "fallback": (
                    f"Geopolitical risk level is {geopolitical_context.get('risk_level', 'moderate')} "
                    f"with {geopolitical_context.get('headline_count', 0)} recent policy-related headlines."
                ),
            }

        def run_gather_request(request: dict[str, Any]) -> str:
            return self.agent_narrative(
                request["role_id"],
                "gather",
                request["context"],
                request["fallback"],
                max_words=int(request.get("max_words", 200)),
                tools=request.get("tools"),
                temperature=float(request.get("temperature", 0.2)),
            )

        gather_futures: dict[str, Future[str]] = {}
        with ThreadPoolExecutor(max_workers=max(1, len(gather_requests)), thread_name_prefix="gather_analyst") as gather_executor:
            for role_id, request in gather_requests.items():
                gather_futures[role_id] = gather_executor.submit(run_gather_request, request)
            future_to_role = {future: role_id for role_id, future in gather_futures.items()}
            for future in as_completed(future_to_role):
                role_id = future_to_role[future]
                request = gather_requests[role_id]
                try:
                    content_raw = future.result()
                except Exception:
                    content_raw = str(request.get("fallback", ""))

                stance, confidence, content = self.parse_stance_confidence_and_text(role_id, content_raw)
                role_stances[role_id] = stance
                role_confidences[role_id] = confidence

                if role_id == "market_analyst":
                    market_source = self.source_object(
                        ctx,
                        "gather",
                        "market_analyst",
                        make_id("src"),
                        "market_data",
                        f"{display_instrument} momentum and event setup",
                        content,
                        market_freshness,
                    )
                    sources.append(market_source)
                    emit_gather_source(market_source, "market_analyst")
                    continue

                if role_id == "news_analyst":
                    news_title = (
                        news_analyst_items[0].get("title", f"{display_instrument} news context")
                        if news_analyst_items
                        else f"{display_instrument} news context"
                    )
                    news_source = self.source_object(
                        ctx,
                        "gather",
                        "news_analyst",
                        make_id("src"),
                        "news",
                        f"{display_instrument}: {news_title}",
                        content,
                        news_analyst_freshness,
                    )
                    sources.append(news_source)
                    emit_gather_source(news_source, "news_analyst")
                    continue

                if role_id == "fundamentals_analyst":
                    fundamentals_source = self.source_object(
                        ctx,
                        "gather",
                        "fundamentals_analyst",
                        make_id("src"),
                        "fundamentals",
                        f"{display_instrument} estimate revisions and valuation",
                        content,
                        fundamentals_freshness,
                    )
                    sources.append(fundamentals_source)
                    emit_gather_source(fundamentals_source, "fundamentals_analyst")
                    continue

                if role_id == "social_analyst":
                    social_source = self.source_object(
                        ctx,
                        "gather",
                        "social_analyst",
                        make_id("src"),
                        "social",
                        f"{display_instrument} retail sentiment and crowding",
                        content,
                        social_freshness,
                    )
                    sources.append(social_source)
                    emit_gather_source(social_source, "social_analyst")
                    continue

                if role_id == "macro_economist":
                    macro_source = self.source_object(
                        ctx,
                        "gather",
                        "macro_economist",
                        make_id("src"),
                        "macro",
                        f"{display_instrument} macro backdrop",
                        content,
                        macro_freshness,
                    )
                    sources.append(macro_source)
                    emit_gather_source(macro_source, "macro_economist")
                    continue

                if role_id == "geopolitical_analyst":
                    geopolitical_source = self.source_object(
                        ctx,
                        "gather",
                        "geopolitical_analyst",
                        make_id("src"),
                        "geopolitical",
                        f"{display_instrument} geopolitical exposure",
                        content,
                        geopolitical_freshness,
                    )
                    sources.append(geopolitical_source)
                    emit_gather_source(geopolitical_source, "geopolitical_analyst")

        if market_source is None:
            market_source = self.source_object(
                ctx,
                "gather",
                "market_analyst",
                make_id("src"),
                "market_data",
                f"{display_instrument} momentum and event setup",
                str(gather_requests["market_analyst"]["fallback"]),
                market_freshness,
            )
            sources.append(market_source)
            emit_gather_source(market_source, "market_analyst")

        if news_source is None:
            news_title = (
                news_analyst_items[0].get("title", f"{display_instrument} news context")
                if news_analyst_items
                else f"{display_instrument} news context"
            )
            news_source = self.source_object(
                ctx,
                "gather",
                "news_analyst",
                make_id("src"),
                "news",
                f"{display_instrument}: {news_title}",
                str(gather_requests["news_analyst"]["fallback"]),
                news_analyst_freshness,
            )
            sources.append(news_source)
            emit_gather_source(news_source, "news_analyst")

        if fundamentals_source is None:
            fundamentals_source = self.source_object(
                ctx,
                "gather",
                "fundamentals_analyst",
                make_id("src"),
                "fundamentals",
                f"{display_instrument} estimate revisions and valuation",
                str(gather_requests["fundamentals_analyst"]["fallback"]),
                fundamentals_freshness,
            )
            sources.append(fundamentals_source)
            emit_gather_source(fundamentals_source, "fundamentals_analyst")

        # Phase 1 collection should call each gather analyst once. Evidence cards are derived from that single pass.
        market_summary = market_source["content"]
        news_summary = news_source["content"]
        fundamentals_summary = fundamentals_source["content"]
        evidences.append(self.evidence_object(ctx, "gather", "market_analyst", make_id("ev"), "momentum_signal", f"{display_instrument} momentum supports tactical interest", market_summary, [market_source["source_id"]], 0.74, ["positioning", "event"]))
        evidences.append(self.evidence_object(ctx, "gather", "market_analyst", make_id("ev"), "liquidity_ok", f"{display_instrument} liquidity supports entry", market_summary, [market_source["source_id"]], 0.83, ["liquidity", "risk"]))
        evidences.append(self.evidence_object(ctx, "gather", "news_analyst", make_id("ev"), "headline_signal", f"{display_instrument} headline impact context", news_summary, [news_source["source_id"]], 0.72, ["fundamentals", "thesis"]))
        evidences.append(self.evidence_object(ctx, "gather", "fundamentals_analyst", make_id("ev"), "estimate_revision", f"{display_instrument} estimate revisions and quality setup", fundamentals_summary, [fundamentals_source["source_id"]], 0.78, ["fundamentals", "earnings"]))
        evidences.append(self.evidence_object(ctx, "gather", "fundamentals_analyst", make_id("ev"), "valuation_watch", f"{display_instrument} valuation and downside watch", fundamentals_summary, [fundamentals_source["source_id"]], 0.65, ["valuation", "risk"]))
        if social_source:
            evidences.append(self.evidence_object(ctx, "gather", "social_analyst", make_id("ev"), "crowding", f"{display_instrument} crowding risk elevated", social_source["content"], [social_source["source_id"]], 0.61, ["sentiment", "risk"]))
        else:
            evidences.append(self.evidence_object(ctx, "gather", "market_analyst", make_id("ev"), "event_risk", f"{display_instrument} event risk requires sizing discipline", market_summary, [market_source["source_id"]], 0.81, ["earnings", "risk"]))
        if macro_source:
            evidences.append(self.evidence_object(ctx, "gather", "macro_economist", make_id("ev"), "macro_regime", f"{display_instrument} macro regime transmission", macro_source["content"], [macro_source["source_id"]], 0.66, ["macro", "risk"]))
        if geopolitical_source:
            evidences.append(self.evidence_object(ctx, "gather", "geopolitical_analyst", make_id("ev"), "policy_risk", f"{display_instrument} geopolitical downside pathway", geopolitical_source["content"], [geopolitical_source["source_id"]], 0.64, ["geopolitics", "risk"]))
        for ev in evidences:
            producer = ev["provenance"]["producer_role"]
            self._upsert_and_emit(
                artifacts,
                store,
                ctx,
                "gather",
                producer,
                "evidence",
                ev["evidence_id"],
                ev,
                extra_payload={"stance": role_stances.get(producer, DEFAULT_ROLE_STANCE.get(producer, "neutral"))},
            )
        self._stage_completed(
            artifacts,
            store,
            ctx,
            "gather",
            "research_manager",
            "success",
            f"Gather complete with {len(sources)} sources and {len(evidences)} evidence items.",
        )
        sleep_tick()

        if effective_breaking_news:
            if breaking_news_mode_effective == "auto_after_gather":
                sleep_tick(seconds=breaking_news_delay_s)
            self._stage_started(
                artifacts,
                store,
                ctx,
                "gather",
                "research_manager",
                20,
                objective=(
                    "Breaking-news override pass (manual trigger), refresh inputs before debate."
                    if breaking_news_mode_effective == "manual"
                    else "Breaking-news override pass (timed trigger), refresh inputs before debate."
                ),
                depends_on=["gather"],
                active_seat_ids=[seat for seat in ("news_analyst", "market_analyst", "research_manager") if seat in ctx.active_seat_ids],
                reason=f"breaking_news_reroute_{breaking_news_trigger}",
            )
            breaking_context = {
                "ticker": primary_ticker,
                "breaking_news": True,
                "scenario_type": scenario_type,
                "display_instrument": display_instrument,
                **pair_prompt_context,
                "position_context": scenario.get("demo_mode", {}).get("position_context", ""),
                "simulation_intensity": "severe",
                "existing_evidence_count": len(evidences),
                "trigger_mode": breaking_news_trigger,
            }
            breaking_source_raw = self.agent_narrative(
                "news_analyst_breaking_sim",
                "gather",
                breaking_context,
                (
                    f"BREAKING NEWS - Simulated severe development for {display_instrument} requires immediate desk reroute.\n"
                    "RISK ASYMMETRY - Downside-tail and implementation risk rose enough to require immediate reassessment."
                ),
            )
            breaking_stance, breaking_confidence, breaking_source_content = self.parse_stance_confidence_and_text(
                "news_analyst",
                breaking_source_raw,
                role_stances.get("news_analyst"),
            )
            breaking_source_content, breaking_evidence_content = self.split_breaking_news_outputs(
                breaking_source_content,
                display_instrument,
            )
            breaking_source_note = breaking_source_content
            breaking_risk_note = breaking_evidence_content
            role_stances["news_analyst"] = breaking_stance
            role_confidences["news_analyst"] = breaking_confidence
            breaking_source = self.source_object(
                ctx,
                "gather",
                "news_analyst",
                make_id("src"),
                "news",
                f"{display_instrument}: Simulated breaking-news reroute update",
                breaking_source_content,
                "snapshot",
            )
            artifacts.upsert("source", breaking_source["source_id"], breaking_source)
            self.emit(
                artifacts,
                store,
                self.make_event(
                    ctx,
                    "gather",
                    "news_analyst",
                    "source.ingested",
                    {
                        "source_id": breaking_source["source_id"],
                        "source": breaking_source,
                        "stance": role_stances.get("news_analyst", "neutral"),
                        "confidence": role_confidences.get("news_analyst", 70),
                    },
                ),
            )
            breaking_evidence = self.evidence_object(
                ctx,
                "gather",
                "news_analyst",
                make_id("ev"),
                "event_risk",
                f"{display_instrument} breaking-news risk override",
                breaking_evidence_content,
                [breaking_source["source_id"]],
                0.69,
                ["event", "risk", "sentiment"],
            )
            evidences.append(breaking_evidence)
            self._upsert_and_emit(
                artifacts,
                store,
                ctx,
                "gather",
                "news_analyst",
                "evidence",
                breaking_evidence["evidence_id"],
                breaking_evidence,
                extra_payload={"stance": role_stances.get("news_analyst", "neutral")},
            )
            reroute_note = self.artifact_object(
                ctx,
                "gather",
                "research_manager",
                "summary_memo",
                f"Breaking-news reroute applied before debate for {display_instrument}",
                f"var/runs/{ctx.run_id}/artifacts/breaking-news-reroute.txt",
                "text/plain",
            )
            artifacts.upsert("artifact", reroute_note["artifact_id"], reroute_note)
            self.emit(
                artifacts,
                store,
                self.make_event(
                    ctx,
                    "gather",
                    "research_manager",
                    "artifact.created",
                    {
                        "artifact_id": reroute_note["artifact_id"],
                        "artifact_type": reroute_note["artifact_type"],
                        "artifact": reroute_note,
                        "stance": role_stances.get("research_manager", "neutral"),
                    },
                ),
            )
            self._stage_completed(
                artifacts,
                store,
                ctx,
                "gather",
                "research_manager",
                "warning",
                "Breaking-news reroute completed, debate inputs refreshed.",
            )
            sleep_tick()

        # quantify
        phase_1_source_outputs = [
            {
                "role": src["provenance"]["producer_role"],
                "title": src["title"],
                "text": src["content"],
            }
            for src in sources
        ]
        phase_1_evidence_outputs = [
            {
                "role": ev["provenance"]["producer_role"],
                "title": ev["title"],
                "summary": ev["summary"],
                "confidence": ev["confidence"],
                "tags": ev.get("tags", []),
            }
            for ev in evidences
        ]
        quant = quant_prefetch_future.result()
        quant_note = self.agent_narrative(
            "quant_analyst",
            "quantify",
            {
                "ticker": primary_ticker,
                "display_instrument": display_instrument,
                **pair_prompt_context,
                "quant_result": quant.get("result", {}),
                "quant_input_coverage": live_coverage.get("quant_inputs", "fallback"),
                "live_context_coverage": live_coverage,
            },
            "Quant notebook result",
        )
        quant_stance, quant_confidence, quant_note_text = self.parse_stance_confidence_and_text(
            "quant_analyst",
            quant_note,
            role_stances.get("quant_analyst"),
        )
        role_stances["quant_analyst"] = quant_stance
        role_confidences["quant_analyst"] = quant_confidence
        notebook_artifact = self.artifact_object(ctx, "quantify", "quant_analyst", "notebook", self.compact_label(quant_note_text, "Quant notebook result"), quant["stdout_path"])
        artifacts.upsert("artifact", notebook_artifact["artifact_id"], notebook_artifact)
        self.emit(
            artifacts,
            store,
            self.make_event(
                ctx,
                "quantify",
                "quant_analyst",
                "artifact.created",
                {
                    "artifact_id": notebook_artifact["artifact_id"],
                    "artifact_type": notebook_artifact["artifact_type"],
                    "artifact": notebook_artifact,
                    "stance": role_stances.get("quant_analyst", "neutral"),
                    "confidence": role_confidences.get("quant_analyst", 70),
                    "quant_note": quant_note_text,
                },
            ),
        )
        metric_map = {
            "momentum_20d_pct": ("20d momentum", "%", 0.74),
            "estimate_revision_score": ("estimate revision score", "score", 0.7),
            "composite_signal": ("composite signal", "score", 0.68),
        }
        if pair_mode:
            pair_metric_candidates = {
                "pair_correlation": ("pair correlation", "corr", 0.67),
                "spread_momentum_pct": ("spread momentum", "%", 0.66),
                "spread_volatility_pct": ("spread volatility", "%", 0.64),
                "relative_vol_ratio": ("relative vol ratio", "ratio", 0.64),
            }
            for metric_key, metric_meta in pair_metric_candidates.items():
                if quant["result"].get(metric_key) is not None:
                    metric_map[metric_key] = metric_meta
        metric_ids = []
        phase_1_metric_outputs = []
        for key, (name, unit, confidence) in metric_map.items():
            metric = self.metric_object(ctx, make_id("met"), name, quant["result"][key], unit, notebook_artifact["artifact_id"], confidence, quant["result"]["coverage"])
            metric_ids.append(metric["metric_id"])
            phase_1_metric_outputs.append(
                {
                    "metric_id": metric["metric_id"],
                    "name": metric["name"],
                    "value": metric["value"],
                    "unit": metric["unit"],
                    "confidence": metric["confidence"],
                }
            )
            self._upsert_and_emit(
                artifacts,
                store,
                ctx,
                "quantify",
                "quant_analyst",
                "metric",
                metric["metric_id"],
                metric,
                extra_payload={"stance": role_stances.get("quant_analyst", "neutral")},
            )
        self._stage_completed(
            artifacts,
            store,
            ctx,
            "quantify",
            "research_manager",
            "success",
            f"Quant validation complete with {len(metric_ids)} metrics and notebook artifact.",
        )
        if phase_pause:
            phase_pause("quantify")
        sleep_tick()

        # debate
        self._stage_started(
            artifacts,
            store,
            ctx,
            "debate",
            "research_manager",
            30 if time_sensitive_debate else 40,
            objective=self.stage_objective(
                scenario,
                "debate",
                "Run structured long-short committee debate using gathered analyst outputs and Phase 1 quant signals.",
            ),
            depends_on=["gather", "quantify"],
            active_seat_ids=[seat for seat in ("bull_researcher", "bear_researcher", "aggressive_analyst", "conservative_analyst", "neutral_analyst") if seat in ctx.active_seat_ids],
        )
        debate_context = {
            "ticker": primary_ticker,
            "display_instrument": display_instrument,
            **pair_prompt_context,
            "evidence_titles": [item["title"] for item in evidences],
            "quant_summary": quant_note_text,
            "quant_metrics": phase_1_metric_outputs,
            "stage": "debate",
            "requires_news_confirmation": requires_news_confirmation,
            "time_sensitive_debate": time_sensitive_debate,
            "breaking_news_note": breaking_source_note,
            "breaking_risk_asymmetry": breaking_risk_note,
        }
        risk_evidence_ids = [ev["evidence_id"] for ev in evidences if "risk" in ev.get("tags", [])]
        support_evidence_ids = [ev["evidence_id"] for ev in evidences if "risk" not in ev.get("tags", [])]
        if not support_evidence_ids:
            support_evidence_ids = [ev["evidence_id"] for ev in evidences]
        if not risk_evidence_ids:
            risk_evidence_ids = [ev["evidence_id"] for ev in evidences]

        debate_seat_order = [
            seat
            for seat in (
                "bull_researcher",
                "bear_researcher",
                "aggressive_analyst",
                "conservative_analyst",
                "neutral_analyst",
            )
            if seat in ctx.active_seat_ids
        ]
        fallback_debate_lines = {
            "bull_researcher": "Positive estimate drift and durable demand support a measured long.",
            "bear_researcher": "Valuation, crowding, and event risk argue against rushing into size.",
            "aggressive_analyst": "If the edge is real, the desk should express it decisively rather than hide behind tiny sizing.",
            "conservative_analyst": "The desk should preserve optionality until conviction and implementation quality improve.",
            "neutral_analyst": "Any action should reflect both the signal quality and the cost of being early.",
        }
        if scenario_type == "breaking_news_reunderwrite":
            fallback_debate_lines.update(
                {
                    "bull_researcher": "The headline creates an opportunity only if confirmation arrives quickly and the market has not already fully repriced it.",
                    "bear_researcher": "Incomplete confirmation and wider spreads make neutrality or delay the safer default.",
                    "aggressive_analyst": "Headline dislocations can create fast opportunity, but only if urgency is matched by clear edge.",
                    "conservative_analyst": "The desk should not chase a single-source story into poor implementation conditions.",
                    "neutral_analyst": "A defer decision is acceptable if confirmation is still lagging the market move.",
                }
            )
        elif scenario_type == "relative_value_pair":
            fallback_debate_lines.update(
                {
                    "bull_researcher": "The relative revisions and spread setup support putting on the intended pair rather than staying outright neutral.",
                    "bear_researcher": "If the spread is crowded or the hedge is imperfect, the pair can fail even if the primary thesis sounds right.",
                    "aggressive_analyst": "A clean spread setup should be expressed while the relative edge still exists.",
                    "conservative_analyst": "Borrow, leg balance, and beta leakage matter more than a catchy pair narrative.",
                    "neutral_analyst": "Only run the pair if the edge remains spread-driven rather than disguised market beta.",
                }
            )
        elif scenario_type == "thesis_break_review":
            fallback_debate_lines.update(
                {
                    "bull_researcher": "The original position may still deserve to be held if deterioration is temporary rather than structural.",
                    "bear_researcher": "Thesis damage is real enough that the desk should seriously consider trimming or exiting.",
                    "aggressive_analyst": "If the damage is overdone, holding through the drawdown may still be justified.",
                    "conservative_analyst": "Capital preservation should dominate until the desk can prove the thesis still holds.",
                    "neutral_analyst": "The decision is not fresh initiation; it is whether remaining exposure still earns its place in the book.",
                }
            )
        claims: list[dict[str, Any]] = []
        previous_claim: dict[str, Any] | None = None
        turn_index = 0
        if not debate_seat_order:
            debate_seat_order = ["research_manager"]
            fallback_debate_lines["research_manager"] = "No debate seats were selected; maintain neutral posture and request fuller seat coverage."
        for round_index in range(1, debate_depth + 1):
            for role_id in debate_seat_order:
                turn_index += 1
                role_context = {
                    **debate_context,
                    "debate_round": round_index,
                    "debate_turn": turn_index,
                    "phase_1_source_outputs": phase_1_source_outputs,
                    "phase_1_evidence_outputs": phase_1_evidence_outputs,
                    "phase_1_quant_summary": quant_note_text,
                    "phase_1_metric_outputs": phase_1_metric_outputs,
                    "debate_history": [
                        {
                            "role": claim["provenance"]["producer_role"],
                            "stance": claim["stance"],
                            "statement": claim["statement"],
                            "round_index": claim.get("round_index", 0),
                            "turn_index": claim.get("turn_index", 0),
                        }
                        for claim in claims[-12:]
                    ],
                    "previous_claim_role": previous_claim["provenance"]["producer_role"] if previous_claim else "",
                    "previous_claim_stance": previous_claim["stance"] if previous_claim else "",
                    "previous_claim_statement": previous_claim["statement"] if previous_claim else "",
                }
                role_raw = self.agent_narrative(
                    role_id,
                    "debate",
                    role_context,
                    fallback_debate_lines.get(role_id, "Provide a concise evidence-based desk view."),
                )
                role_stance, role_confidence, role_statement = self.parse_stance_confidence_and_text(
                    role_id,
                    role_raw,
                    DEFAULT_ROLE_STANCE.get(role_id, "neutral"),
                )
                role_stances[role_id] = role_stance
                role_confidences[role_id] = role_confidence
                if role_stance == "long":
                    supporting = support_evidence_ids[:3]
                    counter = risk_evidence_ids[:2]
                elif role_stance == "short":
                    supporting = risk_evidence_ids[:3]
                    counter = support_evidence_ids[:2]
                else:
                    supporting = (support_evidence_ids[:1] + risk_evidence_ids[:1]) or [evidences[0]["evidence_id"]]
                    counter = support_evidence_ids[:1]
                base_confidence = {
                    "long": 0.67,
                    "short": 0.69,
                    "neutral": 0.61,
                }.get(role_stance, 0.6)
                confidence = round(max(0.51, min(0.95, (base_confidence + (role_confidence / 100.0)) / 2.0)), 3)
                claim = self.claim_object(ctx, role_id, role_statement, role_stance, supporting, counter, confidence)
                claim["round_index"] = round_index
                claim["turn_index"] = turn_index
                if previous_claim is not None:
                    claim["reply_to_claim_id"] = previous_claim["claim_id"]
                claims.append(claim)
                self._upsert_and_emit(
                    artifacts,
                    store,
                    ctx,
                    "debate",
                    role_id,
                    "claim",
                    claim["claim_id"],
                    claim,
                    extra_payload={
                        "stance": role_stance,
                        "confidence": role_confidence,
                        "round_index": round_index,
                        "turn_index": turn_index,
                        "reply_to_claim_id": previous_claim["claim_id"] if previous_claim else "",
                    },
                )
                previous_claim = claim
                # Deliberate pacing keeps the live debate readable: one response arrives, then the next.
                sleep_debate_turn()

        long_claim_ids = [claim["claim_id"] for claim in claims if claim["stance"] == "long"]
        short_claim_ids = [claim["claim_id"] for claim in claims if claim["stance"] == "short"]
        neutral_claim_ids = [claim["claim_id"] for claim in claims if claim["stance"] == "neutral"]
        all_claim_ids = [claim["claim_id"] for claim in claims]

        def _dedupe_claim_ids(items: list[str]) -> list[str]:
            seen: set[str] = set()
            ordered: list[str] = []
            for item in items:
                if item and item not in seen:
                    seen.add(item)
                    ordered.append(item)
            return ordered

        def _claim_linkage_for_stance(stance: str) -> tuple[list[str], list[str]]:
            normalized = self.normalize_stance(stance, "neutral")
            if normalized == "long":
                supportive = long_claim_ids or neutral_claim_ids or all_claim_ids[:1]
                dissent = short_claim_ids or neutral_claim_ids or all_claim_ids[:1]
            elif normalized == "short":
                supportive = short_claim_ids or neutral_claim_ids or all_claim_ids[:1]
                dissent = long_claim_ids or neutral_claim_ids or all_claim_ids[:1]
            else:
                supportive = neutral_claim_ids or _dedupe_claim_ids((long_claim_ids[:1] + short_claim_ids[:1])) or all_claim_ids[:1]
                dissent = _dedupe_claim_ids((long_claim_ids[:1] + short_claim_ids[:1])) or supportive
            return _dedupe_claim_ids(supportive), _dedupe_claim_ids(dissent)

        self._stage_completed(
            artifacts,
            store,
            ctx,
            "debate",
            "research_manager",
            "success",
            f"Debate complete with {len(claims)} turn-based claims across depth {debate_depth}.",
        )
        if phase_pause:
            phase_pause("debate")
        sleep_tick()

        # synthesize
        self._stage_started(
            artifacts,
            store,
            ctx,
            "synthesize",
            "research_manager",
            20,
            objective=self.stage_objective(
                scenario,
                "synthesize",
                "Research Manager synthesizes debate and quant output into desk recommendation.",
            ),
            depends_on=["debate", "quantify"],
            active_seat_ids=["research_manager"],
        )
        research_note = self.agent_narrative(
            "research_manager",
            "synthesize",
            {
                "ticker": primary_ticker,
                "display_instrument": display_instrument,
                **pair_prompt_context,
                "claim_count": len(claims),
                "metric_count": len(metric_ids),
            },
            "Research manager summary",
        )
        research_stance, research_confidence, research_note_text = self.parse_stance_confidence_and_text(
            "research_manager",
            research_note,
            role_stances.get("research_manager"),
        )
        role_stances["research_manager"] = research_stance
        role_confidences["research_manager"] = research_confidence
        research_supportive_claim_ids, research_dissent_claim_ids = _claim_linkage_for_stance(research_stance)
        summary_artifact = self.artifact_object(
            ctx,
            "synthesize",
            "research_manager",
            "summary_memo",
            self.compact_label(research_note_text, "Research manager summary"),
            f"var/runs/{ctx.run_id}/artifacts/research-summary.txt",
            "text/plain",
        )
        synth_position_size = 75 if scenario_type != "thesis_break_review" else int(scenario.get("starting_position_state", {}).get("size_bps", 100) or 100)
        synth_action = "initiate" if scenario_type != "thesis_break_review" else "hold"
        decision = self.decision_object(
            ctx,
            "synthesize",
            "research_manager",
            "research_recommendation",
            "approved_with_changes",
            research_supportive_claim_ids,
            [],
            synth_position_size,
            research_dissent_claim_ids,
            False,
            stance=research_stance,
            position_action=synth_action,
        )
        artifacts.upsert("artifact", summary_artifact["artifact_id"], summary_artifact)
        self.emit(
            artifacts,
            store,
            self.make_event(
                ctx,
                "synthesize",
                "research_manager",
                "artifact.created",
                {
                    "artifact_id": summary_artifact["artifact_id"],
                    "artifact_type": summary_artifact["artifact_type"],
                    "artifact": summary_artifact,
                    "stance": role_stances.get("research_manager", "neutral"),
                    "confidence": role_confidences.get("research_manager", 70),
                },
            ),
        )
        artifacts.upsert("decision", decision["decision_id"], decision)
        self._stage_completed(
            artifacts,
            store,
            ctx,
            "synthesize",
            "research_manager",
            "success",
            "Desk recommendation package prepared for risk review.",
        )
        if phase_pause:
            phase_pause("synthesize")
        sleep_tick()

        # risk review
        self._stage_started(
            artifacts,
            store,
            ctx,
            "risk_review",
            "research_manager",
            20,
            objective=self.stage_objective(
                scenario,
                "risk_review",
                "Risk Manager evaluates constraints, concentration, and downside controls.",
            ),
            depends_on=["synthesize"],
            active_seat_ids=["risk_manager"],
        )
        constraint_ids = []
        for raw in scenario["constraints"]:
            constraint = self.constraint_object(
                ctx,
                raw["constraint_id"],
                raw["constraint_type"],
                str(raw.get("label", raw["constraint_id"]) or raw["constraint_id"]),
                raw["value"],
                raw["severity"],
            )
            constraint_ids.append(constraint["constraint_id"])
            artifacts.upsert("constraint", constraint["constraint_id"], constraint)
        risk_note = self.agent_narrative(
            "risk_manager",
            "risk_review",
            {
                "ticker": primary_ticker,
                "display_instrument": display_instrument,
                **pair_prompt_context,
                "constraints": scenario["constraints"],
                "claim_count": len(claims),
            },
            "Risk review memo",
        )
        risk_stance, risk_confidence, risk_note_text = self.parse_stance_confidence_and_text(
            "risk_manager",
            risk_note,
            role_stances.get("risk_manager"),
        )
        role_stances["risk_manager"] = risk_stance
        role_confidences["risk_manager"] = risk_confidence
        risk_supportive_claim_ids, risk_dissent_claim_ids = _claim_linkage_for_stance(risk_stance)
        risk_artifact = self.artifact_object(
            ctx,
            "risk_review",
            "risk_manager",
            "risk_memo",
            self.compact_label(risk_note_text, "Risk review memo"),
            f"var/runs/{ctx.run_id}/artifacts/risk-review.json",
        )
        artifacts.upsert("artifact", risk_artifact["artifact_id"], risk_artifact)
        self.emit(
            artifacts,
            store,
            self.make_event(
                ctx,
                "risk_review",
                "risk_manager",
                "artifact.created",
                {
                    "artifact_id": risk_artifact["artifact_id"],
                    "artifact_type": risk_artifact["artifact_type"],
                    "artifact": risk_artifact,
                    "stance": role_stances.get("risk_manager", "neutral"),
                    "confidence": role_confidences.get("risk_manager", 70),
                },
            ),
        )
        risk_position_size = 75 if scenario_type != "thesis_break_review" else int(scenario.get("starting_position_state", {}).get("size_bps", 100) or 100)
        risk_decision = self.decision_object(
            ctx,
            "risk_review",
            "risk_manager",
            "risk_review",
            "approved_with_changes",
            risk_supportive_claim_ids,
            constraint_ids,
            risk_position_size,
            risk_dissent_claim_ids,
            False,
            stance=risk_stance,
            position_action="hold" if scenario_type == "thesis_break_review" else "initiate",
        )
        artifacts.upsert("decision", risk_decision["decision_id"], risk_decision)
        self._stage_completed(
            artifacts,
            store,
            ctx,
            "risk_review",
            "research_manager",
            "success",
            f"Risk review complete across {len(constraint_ids)} constraints.",
        )
        sleep_tick()

        # pm review
        self._stage_started(
            artifacts,
            store,
            ctx,
            "pm_review",
            "research_manager",
            120,
            objective=self.stage_objective(
                scenario,
                "pm_review",
                "Portfolio Manager decides approve/reject/modify using research and risk package.",
            ),
            depends_on=["risk_review"],
            active_seat_ids=["portfolio_manager", "risk_manager"],
        )
        approval_request_id = make_id("apr")
        self.emit(
            artifacts,
            store,
            self.make_event(
                ctx,
                "pm_review",
                "portfolio_manager",
                "approval.requested",
                {
                    "approval_request_id": approval_request_id,
                    "decision_id": decision["decision_id"],
                    "editable_fields": ["size_bps", "entry_conditions", "exit_conditions", "approval_notes"],
                    "stance": role_stances.get("portfolio_manager", "neutral"),
                },
            ),
        )
        resolved_constraints = [
            artifacts.objects.get("constraint", {}).get(item_id)
            for item_id in constraint_ids
            if artifacts.objects.get("constraint", {}).get(item_id)
        ]
        pm_outcome = self.resolve_pm_policy(
            ctx,
            scenario,
            claims,
            phase_1_metric_outputs,
            resolved_constraints,
            effective_breaking_news,
        )
        pm_note = self.agent_narrative(
            "portfolio_manager",
            "pm_review",
            {
                "ticker": primary_ticker,
                "display_instrument": display_instrument,
                **pair_prompt_context,
                "scenario_type": scenario_type,
                "decision_question": decision_question,
                "recommended_size_bps": pm_outcome["position_size_bps"],
                "recommended_stance": pm_outcome["stance"],
                "position_action": pm_outcome["position_action"],
                "risk_constraints": constraint_ids,
                "vote_breakdown": pm_outcome["votes"],
                "adjusted_signal": pm_outcome["adjusted_signal"],
                "requires_news_confirmation": pm_outcome.get("requires_news_confirmation", requires_news_confirmation),
                "time_sensitive_debate": time_sensitive_debate,
                "breaking_news_mode": breaking_news_mode_effective,
                "breaking_news_trigger": breaking_news_trigger,
                "breaking_news_note": breaking_source_note,
                "breaking_risk_asymmetry": breaking_risk_note,
            },
            pm_outcome.get("approval_notes", "Start half-size ahead of earnings, add only on confirmation."),
        )
        _, pm_confidence, pm_note_text = self.parse_stance_confidence_and_text(
            "portfolio_manager",
            pm_note,
            pm_outcome["stance"],
        )
        role_confidences["portfolio_manager"] = pm_confidence
        role_stances["portfolio_manager"] = pm_outcome["stance"]
        pm_supportive_claim_ids, pm_dissent_claim_ids = _claim_linkage_for_stance(pm_outcome["stance"])
        pm_decision = self.decision_object(
            ctx,
            "pm_review",
            "portfolio_manager",
            "pm_approval",
            pm_outcome["outcome"],
            pm_supportive_claim_ids,
            constraint_ids,
            pm_outcome["position_size_bps"],
            pm_dissent_claim_ids,
            True,
            stance=pm_outcome["stance"],
            position_action=pm_outcome["position_action"],
        )
        artifacts.upsert("decision", pm_decision["decision_id"], pm_decision)
        self.emit(
            artifacts,
            store,
            self.make_event(
                ctx,
                "pm_review",
                "portfolio_manager",
                "approval.resolved",
                {
                    "approval_request_id": approval_request_id,
                    "outcome": pm_outcome["outcome"],
                    "requires_risk_recheck": True,
                    "decision": pm_decision,
                    "stance": role_stances.get("portfolio_manager", self.role_stance_from_decision(pm_outcome["outcome"])),
                    "position_action": pm_outcome["position_action"],
                    "confidence": role_confidences.get("portfolio_manager", 70),
                    "note": pm_note_text,
                },
            ),
        )
        self.emit(
            artifacts,
            store,
            self.make_event(
                ctx,
                "pm_review",
                "risk_manager",
                "risk.rechecked",
                {
                    "decision_id": pm_decision["decision_id"],
                    "status": "blocked" if pm_outcome["outcome"] == "rejected" else ("adjusted" if pm_outcome["outcome"] == "approved_with_changes" else "passed"),
                    "constraints": resolved_constraints,
                    "stance": role_stances.get("risk_manager", "neutral"),
                },
            ),
        )
        self._stage_completed(
            artifacts,
            store,
            ctx,
            "pm_review",
            "research_manager",
            "success",
            f"PM decision resolved as {pm_outcome['outcome']} with {pm_outcome['position_action']} / {pm_outcome['stance']} at {pm_outcome['position_size_bps']} bps.",
        )
        if phase_pause:
            phase_pause("pm_review")
        sleep_tick()

        # trade finalize
        self._stage_started(
            artifacts,
            store,
            ctx,
            "trade_finalize",
            "research_manager",
            15,
            objective=self.stage_objective(
                scenario,
                "trade_finalize",
                "Trader simulates execution, slippage, and final ticket viability.",
            ),
            depends_on=["pm_review"],
            active_seat_ids=["trader"],
        )
        trader_note = self.agent_narrative(
            "trader",
            "trade_finalize",
            {
                "ticker": primary_ticker,
                "display_instrument": display_instrument,
                **pair_prompt_context,
                "scenario_type": scenario_type,
                "size_bps": pm_outcome["position_size_bps"],
                "trade_side": pm_outcome["trade_side"],
                "position_action": pm_outcome["position_action"],
                "pm_note": pm_note_text,
            },
            "Execute before close with spread discipline; reassess post-event and enforce stop-loss.",
        )
        trader_stance, trader_confidence, trader_note_text = self.parse_stance_confidence_and_text(
            "trader",
            trader_note,
            role_stances.get("trader"),
        )
        role_stances["trader"] = trader_stance
        role_confidences["trader"] = trader_confidence
        hedge_leg_note = str(scenario.get("demo_mode", {}).get("hedge_leg_note", "") or "")
        if is_pair_scenario and scenario_pair_peer and pair_peer and scenario_pair_peer != pair_peer:
            hedge_leg_note = hedge_leg_note.replace(scenario_pair_peer.lower(), pair_peer.lower()).replace(scenario_pair_peer, pair_peer)
        primary_leg = {
            "instrument": primary_ticker,
            "side": pm_outcome["trade_side"],
            "size_bps": pm_outcome["position_size_bps"],
            "role": "primary",
        }
        ticket_legs = [primary_leg]
        if is_pair_scenario and pair_peer:
            hedge_side = "HOLD"
            if pm_outcome["trade_side"] == "BUY":
                hedge_side = "SELL"
            elif pm_outcome["trade_side"] == "SELL":
                hedge_side = "BUY"
            hedge_size_bps = pm_outcome["position_size_bps"] if hedge_side in {"BUY", "SELL"} else 0
            ticket_legs.append(
                {
                    "instrument": pair_peer,
                    "side": hedge_side,
                    "size_bps": hedge_size_bps,
                    "role": "hedge",
                }
            )
        if scenario_type == "thesis_break_review":
            ticket_entry_conditions = [
                item
                for item in [
                    "thesis_break_review",
                    "liquidity_window_confirmed",
                    "de_risking_plan_confirmed",
                    pm_note_text,
                ]
                if item
            ]
            ticket_exit_conditions = [
                item
                for item in [
                    "reentry_requires_fresh_underwriting",
                    "monitor_residual_drawdown",
                    trader_note_text,
                ]
                if item
            ]
        elif is_pair_scenario:
            ticket_entry_conditions = [item for item in ["before_close", "spread_under_threshold", pm_note_text, hedge_leg_note] if item]
            ticket_exit_conditions = [item for item in ["post_earnings_review", "stop_loss_4pct", trader_note_text] if item]
        else:
            ticket_entry_conditions = [item for item in ["before_close", "event_confirmation", pm_note_text] if item]
            ticket_exit_conditions = [item for item in ["post_event_review", "stop_loss_4pct", trader_note_text] if item]
        ticket = self.ticket_object(
            ctx,
            "pair_trade" if len(ticket_legs) > 1 else "single_leg",
            display_instrument or scenario.get("demo_mode", {}).get("instrument_label") or primary_ticker,
            ticket_legs,
            constraint_ids,
            "portfolio_manager",
            ticket_entry_conditions,
            ticket_exit_conditions,
            time_horizon,
        )
        artifacts.upsert("trade_ticket", ticket["ticket_id"], ticket)
        trader_artifact = self.artifact_object(
            ctx,
            "trade_finalize",
            "trader",
            "trade_ticket",
            self.compact_label(trader_note_text, "Trader execution ticket"),
            f"var/runs/{ctx.run_id}/artifacts/trade-ticket.json",
        )
        artifacts.upsert("artifact", trader_artifact["artifact_id"], trader_artifact)
        self.emit(
            artifacts,
            store,
            self.make_event(
                ctx,
                "trade_finalize",
                "trader",
                "artifact.created",
                {
                    "artifact_id": trader_artifact["artifact_id"],
                    "artifact_type": trader_artifact["artifact_type"],
                    "artifact": trader_artifact,
                    "stance": role_stances.get("trader", "neutral"),
                    "confidence": role_confidences.get("trader", 70),
                    "execution_note": trader_note_text,
                },
            ),
        )
        self.emit(
            artifacts,
            store,
            self.make_event(
                ctx,
                "trade_finalize",
                "trader",
                "ticket.updated",
                {
                    "ticket_id": ticket["ticket_id"],
                    "ticket": ticket,
                    "status": "final",
                    "stance": role_stances.get("trader", "neutral"),
                    "confidence": role_confidences.get("trader", 70),
                    "execution_note": trader_note_text,
                },
            ),
        )
        self._stage_completed(
            artifacts,
            store,
            ctx,
            "trade_finalize",
            "research_manager",
            "success",
            "Execution simulation complete and ticket finalized.",
        )
        if phase_pause:
            phase_pause("trade_finalize")
        sleep_tick()

        # monitor
        self._stage_started(
            artifacts,
            store,
            ctx,
            "monitor",
            "research_manager",
            30,
            objective=self.stage_objective(
                scenario,
                "monitor",
                "Monitor post-trade signals and trigger reroute on new breaking developments.",
            ),
            depends_on=["trade_finalize"],
            active_seat_ids=["trader", "research_manager"],
        )
        monitoring_note = self.agent_narrative(
            "trader",
            "monitor",
            {
                "ticker": primary_ticker,
                "display_instrument": display_instrument,
                **pair_prompt_context,
                "pm_note": pm_note_text,
                "trader_note": trader_note_text,
            },
            "Monitoring plan",
        )
        monitoring_stance, monitoring_confidence, monitoring_note_text = self.parse_stance_confidence_and_text(
            "trader",
            monitoring_note,
            role_stances.get("trader"),
        )
        role_stances["trader"] = monitoring_stance
        role_confidences["trader"] = monitoring_confidence
        monitor_artifact = self.artifact_object(
            ctx,
            "monitor",
            "trader",
            "monitoring_plan",
            self.compact_label(monitoring_note_text, "Monitoring plan"),
            f"var/runs/{ctx.run_id}/artifacts/monitoring-plan.json",
        )
        artifacts.upsert("artifact", monitor_artifact["artifact_id"], monitor_artifact)
        self.emit(
            artifacts,
            store,
            self.make_event(
                ctx,
                "monitor",
                "trader",
                "artifact.created",
                {
                    "artifact_id": monitor_artifact["artifact_id"],
                    "artifact_type": monitor_artifact["artifact_type"],
                    "artifact": monitor_artifact,
                    "stance": role_stances.get("trader", "neutral"),
                    "confidence": role_confidences.get("trader", 70),
                    "monitoring_note": monitoring_note_text,
                },
            ),
        )
        self._stage_completed(
            artifacts,
            store,
            ctx,
            "monitor",
            "research_manager",
            "success",
            "Monitoring plan delivered; desk ready for continuous loop checks.",
        )
        artifacts.stage_sequence.append("completed")
        self.emit(artifacts, store, self.make_event(ctx, "completed", "trader", "run.completed", {"final_decision_id": pm_decision["decision_id"], "ticket_id": ticket["ticket_id"]}))

        store.write_objects(artifacts)
        summary = {
            "run_id": ctx.run_id,
            "scenario_id": scenario_id,
            "scenario_type": scenario_type,
            "runtime": self.runtime_name,
            "ticker": ctx.ticker,
            "tickers": [primary_ticker, *peer_tickers],
            "pair_mode": pair_mode,
            "peer_tickers": peer_tickers,
            "stage_sequence": artifacts.stage_sequence,
            "object_counts": {key: len(value) for key, value in artifacts.objects.items()},
            "ticket_id": ticket["ticket_id"],
            "decision_id": pm_decision["decision_id"],
            "breaking_news_reroute": effective_breaking_news,
            "breaking_news_mode": breaking_news_mode_effective,
            "breaking_news_mode_requested": breaking_news_mode_requested,
            "breaking_news_mode_effective": breaking_news_mode_effective,
            "breaking_news_forced_by_scenario": scenario_forced_breaking_news,
            "breaking_news_trigger": breaking_news_trigger,
            "breaking_news_delay_s": breaking_news_delay_s,
            "requires_news_confirmation": requires_news_confirmation,
            "time_sensitive_debate": time_sensitive_debate,
            "debate_depth": debate_depth,
            "llm": self.agent_text.diagnostics(),
        }
        store.write_summary(summary)
        result = {
            "run_id": ctx.run_id,
            "runtime": self.runtime_name,
            "events": artifacts.event_log,
            "objects": artifacts.objects,
            "summary": summary,
        }
        validate_run_payload(result)
        return result
