from __future__ import annotations

import json
import os
import re
import threading
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import urllib.error

from runtime.common.agent_prompts import AGENT_SYSTEM_PROMPTS
from runtime.common.analyst_tools import execute_local_tool, tool_specs_for_role
from runtime.common.data_providers import (
    CHART_WINDOWS,
    fetch_fundamentals_domain,
    fetch_geopolitical_domain,
    fetch_macro_domain,
    fetch_market_domain,
    fetch_news_domain,
    fetch_social_domain,
    fetch_chart_snapshot,
    provider_chain,
)
from runtime.common.env_validation import collect_environment_status, load_env_file
from runtime.common.live_context import build_live_context
from runtime.common.oci_genai import OciGenAIClient, build_agent_prompt_preview
from runtime.common.scenario_loader import load_demo_dataset, load_scenario_catalog
from runtime.common.service import get_adapter
from runtime.common.store import RunStore
from runtime.common.utils import ensure_runs_root, make_id, now_iso

ROOT = Path(__file__).resolve().parent
TICKER_PATTERN = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")
RUN_JOBS: dict[str, dict] = {}
RUN_JOBS_LOCK = threading.Lock()
RUN_CONTROLS: dict[str, threading.Event] = {}
RUN_CANCELLATIONS: dict[str, threading.Event] = {}
RUN_THREADS: dict[str, threading.Thread] = {}
PHASE_BOUNDARY_STAGES = {"quantify", "synthesize", "pm_review", "trade_finalize"}


class RunCancelled(Exception):
    """Raised when a run is cancelled via reset."""


def normalize_single_ticker(raw: str) -> str:
    match = TICKER_PATTERN.search((raw or "").upper())
    return match.group(0) if match else ""


def normalize_ticker_request(raw: str, max_symbols: int = 4) -> str:
    matches = TICKER_PATTERN.findall((raw or "").upper())
    if not matches:
        return ""
    limit = max(1, min(int(max_symbols or 1), 4))
    unique: list[str] = []
    for item in matches:
        if item not in unique:
            unique.append(item)
    return ",".join(unique[:limit])


def parse_bool_flag(raw: str) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def normalize_breaking_news_mode(raw: str) -> str:
    key = str(raw or "").strip().lower()
    if key in {"manual", "manual_now", "manual-now", "immediate"}:
        return "manual"
    if key in {"auto_after_gather", "auto-after-gather", "auto", "timer", "timed", "delayed"}:
        return "auto_after_gather"
    return "off"


def parse_debate_depth(raw: str, default: int = 1) -> int:
    try:
        value = int(str(raw or "").strip())
    except (TypeError, ValueError):
        value = default
    return max(1, min(value, 8))


def normalize_chart_range(raw: str) -> str:
    key = str(raw or "").strip().lower()
    return key if key in CHART_WINDOWS else "30d"


def runtime_adapter_status() -> list[dict[str, str]]:
    checks = []
    for runtime_name in ("wayflow", "langgraph"):
        try:
            adapter = get_adapter(runtime_name)
            adapter.stage_order()
            checks.append({"runtime": runtime_name, "status": "ok", "detail": "adapter initialized"})
        except Exception as exc:  # noqa: BLE001
            checks.append({"runtime": runtime_name, "status": "degraded", "detail": str(exc)})
    return checks


def filesystem_status() -> dict[str, str]:
    try:
        runs_root = ensure_runs_root()
        probe = runs_root / ".healthcheck"
        probe.write_text("ok")
        probe.unlink(missing_ok=True)
        return {"status": "ok", "detail": f"writable runs root at {runs_root}"}
    except Exception as exc:  # noqa: BLE001
        return {"status": "degraded", "detail": str(exc)}


def genai_status() -> dict[str, str]:
    client = OciGenAIClient()
    capability = client.capability_profile()
    if not client.enabled:
        return {"status": "ok", "detail": "OCI GenAI disabled; deterministic fallback mode active"}
    if client.ready() and client.api_key:
        return {"status": "ok", "detail": "OCI GenAI configured (API key mode)"}
    if client.ready():
        if capability["auth_mode"] == "instance_principal_sdk":
            return {"status": "ok", "detail": "OCI GenAI configured (instance-principal signing via OCI SDK)"}
        if capability["auth_mode"] == "user_principal_sdk":
            return {"status": "ok", "detail": "OCI GenAI configured (user-principal signing via OCI SDK/config profile)"}
        if capability["auth_mode"].endswith("_unsigned_no_sdk"):
            return {
                "status": "ok",
                "detail": "OCI GenAI configured (no-key mode, OCI SDK unavailable). Live calls may fallback unless endpoint accepts unsigned requests.",
            }
        return {"status": "ok", "detail": f"OCI GenAI configured ({capability['auth_mode']})"}
    return {"status": "degraded", "detail": "OCI endpoint/model not configured for agent LLM generation"}


def _key_status(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        return "missing"
    return f"set(len={len(value)})"


def health_payload() -> dict:
    env_status = collect_environment_status()
    runtime_status = runtime_adapter_status()
    fs_status = filesystem_status()
    llm_status = genai_status()
    scenario_status = {
        "status": "ok",
        "detail": f"loaded {len(load_scenario_catalog())} scenario(s)",
    }
    checks = [
        {"name": "environment", "status": env_status["status"], "detail": env_status["message"]},
        {"name": "filesystem", "status": fs_status["status"], "detail": fs_status["detail"]},
        {"name": "scenarios", "status": scenario_status["status"], "detail": scenario_status["detail"]},
        {"name": "llm.oci_genai", "status": llm_status["status"], "detail": llm_status["detail"]},
    ]
    for item in runtime_status:
        checks.append({"name": f"runtime.{item['runtime']}", "status": item["status"], "detail": item["detail"]})

    overall = "ok"
    if any(item["status"] != "ok" for item in checks):
        overall = "degraded"

    return {
        "status": overall,
        "service": "agentic-trading-desk",
        "timestamp": now_iso(),
        "checks": checks,
        "environment": env_status,
    }


def _compact_data_status(meta: dict[str, object], *, default_provider: str = "", default_freshness: str = "") -> dict[str, object]:
    status: dict[str, object] = {}
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


def _decode_tool_output(raw: str) -> object:
    text = str(raw or "")
    try:
        return json.loads(text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return text


def _safe_fetch(fn):
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"debug_fetch_failed:{exc}"}


def _build_debug_provider_payload(ticker: str, scenario_id: str, max_words: int, include_statements: bool) -> dict[str, object]:
    dataset = load_demo_dataset(scenario_id=scenario_id, ticker=ticker)
    active_debug_seats = [
        "market_analyst",
        "news_analyst",
        "fundamentals_analyst",
        "social_analyst",
        "macro_economist",
        "geopolitical_analyst",
    ]
    live_context = build_live_context(
        ticker,
        dataset,
        active_seat_ids=active_debug_seats,
        run_id=f"debug_{ticker.lower()}",
    )
    domain_metadata = dict(live_context.get("domain_metadata", {}) or {})
    coverage = dict(live_context.get("coverage", {}) or {})
    market_context = dict(live_context.get("market_context", {}) or {})
    fundamentals = dict(live_context.get("fundamentals", {}) or {})
    macro_context = dict(live_context.get("macro_context", {}) or {})
    geopolitical_context = dict(live_context.get("geopolitical_context", {}) or {})
    social_context = dict(live_context.get("social_context", {}) or {})
    news_items = list(live_context.get("news_items", []) or [])

    trade_date = datetime.now(UTC).date().isoformat()
    news_meta = dict(domain_metadata.get("news", {}) or {})
    market_freshness = "live" if coverage.get("market") == "live" else "snapshot"
    news_freshness = "live" if coverage.get("news") == "live" else "snapshot"
    fundamentals_freshness = "live" if coverage.get("fundamentals") == "live" else "snapshot"
    social_freshness = "live" if coverage.get("social") == "live" else "snapshot"
    macro_freshness = "live" if coverage.get("macro") == "live" else "snapshot"
    geopolitical_freshness = "live" if coverage.get("geopolitical") == "live" else "snapshot"
    social_end_date = datetime.now(UTC).date()
    social_start_date = social_end_date - timedelta(days=2)

    prompt_context_by_role: dict[str, dict[str, object]] = {
        "market_analyst": {
            "ticker": ticker,
            "trade_date": trade_date,
            "analysis_window_days": 120,
            "market_data_status": _compact_data_status(
                dict(domain_metadata.get("market", {}) or {}),
                default_freshness=market_freshness,
            ),
        },
        "news_analyst": {
            "ticker": ticker,
            "trade_date": trade_date,
            "news_mode": "live_news",
            "analysis_window_days": 7,
            "news_data_status": _compact_data_status(
                news_meta,
                default_provider="yfinance",
                default_freshness=news_freshness,
            ),
        },
        "fundamentals_analyst": {
            "ticker": ticker,
            "trade_date": trade_date,
            "statement_period": "quarterly",
            "fundamentals_data_status": _compact_data_status(
                dict(domain_metadata.get("fundamentals", {}) or {}),
                default_freshness=fundamentals_freshness,
            ),
        },
        "social_analyst": {
            "ticker": ticker,
            "social_context": social_context,
            "social_data_status": _compact_data_status(
                dict(domain_metadata.get("social", {}) or {}),
                default_freshness=social_freshness,
            ),
            "stocktwits_snapshot": {
                "ok": bool(dict(domain_metadata.get("social", {}) or {}).get("ok", False)),
                "provider": str(dict(domain_metadata.get("social", {}) or {}).get("provider", "stocktwits") or "stocktwits"),
                "source_count": int(dict(domain_metadata.get("social", {}) or {}).get("source_count", 0) or 0),
                "freshness": str(dict(domain_metadata.get("social", {}) or {}).get("freshness", "snapshot") or "snapshot"),
                "as_of": str(dict(domain_metadata.get("social", {}) or {}).get("as_of", now_iso()) or now_iso()),
                "social_context": social_context,
            },
            "x_search_window": {
                "from_date": social_start_date.isoformat(),
                "to_date": social_end_date.isoformat(),
            },
        },
        "macro_economist": {
            "ticker": ticker,
            "macro_context": macro_context,
            "macro_data_status": _compact_data_status(
                dict(domain_metadata.get("macro", {}) or {}),
                default_freshness=macro_freshness,
            ),
        },
        "geopolitical_analyst": {
            "ticker": ticker,
            "geopolitical_context": geopolitical_context,
            "geopolitical_data_status": _compact_data_status(
                dict(domain_metadata.get("geopolitical", {}) or {}),
                default_freshness=geopolitical_freshness,
            ),
        },
    }

    role_words = {
        "social_analyst": min(max_words, 160),
    }
    prompt_preview: dict[str, object] = {}
    for role_id, context in prompt_context_by_role.items():
        prompt_preview[role_id] = {
            "max_words": int(role_words.get(role_id, max_words)),
            "tools": tool_specs_for_role(role_id),
            "context": context,
            "prompt": build_agent_prompt_preview(role_id, context, max_words=int(role_words.get(role_id, max_words))),
        }

    today = datetime.now(UTC).date()
    start = (today - timedelta(days=7)).isoformat()
    end = today.isoformat()
    tool_runs: dict[str, object] = {
        "market_analyst": {
            "get_stock_data": _decode_tool_output(execute_local_tool("get_stock_data", {"ticker": ticker, "lookback_days": 120})),
            "get_indicators": _decode_tool_output(
                execute_local_tool(
                    "get_indicators",
                    {
                        "ticker": ticker,
                        "lookback_days": 120,
                        "indicators": ["sma_20", "ema_20", "rsi_14", "macd", "bollinger_bands", "atr_14", "support_resistance"],
                    },
                )
            ),
        },
        "news_analyst": {
            "get_news": _decode_tool_output(
                execute_local_tool(
                    "get_news",
                    {
                        "ticker": ticker,
                        "start_date": start,
                        "end_date": end,
                        "limit": 8,
                    },
                )
            ),
            "get_global_news": _decode_tool_output(
                execute_local_tool(
                    "get_global_news",
                    {
                        "curr_date": end,
                        "look_back_days": 7,
                        "limit": 10,
                    },
                )
            ),
        },
        "macro_economist": {
            "get_global_news": _decode_tool_output(
                execute_local_tool(
                    "get_global_news",
                    {
                        "curr_date": end,
                        "look_back_days": 7,
                        "limit": 10,
                    },
                )
            ),
            "get_news": _decode_tool_output(
                execute_local_tool(
                    "get_news",
                    {
                        "ticker": ticker,
                        "start_date": start,
                        "end_date": end,
                        "limit": 8,
                    },
                )
            ),
        },
        "fundamentals_analyst": {
            "get_fundamentals": _decode_tool_output(execute_local_tool("get_fundamentals", {"ticker": ticker})),
        },
        "social_analyst": {
            "x_search": "external tool (not executed in local debug mode)",
        },
    }
    if include_statements:
        tool_runs["fundamentals_analyst"]["get_balance_sheet"] = _decode_tool_output(
            execute_local_tool("get_balance_sheet", {"ticker": ticker, "period": "quarterly"})
        )
        tool_runs["fundamentals_analyst"]["get_cashflow"] = _decode_tool_output(
            execute_local_tool("get_cashflow", {"ticker": ticker, "period": "quarterly"})
        )
        tool_runs["fundamentals_analyst"]["get_income_statement"] = _decode_tool_output(
            execute_local_tool("get_income_statement", {"ticker": ticker, "period": "quarterly"})
        )

    provider_payloads = {
        "market_domain": _safe_fetch(lambda: fetch_market_domain(ticker)),
        "fundamentals_domain": _safe_fetch(lambda: fetch_fundamentals_domain(ticker)),
        "macro_domain": _safe_fetch(fetch_macro_domain),
        "geopolitical_domain": _safe_fetch(lambda: fetch_geopolitical_domain(ticker)),
        "social_domain": _safe_fetch(lambda: fetch_social_domain(ticker, run_id=f"debug_{ticker.lower()}")),
        "news_domain_default_chain": _safe_fetch(lambda: fetch_news_domain(ticker)),
        "news_domain_by_provider": {
            "yfinance": _safe_fetch(lambda: fetch_news_domain(ticker, providers=["yfinance"])),
            "google_news": _safe_fetch(lambda: fetch_news_domain(ticker, providers=["google_news"])),
            "finnhub": _safe_fetch(lambda: fetch_news_domain(ticker, providers=["finnhub"])),
        },
    }

    return {
        "ticker": ticker,
        "scenario_id": scenario_id,
        "generated_at": now_iso(),
        "max_words": max_words,
        "include_statements": include_statements,
        "provider_chains": {
            "market": provider_chain("market"),
            "news": provider_chain("news"),
            "fundamentals": provider_chain("fundamentals"),
            "macro": provider_chain("macro"),
            "social": provider_chain("social"),
            "geopolitical": provider_chain("geopolitical"),
        },
        "live_context_summary": {
            "coverage": coverage,
            "errors": list(live_context.get("errors", []) or []),
            "domain_metadata": domain_metadata,
            "news_items_count": len(news_items),
            "market_context": market_context,
            "macro_context": macro_context,
            "fundamentals": fundamentals,
            "social_context": social_context,
            "geopolitical_context": geopolitical_context,
        },
        "providers": provider_payloads,
        "tool_outputs": tool_runs,
        "prompt_preview": prompt_preview,
        "known_prompt_roles": sorted(AGENT_SYSTEM_PROMPTS.keys()),
    }


def enforce_startup_environment() -> None:
    dotenv = load_env_file(ROOT / ".env", override=False)
    if dotenv["exists"] and dotenv["loaded"]:
        print(f"[INFO] Loaded {len(dotenv['loaded'])} env var(s) from {dotenv['path']}")
    elif not dotenv["exists"]:
        print(f"[WARN] .env file not found at {ROOT / '.env'}")
    llm_log = "on" if os.environ.get("ATD_LOG_LLM", "1").strip().lower() in {"1", "true", "yes", "on"} else "off"
    print(f"[INFO] LLM diagnostics logging is {llm_log} (set ATD_LOG_LLM=0 to disable)")
    print(
        "[INFO] Data provider keys: "
        f"FINNHUB_API_KEY={_key_status('FINNHUB_API_KEY')} "
        f"FRED_API_KEY={_key_status('FRED_API_KEY')} "
        f"STOCKTWITS_ACCESS_TOKEN={_key_status('STOCKTWITS_ACCESS_TOKEN')} "
        f"ACLED_ACCESS_TOKEN={_key_status('ACLED_ACCESS_TOKEN')}"
    )
    env_status = collect_environment_status()
    if env_status["strict_mode"] and env_status["missing_required"]:
        missing = ", ".join(env_status["missing_required"])
        raise RuntimeError(f"STRICT_ENV_VALIDATION is enabled and required env vars are missing: {missing}")
    if env_status["missing_required"]:
        missing = ", ".join(env_status["missing_required"])
        print(f"[WARN] OCI env not fully configured: {missing}")
        print("[WARN] Set STRICT_ENV_VALIDATION=1 to fail fast when required vars are missing.")


def _record_job_state(run_id: str, payload: dict) -> None:
    with RUN_JOBS_LOCK:
        current = RUN_JOBS.get(run_id, {})
        current.update(payload)
        RUN_JOBS[run_id] = current


def _launch_run_job(
    run_id: str,
    runtime: str,
    scenario: str,
    seats: list[str],
    ticker: str | None,
    breaking_news: bool = False,
    breaking_news_mode: str = "off",
    debate_depth: int = 1,
) -> None:
    _record_job_state(
        run_id,
        {
            "run_id": run_id,
            "runtime": runtime,
            "scenario": scenario,
            "ticker": ticker,
            "breaking_news": breaking_news,
            "breaking_news_mode": breaking_news_mode,
            "debate_depth": debate_depth,
            "started_at": now_iso(),
            "status": "running",
            "error": "",
            "awaiting_continue": False,
            "paused_after_stage": "",
        },
    )
    RUN_CONTROLS[run_id] = threading.Event()
    RUN_CANCELLATIONS[run_id] = threading.Event()
    cancel_event = RUN_CANCELLATIONS[run_id]

    def _target() -> None:
        try:
            adapter = get_adapter(runtime)

            def _pause_after_phase(stage_id: str) -> None:
                if cancel_event.is_set():
                    raise RunCancelled("reset_requested")
                if stage_id not in PHASE_BOUNDARY_STAGES:
                    return
                control = RUN_CONTROLS.get(run_id)
                if control is None:
                    return
                _record_job_state(
                    run_id,
                    {
                        "status": "paused",
                        "awaiting_continue": True,
                        "paused_after_stage": stage_id,
                    },
                )
                control.clear()
                while True:
                    control.wait(timeout=0.25)
                    if cancel_event.is_set():
                        raise RunCancelled("reset_requested")
                    if control.is_set():
                        break
                _record_job_state(
                    run_id,
                    {
                        "status": "running",
                        "awaiting_continue": False,
                        "paused_after_stage": "",
                    },
                )

            result = adapter.execute(
                scenario,
                seats or None,
                ticker or None,
                run_id=run_id,
                breaking_news=breaking_news,
                breaking_news_mode=breaking_news_mode,
                debate_depth=debate_depth,
                phase_pause=_pause_after_phase,
            )
            if cancel_event.is_set():
                raise RunCancelled("reset_requested")
            llm = result.get("summary", {}).get("llm", {})
            print(
                "[RUN] "
                f"run_id={run_id} runtime={runtime} scenario={scenario} ticker={result.get('summary', {}).get('ticker', '')} "
                f"breaking_news_mode={result.get('summary', {}).get('breaking_news_mode', 'off')} "
                f"debate_depth={result.get('summary', {}).get('debate_depth', debate_depth)} "
                f"llm_live={llm.get('live_count', 'n/a')} llm_fallback={llm.get('fallback_count', 'n/a')} "
                f"llm_mode={llm.get('last_mode', 'n/a')} auth={llm.get('auth_mode', 'n/a')} "
                f"error={llm.get('last_error', '')}",
                flush=True,
            )
            _record_job_state(
                run_id,
                {
                    "status": "completed",
                    "completed_at": now_iso(),
                    "error": "",
                    "awaiting_continue": False,
                    "paused_after_stage": "",
                },
            )
        except RunCancelled as exc:
            _record_job_state(
                run_id,
                {
                    "status": "cancelled",
                    "completed_at": now_iso(),
                    "error": str(exc),
                    "awaiting_continue": False,
                    "paused_after_stage": "",
                },
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[RUN][ERROR] run_id={run_id} runtime={runtime} scenario={scenario} error={exc}", flush=True)
            _record_job_state(
                run_id,
                {
                    "status": "failed",
                    "completed_at": now_iso(),
                    "error": str(exc),
                    "awaiting_continue": False,
                },
            )
        finally:
            RUN_CONTROLS.pop(run_id, None)
            RUN_CANCELLATIONS.pop(run_id, None)
            RUN_THREADS.pop(run_id, None)

    thread = threading.Thread(target=_target, daemon=True, name=f"run-job-{run_id}")
    RUN_THREADS[run_id] = thread
    thread.start()


class AppHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        # Suppress per-request access logs so run and diagnostics output stays readable.
        return

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict | None:
        content_length_raw = self.headers.get("Content-Length", "0")
        try:
            content_length = max(0, int(content_length_raw))
        except (TypeError, ValueError):
            content_length = 0
        if content_length <= 0:
            return None
        raw_body = self.rfile.read(content_length)
        try:
            parsed = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == '/':
            return self._send_file(ROOT / 'frontend/app/index.html', 'text/html; charset=utf-8')
        if parsed.path == '/debug/providers':
            return self._send_file(ROOT / 'frontend/app/providers_debug.html', 'text/html; charset=utf-8')
        if parsed.path == '/app.js':
            return self._send_file(ROOT / 'frontend/app/app.js', 'text/javascript; charset=utf-8')
        if parsed.path == '/providers_debug.js':
            return self._send_file(ROOT / 'frontend/app/providers_debug.js', 'text/javascript; charset=utf-8')
        if parsed.path.startswith('/var/runs/'):
            target = (ROOT / parsed.path.lstrip('/')).resolve()
            allowed_root = (ROOT / 'var' / 'runs').resolve()
            if allowed_root not in target.parents and target != allowed_root:
                return self.send_error(HTTPStatus.FORBIDDEN)
            content_type = 'application/json; charset=utf-8'
            if target.suffix == '.jsonl':
                content_type = 'application/x-ndjson; charset=utf-8'
            return self._send_file(target, content_type)
        if parsed.path == '/api/market/chart':
            params = parse_qs(parsed.query)
            ticker = normalize_single_ticker(params.get('ticker', [''])[0])
            if not ticker:
                return self._send_json({"error": "missing_ticker", "message": "A valid ticker is required."}, status=400)
            range_key = normalize_chart_range(params.get('range', ['30d'])[0])
            try:
                payload = fetch_chart_snapshot(ticker, range_key)
            except urllib.error.HTTPError as exc:
                return self._send_json(
                    {
                        "error": "market_data_http_error",
                        "message": f"Market data request failed ({exc.code}).",
                    },
                    status=502,
                )
            except (urllib.error.URLError, TimeoutError) as exc:
                return self._send_json(
                    {
                        "error": "market_data_network_error",
                        "message": f"Market data network error: {exc}",
                    },
                    status=502,
                )
            except ValueError as exc:
                return self._send_json({"error": "market_data_unavailable", "message": str(exc)}, status=502)
            return self._send_json(payload)
        if parsed.path == '/api/scenarios':
            return self._send_json(load_scenario_catalog())
        if parsed.path == '/api/debug/providers':
            params = parse_qs(parsed.query)
            ticker = normalize_single_ticker(params.get('ticker', ['NVDA'])[0]) or 'NVDA'
            scenario_id = str(params.get('scenario_id', ['single_name_earnings'])[0] or 'single_name_earnings').strip()
            max_words_raw = params.get('max_words', ['220'])[0]
            include_statements = parse_bool_flag(params.get('include_statements', ['0'])[0])
            try:
                max_words = max(80, min(int(max_words_raw), 800))
            except (TypeError, ValueError):
                max_words = 220
            try:
                payload = _build_debug_provider_payload(
                    ticker=ticker,
                    scenario_id=scenario_id,
                    max_words=max_words,
                    include_statements=include_statements,
                )
            except KeyError as exc:
                return self._send_json({"error": "invalid_scenario", "message": str(exc)}, status=400)
            except Exception as exc:  # noqa: BLE001
                return self._send_json({"error": "debug_build_failed", "message": str(exc)}, status=500)
            return self._send_json(payload)
        if parsed.path == '/api/health':
            params = parse_qs(parsed.query)
            verbose = params.get("verbose", ["0"])[0].strip().lower() in {"1", "true", "yes"}
            payload = health_payload()
            if not verbose:
                payload.pop("environment", None)
            status = 200 if payload["status"] == "ok" else 503
            return self._send_json(payload, status=status)
        if parsed.path == '/api/run':
            params = parse_qs(parsed.query)
            runtime = params.get('runtime', ['wayflow'])[0]
            scenario = params.get('scenario', ['single_name_earnings'])[0]
            seats = params.get('seat', [])
            # Ticker request mode: accept single or pair input (e.g., AMZN,ORCL).
            ticker = normalize_ticker_request(params.get('ticker', [''])[0], max_symbols=4)
            if not ticker:
                ticker = normalize_ticker_request(params.get('tickers', [''])[0], max_symbols=4)
            breaking_news = parse_bool_flag(params.get('breaking_news', ['0'])[0])
            breaking_news_mode = normalize_breaking_news_mode(params.get('breaking_news_mode', [''])[0])
            if breaking_news and breaking_news_mode == "off":
                breaking_news_mode = "manual"
            debate_depth = parse_debate_depth(params.get('debate_depth', ['1'])[0], default=1)
            try:
                adapter = get_adapter(runtime)
                result = adapter.execute(
                    scenario,
                    seats or None,
                    ticker or None,
                    breaking_news=breaking_news,
                    breaking_news_mode=breaking_news_mode,
                    debate_depth=debate_depth,
                )
                llm = result.get("summary", {}).get("llm", {})
                print(
                    "[RUN] "
                    f"run_id={result.get('run_id')} runtime={runtime} scenario={scenario} ticker={result.get('summary', {}).get('ticker', '')} "
                    f"breaking_news_mode={result.get('summary', {}).get('breaking_news_mode', breaking_news_mode)} "
                    f"debate_depth={result.get('summary', {}).get('debate_depth', debate_depth)} "
                    f"llm_live={llm.get('live_count', 'n/a')} llm_fallback={llm.get('fallback_count', 'n/a')} "
                    f"llm_mode={llm.get('last_mode', 'n/a')} auth={llm.get('auth_mode', 'n/a')} "
                    f"error={llm.get('last_error', '')}",
                    flush=True,
                )
            except (KeyError, ValueError) as exc:
                return self._send_json({"error": "run_failed", "message": str(exc)}, status=400)
            except Exception as exc:  # noqa: BLE001
                return self._send_json({"error": "run_failed", "message": str(exc)}, status=500)
            return self._send_json(result)
        if parsed.path == '/api/run/start':
            params = parse_qs(parsed.query)
            runtime = params.get('runtime', ['wayflow'])[0]
            scenario = params.get('scenario', ['single_name_earnings'])[0]
            seats = params.get('seat', [])
            ticker = normalize_ticker_request(params.get('ticker', [''])[0], max_symbols=4)
            if not ticker:
                ticker = normalize_ticker_request(params.get('tickers', [''])[0], max_symbols=4)
            breaking_news = parse_bool_flag(params.get('breaking_news', ['0'])[0])
            breaking_news_mode = normalize_breaking_news_mode(params.get('breaking_news_mode', [''])[0])
            if breaking_news and breaking_news_mode == "off":
                breaking_news_mode = "manual"
            debate_depth = parse_debate_depth(params.get('debate_depth', ['1'])[0], default=1)
            try:
                get_adapter(runtime)
            except (KeyError, ValueError) as exc:
                return self._send_json({"error": "run_failed", "message": str(exc)}, status=400)
            run_id = make_id("run")
            _launch_run_job(
                run_id,
                runtime,
                scenario,
                seats,
                ticker or None,
                breaking_news=breaking_news,
                breaking_news_mode=breaking_news_mode,
                debate_depth=debate_depth,
            )
            return self._send_json(
                {
                    "run_id": run_id,
                    "status": "started",
                    "runtime": runtime,
                    "scenario_id": scenario,
                    "ticker": ticker,
                    "breaking_news_mode": breaking_news_mode,
                    "debate_depth": debate_depth,
                }
            )
        if parsed.path == '/api/run/status':
            params = parse_qs(parsed.query)
            run_id = params.get('run_id', [None])[0]
            if not run_id:
                return self._send_json({"error": "missing run_id"}, status=400)
            with RUN_JOBS_LOCK:
                status = RUN_JOBS.get(run_id)
            if not status:
                return self._send_json({"error": "unknown run"}, status=404)
            return self._send_json(status)
        if parsed.path == '/api/run/reset':
            params = parse_qs(parsed.query)
            run_id = params.get('run_id', [None])[0]
            if not run_id:
                return self._send_json({"error": "missing run_id"}, status=400)
            with RUN_JOBS_LOCK:
                status = dict(RUN_JOBS.get(run_id) or {})
            if not status:
                return self._send_json({"error": "unknown run"}, status=404)
            previous_status = status.get("status", "unknown")
            if previous_status in {"completed", "failed", "cancelled"}:
                status["reset_applied"] = False
                status["thread_alive"] = False
                status["previous_status"] = previous_status
                return self._send_json(status)

            cancelled = RUN_CANCELLATIONS.get(run_id)
            if cancelled is not None:
                cancelled.set()
            control = RUN_CONTROLS.get(run_id)
            if control is not None:
                control.set()

            _record_job_state(
                run_id,
                {
                    "status": "cancelled",
                    "completed_at": now_iso(),
                    "error": "reset_requested",
                    "awaiting_continue": False,
                    "paused_after_stage": "",
                },
            )
            thread = RUN_THREADS.get(run_id)
            if thread is not None and thread.is_alive():
                thread.join(timeout=0.3)
            thread_alive = bool(thread is not None and thread.is_alive())
            with RUN_JOBS_LOCK:
                updated = dict(RUN_JOBS.get(run_id) or {})
            updated["reset_applied"] = True
            updated["thread_alive"] = thread_alive
            updated["previous_status"] = previous_status
            return self._send_json(updated)
        if parsed.path == '/api/run/continue':
            params = parse_qs(parsed.query)
            run_id = params.get('run_id', [None])[0]
            if not run_id:
                return self._send_json({"error": "missing run_id"}, status=400)
            with RUN_JOBS_LOCK:
                status = dict(RUN_JOBS.get(run_id) or {})
            if not status:
                return self._send_json({"error": "unknown run"}, status=404)
            if status.get("status") != "paused":
                return self._send_json({"error": "run_not_paused", "status": status}, status=409)
            control = RUN_CONTROLS.get(run_id)
            if control is None:
                return self._send_json({"error": "run_control_missing"}, status=409)
            control.set()
            _record_job_state(
                run_id,
                {
                    "status": "running",
                    "awaiting_continue": False,
                    "paused_after_stage": "",
                },
            )
            with RUN_JOBS_LOCK:
                updated = dict(RUN_JOBS.get(run_id) or {})
            return self._send_json(updated)
        if parsed.path == '/api/runs':
            params = parse_qs(parsed.query)
            run_id = params.get('run_id', [None])[0]
            if run_id == 'recent':
                limit = int(params.get('limit', ['20'])[0])
                return self._send_json(RunStore.list_runs(limit=limit))
            if not run_id:
                return self._send_json({"error": "missing run_id"}, status=400)
            try:
                return self._send_json(RunStore.load_run(run_id))
            except FileNotFoundError:
                return self._send_json({"error": "unknown run"}, status=404)
        if parsed.path == '/api/audit':
            params = parse_qs(parsed.query)
            limit = int(params.get('limit', ['50'])[0])
            return self._send_json(RunStore.list_audit(limit=limit))
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self.send_error(HTTPStatus.NOT_FOUND)


def main() -> None:
    enforce_startup_environment()
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', '8000'))
    server = ThreadingHTTPServer((host, port), AppHandler)
    print(f'Serving on http://{host}:{port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
