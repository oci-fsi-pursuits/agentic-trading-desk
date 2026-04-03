from __future__ import annotations

import hashlib
import json
import re
from abc import ABC, abstractmethod
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Callable, Iterable

from runtime.common.contract_validation import validate_event, validate_run_payload
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


class BaseAdapter(ABC):
    runtime_name: str

    def __init__(self) -> None:
        self.dataset = load_demo_dataset()
        self.agent_text = AgentTextService()

    def agent_narrative(self, role_id: str, phase_id: str, task: str, context: dict[str, Any], fallback: str, max_words: int = 200) -> str:
        return self.agent_text.generate(role_id, phase_id, task, context, fallback, max_words=max_words)

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
        for claim in claims:
            stance = self.normalize_stance(claim.get("stance"), "neutral")
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
        votes = self.vote_breakdown(claims)
        net_votes = votes["long"] - votes["short"]
        composite_signal = self.metric_value(metrics, "composite signal")
        revision_signal = self.metric_value(metrics, "estimate revision score")
        momentum_signal = self.metric_value(metrics, "20d momentum")
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
        }

    def parse_stance_confidence_and_text(
        self,
        role_id: str,
        narrative: str,
        default_stance: str | None = None,
        default_confidence: int = 70,
    ) -> tuple[str, int, str]:
        default = self.normalize_stance(default_stance or DEFAULT_ROLE_STANCE.get(role_id, "neutral"))
        text = " ".join((narrative or "").split()).strip()
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
        cleaned = " ".join(cleaned.split()).strip()
        return stance, confidence, cleaned or text

    def parse_stance_and_text(self, role_id: str, narrative: str, default_stance: str | None = None) -> tuple[str, str]:
        stance, _, text = self.parse_stance_confidence_and_text(role_id, narrative, default_stance)
        return stance, text

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
        requested_tokens = self.extract_tickers(ticker, limit=1)
        requested_ticker = requested_tokens[0] if requested_tokens else (ticker or scenario.get("instrument", "NVDA")).strip().upper()
        if not requested_ticker:
            requested_ticker = "NVDA"
        self.dataset = load_demo_dataset(scenario_id, requested_ticker)
        return (
            RunContext(
                run_id=run_id or make_id("run"),
                scenario_id=scenario_id,
                runtime=self.runtime_name,
                active_seat_ids=seats,
                ticker=requested_ticker,
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

    def ticket_object(self, ctx: RunContext, instrument: str, side: str, size_bps: int, constraint_ids: list[str], approved_by: str, entry_conditions: list[str], exit_conditions: list[str]) -> dict[str, Any]:
        return {
            "schema_version": "v1",
            "ticket_id": make_id("tkt"),
            "instrument": instrument,
            "side": side,
            "size_bps": size_bps,
            "time_horizon": "event_tactical",
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
        debate_depth: int = 1,
        phase_pause: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        requested_tickers = self.extract_tickers(ticker, limit=2)
        primary_override = requested_tickers[0] if requested_tickers else None
        peer_override = requested_tickers[1] if len(requested_tickers) > 1 else ""
        ctx, scenario = self.build_context(scenario_id, active_seat_ids, primary_override, run_id=run_id)
        artifacts = RunArtifacts()
        store = RunStore(ctx.run_id)
        primary_ticker = ctx.ticker
        scenario_primary_ticker = str(scenario.get("instrument", "") or "").strip().upper()
        scenario_pair_peer = str(scenario.get("pair_peer", "") or "").strip().upper()
        is_pair_scenario = len(scenario.get("instrument_universe", [])) > 1
        pair_peer = peer_override if (is_pair_scenario and peer_override) else scenario_pair_peer
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
        effective_breaking_news = (
            breaking_news
            or bool(scenario.get("branch_conditions", {}).get("force_breaking_news"))
            or bool(scenario.get("demo_mode", {}).get("force_breaking_news"))
        )
        debate_depth = max(1, min(int(debate_depth or 1), 8))
        dataset_for_narrative = self.dataset
        if is_pair_scenario and scenario_pair_peer and pair_peer and scenario_pair_peer != pair_peer:
            dataset_for_narrative = json.loads(
                json.dumps(self.dataset).replace(scenario_pair_peer, pair_peer)
            )
        live_context = build_live_context(
            primary_ticker,
            dataset_for_narrative,
            pair_peer=pair_peer,
            active_seat_ids=ctx.active_seat_ids,
            run_id=ctx.run_id,
        )
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
        social_source = None
        macro_source = None
        geopolitical_source = None
        market_context = live_context.get("market_context", dataset_for_narrative.get("market_context", {}))
        fundamentals = live_context.get("fundamentals", dataset_for_narrative.get("fundamentals", {}))
        news_items = live_context.get("news_items", dataset_for_narrative.get("news_items", []))
        social_context = live_context.get("social_context", {})
        macro_context = live_context.get("macro_context", {})
        geopolitical_context = live_context.get("geopolitical_context", {})
        live_coverage = dict(live_context.get("coverage", {}) or {})
        live_errors = list(live_context.get("errors", []) or [])
        live_domain_metadata = dict(live_context.get("domain_metadata", {}) or {})
        market_freshness = "live" if live_coverage.get("market") == "live" else "snapshot"
        news_freshness = "live" if live_coverage.get("news") == "live" else "snapshot"
        fundamentals_freshness = "live" if live_coverage.get("fundamentals") == "live" else "snapshot"
        social_freshness = "live" if live_coverage.get("social") == "live" else "snapshot"
        macro_freshness = "live" if live_coverage.get("macro") == "live" else "snapshot"
        geopolitical_freshness = "live" if live_coverage.get("geopolitical") == "live" else "snapshot"
        gather_context = {
            "ticker": primary_ticker,
            "market_context": market_context,
            "fundamentals": fundamentals,
            "news": news_items,
            "news_mode": "live_news",
            "has_live_news": bool(news_items),
            "sentiment_score": live_context.get("sentiment_score", dataset_for_narrative.get("sentiment_score")),
            "ai_basket_correlation": live_context.get("ai_basket_correlation", dataset_for_narrative.get("ai_basket_correlation")),
            "social_context": social_context,
            "macro_context": macro_context,
            "geopolitical_context": geopolitical_context,
            "live_context_coverage": live_coverage,
            "live_context_errors": live_errors,
            "live_context_sources": live_domain_metadata,
            "display_instrument": display_instrument,
            "pair_peer": pair_peer,
            "scenario_name": scenario.get("name", ""),
            "scenario_type": scenario_type,
            "scenario_summary": scenario_summary,
            "decision_question": decision_question,
            "thesis_prompt": thesis_prompt,
            "position_context": scenario.get("demo_mode", {}).get("position_context", ""),
            "starting_position_state": scenario.get("starting_position_state", {}),
        }
        quant_input_dataset = live_context.get("quant_dataset", dataset_for_narrative)
        role_stances: dict[str, str] = dict(DEFAULT_ROLE_STANCE)
        role_confidences: dict[str, int] = {}
        # Start quant compute in parallel with gather analyst narratives so quant no longer waits
        # for every gather seat to finish before beginning heavy work.
        quant_prefetch_executor: ThreadPoolExecutor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quant_prefetch")
        quant_prefetch_future: Future[dict[str, Any]] = quant_prefetch_executor.submit(run_quant, quant_input_dataset, ctx.run_id)
        quant_prefetch_executor.shutdown(wait=False)
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

        market_content_raw = self.agent_narrative(
            "market_analyst",
            "gather",
            "Summarize technical setup, momentum, volatility, and actionable levels.",
            gather_context,
            f"{primary_ticker} has 20D momentum of {market_context.get('momentum_20d_pct', 'n/a')}% with event risk marked {market_context.get('event_risk', 'n/a')}.",
        )
        market_stance, market_confidence, market_content = self.parse_stance_confidence_and_text("market_analyst", market_content_raw)
        role_stances["market_analyst"] = market_stance
        role_confidences["market_analyst"] = market_confidence
        market_source = self.source_object(
            ctx,
            "gather",
            "market_analyst",
            make_id("src"),
            "market_data",
            f"{display_instrument} momentum and event setup",
            market_content,
            market_freshness,
        )
        sources.append(market_source)
        emit_gather_source(market_source, "market_analyst")
        news_content_raw = self.agent_narrative(
            "news_analyst",
            "gather",
            "Summarize the latest live news context for this ticker without inventing any headline if none was provided.",
            gather_context,
            (news_items[0].get("summary", "") if news_items else f"No current live news provided for {display_instrument}."),
        )
        news_stance, news_confidence, news_content = self.parse_stance_confidence_and_text("news_analyst", news_content_raw)
        role_stances["news_analyst"] = news_stance
        role_confidences["news_analyst"] = news_confidence
        news_title = news_items[0].get("title", f"{display_instrument} news context") if news_items else f"{display_instrument} news context"
        news_source = self.source_object(
            ctx,
            "gather",
            "news_analyst",
            make_id("src"),
            "news",
            f"{display_instrument}: {news_title}",
            news_content,
            news_freshness,
        )
        sources.append(news_source)
        emit_gather_source(news_source, "news_analyst")
        fundamentals_content_raw = self.agent_narrative(
            "fundamentals_analyst",
            "gather",
            "Summarize earnings quality, valuation context, and balance sheet signal.",
            gather_context,
            f"{primary_ticker} estimate revisions remain {fundamentals.get('estimate_revision_trend', 'mixed')} while valuation is {fundamentals.get('valuation_state', 'uncertain')}.",
        )
        fundamentals_stance, fundamentals_confidence, fundamentals_content = self.parse_stance_confidence_and_text("fundamentals_analyst", fundamentals_content_raw)
        role_stances["fundamentals_analyst"] = fundamentals_stance
        role_confidences["fundamentals_analyst"] = fundamentals_confidence
        fundamentals_source = self.source_object(
            ctx,
            "gather",
            "fundamentals_analyst",
            make_id("src"),
            "fundamentals",
            f"{display_instrument} estimate revisions and valuation",
            fundamentals_content,
            fundamentals_freshness,
        )
        sources.append(fundamentals_source)
        emit_gather_source(fundamentals_source, "fundamentals_analyst")
        if "social_analyst" in ctx.active_seat_ids:
            social_content_raw = self.agent_narrative(
                "social_analyst",
                "gather",
                "Summarize social sentiment velocity, tone, and crowding risk.",
                gather_context,
                (
                    f"Social feed around {primary_ticker} is currently {social_context.get('sentiment_label', 'mixed')} "
                    f"with sentiment score {float(gather_context.get('sentiment_score', 0.5)):.2f} "
                    f"and mention velocity {float(social_context.get('mention_velocity_per_hour', 0.0)):.2f}/hr."
                ),
            )
            social_stance, social_confidence, social_content = self.parse_stance_confidence_and_text("social_analyst", social_content_raw)
            role_stances["social_analyst"] = social_stance
            role_confidences["social_analyst"] = social_confidence
            social_source = self.source_object(
                ctx,
                "gather",
                "social_analyst",
                make_id("src"),
                "social",
                f"{display_instrument} retail sentiment and crowding",
                social_content,
                social_freshness,
            )
            sources.append(social_source)
            emit_gather_source(social_source, "social_analyst")
        if "macro_economist" in ctx.active_seat_ids:
            macro_content_raw = self.agent_narrative(
                "macro_economist",
                "gather",
                "Provide macro regime context linked to ticker and sector.",
                gather_context,
                (
                    f"Macro regime is {macro_context.get('regime', 'mixed')}, VIX is {macro_context.get('vix_level', 'n/a')}, "
                    f"US 10Y is {macro_context.get('us10y_yield_pct', 'n/a')}%, and SPX daily change is {macro_context.get('spx_change_pct', 'n/a')}%."
                ),
            )
            macro_stance, macro_confidence, macro_content = self.parse_stance_confidence_and_text("macro_economist", macro_content_raw)
            role_stances["macro_economist"] = macro_stance
            role_confidences["macro_economist"] = macro_confidence
            macro_source = self.source_object(
                ctx,
                "gather",
                "macro_economist",
                make_id("src"),
                "macro",
                f"{display_instrument} macro backdrop",
                macro_content,
                macro_freshness,
            )
            sources.append(macro_source)
            emit_gather_source(macro_source, "macro_economist")
        if "geopolitical_analyst" in ctx.active_seat_ids:
            geopolitical_content_raw = self.agent_narrative(
                "geopolitical_analyst",
                "gather",
                "Summarize geopolitical and trade-policy pathways affecting this ticker.",
                gather_context,
                (
                    f"Geopolitical risk level is {geopolitical_context.get('risk_level', 'moderate')} "
                    f"with {geopolitical_context.get('headline_count', 0)} recent policy-related headlines."
                ),
            )
            geopolitical_stance, geopolitical_confidence, geopolitical_content = self.parse_stance_confidence_and_text("geopolitical_analyst", geopolitical_content_raw)
            role_stances["geopolitical_analyst"] = geopolitical_stance
            role_confidences["geopolitical_analyst"] = geopolitical_confidence
            geopolitical_source = self.source_object(
                ctx,
                "gather",
                "geopolitical_analyst",
                make_id("src"),
                "geopolitical",
                f"{display_instrument} geopolitical exposure",
                geopolitical_content,
                geopolitical_freshness,
            )
            sources.append(geopolitical_source)
            emit_gather_source(geopolitical_source, "geopolitical_analyst")

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
            self._stage_started(
                artifacts,
                store,
                ctx,
                "gather",
                "research_manager",
                20,
                objective="Breaking-news override pass, refresh inputs before debate.",
                depends_on=["gather"],
                active_seat_ids=[seat for seat in ("news_analyst", "market_analyst", "research_manager") if seat in ctx.active_seat_ids],
                reason="breaking_news_reroute",
            )
            breaking_context = {
                "ticker": primary_ticker,
                "breaking_news": True,
                "news_mode": "synthetic_breaking_news",
                "scenario_type": scenario_type,
                "display_instrument": display_instrument,
                "position_context": scenario.get("demo_mode", {}).get("position_context", ""),
                "simulation_intensity": "severe",
                "existing_evidence_count": len(evidences),
            }
            breaking_source_raw = self.agent_narrative(
                "news_analyst",
                "gather",
                "Invent a severe, ticker-specific breaking-news development that forces an immediate desk reroute and summarize its implications.",
                breaking_context,
                f"Synthetic breaking news drill: invent one severe, ticker-specific development for {display_instrument} that materially changes near-term positioning.",
            )
            breaking_stance, breaking_confidence, breaking_source_content = self.parse_stance_confidence_and_text(
                "news_analyst",
                breaking_source_raw,
                role_stances.get("news_analyst"),
            )
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
            breaking_evidence_raw = self.agent_narrative(
                "news_analyst",
                "gather",
                "Explain in one sentence why this severe synthetic breaking development changes risk asymmetry for the ticker.",
                breaking_context,
                f"The synthetic breaking development materially changes risk asymmetry for {display_instrument} and requires immediate desk reassessment.",
            )
            breaking_stance2, breaking_confidence2, breaking_evidence_content = self.parse_stance_confidence_and_text(
                "news_analyst",
                breaking_evidence_raw,
                role_stances.get("news_analyst"),
            )
            role_stances["news_analyst"] = breaking_stance2
            role_confidences["news_analyst"] = breaking_confidence2
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
            "Summarize quant findings and limitations that should inform the upcoming debate.",
            {
                "ticker": primary_ticker,
                "display_instrument": display_instrument,
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
            40,
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
            "evidence_titles": [item["title"] for item in evidences],
            "quant_summary": quant_note_text,
            "quant_metrics": phase_1_metric_outputs,
            "stage": "debate",
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
                response_task = "Present your desk view using the Phase 1 analyst outputs and gathered evidence."
                if previous_claim is not None:
                    response_task = (
                        f"Respond directly to the previous speaker ({previous_claim['provenance']['producer_role']}) "
                        f"and refine or challenge their point using Phase 1 analyst outputs and evidence."
                    )
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
                    response_task,
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

        supportive_claim_ids = [claim["claim_id"] for claim in claims if claim["stance"] == "long"] or [claims[0]["claim_id"]]
        dissent_claim_ids = [claim["claim_id"] for claim in claims if claim["stance"] == "short"] or [claims[0]["claim_id"]]
        all_claim_ids = [claim["claim_id"] for claim in claims]
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
            "Provide a concise desk-level synthesis for PM handoff.",
            {"ticker": primary_ticker, "display_instrument": display_instrument, "claim_count": len(claims), "metric_count": len(metric_ids)},
            "Research manager summary",
        )
        research_stance, research_confidence, research_note_text = self.parse_stance_confidence_and_text(
            "research_manager",
            research_note,
            role_stances.get("research_manager"),
        )
        role_stances["research_manager"] = research_stance
        role_confidences["research_manager"] = research_confidence
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
            supportive_claim_ids,
            [],
            synth_position_size,
            dissent_claim_ids,
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
            constraint = self.constraint_object(ctx, raw["constraint_id"], raw["constraint_type"], raw["constraint_id"], raw["value"], raw["severity"])
            constraint_ids.append(constraint["constraint_id"])
            artifacts.upsert("constraint", constraint["constraint_id"], constraint)
        risk_note = self.agent_narrative(
            "risk_manager",
            "risk_review",
            "Summarize key risk controls, constraints, and gating conclusion.",
            {"ticker": primary_ticker, "display_instrument": display_instrument, "constraints": scenario["constraints"], "claim_count": len(claims)},
            "Risk review memo",
        )
        risk_stance, risk_confidence, risk_note_text = self.parse_stance_confidence_and_text(
            "risk_manager",
            risk_note,
            role_stances.get("risk_manager"),
        )
        role_stances["risk_manager"] = risk_stance
        role_confidences["risk_manager"] = risk_confidence
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
            all_claim_ids,
            constraint_ids,
            risk_position_size,
            dissent_claim_ids,
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
            "Provide concise PM decision note with size and horizon framing.",
            {
                "ticker": primary_ticker,
                "display_instrument": display_instrument,
                "scenario_type": scenario_type,
                "decision_question": decision_question,
                "recommended_size_bps": pm_outcome["position_size_bps"],
                "recommended_stance": pm_outcome["stance"],
                "position_action": pm_outcome["position_action"],
                "risk_constraints": constraint_ids,
                "vote_breakdown": pm_outcome["votes"],
                "adjusted_signal": pm_outcome["adjusted_signal"],
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
        pm_decision = self.decision_object(
            ctx,
            "pm_review",
            "portfolio_manager",
            "pm_approval",
            pm_outcome["outcome"],
            supportive_claim_ids,
            constraint_ids,
            pm_outcome["position_size_bps"],
            dissent_claim_ids,
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
            "Provide best-execution implementation guidance with slippage and timing notes.",
            {
                "ticker": primary_ticker,
                "display_instrument": display_instrument,
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
        ticket = self.ticket_object(
            ctx,
            display_instrument or scenario.get("demo_mode", {}).get("instrument_label"),
            pm_outcome["trade_side"],
            pm_outcome["position_size_bps"],
            constraint_ids,
            "portfolio_manager",
            ["before_close", "spread_under_threshold", pm_note_text, hedge_leg_note],
            ["post_earnings_review", "stop_loss_4pct", trader_note_text],
        )
        ticket["entry_conditions"] = [item for item in ticket["entry_conditions"] if item]
        ticket["time_horizon"] = time_horizon
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
            "Provide concise post-trade monitoring priorities for the desk.",
            {"ticker": primary_ticker, "display_instrument": display_instrument, "pm_note": pm_note_text, "trader_note": trader_note_text},
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
            "runtime": self.runtime_name,
            "ticker": ctx.ticker,
            "stage_sequence": artifacts.stage_sequence,
            "object_counts": {key: len(value) for key, value in artifacts.objects.items()},
            "ticket_id": ticket["ticket_id"],
            "decision_id": pm_decision["decision_id"],
            "breaking_news_reroute": effective_breaking_news,
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
