from __future__ import annotations

import json
import logging
import math
import os
import re
import statistics
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

from runtime.common.utils import now_iso

try:
    import yfinance as yf
except Exception:  # noqa: BLE001
    yf = None
else:
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

try:
    from fredapi import Fred
except Exception:  # noqa: BLE001
    Fred = None

USER_AGENT = "agentic-trading-desk/1.0 (+providers)"
REQUEST_TIMEOUT_S = 5.0
DATA_LOG_ENABLED = str(os.environ.get("ATD_DATA_LOG", "1")).strip().lower() in {"1", "true", "yes", "on"}
LOGGER = logging.getLogger("agentic_trading_desk.data")
LLM_LOG_ENABLED = str(os.environ.get("ATD_LOG_LLM", "1")).strip().lower() in {"1", "true", "yes", "on"}
try:
    X_SEARCH_LOOKBACK_DAYS = max(1, min(14, int(os.environ.get("ATD_X_SEARCH_LOOKBACK_DAYS", "2") or "2")))
except (TypeError, ValueError):
    X_SEARCH_LOOKBACK_DAYS = 2

CHART_WINDOWS: dict[str, dict[str, Any]] = {
    "1d": {"yahoo_range": "1d", "yahoo_interval": "5m", "finnhub_resolution": "5", "lookback_days": 1},
    "5d": {"yahoo_range": "5d", "yahoo_interval": "15m", "finnhub_resolution": "15", "lookback_days": 5},
    "30d": {"yahoo_range": "1mo", "yahoo_interval": "1d", "finnhub_resolution": "D", "lookback_days": 30},
    "180d": {"yahoo_range": "6mo", "yahoo_interval": "1d", "finnhub_resolution": "D", "lookback_days": 180},
    "1y": {"yahoo_range": "1y", "yahoo_interval": "1d", "finnhub_resolution": "D", "lookback_days": 365},
}

DEFAULT_PROVIDER_CHAINS: dict[str, tuple[str, ...]] = {
    "chart": ("yfinance", "finnhub"),
    "market": ("yfinance", "finnhub"),
    "news": ("google_news", "yfinance", "finnhub"),
    "macro": ("fred", "yfinance"),
    "geopolitical": ("google_news", "finnhub"),
    "fundamentals": ("yfinance", "finnhub"),
    "social": ("stocktwits",),
}

GEOPOLITICAL_RISK_KEYWORDS = (
    "sanction",
    "export",
    "ban",
    "tariff",
    "probe",
    "antitrust",
    "conflict",
    "war",
    "regulation",
    "review",
    "restriction",
)


def _request(
    url: str,
    *,
    timeout: float = REQUEST_TIMEOUT_S,
    accept: str = "application/json,text/plain,*/*",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
) -> str | None:
    request = urllib.request.Request(
        url=url,
        data=data,
        method=method or ("POST" if data is not None else "GET"),
        headers={
            "Accept": accept,
            "User-Agent": USER_AGENT,
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError):
        return None


def _data_log(domain: str, provider: str, status: str, detail: str) -> None:
    if not DATA_LOG_ENABLED:
        return
    if status != "failed":
        return
    print(f"[DATA] domain={domain} provider={provider} status={status} detail={detail}", flush=True)
    LOGGER.info("domain=%s provider=%s status=%s detail=%s", domain, provider, status, detail)


def _social_debug(detail: str) -> None:
    if not LLM_LOG_ENABLED:
        return
    print(f"[LLM] social_pipeline {detail}", flush=True)


def _request_json(
    url: str,
    *,
    timeout: float = REQUEST_TIMEOUT_S,
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
    method: str | None = None,
) -> dict[str, Any] | list[Any] | None:
    payload = _request(url, timeout=timeout, headers=headers, data=data, method=method)
    if payload is None:
        return None
    try:
        return json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _request_text(
    url: str,
    *,
    timeout: float = REQUEST_TIMEOUT_S,
    headers: dict[str, str] | None = None,
) -> str | None:
    return _request(
        url,
        timeout=timeout,
        headers=headers,
        accept="application/xml,text/xml,text/plain,*/*",
    )


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return ((current - previous) / previous) * 100.0


def _pearson(a: list[float], b: list[float]) -> float | None:
    if len(a) != len(b) or len(a) < 3:
        return None
    mean_a = sum(a) / len(a)
    mean_b = sum(b) / len(b)
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = sum((x - mean_a) ** 2 for x in a)
    var_b = sum((y - mean_b) ** 2 for y in b)
    if var_a <= 0 or var_b <= 0:
        return None
    return _clamp(cov / math.sqrt(var_a * var_b), -1.0, 1.0)


def _freshness_from_timestamp(iso_ts: str | None) -> str:
    if not iso_ts:
        return "snapshot"
    try:
        published = datetime.fromisoformat(iso_ts.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return "snapshot"
    age_hours = (datetime.now(UTC) - published).total_seconds() / 3600.0
    if age_hours <= 6:
        return "live"
    if age_hours <= 48:
        return "delayed"
    return "snapshot"


def _format_epoch(epoch_value: Any) -> str | None:
    try:
        epoch_int = int(epoch_value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(epoch_int, tz=UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_datetime_to_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    candidates = [raw]
    if raw.endswith("Z"):
        candidates.append(raw[:-1] + "+00:00")
    if len(raw) >= 5 and raw[-5] in {"+", "-"} and raw[-3] != ":":
        candidates.append(f"{raw[:-2]}:{raw[-2:]}")
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate).astimezone(UTC)
        except ValueError:
            continue
    return None


def _parse_rfc822(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _classify_event_risk(vol_pct: float | None, abs_momentum_pct: float) -> str:
    if vol_pct is None:
        return "moderate"
    if vol_pct >= 3.2 or abs_momentum_pct >= 10:
        return "very_high"
    if vol_pct >= 2.2 or abs_momentum_pct >= 7:
        return "high"
    if vol_pct >= 1.2:
        return "moderate"
    return "low"


def _classify_liquidity(avg_volume_millions: float | None) -> str:
    if avg_volume_millions is None:
        return "unknown"
    if avg_volume_millions >= 20:
        return "deep"
    if avg_volume_millions >= 4:
        return "acceptable"
    if avg_volume_millions >= 1:
        return "acceptable_with_wider_spreads"
    return "thin"


def _pick_float(mapping: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        if key in mapping:
            value = _safe_float(mapping.get(key))
            if value is not None:
                return value
    return None


def provider_chain(domain: str) -> list[str]:
    def normalize(domain_name: str, providers: list[str]) -> list[str]:
        if domain_name != "social":
            return providers
        aliases = {
            "x_search": "stocktwits",
            "x": "stocktwits",
            "news_proxy": "stocktwits",
            "yfinance_proxy": "stocktwits",
            "finnhub": "stocktwits",
        }
        normalized: list[str] = []
        seen: set[str] = set()
        for provider in providers:
            mapped = aliases.get(provider, provider)
            if mapped in seen:
                continue
            normalized.append(mapped)
            seen.add(mapped)
        return normalized

    defaults = list(DEFAULT_PROVIDER_CHAINS.get(domain, ()))
    configured = os.environ.get(f"ATD_PROVIDER_{domain.upper()}", "")
    if configured.strip():
        selected = [item.strip().lower() for item in configured.split(",") if item.strip()]
        if selected:
            return normalize(domain, selected)
    return normalize(domain, defaults)


def _base_result(domain: str, provider: str, ok: bool, *, error: str = "", source_count: int = 0, freshness: str = "snapshot", fallback_used: bool = False, degraded_reason: str = "", errors: list[str] | None = None) -> dict[str, Any]:
    return {
        "ok": ok,
        "domain": domain,
        "provider": provider,
        "as_of": now_iso(),
        "freshness": freshness,
        "source_count": source_count,
        "fallback_used": fallback_used,
        "degraded": fallback_used or bool(degraded_reason),
        "degraded_reason": degraded_reason,
        "errors": list(errors or []),
        "error": error,
    }


def _run_provider_chain(domain: str, handlers: dict[str, Any], *, providers: list[str] | None = None) -> dict[str, Any]:
    attempts: list[str] = []
    chain = list(providers or provider_chain(domain))
    for idx, provider in enumerate(chain):
        handler = handlers.get(provider)
        if handler is None:
            _data_log(domain, provider, "skipped", "unsupported provider")
            attempts.append(f"{provider}:unsupported")
            continue
        try:
            _data_log(domain, provider, "start", f"attempt={idx + 1}/{len(chain)}")
            result = handler()
        except Exception as exc:  # noqa: BLE001
            _data_log(domain, provider, "error", str(exc))
            attempts.append(f"{provider}:{exc}")
            continue
        if result.get("ok"):
            result["fallback_used"] = idx > 0
            result["degraded"] = idx > 0 or bool(result.get("degraded_reason"))
            if idx > 0 and not result.get("degraded_reason"):
                result["degraded_reason"] = "fallback_provider"
            result["errors"] = attempts + list(result.get("errors", []))
            _data_log(
                domain,
                provider,
                "ok",
                f"fallback_used={result['fallback_used']} source_count={result.get('source_count', 0)} freshness={result.get('freshness', 'snapshot')}",
            )
            return result
        _data_log(domain, provider, "failed", result.get("error") or "unavailable")
        attempts.append(f"{provider}:{result.get('error') or 'unavailable'}")
    _data_log(domain, "chain", "failed", "; ".join(attempts) if attempts else "no providers configured")
    return _base_result(domain, "", False, error=f"{domain}_unavailable", degraded_reason="all_providers_failed", errors=attempts)


def _finnhub_key() -> str:
    return os.environ.get("FINNHUB_API_KEY", "").strip()


def _fred_key() -> str:
    return os.environ.get("FRED_API_KEY", "").strip()


def _acled_access_token() -> str:
    access_token = os.environ.get("ACLED_ACCESS_TOKEN", "").strip()
    if access_token:
        return access_token
    email = os.environ.get("ACLED_EMAIL", "").strip() or os.environ.get("ACLED_USERNAME", "").strip()
    password = os.environ.get("ACLED_PASSWORD", "").strip()
    if not email or not password:
        return ""
    payload = urllib.parse.urlencode({"email": email, "password": password}).encode("utf-8")
    token_response = _request_json(
        "https://acleddata.com/oauth/token",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    if not isinstance(token_response, dict):
        return ""
    token = str(token_response.get("access_token", "") or "").strip()
    if token:
        os.environ["ACLED_ACCESS_TOKEN"] = token
    return token


def _finnhub_json(path: str, params: dict[str, Any]) -> dict[str, Any] | list[Any] | None:
    token = _finnhub_key()
    if not token:
        return None
    query = urllib.parse.urlencode({**params, "token": token})
    return _request_json(f"https://finnhub.io/api/v1{path}?{query}")


def _fred_series_observations(series_id: str, *, limit: int = 24) -> list[dict[str, Any]]:
    api_key = _fred_key()
    if not api_key or Fred is None:
        return []
    try:
        client = Fred(api_key=api_key)
        series = client.get_series(series_id)
    except Exception:  # noqa: BLE001
        return []
    if series is None or len(series) == 0:
        return []
    cleaned = series.dropna().sort_index(ascending=False).head(limit)
    observations: list[dict[str, Any]] = []
    for idx, value in cleaned.items():
        try:
            date_value = idx.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            date_value = str(idx)
        observations.append({"date": date_value, "value": value})
    return observations


def _acled_json(query_params: dict[str, Any]) -> dict[str, Any] | None:
    token = _acled_access_token()
    if not token:
        return None
    query = urllib.parse.urlencode(query_params)
    payload = _request_json(
        f"https://acleddata.com/api/acled/read?{query}",
        headers={"Authorization": f"Bearer {token}"},
    )
    return payload if isinstance(payload, dict) else None


def _yahoo_chart(symbol: str, range_key: str, interval: str) -> dict[str, Any] | None:
    if yf is None:
        return None
    history = None
    try:
        ticker_obj = yf.Ticker(symbol)
        history = ticker_obj.history(period=range_key, interval=interval, auto_adjust=False)
    except Exception:  # noqa: BLE001
        history = None
    try:
        if history is None or getattr(history, "empty", True):
            history = yf.download(
                tickers=symbol,
                period=range_key,
                interval=interval,
                auto_adjust=False,
                progress=False,
                threads=False,
            )
    except Exception:  # noqa: BLE001
        pass
    if history is not None and not getattr(history, "empty", True) and hasattr(history, "columns"):
        # yfinance can return MultiIndex columns for single tickers.
        try:
            if getattr(history.columns, "nlevels", 1) > 1:
                history.columns = history.columns.get_level_values(0)
        except Exception:  # noqa: BLE001
            pass
    if history is None or getattr(history, "empty", True):
        return None
    points: list[dict[str, Any]] = []
    for idx, row in history.iterrows():
        close = _safe_float(row.get("Close"))
        if close is None:
            continue
        volume = _safe_float(row.get("Volume"))
        try:
            ts = idx.to_pydatetime() if hasattr(idx, "to_pydatetime") else idx
            if getattr(ts, "tzinfo", None) is None:
                ts = ts.replace(tzinfo=UTC)
            timestamp = int(ts.timestamp())
        except Exception:  # noqa: BLE001
            continue
        points.append({"ts": timestamp, "close": round(close, 6), "volume": int(volume) if volume is not None else None})
    if len(points) < 2:
        return None
    exchange = ""
    currency = "USD"
    market_price = points[-1]["close"]
    previous_close = points[-2]["close"]
    try:
        ticker_obj = yf.Ticker(symbol)
        fast = dict(ticker_obj.fast_info or {})
        exchange = str(fast.get("exchange", "") or "")
        currency = str(fast.get("currency", "USD") or "USD")
        market_price = _safe_float(fast.get("last_price")) or market_price
        previous_close = _safe_float(fast.get("previous_close")) or previous_close
    except Exception:  # noqa: BLE001
        pass
    return {
        "symbol": symbol,
        "exchange": exchange,
        "currency": currency,
        "regular_market_price": market_price,
        "previous_close": previous_close,
        "points": points,
        "as_of": _format_epoch(points[-1]["ts"]) or now_iso(),
    }


def _yahoo_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    if yf is None:
        return {}
    quotes: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        try:
            ticker_obj = yf.Ticker(symbol)
            fast = dict(ticker_obj.fast_info or {})
            last_price = _safe_float(fast.get("last_price"))
            previous_close = _safe_float(fast.get("previous_close"))
            if last_price is None:
                history = ticker_obj.history(period="5d", interval="1d", auto_adjust=False)
                if history is None or getattr(history, "empty", True):
                    continue
                closes = [float(value) for value in history["Close"].dropna().tolist()]
                if not closes:
                    continue
                last_price = closes[-1]
                previous_close = closes[-2] if len(closes) >= 2 else closes[-1]
            quotes[symbol] = {
                "symbol": symbol,
                "regularMarketPrice": last_price,
                "regularMarketPreviousClose": previous_close,
            }
        except Exception:  # noqa: BLE001
            continue
    return quotes


def _article_freshness(items: list[dict[str, Any]]) -> str:
    timestamps = [str(item.get("published_at", "") or "").strip() for item in items if item.get("published_at")]
    if not timestamps:
        return "snapshot"
    freshest = sorted(timestamps, reverse=True)[0]
    return _freshness_from_timestamp(freshest)


def _google_news_rss_url(ticker: str) -> str:
    query = urllib.parse.quote(str(ticker or "").strip())
    return f"https://news.google.com/rss/search?q={query}+when:7d&hl=en-US&gl=US&ceid=US:en"


def _compute_market_payload(ticker: str, chart_payload: dict[str, Any], benchmark_payload: dict[str, Any] | None, provider: str) -> dict[str, Any]:
    points = chart_payload["points"]
    closes = [float(point["close"]) for point in points]
    volumes = [point["volume"] for point in points if isinstance(point.get("volume"), int)]
    returns = [
        (closes[idx] - closes[idx - 1]) / closes[idx - 1]
        for idx in range(1, len(closes))
        if closes[idx - 1]
    ]
    momentum = ((closes[-1] - closes[0]) / closes[0] * 100.0) if closes and closes[0] else 0.0
    volatility = statistics.pstdev(returns) * 100.0 if len(returns) >= 2 else 0.0
    avg_volume = (_mean([float(value) for value in volumes]) / 1_000_000.0) if volumes else None
    support = min(closes[-20:]) if len(closes) >= 5 else min(closes)
    resistance = max(closes[-20:]) if len(closes) >= 5 else max(closes)
    ai_corr = None
    if benchmark_payload and benchmark_payload.get("points"):
        bench_points = benchmark_payload["points"]
        benchmark_closes = [float(point["close"]) for point in bench_points]
        window = min(len(closes), len(benchmark_closes), 22)
        if window >= 4:
            base_returns = [
                (closes[idx] - closes[idx - 1]) / closes[idx - 1]
                for idx in range(len(closes) - window + 1, len(closes))
                if closes[idx - 1]
            ]
            bench_returns = [
                (benchmark_closes[idx] - benchmark_closes[idx - 1]) / benchmark_closes[idx - 1]
                for idx in range(len(benchmark_closes) - window + 1, len(benchmark_closes))
                if benchmark_closes[idx - 1]
            ]
            if len(base_returns) == len(bench_returns) and len(base_returns) >= 3:
                ai_corr = _pearson(base_returns, bench_returns)
    market_context = {
        "momentum_20d_pct": round(momentum, 2),
        "event_risk": _classify_event_risk(volatility, abs(momentum)),
        "liquidity": _classify_liquidity(avg_volume),
        "regular_market_price": round(float(chart_payload["regular_market_price"]), 4),
        "previous_close": round(float(chart_payload["previous_close"]), 4),
        "return_volatility_pct": round(volatility, 3),
        "avg_volume_millions": round(avg_volume, 3) if avg_volume is not None else None,
        "support_level": round(float(support), 4),
        "resistance_level": round(float(resistance), 4),
        "as_of": chart_payload["as_of"],
    }
    result = _base_result("market", provider, True, source_count=2 if benchmark_payload else 1, freshness="live")
    result.update(
        {
            "market_context": market_context,
            "price_series": [round(value, 4) for value in closes[-30:]],
            "volume_millions": [round(float(value) / 1_000_000.0, 3) for value in volumes[-30:]],
            "ai_basket_correlation": round(float(ai_corr), 3) if ai_corr is not None else None,
            "chart": {
                "ticker": ticker,
                "exchange": chart_payload.get("exchange", ""),
                "currency": chart_payload.get("currency", "USD"),
                "regular_market_price": chart_payload.get("regular_market_price"),
                "previous_close": chart_payload.get("previous_close"),
                "points": [{"ts": point["ts"], "close": point["close"]} for point in points],
                "source": provider,
                "as_of": chart_payload["as_of"],
            },
        }
    )
    return result


def _fetch_chart_yahoo(ticker: str, range_key: str) -> dict[str, Any]:
    if yf is None:
        return _base_result("chart", "yfinance", False, error="yfinance_not_installed")
    window = CHART_WINDOWS[range_key]
    chart = _yahoo_chart(ticker, window["yahoo_range"], window["yahoo_interval"])
    if not chart:
        return _base_result("chart", "yfinance", False, error="chart_unavailable")
    result = _base_result("chart", "yfinance", True, freshness="live", source_count=1)
    result.update(
        {
            "ticker": ticker,
            "range": range_key,
            "interval": window["yahoo_interval"],
            "exchange": chart.get("exchange", ""),
            "currency": chart.get("currency", "USD"),
            "regular_market_price": chart.get("regular_market_price"),
            "previous_close": chart.get("previous_close"),
            "points": [
                {
                    "ts": point["ts"],
                    "close": point["close"],
                    "volume": point.get("volume"),
                }
                for point in chart["points"]
            ],
            "source": "yfinance",
            "as_of": chart["as_of"],
        }
    )
    return result


def _finnhub_chart_payload(ticker: str, range_key: str) -> dict[str, Any] | None:
    token = _finnhub_key()
    if not token:
        return None
    window = CHART_WINDOWS[range_key]
    now_dt = datetime.now(UTC)
    start_dt = now_dt - timedelta(days=int(window["lookback_days"]))
    quote = _finnhub_json("/quote", {"symbol": ticker})
    candles = _finnhub_json(
        "/stock/candle",
        {
            "symbol": ticker,
            "resolution": window["finnhub_resolution"],
            "from": int(start_dt.timestamp()),
            "to": int(now_dt.timestamp()),
        },
    )
    if not isinstance(quote, dict) or not isinstance(candles, dict) or candles.get("s") != "ok":
        return None
    closes = candles.get("c") or []
    timestamps = candles.get("t") or []
    volumes = candles.get("v") or []
    points: list[dict[str, Any]] = []
    for idx, ts in enumerate(timestamps):
        close = _safe_float(closes[idx]) if idx < len(closes) else None
        if close is None:
            continue
        volume = _safe_float(volumes[idx]) if idx < len(volumes) else None
        try:
            timestamp = int(ts)
        except (TypeError, ValueError):
            continue
        points.append({"ts": timestamp, "close": round(close, 6), "volume": int(volume) if volume is not None else None})
    if len(points) < 2:
        return None
    profile = _finnhub_json("/stock/profile2", {"symbol": ticker})
    exchange = str((profile or {}).get("exchange", "") or "")
    currency = str((profile or {}).get("currency", "USD") or "USD")
    regular_market_price = _safe_float(quote.get("c")) or points[-1]["close"]
    previous_close = _safe_float(quote.get("pc")) or points[-2]["close"]
    return {
        "symbol": ticker,
        "exchange": exchange,
        "currency": currency,
        "regular_market_price": regular_market_price,
        "previous_close": previous_close,
        "points": points,
        "as_of": _format_epoch(points[-1]["ts"]) or now_iso(),
        "interval": window["finnhub_resolution"],
    }


def _fetch_chart_finnhub(ticker: str, range_key: str) -> dict[str, Any]:
    if not _finnhub_key():
        return _base_result("chart", "finnhub", False, error="finnhub_api_key_missing")
    chart = _finnhub_chart_payload(ticker, range_key)
    if not chart:
        return _base_result("chart", "finnhub", False, error="chart_unavailable")
    result = _base_result("chart", "finnhub", True, freshness="live", source_count=1)
    result.update(
        {
            "ticker": ticker,
            "range": range_key,
            "interval": chart.get("interval", ""),
            "exchange": chart.get("exchange", ""),
            "currency": chart.get("currency", "USD"),
            "regular_market_price": chart.get("regular_market_price"),
            "previous_close": chart.get("previous_close"),
            "points": [
                {
                    "ts": point["ts"],
                    "close": point["close"],
                    "volume": point.get("volume"),
                }
                for point in chart["points"]
            ],
            "source": "Finnhub",
            "as_of": chart["as_of"],
        }
    )
    return result


def fetch_chart_snapshot(ticker: str, range_key: str = "30d") -> dict[str, Any]:
    normalized_range = range_key if range_key in CHART_WINDOWS else "30d"
    result = _run_provider_chain(
        "chart",
        {
            "finnhub": lambda: _fetch_chart_finnhub(ticker, normalized_range),
            "yfinance": lambda: _fetch_chart_yahoo(ticker, normalized_range),
        },
    )
    if not result.get("ok"):
        raise ValueError(result.get("error") or "market_data_unavailable")
    return result


def _fetch_market_yahoo(ticker: str) -> dict[str, Any]:
    if yf is None:
        return _base_result("market", "yfinance", False, error="yfinance_not_installed")
    chart = _yahoo_chart(ticker, "1mo", "1d")
    benchmark = _yahoo_chart("QQQ", "1mo", "1d")
    if not chart:
        return _base_result("market", "yfinance", False, error="market_unavailable")
    return _compute_market_payload(ticker, chart, benchmark, "yfinance")


def _fetch_market_finnhub(ticker: str) -> dict[str, Any]:
    if not _finnhub_key():
        return _base_result("market", "finnhub", False, error="finnhub_api_key_missing")
    chart = _finnhub_chart_payload(ticker, "30d")
    benchmark = _finnhub_chart_payload("QQQ", "30d")
    if not chart:
        return _base_result("market", "finnhub", False, error="market_unavailable")
    return _compute_market_payload(ticker, chart, benchmark, "finnhub")


def fetch_market_domain(ticker: str) -> dict[str, Any]:
    return _run_provider_chain(
        "market",
        {
            "finnhub": lambda: _fetch_market_finnhub(ticker),
            "yfinance": lambda: _fetch_market_yahoo(ticker),
        },
    )


def _fetch_news_yahoo(ticker: str) -> dict[str, Any]:
    if yf is None:
        return _base_result("news", "yfinance", False, error="news_unavailable")
    try:
        ticker_obj = yf.Ticker(ticker)
        raw_items = list(ticker_obj.news or [])
    except Exception:  # noqa: BLE001
        return _base_result("news", "yfinance", False, error="news_unavailable")
    news_items: list[dict[str, Any]] = []
    for item in raw_items:
        content = item.get("content") if isinstance(item.get("content"), dict) else item
        title = str((content or {}).get("title", "") or "").strip()
        if not title:
            continue
        publisher_obj = (content or {}).get("provider")
        if isinstance(publisher_obj, dict):
            publisher = str(publisher_obj.get("displayName", "") or "").strip()
        else:
            publisher = str((content or {}).get("publisher", "") or "").strip()
        url_obj = (content or {}).get("canonicalUrl") or (content or {}).get("clickThroughUrl") or {}
        if isinstance(url_obj, dict):
            link = str(url_obj.get("url", "") or "").strip()
        else:
            link = str((content or {}).get("link", "") or "").strip()
        pub_date = str((content or {}).get("pubDate", "") or "").strip()
        published_at = ""
        if pub_date:
            try:
                published_at = datetime.fromisoformat(pub_date.replace("Z", "+00:00")).astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
            except ValueError:
                published_at = ""
        if not published_at:
            published_at = _format_epoch((content or {}).get("providerPublishTime")) or ""
        news_items.append(
            {
                "title": title,
                "summary": str((content or {}).get("summary", "") or "").strip() or title,
                "freshness": _freshness_from_timestamp(published_at),
                "publisher": publisher,
                "published_at": published_at or "",
                "link": link,
            }
        )
    news_items = news_items[:20]
    if not news_items:
        return _base_result("news", "yfinance", False, error="news_empty")
    result = _base_result("news", "yfinance", True, freshness=_article_freshness(news_items), source_count=len(news_items))
    result["news_items"] = news_items
    return result


def _fetch_news_google(ticker: str) -> dict[str, Any]:
    xml_text = _request_text(_google_news_rss_url(ticker))
    if not xml_text:
        return _base_result("news", "google_news", False, error="news_unavailable")
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return _base_result("news", "google_news", False, error="news_parse_error")
    news_items: list[dict[str, Any]] = []
    for item in root.findall("./channel/item")[:8]:
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        published_at = _parse_rfc822(item.findtext("pubDate"))
        article_link = (item.findtext("link") or "").strip()
        news_items.append(
            {
                "title": title,
                "summary": title,
                "freshness": _freshness_from_timestamp(published_at),
                "publisher": (item.findtext("source") or "").strip(),
                "published_at": published_at or "",
                "link": article_link,
            }
        )
    if not news_items:
        return _base_result("news", "google_news", False, error="news_empty")
    result = _base_result("news", "google_news", True, freshness=_article_freshness(news_items), source_count=len(news_items))
    result["news_items"] = news_items[:8]
    result["article_links"] = [str(item.get("link", "") or "").strip() for item in result["news_items"] if str(item.get("link", "") or "").strip()][:8]
    return result


def _fetch_news_finnhub(ticker: str) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    start = today - timedelta(days=7)
    payload = _finnhub_json("/company-news", {"symbol": ticker, "from": start.isoformat(), "to": today.isoformat()})
    if not isinstance(payload, list):
        return _base_result("news", "finnhub", False, error="news_unavailable")
    news_items: list[dict[str, Any]] = []
    for item in payload[:10]:
        title = str(item.get("headline", "") or "").strip()
        if not title:
            continue
        published_at = _format_epoch(item.get("datetime"))
        news_items.append(
            {
                "title": title,
                "summary": str(item.get("summary", "") or "").strip() or title,
                "freshness": _freshness_from_timestamp(published_at),
                "publisher": str(item.get("source", "") or "").strip(),
                "published_at": published_at or "",
                "link": str(item.get("url", "") or "").strip(),
            }
        )
    if not news_items:
        return _base_result("news", "finnhub", False, error="news_empty")
    result = _base_result("news", "finnhub", True, freshness=_article_freshness(news_items), source_count=len(news_items))
    result["news_items"] = news_items[:6]
    return result


def fetch_news_domain(ticker: str, *, providers: list[str] | None = None) -> dict[str, Any]:
    return _run_provider_chain(
        "news",
        {
            "google_news": lambda: _fetch_news_google(ticker),
            "yfinance": lambda: _fetch_news_yahoo(ticker),
            "finnhub": lambda: _fetch_news_finnhub(ticker),
        },
        providers=providers,
    )


def _fetch_macro_yahoo() -> dict[str, Any]:
    symbols = ["^GSPC", "^VIX", "^TNX", "DX-Y.NYB", "CL=F", "GC=F"]
    quotes = _yahoo_quotes(symbols)
    if not quotes:
        return _base_result("macro", "yfinance", False, error="macro_unavailable")

    def quote_field(symbol: str, key: str) -> float | None:
        return _safe_float((quotes.get(symbol) or {}).get(key))

    spx = quote_field("^GSPC", "regularMarketPrice")
    vix = quote_field("^VIX", "regularMarketPrice")
    tnx = quote_field("^TNX", "regularMarketPrice")
    dxy = quote_field("DX-Y.NYB", "regularMarketPrice")
    wti = quote_field("CL=F", "regularMarketPrice")
    gold = quote_field("GC=F", "regularMarketPrice")
    spx_chg = _pct_change(spx, quote_field("^GSPC", "regularMarketPreviousClose"))
    vix_chg = _pct_change(vix, quote_field("^VIX", "regularMarketPreviousClose"))
    dxy_chg = _pct_change(dxy, quote_field("DX-Y.NYB", "regularMarketPreviousClose"))

    if spx_chg is not None and vix_chg is not None and dxy_chg is not None:
        if spx_chg > 0 and vix_chg < 0 and dxy_chg <= 0.25:
            regime = "risk_on"
            macro_risk = "low"
        elif spx_chg < 0 and (vix_chg > 0 or dxy_chg > 0.5):
            regime = "risk_off"
            macro_risk = "high"
        else:
            regime = "mixed"
            macro_risk = "moderate"
    else:
        regime = "mixed"
        macro_risk = "moderate"

    result = _base_result("macro", "yfinance", True, freshness="live", source_count=len(quotes))
    result["macro_context"] = {
        "regime": regime,
        "macro_risk": macro_risk,
        "us10y_yield_pct": round(tnx, 3) if tnx is not None else None,
        "vix_level": round(vix, 3) if vix is not None else None,
        "dxy_level": round(dxy, 3) if dxy is not None else None,
        "spx_change_pct": round(spx_chg, 3) if spx_chg is not None else None,
        "wti_usd": round(wti, 3) if wti is not None else None,
        "gold_usd": round(gold, 3) if gold is not None else None,
        "as_of": now_iso(),
    }
    return result


def _augment_macro_with_finnhub(macro_context: dict[str, Any], providers: list[str]) -> tuple[dict[str, Any], list[str]]:
    if "finnhub" not in providers or not _finnhub_key():
        return macro_context, []
    today = datetime.now(UTC).date()
    payload = _finnhub_json(
        "/calendar/economic",
        {"from": today.isoformat(), "to": (today + timedelta(days=7)).isoformat()},
    )
    if not isinstance(payload, dict):
        return macro_context, []
    entries = payload.get("economicCalendar") or payload.get("data") or []
    if not isinstance(entries, list):
        return macro_context, []
    high_impact = []
    for item in entries[:20]:
        event = str(item.get("event", "") or item.get("indicator", "") or "").strip()
        if not event:
            continue
        importance = str(item.get("importance", "") or item.get("impact", "") or "").strip().lower()
        if importance in {"high", "3"} or "fed" in event.lower() or "cpi" in event.lower():
            high_impact.append(
                {
                    "event": event,
                    "date": str(item.get("date", "") or item.get("time", "") or "").strip(),
                    "country": str(item.get("country", "") or "").strip(),
                }
            )
    if high_impact:
        macro_context["upcoming_events"] = high_impact[:5]
        macro_context["event_calendar_count"] = len(high_impact)
        return macro_context, ["finnhub"]
    return macro_context, []


def _fetch_macro_fred() -> dict[str, Any]:
    if Fred is None:
        return _base_result("macro", "fred", False, error="fredapi_not_installed")
    if not _fred_key():
        return _base_result("macro", "fred", False, error="fred_api_key_missing")
    fedfunds = _fred_series_observations("FEDFUNDS", limit=18)
    dgs10 = _fred_series_observations("DGS10", limit=10)
    unrate = _fred_series_observations("UNRATE", limit=18)
    cpi = _fred_series_observations("CPIAUCSL", limit=18)
    gdp = _fred_series_observations("GDP", limit=8)
    if not fedfunds or not dgs10 or not unrate:
        return _base_result("macro", "fred", False, error="macro_unavailable")

    policy_rate = _safe_float(fedfunds[0].get("value"))
    ten_year = _safe_float(dgs10[0].get("value"))
    unemployment = _safe_float(unrate[0].get("value"))
    prior_unemployment = _safe_float(unrate[1].get("value")) if len(unrate) > 1 else None
    unemployment_delta = unemployment - prior_unemployment if unemployment is not None and prior_unemployment is not None else None

    cpi_yoy = None
    if len(cpi) >= 13:
        latest_cpi = _safe_float(cpi[0].get("value"))
        prior_cpi = _safe_float(cpi[12].get("value"))
        cpi_yoy = _pct_change(latest_cpi, prior_cpi)

    gdp_yoy = None
    if len(gdp) >= 5:
        latest_gdp = _safe_float(gdp[0].get("value"))
        prior_gdp = _safe_float(gdp[4].get("value"))
        gdp_yoy = _pct_change(latest_gdp, prior_gdp)

    yield_curve_bps = None
    if ten_year is not None and policy_rate is not None:
        yield_curve_bps = round((ten_year - policy_rate) * 100.0, 1)

    if (policy_rate is not None and policy_rate >= 5.0) or (unemployment_delta is not None and unemployment_delta >= 0.3):
        macro_risk = "high"
        regime = "restrictive"
    elif cpi_yoy is not None and cpi_yoy <= 3.0 and unemployment is not None and unemployment <= 4.3:
        macro_risk = "low"
        regime = "cooling_but_stable"
    else:
        macro_risk = "moderate"
        regime = "mixed"

    as_of_dates = [str(series[0].get("date", "") or "") for series in (fedfunds, dgs10, unrate, cpi, gdp) if series]
    macro_context = {
        "regime": regime,
        "macro_risk": macro_risk,
        "policy_rate_pct": round(policy_rate, 3) if policy_rate is not None else None,
        "us10y_yield_pct": round(ten_year, 3) if ten_year is not None else None,
        "unemployment_pct": round(unemployment, 3) if unemployment is not None else None,
        "unemployment_change_pct": round(unemployment_delta, 3) if unemployment_delta is not None else None,
        "cpi_yoy_pct": round(cpi_yoy, 3) if cpi_yoy is not None else None,
        "gdp_yoy_pct": round(gdp_yoy, 3) if gdp_yoy is not None else None,
        "yield_curve_bps": yield_curve_bps,
        "as_of": max(as_of_dates) if as_of_dates else now_iso(),
    }
    macro_context, augment_providers = _augment_macro_with_finnhub(macro_context, provider_chain("macro"))
    provider_name = "fred+finnhub" if augment_providers else "fred"
    result = _base_result("macro", provider_name, True, freshness="snapshot", source_count=5 + len(augment_providers))
    result["macro_context"] = macro_context
    return result


def fetch_macro_domain() -> dict[str, Any]:
    return _run_provider_chain(
        "macro",
        {
            "fred": _fetch_macro_fred,
            "yfinance": _fetch_macro_yahoo,
        },
    )


def _fetch_geo_news_finnhub(ticker: str) -> list[dict[str, Any]]:
    news_result = _fetch_news_finnhub(ticker)
    if not news_result.get("ok"):
        general_payload = _finnhub_json("/news", {"category": "general"})
        if not isinstance(general_payload, list):
            return []
        items = general_payload[:20]
    else:
        items = news_result.get("news_items", [])
        normalized = []
        for item in items:
            normalized.append(
                {
                    "title": item["title"],
                    "source": item.get("publisher", ""),
                    "link": item.get("link", ""),
                    "published_at": item.get("published_at", ""),
                    "freshness": item.get("freshness", "snapshot"),
                }
            )
        return normalized[:6]

    headlines: list[dict[str, Any]] = []
    for item in items:
        title = str(item.get("headline", "") or "").strip()
        if not title:
            continue
        title_lower = title.lower()
        if ticker.lower() not in title_lower and not any(token in title_lower for token in GEOPOLITICAL_RISK_KEYWORDS):
            continue
        published_at = _format_epoch(item.get("datetime"))
        headlines.append(
            {
                "title": title,
                "source": str(item.get("source", "") or "").strip(),
                "link": str(item.get("url", "") or "").strip(),
                "published_at": published_at or "",
                "freshness": _freshness_from_timestamp(published_at),
            }
        )
    return headlines[:6]


def _fetch_geo_news_google(ticker: str) -> list[dict[str, Any]]:
    query = urllib.parse.quote(f"{ticker} trade policy sanctions export controls antitrust regulation")
    xml_text = _request_text(f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en")
    if not xml_text:
        return []
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    headlines: list[dict[str, Any]] = []
    for item in root.findall("./channel/item")[:6]:
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        published_at = _parse_rfc822(item.findtext("pubDate"))
        headlines.append(
            {
                "title": title,
                "source": (item.findtext("source") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "published_at": published_at or "",
                "freshness": _freshness_from_timestamp(published_at),
            }
        )
    return headlines


def _fetch_acled_overlay() -> dict[str, Any]:
    countries = [item.strip() for item in os.environ.get("ATD_ACLED_COUNTRIES", "United States,China,Taiwan,Russia,Ukraine,Israel,Iran").split(",") if item.strip()]
    if not countries:
        return {"ok": False}
    start_date = (datetime.now(UTC) - timedelta(days=14)).date().isoformat()
    query_params = {
        "event_date": start_date,
        "event_date_where": ">=",
        "limit": "25",
        "fields": "event_date,event_type,sub_event_type,country,admin1,notes,fatalities",
    }
    if countries:
        query_params["country"] = "|".join(countries)
    payload = _acled_json(query_params)
    if not isinstance(payload, dict):
        return {"ok": False}
    items = payload.get("data") or []
    if not isinstance(items, list) or not items:
        return {"ok": False}
    events = []
    for item in items[:8]:
        note = str(item.get("notes", "") or "").strip()
        events.append(
            {
                "event_date": str(item.get("event_date", "") or "").strip(),
                "country": str(item.get("country", "") or "").strip(),
                "event_type": str(item.get("event_type", "") or "").strip(),
                "sub_event_type": str(item.get("sub_event_type", "") or "").strip(),
                "fatalities": int(_safe_float(item.get("fatalities")) or 0),
                "note": note[:200],
            }
        )
    return {"ok": bool(events), "events": events, "event_count": len(items)}


def fetch_geopolitical_domain(ticker: str) -> dict[str, Any]:
    chain = provider_chain("geopolitical")
    headlines: list[dict[str, Any]] = []
    provider_name = ""
    for provider in chain:
        if provider == "google_news":
            headlines = _fetch_geo_news_google(ticker)
        if provider == "finnhub":
            headlines = _fetch_geo_news_finnhub(ticker)
        if headlines:
            provider_name = provider
            break
    acled = _fetch_acled_overlay()
    if not headlines and not acled.get("ok"):
        return _base_result("geopolitical", "", False, error="geopolitical_unavailable")
    risk_hits = sum(1 for item in headlines if any(token in item.get("title", "").lower() for token in GEOPOLITICAL_RISK_KEYWORDS))
    if acled.get("ok") and int(acled.get("event_count", 0)) >= 12:
        risk_hits += 2
    if risk_hits >= 3:
        risk_level = "high"
    elif risk_hits >= 1:
        risk_level = "moderate"
    else:
        risk_level = "low"
    provider_parts = [provider_name] if provider_name else []
    if acled.get("ok"):
        provider_parts.append("acled")
    provider_label = "+".join(provider_parts) if provider_parts else "acled"
    result = _base_result(
        "geopolitical",
        provider_label,
        True,
        freshness=_article_freshness(headlines),
        source_count=len(headlines) + int(acled.get("event_count", 0)),
        degraded_reason="limited_sources" if not headlines else "",
    )
    result["geopolitical_context"] = {
        "risk_level": risk_level,
        "headline_count": len(headlines),
        "headlines": headlines,
        "event_count": int(acled.get("event_count", 0)) if acled.get("ok") else 0,
        "acled_events": acled.get("events", []) if acled.get("ok") else [],
        "as_of": now_iso(),
    }
    return result


def _estimate_trend_from_recommendation(recommendation_mean: float | None) -> str:
    if recommendation_mean is None:
        return "mixed"
    if recommendation_mean <= 2.0:
        return "positive"
    if recommendation_mean >= 3.0:
        return "negative"
    return "mixed"


def _valuation_state_from_pe(forward_pe: float | None) -> str:
    if forward_pe is None or forward_pe <= 0:
        return "uncertain"
    if forward_pe >= 35:
        return "rich"
    if forward_pe >= 22:
        return "fair_to_rich"
    return "reasonable"


def _build_fundamentals_payload(provider: str, *, revenue_growth: float | None, earnings_growth: float | None, gross_margin: float | None, forward_pe: float | None, trailing_pe: float | None, price_to_book: float | None, debt_to_equity: float | None, recommendation_mean: float | None, next_earnings_date: str = "") -> dict[str, Any]:
    demand_signal = "softening"
    if revenue_growth is not None and revenue_growth >= 0.12:
        demand_signal = "strong"
    elif revenue_growth is not None and revenue_growth >= 0.04:
        demand_signal = "steady"

    fundamentals = {
        "estimate_revision_trend": _estimate_trend_from_recommendation(recommendation_mean),
        "valuation_state": _valuation_state_from_pe(forward_pe),
        "gross_margin_watch": bool(gross_margin is not None and gross_margin < 0.52),
        "demand_signal": demand_signal,
        "revenue_growth_pct": round(revenue_growth * 100.0, 2) if revenue_growth is not None and abs(revenue_growth) <= 5 else round(revenue_growth, 2) if revenue_growth is not None else None,
        "earnings_growth_pct": round(earnings_growth * 100.0, 2) if earnings_growth is not None and abs(earnings_growth) <= 5 else round(earnings_growth, 2) if earnings_growth is not None else None,
        "gross_margin_pct": round(gross_margin * 100.0, 2) if gross_margin is not None and gross_margin <= 5 else round(gross_margin, 2) if gross_margin is not None else None,
        "forward_pe": round(forward_pe, 3) if forward_pe is not None else None,
        "trailing_pe": round(trailing_pe, 3) if trailing_pe is not None else None,
        "price_to_book": round(price_to_book, 3) if price_to_book is not None else None,
        "debt_to_equity": round(debt_to_equity, 3) if debt_to_equity is not None else None,
        "next_earnings_date": next_earnings_date or "",
        "as_of": now_iso(),
    }
    result = _base_result("fundamentals", provider, True, freshness="snapshot", source_count=1)
    result["fundamentals"] = fundamentals
    return result


def _fetch_fundamentals_yahoo(ticker: str) -> dict[str, Any]:
    if yf is None:
        return _base_result("fundamentals", "yfinance", False, error="fundamentals_unavailable")
    try:
        ticker_obj = yf.Ticker(ticker)
        info = dict(ticker_obj.info or {})
        fast_info = dict(ticker_obj.fast_info or {})
    except Exception:  # noqa: BLE001
        return _base_result("fundamentals", "yfinance", False, error="fundamentals_unavailable")
    if not info and not fast_info:
        return _base_result("fundamentals", "yfinance", False, error="fundamentals_empty")
    next_earnings = _format_epoch(info.get("earningsTimestamp")) or ""
    if not next_earnings:
        try:
            calendar = ticker_obj.calendar
            if calendar is not None and not getattr(calendar, "empty", True):
                index_value = calendar.index[0]
                if hasattr(index_value, "to_pydatetime"):
                    date_obj = index_value.to_pydatetime()
                    if getattr(date_obj, "tzinfo", None) is None:
                        date_obj = date_obj.replace(tzinfo=UTC)
                    next_earnings = date_obj.isoformat(timespec="seconds").replace("+00:00", "Z")
        except Exception:  # noqa: BLE001
            pass
    return _build_fundamentals_payload(
        "yfinance",
        revenue_growth=_safe_float(info.get("revenueGrowth")),
        earnings_growth=_safe_float(info.get("earningsGrowth")),
        gross_margin=_safe_float(info.get("grossMargins")),
        forward_pe=_safe_float(info.get("forwardPE")) or _safe_float(fast_info.get("forward_pe")),
        trailing_pe=_safe_float(info.get("trailingPE")) or _safe_float(fast_info.get("trailing_pe")),
        price_to_book=_safe_float(info.get("priceToBook")),
        debt_to_equity=_safe_float(info.get("debtToEquity")),
        recommendation_mean=_safe_float(info.get("recommendationMean")),
        next_earnings_date=next_earnings or "",
    )


def _fetch_fundamentals_finnhub(ticker: str) -> dict[str, Any]:
    metrics_payload = _finnhub_json("/stock/metric", {"symbol": ticker, "metric": "all"})
    if not isinstance(metrics_payload, dict):
        return _base_result("fundamentals", "finnhub", False, error="fundamentals_unavailable")
    metrics = metrics_payload.get("metric") or {}
    if not isinstance(metrics, dict) or not metrics:
        return _base_result("fundamentals", "finnhub", False, error="fundamentals_empty")
    rec_payload = _finnhub_json("/stock/recommendation", {"symbol": ticker})
    recommendation_mean = None
    if isinstance(rec_payload, list) and rec_payload:
        latest = rec_payload[0]
        total = sum(int(latest.get(key) or 0) for key in ("strongBuy", "buy", "hold", "sell", "strongSell"))
        if total > 0:
            weighted = (
                int(latest.get("strongBuy") or 0) * 1
                + int(latest.get("buy") or 0) * 2
                + int(latest.get("hold") or 0) * 3
                + int(latest.get("sell") or 0) * 4
                + int(latest.get("strongSell") or 0) * 5
            )
            recommendation_mean = weighted / total

    earnings_payload = _finnhub_json(
        "/calendar/earnings",
        {
            "symbol": ticker,
            "from": datetime.now(UTC).date().isoformat(),
            "to": (datetime.now(UTC).date() + timedelta(days=120)).isoformat(),
        },
    )
    next_earnings = ""
    if isinstance(earnings_payload, dict):
        calendar_items = earnings_payload.get("earningsCalendar") or earnings_payload.get("earnings") or []
        if isinstance(calendar_items, list) and calendar_items:
            next_earnings = str(calendar_items[0].get("date", "") or "").strip()

    return _build_fundamentals_payload(
        "finnhub",
        revenue_growth=_pick_float(metrics, "revenueGrowthTTMYoy", "revenueGrowthQuarterlyYoy", "revenueGrowth5Y"),
        earnings_growth=_pick_float(metrics, "epsGrowthTTMYoy", "epsGrowthQuarterlyYoy", "epsGrowth5Y"),
        gross_margin=_pick_float(metrics, "grossMarginTTM", "grossMargin5Y"),
        forward_pe=_pick_float(metrics, "peTTM", "peBasicExclExtraTTM", "peNormalizedAnnual"),
        trailing_pe=_pick_float(metrics, "peTTM", "peBasicExclExtraTTM"),
        price_to_book=_pick_float(metrics, "pbAnnual", "pbQuarterly"),
        debt_to_equity=_pick_float(metrics, "totalDebt/totalEquityAnnual", "totalDebt/totalEquityQuarterly"),
        recommendation_mean=recommendation_mean,
        next_earnings_date=next_earnings,
    )


def fetch_fundamentals_domain(ticker: str) -> dict[str, Any]:
    return _run_provider_chain(
        "fundamentals",
        {
            "yfinance": lambda: _fetch_fundamentals_yahoo(ticker),
            "finnhub": lambda: _fetch_fundamentals_finnhub(ticker),
        },
    )


def _lexical_sentiment_score(text: str) -> float:
    positives = ("beat", "bull", "upgrade", "long", "buy", "strong", "breakout", "outperform", "growth", "surge")
    negatives = ("miss", "bear", "downgrade", "short", "sell", "weak", "breakdown", "underperform", "fraud", "plunge")
    lower = text.lower()
    pos = sum(1 for token in positives if token in lower)
    neg = sum(1 for token in negatives if token in lower)
    if pos + neg == 0:
        return 0.5
    return _clamp(0.5 + ((pos - neg) / (2.0 * (pos + neg))), 0.02, 0.98)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    candidate = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        pass
    match = re.search(r"\{[\s\S]*\}", candidate)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _label_from_sentiment_score(score: float) -> str:
    if score >= 0.65:
        return "long"
    if score <= 0.35:
        return "short"
    return "neutral"


def _stocktwits_access_token() -> str:
    return os.environ.get("STOCKTWITS_ACCESS_TOKEN", "").strip()


def _fetch_social_stocktwits(ticker: str) -> dict[str, Any]:
    symbol = str(ticker or "").strip().upper()
    if not symbol:
        return _base_result("social", "stocktwits", False, error="ticker_missing")
    params: dict[str, str] = {}
    access_token = _stocktwits_access_token()
    if access_token:
        params["access_token"] = access_token
    query = urllib.parse.urlencode(params)
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{urllib.parse.quote(symbol)}.json"
    if query:
        url = f"{url}?{query}"
    payload = _request_json(url, headers={"User-Agent": USER_AGENT})
    if not isinstance(payload, dict):
        return _base_result("social", "stocktwits", False, error="social_unavailable")
    messages = payload.get("messages") or []
    if not isinstance(messages, list) or not messages:
        return _base_result("social", "stocktwits", False, error="social_empty")

    now_dt = datetime.now(UTC)
    posts: list[dict[str, Any]] = []
    for message in messages[:40]:
        body = str(message.get("body", "") or "").strip()
        if not body:
            continue
        sentiment = ((message.get("entities") or {}).get("sentiment") or {})
        basic = str(sentiment.get("basic", "") or "").strip().lower()
        if basic == "bullish":
            sentiment_score = 0.8
        elif basic == "bearish":
            sentiment_score = 0.2
        else:
            sentiment_score = _lexical_sentiment_score(body)
        created_at = str(message.get("created_at", "") or "").strip()
        created_dt = _parse_datetime_to_utc(created_at)
        created_iso = created_dt.isoformat(timespec="seconds").replace("+00:00", "Z") if created_dt else ""
        message_id = str(message.get("id", "") or "").strip()
        likes = int(((message.get("likes") or {}).get("total")) or 0)
        comments = int(((message.get("comments") or {}).get("total")) or 0)
        posts.append(
            {
                "title": body,
                "subreddit": "stocktwits",
                "score": likes,
                "comments": comments,
                "created_at": created_iso,
                "url": f"https://stocktwits.com/message/{message_id}" if message_id else "",
                "sentiment_score": sentiment_score,
            }
        )
    if not posts:
        return _base_result("social", "stocktwits", False, error="social_empty")

    recent_posts = []
    for item in posts:
        created_dt = _parse_datetime_to_utc(item.get("created_at"))
        if created_dt is None:
            continue
        if (now_dt - created_dt).total_seconds() / 3600.0 <= 24:
            recent_posts.append(item)
    samples = recent_posts if recent_posts else posts[:12]
    sentiment_score = _mean([float(item.get("sentiment_score") or 0.5) for item in samples]) or 0.5
    if sentiment_score >= 0.65:
        label = "long"
    elif sentiment_score <= 0.35:
        label = "short"
    else:
        label = "neutral"

    result = _base_result("social", "stocktwits", True, freshness="live", source_count=len(samples))
    result["social_context"] = {
        "sentiment_score": round(sentiment_score, 3),
        "sentiment_label": label,
        "mention_count_24h": len(recent_posts),
        "mention_velocity_per_hour": round(len(recent_posts) / 24.0, 3),
        "posts": [
            {
                "title": item.get("title", ""),
                "subreddit": "stocktwits",
                "score": item.get("score", 0),
                "comments": item.get("comments", 0),
                "created_at": item.get("created_at", ""),
                "url": item.get("url", ""),
            }
            for item in samples[:8]
        ],
        "as_of": now_iso(),
    }
    return result


def _stocktwits_snapshot(ticker: str) -> dict[str, Any]:
    result = _fetch_social_stocktwits(ticker)
    return {
        "ok": bool(result.get("ok", False)),
        "provider": "stocktwits",
        "error": str(result.get("error", "") or ""),
        "source_count": int(result.get("source_count", 0) or 0),
        "freshness": str(result.get("freshness", "snapshot") or "snapshot"),
        "as_of": str(result.get("as_of", now_iso()) or now_iso()),
        "social_context": dict(result.get("social_context") or {}),
    }


def _fetch_social_news_proxy(ticker: str) -> dict[str, Any]:
    # Last-resort proxy when direct social APIs are unavailable: derive crowd tone from available news feeds.
    news_result = fetch_news_domain(ticker)
    if not news_result.get("ok"):
        return _base_result("social", "news_proxy", False, error="social_unavailable")
    items = list(news_result.get("news_items") or [])[:8]
    if not items:
        return _base_result("social", "news_proxy", False, error="social_empty")
    scores = [_lexical_sentiment_score(item.get("title", "")) for item in items]
    sentiment_score = _mean(scores) or 0.5
    if sentiment_score >= 0.65:
        label = "long"
    elif sentiment_score <= 0.35:
        label = "short"
    else:
        label = "neutral"
    result = _base_result("social", "news_proxy", True, freshness=news_result.get("freshness", "snapshot"), source_count=len(items), degraded_reason="proxy_from_news")
    result["social_context"] = {
        "sentiment_score": round(sentiment_score, 3),
        "sentiment_label": label,
        "mention_count_24h": len(items),
        "mention_velocity_per_hour": round(len(items) / 24.0, 3),
        "posts": [
            {
                "title": item.get("title", ""),
                "created_at": item.get("published_at", ""),
                "score": 1,
                "comments": 0,
                "url": item.get("link", ""),
            }
            for item in items
        ],
        "as_of": now_iso(),
    }
    return result


def _fetch_social_yfinance_proxy(ticker: str) -> dict[str, Any]:
    # Backward-compatible alias for existing ATD_PROVIDER_SOCIAL values.
    return _fetch_social_news_proxy(ticker)


def fetch_social_domain(ticker: str, run_id: str | None = None) -> dict[str, Any]:
    _ = run_id
    return _fetch_social_stocktwits(ticker)
