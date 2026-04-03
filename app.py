from __future__ import annotations

import json
import os
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import urllib.error

from runtime.common.data_providers import (
    CHART_WINDOWS,
    fetch_chart_snapshot,
)
from runtime.common.env_validation import collect_environment_status, load_env_file
from runtime.common.oci_genai import OciGenAIClient
from runtime.common.scenario_loader import load_scenario_catalog
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


def normalize_ticker_request(raw: str, max_symbols: int = 2) -> str:
    matches = TICKER_PATTERN.findall((raw or "").upper())
    if not matches:
        return ""
    limit = max(1, min(int(max_symbols or 1), 4))
    return ",".join(matches[:limit])


def parse_bool_flag(raw: str) -> bool:
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


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
        if capability["auth_mode"] == "unsigned_no_sdk":
            return {
                "status": "ok",
                "detail": "OCI GenAI configured (no-key mode, OCI SDK unavailable). Live calls may fallback unless endpoint accepts unsigned requests.",
            }
        return {"status": "ok", "detail": "OCI GenAI configured (no-key unsigned mode)"}
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
                debate_depth=debate_depth,
                phase_pause=_pause_after_phase,
            )
            if cancel_event.is_set():
                raise RunCancelled("reset_requested")
            llm = result.get("summary", {}).get("llm", {})
            print(
                "[RUN] "
                f"run_id={run_id} runtime={runtime} scenario={scenario} ticker={result.get('summary', {}).get('ticker', '')} "
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
        if parsed.path == '/app.js':
            return self._send_file(ROOT / 'frontend/app/app.js', 'text/javascript; charset=utf-8')
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
            ticker = normalize_ticker_request(params.get('ticker', [''])[0], max_symbols=2)
            if not ticker:
                ticker = normalize_ticker_request(params.get('tickers', [''])[0], max_symbols=2)
            breaking_news = parse_bool_flag(params.get('breaking_news', ['0'])[0])
            debate_depth = parse_debate_depth(params.get('debate_depth', ['1'])[0], default=1)
            try:
                adapter = get_adapter(runtime)
                result = adapter.execute(
                    scenario,
                    seats or None,
                    ticker or None,
                    breaking_news=breaking_news,
                    debate_depth=debate_depth,
                )
                llm = result.get("summary", {}).get("llm", {})
                print(
                    "[RUN] "
                    f"run_id={result.get('run_id')} runtime={runtime} scenario={scenario} ticker={result.get('summary', {}).get('ticker', '')} "
                    f"breaking_news={breaking_news} debate_depth={result.get('summary', {}).get('debate_depth', debate_depth)} "
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
            ticker = normalize_ticker_request(params.get('ticker', [''])[0], max_symbols=2)
            if not ticker:
                ticker = normalize_ticker_request(params.get('tickers', [''])[0], max_symbols=2)
            breaking_news = parse_bool_flag(params.get('breaking_news', ['0'])[0])
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
                debate_depth=debate_depth,
            )
            return self._send_json(
                {
                    "run_id": run_id,
                    "status": "started",
                    "runtime": runtime,
                    "scenario_id": scenario,
                    "ticker": ticker,
                    "breaking_news": breaking_news,
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
