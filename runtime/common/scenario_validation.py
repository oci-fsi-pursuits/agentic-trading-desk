from __future__ import annotations

from typing import Any

from runtime.common.contract_validation import IDENTIFIER_RE, TICKER_RE
from runtime.common.registry import role_ids

ROLE_IDS = set(role_ids())
RUNTIMES = {"wayflow", "langgraph"}
SCENARIO_TYPES = {
    "pre_event_initiation",
    "breaking_news_reunderwrite",
    "relative_value_pair",
    "thesis_break_review",
}
STANCE_VALUES = {"long", "short", "neutral"}
POSITION_ACTIONS = {"initiate", "add", "hold", "trim", "exit", "defer"}
TRADE_SIDES = {"BUY", "SELL", "PAIR", "HOLD"}
TIME_HORIZONS = {"event_tactical", "swing", "core_position"}
CONSTRAINT_TYPES = {"position_limit", "liquidity", "correlation", "event_risk", "mandate"}
CONSTRAINT_SEVERITIES = {"info", "warning", "blocking"}
DECISION_OUTCOMES = {"approved", "approved_with_changes", "rejected"}
TOP_LEVEL_KEYS = {
    "schema_version",
    "scenario_id",
    "name",
    "summary",
    "instrument",
    "instrument_universe",
    "instrument_label",
    "pair_peer",
    "primary_runtime",
    "parity_runtime",
    "runtime_goal",
    "thesis_prompt",
    "decision_question",
    "scenario_type",
    "starting_position_state",
    "allowed_end_states",
    "seat_plan",
    "required_seat_ids",
    "optional_seat_ids",
    "branch_conditions",
    "pm_decision_policy",
    "constraints",
    "demo_mode",
}


def _fail(context: str, message: str) -> None:
    raise ValueError(f"{context}: {message}")


def _require_object(context: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(context, "must be an object")
    return value


def _require_keys(context: str, payload: dict[str, Any], required: set[str]) -> None:
    missing = sorted(required.difference(payload.keys()))
    if missing:
        _fail(context, f"missing keys {missing}")


def _assert_allowed_keys(context: str, payload: dict[str, Any], allowed: set[str]) -> None:
    extra = sorted(set(payload.keys()).difference(allowed))
    if extra:
        _fail(context, f"unexpected keys {extra}")


def _assert_non_empty_string(context: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(context, "must be a non-empty string")
    return value.strip()


def _assert_identifier(context: str, value: Any) -> str:
    if not isinstance(value, str) or not IDENTIFIER_RE.fullmatch(value):
        _fail(context, f"invalid identifier '{value}'")
    return value


def _assert_ticker(context: str, value: Any) -> str:
    if not isinstance(value, str) or not TICKER_RE.fullmatch(value):
        _fail(context, f"invalid ticker '{value}'")
    return value


def _assert_bool(context: str, value: Any) -> bool:
    if not isinstance(value, bool):
        _fail(context, "must be a boolean")
    return value


def _assert_int(context: str, value: Any, minimum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail(context, "must be an integer")
    if minimum is not None and value < minimum:
        _fail(context, f"must be >= {minimum}")
    return value


def _assert_number(context: str, value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        _fail(context, "must be numeric")
    return float(value)


def _assert_enum(context: str, value: Any, allowed: set[str]) -> str:
    if value not in allowed:
        _fail(context, f"invalid value '{value}'")
    return str(value)


def _assert_string_list(
    context: str,
    value: Any,
    *,
    non_empty: bool = True,
    allowed: set[str] | None = None,
) -> list[str]:
    if not isinstance(value, list):
        _fail(context, "must be a list")
    if non_empty and not value:
        _fail(context, "must not be empty")
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        normalized = _assert_non_empty_string(f"{context}[]", item)
        if normalized in seen:
            _fail(context, f"duplicate entry '{normalized}'")
        if allowed is not None and normalized not in allowed:
            _fail(context, f"invalid value '{normalized}'")
        seen.add(normalized)
        items.append(normalized)
    return items


def _assert_role_list(context: str, value: Any, *, non_empty: bool = True) -> list[str]:
    return _assert_string_list(context, value, non_empty=non_empty, allowed=ROLE_IDS)


def _validate_runtime_goal(runtime_goal: Any) -> None:
    payload = _require_object("scenario.runtime_goal", runtime_goal)
    _assert_allowed_keys("scenario.runtime_goal", payload, {"primary_runtime", "parity_runtime"})
    if "primary_runtime" in payload:
        _assert_enum("scenario.runtime_goal.primary_runtime", payload["primary_runtime"], RUNTIMES)
    if "parity_runtime" in payload:
        _assert_enum("scenario.runtime_goal.parity_runtime", payload["parity_runtime"], RUNTIMES)


def _validate_position_state(
    context: str,
    payload: Any,
    *,
    allowed_stances: set[str],
    allowed_actions: set[str],
) -> dict[str, Any]:
    state = _require_object(context, payload)
    _assert_allowed_keys(context, state, {"stance", "position_action", "size_bps", "existing_position"})
    _require_keys(context, state, {"stance", "position_action", "size_bps", "existing_position"})
    stance = _assert_enum(f"{context}.stance", state["stance"], STANCE_VALUES)
    action = _assert_enum(f"{context}.position_action", state["position_action"], POSITION_ACTIONS)
    size_bps = _assert_int(f"{context}.size_bps", state["size_bps"], minimum=0)
    existing_position = _assert_bool(f"{context}.existing_position", state["existing_position"])

    if stance not in allowed_stances:
        _fail(f"{context}.stance", f"must be one of the allowed end-state stances {sorted(allowed_stances)}")
    if action not in allowed_actions:
        _fail(f"{context}.position_action", f"must be one of the allowed end-state actions {sorted(allowed_actions)}")
    if existing_position and size_bps == 0:
        _fail(f"{context}.size_bps", "must be > 0 when existing_position is true")
    if not existing_position and size_bps != 0:
        _fail(f"{context}.size_bps", "must be 0 when existing_position is false")
    if action == "defer" and size_bps != 0:
        _fail(f"{context}.size_bps", "must be 0 when position_action is defer")
    if action in {"hold", "trim", "exit"} and not existing_position:
        _fail(f"{context}.existing_position", f"must be true when position_action is {action}")

    return {
        "stance": stance,
        "position_action": action,
        "size_bps": size_bps,
        "existing_position": existing_position,
    }


def _validate_allowed_end_states(context: str, payload: Any) -> tuple[list[str], list[str]]:
    state = _require_object(context, payload)
    _assert_allowed_keys(context, state, {"stances", "actions"})
    _require_keys(context, state, {"stances", "actions"})
    stances = _assert_string_list(f"{context}.stances", state["stances"], allowed=STANCE_VALUES)
    actions = _assert_string_list(f"{context}.actions", state["actions"], allowed=POSITION_ACTIONS)
    return stances, actions


def _validate_seat_plan(context: str, payload: Any) -> tuple[list[str], list[str], list[str], list[str]]:
    seat_plan = _require_object(context, payload)
    _assert_allowed_keys(context, seat_plan, {"required", "optional", "scenario_overrides"})
    _require_keys(context, seat_plan, {"required", "optional", "scenario_overrides"})
    required = _assert_role_list(f"{context}.required", seat_plan["required"])
    optional = _assert_role_list(f"{context}.optional", seat_plan["optional"], non_empty=False)
    overlap = sorted(set(required).intersection(optional))
    if overlap:
        _fail(context, f"required and optional seats overlap: {overlap}")

    overrides = _require_object(f"{context}.scenario_overrides", seat_plan["scenario_overrides"])
    _assert_allowed_keys(f"{context}.scenario_overrides", overrides, {"prefer_enabled", "suppress"})
    prefer_enabled = _assert_role_list(f"{context}.scenario_overrides.prefer_enabled", overrides.get("prefer_enabled", []), non_empty=False)
    suppress = _assert_role_list(f"{context}.scenario_overrides.suppress", overrides.get("suppress", []), non_empty=False)
    suppressible_roles = ROLE_IDS.difference(required)
    if not set(suppress).issubset(suppressible_roles):
        _fail(f"{context}.scenario_overrides.suppress", "must not include required seats")
    conflict = sorted(set(prefer_enabled).intersection(suppress))
    if conflict:
        _fail(f"{context}.scenario_overrides", f"prefer_enabled and suppress overlap: {conflict}")

    return required, optional, prefer_enabled, suppress


def _validate_branch_conditions(
    context: str,
    payload: Any,
    *,
    optional_roles: set[str],
    expected_prefer: list[str],
    expected_suppress: list[str],
    required_roles: set[str],
) -> None:
    branch = _require_object(context, payload)
    _assert_allowed_keys(
        context,
        branch,
        {
            "force_breaking_news",
            "requires_news_confirmation",
            "pair_trade_mode",
            "thesis_break_mode",
            "time_sensitive_debate",
            "prefer_enabled",
            "suppress_seats",
        },
    )
    _require_keys(
        context,
        branch,
        {
            "force_breaking_news",
            "requires_news_confirmation",
            "pair_trade_mode",
            "thesis_break_mode",
            "prefer_enabled",
            "suppress_seats",
        },
    )
    _assert_bool(f"{context}.force_breaking_news", branch["force_breaking_news"])
    _assert_bool(f"{context}.requires_news_confirmation", branch["requires_news_confirmation"])
    _assert_bool(f"{context}.pair_trade_mode", branch["pair_trade_mode"])
    _assert_bool(f"{context}.thesis_break_mode", branch["thesis_break_mode"])
    if "time_sensitive_debate" in branch:
        _assert_bool(f"{context}.time_sensitive_debate", branch["time_sensitive_debate"])
    prefer_enabled = _assert_role_list(f"{context}.prefer_enabled", branch["prefer_enabled"], non_empty=False)
    suppress_seats = _assert_role_list(f"{context}.suppress_seats", branch["suppress_seats"], non_empty=False)
    suppressible_roles = ROLE_IDS.difference(required_roles)
    if not set(suppress_seats).issubset(suppressible_roles):
        _fail(f"{context}.suppress_seats", "must not include required seats")
    if prefer_enabled != expected_prefer:
        _fail(f"{context}.prefer_enabled", "must match seat_plan.scenario_overrides.prefer_enabled")
    if suppress_seats != expected_suppress:
        _fail(f"{context}.suppress_seats", "must match seat_plan.scenario_overrides.suppress")


def _validate_size_ranges(context: str, payload: Any, expected_keys: set[str]) -> None:
    size_ranges = _require_object(context, payload)
    actual_keys = set(size_ranges.keys())
    if actual_keys != expected_keys:
        _fail(context, f"expected keys {sorted(expected_keys)}, got {sorted(actual_keys)}")
    for key in sorted(expected_keys):
        bounds = size_ranges[key]
        if not isinstance(bounds, list) or len(bounds) != 2:
            _fail(f"{context}.{key}", "must be a two-item list")
        low = _assert_int(f"{context}.{key}[0]", bounds[0], minimum=0)
        high = _assert_int(f"{context}.{key}[1]", bounds[1], minimum=0)
        if high < low:
            _fail(f"{context}.{key}", "upper bound must be >= lower bound")


def _validate_pm_preview(context: str, payload: Any) -> None:
    preview = _require_object(context, payload)
    _assert_allowed_keys(context, preview, {"outcome", "position_size_bps", "approval_notes"})
    _require_keys(context, preview, {"outcome", "position_size_bps", "approval_notes"})
    _assert_enum(f"{context}.outcome", preview["outcome"], DECISION_OUTCOMES)
    position_size = preview["position_size_bps"]
    if isinstance(position_size, bool) or not isinstance(position_size, (int, str)):
        _fail(f"{context}.position_size_bps", "must be an integer or range string")
    if isinstance(position_size, str):
        _assert_non_empty_string(f"{context}.position_size_bps", position_size)
    else:
        _assert_int(f"{context}.position_size_bps", position_size, minimum=0)
    _assert_non_empty_string(f"{context}.approval_notes", preview["approval_notes"])


def _validate_pm_policy(
    context: str,
    payload: Any,
    *,
    scenario_type: str,
    allowed_stances: set[str],
    allowed_actions: set[str],
) -> None:
    policy = _require_object(context, payload)
    base_allowed = {
        "signal_bias",
        "warning_size_multiplier",
        "size_ranges_bps",
        "preview",
    }
    if scenario_type == "thesis_break_review":
        allowed_keys = base_allowed | {"exit_signal_threshold", "trim_signal_threshold"}
    else:
        allowed_keys = base_allowed | {
            "long_signal_threshold",
            "short_signal_threshold",
            "neutral_vote_band",
            "confirmation_requires_strong_edge",
            "allow_short",
            "action_map",
        }
    _assert_allowed_keys(context, policy, allowed_keys)
    _require_keys(context, policy, {"warning_size_multiplier", "size_ranges_bps", "preview"})
    _assert_number(f"{context}.warning_size_multiplier", policy["warning_size_multiplier"])
    _validate_pm_preview(f"{context}.preview", policy["preview"])
    if "signal_bias" in policy:
        _assert_number(f"{context}.signal_bias", policy["signal_bias"])

    if scenario_type == "thesis_break_review":
        _require_keys(context, policy, {"exit_signal_threshold", "trim_signal_threshold"})
        _assert_number(f"{context}.exit_signal_threshold", policy["exit_signal_threshold"])
        _assert_number(f"{context}.trim_signal_threshold", policy["trim_signal_threshold"])
        _validate_size_ranges(f"{context}.size_ranges_bps", policy["size_ranges_bps"], {"hold", "trim", "exit"})
        return

    _require_keys(context, policy, {"long_signal_threshold", "neutral_vote_band", "action_map"})
    _assert_number(f"{context}.long_signal_threshold", policy["long_signal_threshold"])
    _assert_int(f"{context}.neutral_vote_band", policy["neutral_vote_band"], minimum=0)

    requires_short_threshold = "short" in allowed_stances or bool(policy.get("allow_short"))
    if requires_short_threshold and "short_signal_threshold" not in policy:
        _fail(f"{context}.short_signal_threshold", "is required when short end-states are allowed")
    if "short_signal_threshold" in policy:
        _assert_number(f"{context}.short_signal_threshold", policy["short_signal_threshold"])
    if "confirmation_requires_strong_edge" in policy:
        _assert_bool(f"{context}.confirmation_requires_strong_edge", policy["confirmation_requires_strong_edge"])
    if "allow_short" in policy:
        _assert_bool(f"{context}.allow_short", policy["allow_short"])
        if not policy["allow_short"] and "short" in allowed_stances:
            _fail(f"{context}.allow_short", "cannot be false when short is an allowed end-state")

    _validate_size_ranges(f"{context}.size_ranges_bps", policy["size_ranges_bps"], allowed_stances)
    action_map = _require_object(f"{context}.action_map", policy["action_map"])
    if set(action_map.keys()) != allowed_stances:
        _fail(f"{context}.action_map", f"expected keys {sorted(allowed_stances)}, got {sorted(action_map.keys())}")
    for stance, action in action_map.items():
        _assert_enum(f"{context}.action_map.{stance}", action, allowed_actions)


def _validate_constraints(context: str, payload: Any) -> None:
    if not isinstance(payload, list) or not payload:
        _fail(context, "must be a non-empty list")
    seen_ids: set[str] = set()
    for index, item in enumerate(payload):
        constraint = _require_object(f"{context}[{index}]", item)
        _assert_allowed_keys(f"{context}[{index}]", constraint, {"constraint_id", "constraint_type", "label", "value", "severity"})
        _require_keys(f"{context}[{index}]", constraint, {"constraint_id", "constraint_type", "label", "value", "severity"})
        constraint_id = _assert_identifier(f"{context}[{index}].constraint_id", constraint["constraint_id"])
        if constraint_id in seen_ids:
            _fail(context, f"duplicate constraint_id '{constraint_id}'")
        seen_ids.add(constraint_id)
        _assert_enum(f"{context}[{index}].constraint_type", constraint["constraint_type"], CONSTRAINT_TYPES)
        _assert_non_empty_string(f"{context}[{index}].label", constraint["label"])
        _assert_enum(f"{context}[{index}].severity", constraint["severity"], CONSTRAINT_SEVERITIES)


def _validate_demo_mode(context: str, payload: Any, *, instrument_label: str, scenario_type: str) -> None:
    demo = _require_object(context, payload)
    _assert_allowed_keys(
        context,
        demo,
        {
            "trade_side",
            "time_horizon",
            "instrument_label",
            "hedge_leg_note",
            "position_context",
            "quant_metric_budget",
            "allow_partial_metrics",
            "force_breaking_news",
            "scripted_pm_default",
        },
    )
    _require_keys(context, demo, {"trade_side", "time_horizon", "position_context", "quant_metric_budget", "allow_partial_metrics"})
    _assert_enum(f"{context}.trade_side", demo["trade_side"], TRADE_SIDES)
    _assert_enum(f"{context}.time_horizon", demo["time_horizon"], TIME_HORIZONS)
    _assert_non_empty_string(f"{context}.position_context", demo["position_context"])
    _assert_int(f"{context}.quant_metric_budget", demo["quant_metric_budget"], minimum=1)
    _assert_bool(f"{context}.allow_partial_metrics", demo["allow_partial_metrics"])
    if "instrument_label" in demo:
        if _assert_non_empty_string(f"{context}.instrument_label", demo["instrument_label"]) != instrument_label:
            _fail(f"{context}.instrument_label", "must match scenario.instrument_label")
    if "force_breaking_news" in demo:
        _assert_bool(f"{context}.force_breaking_news", demo["force_breaking_news"])
    if "scripted_pm_default" in demo:
        _validate_pm_preview(f"{context}.scripted_pm_default", demo["scripted_pm_default"])
    if scenario_type == "relative_value_pair" and not demo.get("hedge_leg_note"):
        _fail(f"{context}.hedge_leg_note", "is required for relative_value_pair scenarios")
    if "hedge_leg_note" in demo:
        _assert_non_empty_string(f"{context}.hedge_leg_note", demo["hedge_leg_note"])


def validate_scenario(scenario: dict[str, Any]) -> None:
    payload = _require_object("scenario", scenario)
    _assert_allowed_keys("scenario", payload, TOP_LEVEL_KEYS)
    _require_keys(
        "scenario",
        payload,
        {
            "schema_version",
            "scenario_id",
            "name",
            "summary",
            "instrument",
            "instrument_universe",
            "instrument_label",
            "primary_runtime",
            "parity_runtime",
            "thesis_prompt",
            "decision_question",
            "scenario_type",
            "starting_position_state",
            "allowed_end_states",
            "seat_plan",
            "branch_conditions",
            "pm_decision_policy",
            "constraints",
            "demo_mode",
        },
    )
    if payload["schema_version"] != "v2":
        _fail("scenario.schema_version", f"unsupported schema_version '{payload['schema_version']}'")

    scenario_id = _assert_identifier("scenario.scenario_id", payload["scenario_id"])
    _assert_non_empty_string("scenario.name", payload["name"])
    _assert_non_empty_string("scenario.summary", payload["summary"])
    instrument = _assert_ticker("scenario.instrument", payload["instrument"])
    instrument_universe = _assert_string_list("scenario.instrument_universe", payload["instrument_universe"])
    for index, ticker in enumerate(instrument_universe):
        _assert_ticker(f"scenario.instrument_universe[{index}]", ticker)
    if instrument not in instrument_universe:
        _fail("scenario.instrument_universe", f"must include primary instrument '{instrument}'")
    instrument_label = _assert_non_empty_string("scenario.instrument_label", payload["instrument_label"])
    _assert_enum("scenario.primary_runtime", payload["primary_runtime"], RUNTIMES)
    _assert_enum("scenario.parity_runtime", payload["parity_runtime"], RUNTIMES)
    if payload["primary_runtime"] == payload["parity_runtime"]:
        _fail("scenario.parity_runtime", "must differ from primary_runtime")
    if "runtime_goal" in payload:
        _validate_runtime_goal(payload["runtime_goal"])
    _assert_non_empty_string("scenario.thesis_prompt", payload["thesis_prompt"])
    _assert_non_empty_string("scenario.decision_question", payload["decision_question"])
    scenario_type = _assert_enum("scenario.scenario_type", payload["scenario_type"], SCENARIO_TYPES)

    if scenario_type == "relative_value_pair":
        pair_peer = _assert_ticker("scenario.pair_peer", payload.get("pair_peer"))
        if pair_peer == instrument:
            _fail("scenario.pair_peer", "must differ from scenario.instrument")
        if pair_peer not in instrument_universe:
            _fail("scenario.instrument_universe", "must include pair_peer for relative_value_pair")
        if len(instrument_universe) < 2:
            _fail("scenario.instrument_universe", "must include both legs for relative_value_pair")
    elif "pair_peer" in payload:
        _fail("scenario.pair_peer", f"is only valid for relative_value_pair, not {scenario_type}")

    stances, actions = _validate_allowed_end_states("scenario.allowed_end_states", payload["allowed_end_states"])
    allowed_stances = set(stances)
    allowed_actions = set(actions)

    if scenario_type == "thesis_break_review":
        if allowed_stances != {"long", "neutral"}:
            _fail("scenario.allowed_end_states.stances", "thesis_break_review must allow exactly long and neutral")
        if allowed_actions != {"hold", "trim", "exit"}:
            _fail("scenario.allowed_end_states.actions", "thesis_break_review must allow exactly hold, trim, and exit")
    elif scenario_type == "relative_value_pair":
        if allowed_actions != {"initiate", "defer"}:
            _fail("scenario.allowed_end_states.actions", "relative_value_pair must allow exactly initiate and defer")
        if not allowed_stances.issubset({"long", "short", "neutral"}) or "neutral" not in allowed_stances:
            _fail("scenario.allowed_end_states.stances", "relative_value_pair must include neutral and only long/short/neutral")
    else:
        if allowed_stances != {"long", "short", "neutral"}:
            _fail("scenario.allowed_end_states.stances", f"{scenario_type} must allow exactly long, short, and neutral")
        if allowed_actions != {"initiate", "defer"}:
            _fail("scenario.allowed_end_states.actions", f"{scenario_type} must allow exactly initiate and defer")

    state = _validate_position_state(
        "scenario.starting_position_state",
        payload["starting_position_state"],
        allowed_stances=allowed_stances,
        allowed_actions=allowed_actions,
    )
    if scenario_type == "thesis_break_review" and not state["existing_position"]:
        _fail("scenario.starting_position_state.existing_position", "must be true for thesis_break_review")
    if scenario_type != "thesis_break_review" and state["existing_position"]:
        _fail("scenario.starting_position_state.existing_position", f"must be false for {scenario_type}")

    required, optional, prefer_enabled, suppress = _validate_seat_plan("scenario.seat_plan", payload["seat_plan"])
    if "required_seat_ids" in payload:
        if _assert_role_list("scenario.required_seat_ids", payload["required_seat_ids"]) != required:
            _fail("scenario.required_seat_ids", "must match seat_plan.required")
    if "optional_seat_ids" in payload:
        if _assert_role_list("scenario.optional_seat_ids", payload["optional_seat_ids"], non_empty=False) != optional:
            _fail("scenario.optional_seat_ids", "must match seat_plan.optional")

    _validate_branch_conditions(
        "scenario.branch_conditions",
        payload["branch_conditions"],
        optional_roles=set(optional),
        expected_prefer=prefer_enabled,
        expected_suppress=suppress,
        required_roles=set(required),
    )
    branch = payload["branch_conditions"]
    if scenario_type == "pre_event_initiation":
        if branch["force_breaking_news"] or branch["requires_news_confirmation"] or branch["pair_trade_mode"] or branch["thesis_break_mode"]:
            _fail("scenario.branch_conditions", "pre_event_initiation must not enable breaking news, pair, or thesis-break modes")
    elif scenario_type == "breaking_news_reunderwrite":
        if not branch["force_breaking_news"] or not branch["requires_news_confirmation"]:
            _fail("scenario.branch_conditions", "breaking_news_reunderwrite must require breaking-news confirmation")
        if branch["pair_trade_mode"] or branch["thesis_break_mode"]:
            _fail("scenario.branch_conditions", "breaking_news_reunderwrite must not enable pair or thesis-break modes")
    elif scenario_type == "relative_value_pair":
        if not branch["pair_trade_mode"] or branch["thesis_break_mode"]:
            _fail("scenario.branch_conditions", "relative_value_pair must enable pair_trade_mode only")
    elif scenario_type == "thesis_break_review":
        if not branch["thesis_break_mode"] or branch["pair_trade_mode"]:
            _fail("scenario.branch_conditions", "thesis_break_review must enable thesis_break_mode only")

    _validate_pm_policy(
        "scenario.pm_decision_policy",
        payload["pm_decision_policy"],
        scenario_type=scenario_type,
        allowed_stances=allowed_stances,
        allowed_actions=allowed_actions,
    )
    _validate_constraints("scenario.constraints", payload["constraints"])
    _validate_demo_mode(
        "scenario.demo_mode",
        payload["demo_mode"],
        instrument_label=instrument_label,
        scenario_type=scenario_type,
    )

    if scenario_type == "breaking_news_reunderwrite" and payload["demo_mode"].get("force_breaking_news") is not True:
        _fail("scenario.demo_mode.force_breaking_news", "must be true for breaking_news_reunderwrite")
    if scenario_type != "breaking_news_reunderwrite" and payload["demo_mode"].get("force_breaking_news") is True:
        _fail("scenario.demo_mode.force_breaking_news", f"must not be true for {scenario_type}")

    if scenario_id != payload["scenario_id"]:
        _fail("scenario.scenario_id", "validation drift detected")


def validate_scenario_catalog(scenarios: list[dict[str, Any]]) -> None:
    if not isinstance(scenarios, list) or not scenarios:
        _fail("scenario_catalog", "must be a non-empty list")
    seen_ids: set[str] = set()
    seen_types: set[str] = set()
    for index, scenario in enumerate(scenarios):
        validate_scenario(scenario)
        scenario_id = scenario["scenario_id"]
        scenario_type = scenario["scenario_type"]
        if scenario_id in seen_ids:
            _fail(f"scenario_catalog[{index}].scenario_id", f"duplicate scenario_id '{scenario_id}'")
        if scenario_type in seen_types:
            _fail(f"scenario_catalog[{index}].scenario_type", f"duplicate scenario_type '{scenario_type}'")
        seen_ids.add(scenario_id)
        seen_types.add(scenario_type)
