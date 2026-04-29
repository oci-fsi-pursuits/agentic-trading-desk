from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from runtime.common.registry import event_types as registry_event_types
from runtime.common.registry import role_ids, stage_ids

IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_\-.]*$")
TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
ROLE_IDS = set(role_ids())
STAGE_IDS = set(stage_ids())
EVENT_TYPES = set(registry_event_types())

ALLOWED_TAGS = {
    "fundamentals",
    "earnings",
    "macro",
    "sentiment",
    "positioning",
    "risk",
    "valuation",
    "liquidity",
    "event",
    "thesis",
    "geopolitics",
}

OBJECT_REQUIRED_FIELDS = {
    "source": {"schema_version", "source_id", "source_type", "title", "content", "provenance", "freshness"},
    "evidence": {"schema_version", "evidence_id", "evidence_type", "title", "summary", "source_ids", "confidence", "provenance", "tags"},
    "claim": {"schema_version", "claim_id", "stance", "statement", "supporting_evidence_ids", "confidence", "provenance"},
    "metric": {"schema_version", "metric_id", "name", "value", "unit", "code_artifact_id", "confidence", "provenance"},
    "artifact": {"schema_version", "artifact_id", "artifact_type", "label", "storage_uri", "provenance"},
    "constraint": {"schema_version", "constraint_id", "constraint_type", "label", "severity", "provenance"},
    "decision": {"schema_version", "decision_id", "decision_type", "outcome", "linked_claim_ids", "linked_constraint_ids", "provenance"},
    "trade_ticket": {
        "schema_version",
        "ticket_id",
        "ticket_type",
        "display_instrument",
        "legs",
        "exposure",
        "time_horizon",
        "entry_conditions",
        "exit_conditions",
        "constraint_ids",
        "approved_by",
        "provenance",
    },
}


def _fail(context: str, message: str) -> None:
    raise ValueError(f"{context}: {message}")


def _require_keys(context: str, payload: dict[str, Any], required: set[str]) -> None:
    missing = sorted(required.difference(payload.keys()))
    if missing:
        _fail(context, f"missing keys {missing}")


def _assert_identifier(context: str, value: Any) -> None:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        _fail(context, f"invalid identifier '{value}'")


def _assert_timestamp(context: str, value: Any) -> None:
    if not isinstance(value, str):
        _fail(context, "timestamp must be a string")
    normalized = value.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError:
        _fail(context, f"invalid timestamp '{value}'")


def _assert_confidence(context: str, value: Any) -> None:
    if not isinstance(value, (int, float)):
        _fail(context, "confidence must be numeric")
    if value < 0 or value > 1:
        _fail(context, f"confidence out of range: {value}")


def _assert_provenance(context: str, provenance: Any) -> None:
    if not isinstance(provenance, dict):
        _fail(context, "provenance must be an object")
    _require_keys(f"{context}.provenance", provenance, {"run_id", "stage_id", "producer_role", "emitted_at"})
    _assert_identifier(f"{context}.provenance.run_id", provenance["run_id"])
    if provenance["stage_id"] not in STAGE_IDS:
        _fail(f"{context}.provenance.stage_id", f"unknown stage '{provenance['stage_id']}'")
    if provenance["producer_role"] not in ROLE_IDS:
        _fail(f"{context}.provenance.producer_role", f"unknown role '{provenance['producer_role']}'")
    _assert_timestamp(f"{context}.provenance.emitted_at", provenance["emitted_at"])


def validate_event(event: dict[str, Any]) -> None:
    context = "event"
    required = {"schema_version", "event_id", "event_type", "run_id", "stage_id", "emitted_at", "producer", "payload"}
    _require_keys(context, event, required)
    if event["schema_version"] != "v1":
        _fail(context, f"unsupported schema_version '{event['schema_version']}'")
    _assert_identifier("event.event_id", event["event_id"])
    _assert_identifier("event.run_id", event["run_id"])
    if event["event_type"] not in EVENT_TYPES:
        _fail("event.event_type", f"unknown event type '{event['event_type']}'")
    if event["stage_id"] not in STAGE_IDS:
        _fail("event.stage_id", f"unknown stage '{event['stage_id']}'")
    if event["producer"] not in ROLE_IDS:
        _fail("event.producer", f"unknown producer role '{event['producer']}'")
    _assert_timestamp("event.emitted_at", event["emitted_at"])
    payload = event["payload"]
    if not isinstance(payload, dict):
        _fail("event.payload", "payload must be an object")

    event_type = event["event_type"]
    if event_type == "run.started":
        _require_keys("event.payload", payload, {"scenario_id", "runtime", "active_seat_ids"})
        if payload["runtime"] not in {"wayflow", "langgraph"}:
            _fail("event.payload.runtime", f"unsupported runtime '{payload['runtime']}'")
        if not isinstance(payload["active_seat_ids"], list):
            _fail("event.payload.active_seat_ids", "must be a list")
        for role in payload["active_seat_ids"]:
            if role not in ROLE_IDS:
                _fail("event.payload.active_seat_ids", f"unknown role '{role}'")
        ticker = payload.get("ticker")
        if ticker is not None and (not isinstance(ticker, str) or not TICKER_RE.fullmatch(ticker)):
            _fail("event.payload.ticker", f"invalid ticker '{ticker}'")
    elif event_type == "stage.started":
        _require_keys("event.payload", payload, {"timeout_s"})
        if not isinstance(payload["timeout_s"], int) or payload["timeout_s"] < 1:
            _fail("event.payload.timeout_s", "must be an integer >= 1")
    elif event_type == "seat.activated":
        _require_keys("event.payload", payload, {"seat_id", "activation_mode"})
        if payload["seat_id"] not in ROLE_IDS:
            _fail("event.payload.seat_id", f"unknown role '{payload['seat_id']}'")
        if payload["activation_mode"] not in {"required", "optional", "conditional"}:
            _fail("event.payload.activation_mode", f"invalid value '{payload['activation_mode']}'")
    elif event_type == "source.ingested":
        _require_keys("event.payload", payload, {"source_id"})
        _assert_identifier("event.payload.source_id", payload["source_id"])
    elif event_type in {"evidence.upserted", "claim.upserted", "metric.upserted"}:
        _require_keys("event.payload", payload, {"object_id", "object_type"})
        _assert_identifier("event.payload.object_id", payload["object_id"])
        expected_type = event_type.split(".")[0]
        if payload["object_type"] != expected_type:
            _fail("event.payload.object_type", f"expected '{expected_type}', got '{payload['object_type']}'")
    elif event_type == "artifact.created":
        _require_keys("event.payload", payload, {"artifact_id", "artifact_type"})
        _assert_identifier("event.payload.artifact_id", payload["artifact_id"])
    elif event_type == "approval.requested":
        _require_keys("event.payload", payload, {"approval_request_id", "decision_id", "editable_fields"})
        if not isinstance(payload["editable_fields"], list):
            _fail("event.payload.editable_fields", "must be a list")
    elif event_type == "approval.resolved":
        _require_keys("event.payload", payload, {"approval_request_id", "outcome", "requires_risk_recheck"})
        if payload["outcome"] not in {"approved", "approved_with_changes", "rejected"}:
            _fail("event.payload.outcome", f"invalid value '{payload['outcome']}'")
        if not isinstance(payload["requires_risk_recheck"], bool):
            _fail("event.payload.requires_risk_recheck", "must be a boolean")
    elif event_type == "risk.rechecked":
        _require_keys("event.payload", payload, {"decision_id", "status"})
        if payload["status"] not in {"passed", "adjusted", "blocked"}:
            _fail("event.payload.status", f"invalid value '{payload['status']}'")
    elif event_type == "ticket.updated":
        _require_keys("event.payload", payload, {"ticket_id", "status"})
        if payload["status"] not in {"draft", "final"}:
            _fail("event.payload.status", f"invalid value '{payload['status']}'")
    elif event_type == "stage.completed":
        _require_keys("event.payload", payload, {"status"})
        if payload["status"] not in {"success", "warning", "failed"}:
            _fail("event.payload.status", f"invalid value '{payload['status']}'")
    elif event_type == "run.completed":
        _require_keys("event.payload", payload, {"final_decision_id", "ticket_id"})
        _assert_identifier("event.payload.final_decision_id", payload["final_decision_id"])
        _assert_identifier("event.payload.ticket_id", payload["ticket_id"])
    elif event_type == "run.failed":
        _require_keys("event.payload", payload, {"error_code", "message"})


def validate_object(object_type: str, obj: dict[str, Any]) -> None:
    if object_type not in OBJECT_REQUIRED_FIELDS:
        _fail("object", f"unknown object type '{object_type}'")
    context = f"object.{object_type}"
    _require_keys(context, obj, OBJECT_REQUIRED_FIELDS[object_type])
    expected_schema_version = "v2" if object_type == "trade_ticket" else "v1"
    if obj["schema_version"] != expected_schema_version:
        _fail(context, f"unsupported schema_version '{obj['schema_version']}'")

    id_field = {
        "source": "source_id",
        "evidence": "evidence_id",
        "claim": "claim_id",
        "metric": "metric_id",
        "artifact": "artifact_id",
        "constraint": "constraint_id",
        "decision": "decision_id",
        "trade_ticket": "ticket_id",
    }[object_type]
    _assert_identifier(f"{context}.{id_field}", obj[id_field])
    _assert_provenance(context, obj["provenance"])

    if object_type == "source":
        if obj["source_type"] not in {"market_data", "news", "social", "fundamentals", "macro", "geopolitical", "internal_note"}:
            _fail(f"{context}.source_type", f"invalid value '{obj['source_type']}'")
        if obj["freshness"] not in {"live", "delayed", "snapshot", "historical"}:
            _fail(f"{context}.freshness", f"invalid value '{obj['freshness']}'")
    elif object_type == "evidence":
        if not isinstance(obj["source_ids"], list) or not obj["source_ids"]:
            _fail(f"{context}.source_ids", "must be a non-empty list")
        for source_id in obj["source_ids"]:
            _assert_identifier(f"{context}.source_ids[]", source_id)
        _assert_confidence(f"{context}.confidence", obj["confidence"])
        if not isinstance(obj["tags"], list) or not obj["tags"]:
            _fail(f"{context}.tags", "must be a non-empty list")
        for tag in obj["tags"]:
            if tag not in ALLOWED_TAGS:
                _fail(f"{context}.tags", f"invalid tag '{tag}'")
    elif object_type == "claim":
        if obj["stance"] not in {"bull", "bear", "neutral", "dissent", "long", "short"}:
            _fail(f"{context}.stance", f"invalid value '{obj['stance']}'")
        if not isinstance(obj["supporting_evidence_ids"], list) or not obj["supporting_evidence_ids"]:
            _fail(f"{context}.supporting_evidence_ids", "must be a non-empty list")
        for evidence_id in obj["supporting_evidence_ids"]:
            _assert_identifier(f"{context}.supporting_evidence_ids[]", evidence_id)
        for evidence_id in obj.get("counter_evidence_ids", []):
            _assert_identifier(f"{context}.counter_evidence_ids[]", evidence_id)
        _assert_confidence(f"{context}.confidence", obj["confidence"])
    elif object_type == "metric":
        _assert_identifier(f"{context}.code_artifact_id", obj["code_artifact_id"])
        _assert_confidence(f"{context}.confidence", obj["confidence"])
        coverage = obj.get("coverage")
        if coverage is not None and coverage not in {"full", "partial"}:
            _fail(f"{context}.coverage", f"invalid value '{coverage}'")
    elif object_type == "artifact":
        if obj["artifact_type"] not in {"chart", "notebook", "summary_memo", "trade_ticket", "monitoring_plan", "risk_memo"}:
            _fail(f"{context}.artifact_type", f"invalid value '{obj['artifact_type']}'")
    elif object_type == "constraint":
        if obj["constraint_type"] not in {"position_limit", "liquidity", "correlation", "event_risk", "mandate"}:
            _fail(f"{context}.constraint_type", f"invalid value '{obj['constraint_type']}'")
        if obj["severity"] not in {"info", "warning", "blocking"}:
            _fail(f"{context}.severity", f"invalid value '{obj['severity']}'")
    elif object_type == "decision":
        if obj["decision_type"] not in {"research_recommendation", "risk_review", "pm_approval"}:
            _fail(f"{context}.decision_type", f"invalid value '{obj['decision_type']}'")
        if obj["outcome"] not in {"approved", "approved_with_changes", "rejected", "needs_follow_up"}:
            _fail(f"{context}.outcome", f"invalid value '{obj['outcome']}'")
        stance = obj.get("stance")
        if stance is not None and stance not in {"long", "short", "neutral"}:
            _fail(f"{context}.stance", f"invalid value '{stance}'")
        position_action = obj.get("position_action")
        if position_action is not None and position_action not in {"initiate", "add", "hold", "trim", "exit", "defer"}:
            _fail(f"{context}.position_action", f"invalid value '{position_action}'")
        for claim_id in obj.get("linked_claim_ids", []):
            _assert_identifier(f"{context}.linked_claim_ids[]", claim_id)
        for claim_id in obj.get("dissent_claim_ids", []):
            _assert_identifier(f"{context}.dissent_claim_ids[]", claim_id)
        for constraint_id in obj.get("linked_constraint_ids", []):
            _assert_identifier(f"{context}.linked_constraint_ids[]", constraint_id)
        if "requires_risk_recheck" in obj and not isinstance(obj["requires_risk_recheck"], bool):
            _fail(f"{context}.requires_risk_recheck", "must be a boolean")
    elif object_type == "trade_ticket":
        if obj["ticket_type"] not in {"single_leg", "pair_trade"}:
            _fail(f"{context}.ticket_type", f"invalid value '{obj['ticket_type']}'")
        if not isinstance(obj["display_instrument"], str) or not obj["display_instrument"].strip():
            _fail(f"{context}.display_instrument", "must be a non-empty string")
        if not isinstance(obj["legs"], list) or not obj["legs"]:
            _fail(f"{context}.legs", "must be a non-empty list")
        leg_roles: list[str] = []
        gross_bps = 0.0
        net_bps = 0.0
        for index, leg in enumerate(obj["legs"]):
            if not isinstance(leg, dict):
                _fail(f"{context}.legs[{index}]", "must be an object")
            _require_keys(f"{context}.legs[{index}]", leg, {"leg_id", "instrument", "side", "size_bps", "role"})
            _assert_identifier(f"{context}.legs[{index}].leg_id", leg["leg_id"])
            if not isinstance(leg["instrument"], str) or not TICKER_RE.fullmatch(leg["instrument"]):
                _fail(f"{context}.legs[{index}].instrument", f"invalid ticker '{leg['instrument']}'")
            if leg["side"] not in {"BUY", "SELL", "HOLD"}:
                _fail(f"{context}.legs[{index}].side", f"invalid value '{leg['side']}'")
            if not isinstance(leg["size_bps"], (int, float)) or leg["size_bps"] < 0:
                _fail(f"{context}.legs[{index}].size_bps", "must be a number >= 0")
            if leg["role"] not in {"primary", "hedge"}:
                _fail(f"{context}.legs[{index}].role", f"invalid value '{leg['role']}'")
            leg_roles.append(str(leg["role"]))
            size_bps = float(leg["size_bps"])
            if leg["side"] in {"BUY", "SELL"}:
                gross_bps += size_bps
            if leg["side"] == "BUY":
                net_bps += size_bps
            elif leg["side"] == "SELL":
                net_bps -= size_bps

        if leg_roles.count("primary") != 1:
            _fail(f"{context}.legs", "must include exactly one primary leg")
        if obj["ticket_type"] == "single_leg":
            if len(obj["legs"]) != 1:
                _fail(f"{context}.legs", "single_leg tickets must contain exactly one leg")
            if "hedge" in leg_roles:
                _fail(f"{context}.legs", "single_leg tickets cannot include hedge legs")
        elif len(obj["legs"]) != 2:
            _fail(f"{context}.legs", "pair_trade tickets must contain exactly two legs")
        if obj["ticket_type"] == "pair_trade" and "hedge" not in leg_roles:
            _fail(f"{context}.legs", "pair_trade tickets must include a hedge leg")

        exposure = obj["exposure"]
        if not isinstance(exposure, dict):
            _fail(f"{context}.exposure", "must be an object")
        _require_keys(f"{context}.exposure", exposure, {"gross_bps", "net_bps"})
        if not isinstance(exposure["gross_bps"], (int, float)) or exposure["gross_bps"] < 0:
            _fail(f"{context}.exposure.gross_bps", "must be a number >= 0")
        if not isinstance(exposure["net_bps"], (int, float)):
            _fail(f"{context}.exposure.net_bps", "must be numeric")
        if abs(float(exposure["gross_bps"]) - gross_bps) > 1e-6:
            _fail(
                f"{context}.exposure.gross_bps",
                f"must match leg-derived gross_bps ({gross_bps})",
            )
        if abs(float(exposure["net_bps"]) - net_bps) > 1e-6:
            _fail(
                f"{context}.exposure.net_bps",
                f"must match leg-derived net_bps ({net_bps})",
            )
        if obj["time_horizon"] not in {"event_tactical", "swing", "core_position"}:
            _fail(f"{context}.time_horizon", f"invalid value '{obj['time_horizon']}'")
        if not isinstance(obj["entry_conditions"], list):
            _fail(f"{context}.entry_conditions", "must be a list")
        for index, condition in enumerate(obj["entry_conditions"]):
            if not isinstance(condition, str):
                _fail(f"{context}.entry_conditions[{index}]", "must be a string")
        if not isinstance(obj["exit_conditions"], list):
            _fail(f"{context}.exit_conditions", "must be a list")
        for index, condition in enumerate(obj["exit_conditions"]):
            if not isinstance(condition, str):
                _fail(f"{context}.exit_conditions[{index}]", "must be a string")
        if not isinstance(obj["constraint_ids"], list):
            _fail(f"{context}.constraint_ids", "must be a list")
        if obj["approved_by"] not in ROLE_IDS:
            _fail(f"{context}.approved_by", f"unknown role '{obj['approved_by']}'")
        for constraint_id in obj["constraint_ids"]:
            _assert_identifier(f"{context}.constraint_ids[]", constraint_id)


def validate_run_payload(result: dict[str, Any]) -> None:
    _require_keys("result", result, {"run_id", "runtime", "events", "objects", "summary"})
    _assert_identifier("result.run_id", result["run_id"])
    if result["runtime"] not in {"wayflow", "langgraph"}:
        _fail("result.runtime", f"invalid runtime '{result['runtime']}'")
    if not isinstance(result["events"], list):
        _fail("result.events", "must be a list")
    if not isinstance(result["objects"], dict):
        _fail("result.objects", "must be an object map")

    for idx, event in enumerate(result["events"]):
        if not isinstance(event, dict):
            _fail("result.events", f"event at index {idx} is not an object")
        validate_event(event)
        if event["run_id"] != result["run_id"]:
            _fail("result.events", f"run_id mismatch at event index {idx}")

    for object_type, entries in result["objects"].items():
        if not isinstance(entries, dict):
            _fail(f"result.objects.{object_type}", "must be an object map")
        for object_id, obj in entries.items():
            if not isinstance(obj, dict):
                _fail(f"result.objects.{object_type}.{object_id}", "must be an object")
            validate_object(object_type, obj)

    summary = result["summary"]
    if not isinstance(summary, dict):
        _fail("result.summary", "must be an object")
    _require_keys("result.summary", summary, {"run_id", "scenario_id", "runtime", "ticker", "stage_sequence", "object_counts", "ticket_id", "decision_id"})
    if summary["run_id"] != result["run_id"]:
        _fail("result.summary.run_id", "does not match top-level run_id")
    if summary["runtime"] != result["runtime"]:
        _fail("result.summary.runtime", "does not match top-level runtime")
    if not isinstance(summary["ticker"], str) or not TICKER_RE.fullmatch(summary["ticker"]):
        _fail("result.summary.ticker", f"invalid ticker '{summary['ticker']}'")
    if not isinstance(summary["stage_sequence"], list):
        _fail("result.summary.stage_sequence", "must be a list")
    for stage in summary["stage_sequence"]:
        if stage not in STAGE_IDS:
            _fail("result.summary.stage_sequence", f"unknown stage '{stage}'")
    if not isinstance(summary["object_counts"], dict):
        _fail("result.summary.object_counts", "must be an object")
    for object_type, count in summary["object_counts"].items():
        if object_type not in result["objects"]:
            _fail("result.summary.object_counts", f"unknown object type '{object_type}'")
        if not isinstance(count, int):
            _fail("result.summary.object_counts", f"count for '{object_type}' must be int")
        if count != len(result["objects"][object_type]):
            _fail(
                "result.summary.object_counts",
                f"count mismatch for '{object_type}': expected {len(result['objects'][object_type])}, got {count}",
            )
