from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from runtime.common.data_providers import (
    fetch_chart_snapshot,
    fetch_fundamentals_domain,
    fetch_news_domain,
)

try:
    import yfinance as yf
except Exception:  # noqa: BLE001
    yf = None


def _safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _ticker_arg(args: dict[str, Any]) -> str:
    ticker = str(
        args.get("ticker")
        or args.get("symbol")
        or args.get("instrument")
        or ""
    ).strip().upper()
    if not ticker:
        raise ValueError("missing ticker")
    return ticker


def _int_arg(args: dict[str, Any], key: str, default: int, *, low: int = 1, high: int = 365) -> int:
    raw = args.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(default)
    return max(int(low), min(int(high), value))


def _parse_datetime_ymd(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _parse_datetime_any(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    candidates = [raw]
    if raw.endswith("Z"):
        candidates.append(raw[:-1] + "+00:00")
    if len(raw) >= 5 and raw[-5] in {"+", "-"} and raw[-3] != ":":
        candidates.append(f"{raw[:-2]}:{raw[-2:]}")
    for candidate in candidates:
        try:
            parsed = datetime.fromisoformat(candidate)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except ValueError:
            continue
    return None


def _to_iso_utc(value: datetime | None) -> str:
    if value is None:
        return ""
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _extract_yfinance_news_article(article: dict[str, Any]) -> dict[str, Any]:
    content = article.get("content") if isinstance(article.get("content"), dict) else article
    title = str((content or {}).get("title", "") or "").strip()
    summary = str((content or {}).get("summary", "") or "").strip()
    provider_obj = (content or {}).get("provider")
    if isinstance(provider_obj, dict):
        publisher = str(provider_obj.get("displayName", "") or "").strip()
    else:
        publisher = str((content or {}).get("publisher", "") or "").strip()
    url_obj = (content or {}).get("canonicalUrl") or (content or {}).get("clickThroughUrl") or {}
    if isinstance(url_obj, dict):
        link = str(url_obj.get("url", "") or "").strip()
    else:
        link = str((content or {}).get("link", "") or "").strip()
    published_dt = _parse_datetime_any(str((content or {}).get("pubDate", "") or ""))
    return {
        "title": title,
        "summary": summary,
        "publisher": publisher,
        "link": link,
        "published_at": _to_iso_utc(published_dt),
        "published_dt": published_dt,
    }


def _range_key_for_lookback(lookback_days: int) -> str:
    if lookback_days <= 2:
        return "1d"
    if lookback_days <= 7:
        return "5d"
    if lookback_days <= 45:
        return "30d"
    if lookback_days <= 240:
        return "180d"
    return "1y"


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (float(period) + 1.0)
    result = [values[0]]
    for value in values[1:]:
        result.append((alpha * value) + ((1.0 - alpha) * result[-1]))
    return result


def _sma(values: list[float], period: int) -> list[float]:
    output: list[float] = []
    window_sum = 0.0
    for idx, value in enumerate(values):
        window_sum += value
        if idx >= period:
            window_sum -= values[idx - period]
        denom = period if idx + 1 >= period else idx + 1
        output.append(window_sum / float(denom))
    return output


def _rsi(values: list[float], period: int = 14) -> list[float]:
    if len(values) < 2:
        return [50.0 for _ in values]
    gains = [0.0]
    losses = [0.0]
    for idx in range(1, len(values)):
        delta = values[idx] - values[idx - 1]
        gains.append(max(0.0, delta))
        losses.append(max(0.0, -delta))
    avg_gain = _sma(gains, period)
    avg_loss = _sma(losses, period)
    rsi_values: list[float] = []
    for gain, loss in zip(avg_gain, avg_loss):
        if loss <= 1e-9:
            rsi_values.append(100.0 if gain > 0 else 50.0)
            continue
        rs = gain / loss
        rsi_values.append(100.0 - (100.0 / (1.0 + rs)))
    return rsi_values


def _std_window(values: list[float], period: int) -> list[float]:
    output: list[float] = []
    for idx in range(len(values)):
        start = max(0, idx - period + 1)
        window = values[start : idx + 1]
        if not window:
            output.append(0.0)
            continue
        mean = sum(window) / float(len(window))
        variance = sum((item - mean) ** 2 for item in window) / float(len(window))
        output.append(math.sqrt(max(0.0, variance)))
    return output


def _atr_from_close(values: list[float], period: int = 14) -> list[float]:
    if len(values) < 2:
        return [0.0 for _ in values]
    true_range = [0.0]
    for idx in range(1, len(values)):
        true_range.append(abs(values[idx] - values[idx - 1]))
    return _sma(true_range, period)


def _json_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def _tool_get_stock_data(args: dict[str, Any]) -> str:
    ticker = _ticker_arg(args)
    lookback_days = _int_arg(args, "lookback_days", 90, low=5, high=365)
    range_key = _range_key_for_lookback(lookback_days)
    snapshot = fetch_chart_snapshot(ticker, range_key=range_key)
    points = list(snapshot.get("points") or [])
    if not points:
        return _json_payload({"ok": False, "error": "market_data_unavailable", "ticker": ticker})
    bars = points[-min(len(points), max(20, lookback_days)) :]
    rows = []
    for point in bars:
        try:
            ts = int(point.get("ts"))
        except (TypeError, ValueError):
            continue
        dt = datetime.fromtimestamp(ts, tz=UTC)
        rows.append({"date": dt.date().isoformat(), "close": _safe_float(point.get("close"))})
    return _json_payload(
        {
            "ok": True,
            "ticker": ticker,
            "lookback_days": lookback_days,
            "as_of": str(snapshot.get("as_of", "") or ""),
            "bars": rows,
            "latest_close": _safe_float(snapshot.get("regular_market_price")),
            "previous_close": _safe_float(snapshot.get("previous_close")),
        }
    )


def _tool_get_indicators(args: dict[str, Any]) -> str:
    ticker = _ticker_arg(args)
    lookback_days = _int_arg(args, "lookback_days", 120, low=30, high=365)
    range_key = _range_key_for_lookback(lookback_days)
    snapshot = fetch_chart_snapshot(ticker, range_key=range_key)
    points = list(snapshot.get("points") or [])
    closes = [_safe_float(point.get("close")) for point in points]
    close_values = [float(value) for value in closes if value is not None]
    if len(close_values) < 10:
        return _json_payload({"ok": False, "error": "insufficient_price_history", "ticker": ticker})

    raw_indicators = args.get("indicators", args.get("indicator", []))
    if isinstance(raw_indicators, str):
        requested = [item.strip().lower() for item in raw_indicators.split(",") if item.strip()]
    elif isinstance(raw_indicators, list):
        requested = [str(item).strip().lower() for item in raw_indicators if str(item).strip()]
    else:
        requested = []
    if not requested:
        requested = ["sma_20", "ema_20", "rsi_14", "macd", "bollinger_bands", "atr_14", "support_resistance"]

    sma20 = _sma(close_values, 20)
    ema12 = _ema(close_values, 12)
    ema20 = _ema(close_values, 20)
    ema26 = _ema(close_values, 26)
    rsi14 = _rsi(close_values, 14)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    macd_signal = _ema(macd_line, 9)
    macd_hist = [a - b for a, b in zip(macd_line, macd_signal)]
    std20 = _std_window(close_values, 20)
    boll_mid = sma20
    boll_up = [m + (2.0 * s) for m, s in zip(boll_mid, std20)]
    boll_low = [m - (2.0 * s) for m, s in zip(boll_mid, std20)]
    atr14 = _atr_from_close(close_values, 14)
    support = min(close_values[-20:]) if len(close_values) >= 20 else min(close_values)
    resistance = max(close_values[-20:]) if len(close_values) >= 20 else max(close_values)

    latest = {
        "sma_20": round(sma20[-1], 6),
        "ema_20": round(ema20[-1], 6),
        "ema_12": round(ema12[-1], 6),
        "ema_26": round(ema26[-1], 6),
        "rsi_14": round(rsi14[-1], 6),
        "macd": round(macd_line[-1], 6),
        "macd_signal": round(macd_signal[-1], 6),
        "macd_hist": round(macd_hist[-1], 6),
        "bollinger_mid": round(boll_mid[-1], 6),
        "bollinger_upper": round(boll_up[-1], 6),
        "bollinger_lower": round(boll_low[-1], 6),
        "atr_14": round(atr14[-1], 6),
        "support_level": round(float(support), 6),
        "resistance_level": round(float(resistance), 6),
        "close": round(close_values[-1], 6),
    }
    requested_payload = {key: value for key, value in latest.items() if any(req in key for req in requested)}
    if not requested_payload:
        requested_payload = latest
    return _json_payload(
        {
            "ok": True,
            "ticker": ticker,
            "lookback_days": lookback_days,
            "as_of": str(snapshot.get("as_of", "") or ""),
            "indicators": requested_payload,
            "notes": [
                "ATR is estimated from close-to-close true range due missing high/low bars in this tool path.",
            ],
        }
    )


def _tool_get_news(args: dict[str, Any]) -> str:
    ticker = _ticker_arg(args)
    limit = _int_arg(args, "limit", 8, low=1, high=20)
    result = fetch_news_domain(ticker, providers=["yfinance"])
    items = list(result.get("news_items") or [])
    start_dt = _parse_datetime_ymd(str(args.get("start_date", "") or ""))
    end_dt = _parse_datetime_ymd(str(args.get("end_date", "") or ""))
    filtered: list[dict[str, Any]] = []
    for item in items:
        published = _parse_datetime_ymd(str(item.get("published_at", "") or ""))
        if start_dt and published and published < start_dt:
            continue
        if end_dt and published and published > (end_dt + timedelta(days=1)):
            continue
        filtered.append(
            {
                "title": str(item.get("title", "") or "").strip(),
                "publisher": str(item.get("publisher", "") or "").strip(),
                "published_at": str(item.get("published_at", "") or "").strip(),
                "link": str(item.get("link", "") or "").strip(),
            }
        )
    rows = [item for item in filtered if item.get("title")][:limit]
    return _json_payload(
        {
            "ok": bool(result.get("ok", False)),
            "ticker": ticker,
            "provider": str(result.get("provider", "yfinance") or "yfinance"),
            "freshness": str(result.get("freshness", "snapshot") or "snapshot"),
            "as_of": str(result.get("as_of", "") or ""),
            "items": rows,
        }
    )


def _tool_get_global_news(args: dict[str, Any]) -> str:
    limit = _int_arg(args, "limit", 10, low=1, high=20)
    look_back_days = _int_arg(args, "look_back_days", 7, low=1, high=14)
    curr_date = str(args.get("curr_date", "") or "").strip()
    current_dt = _parse_datetime_ymd(curr_date) or datetime.now(UTC)
    if yf is None:
        return _json_payload(
            {
                "ok": False,
                "provider": "yfinance",
                "look_back_days": look_back_days,
                "items": [],
                "error": "yfinance_not_installed",
            }
        )
    search_queries = [
        "stock market economy",
        "Federal Reserve interest rates",
        "inflation economic outlook",
        "global markets trading",
    ]
    dedup: dict[str, dict[str, Any]] = {}
    cutoff = current_dt - timedelta(days=look_back_days)
    for query in search_queries:
        try:
            search = yf.Search(
                query=query,
                news_count=limit,
                enable_fuzzy_query=True,
            )
        except Exception:
            continue
        for article in list(getattr(search, "news", None) or []):
            parsed = _extract_yfinance_news_article(article if isinstance(article, dict) else {})
            title = str(parsed.get("title", "") or "").strip()
            if not title:
                continue
            published_dt = parsed.get("published_dt")
            if not isinstance(published_dt, datetime):
                published_dt = None
            if published_dt and published_dt < cutoff:
                continue
            if published_dt and published_dt > (current_dt + timedelta(days=1)):
                continue
            key = title.lower()
            if key in dedup:
                continue
            dedup[key] = {
                "title": title,
                "publisher": str(parsed.get("publisher", "") or "").strip(),
                "published_at": str(parsed.get("published_at", "") or "").strip(),
                "link": str(parsed.get("link", "") or "").strip(),
                "summary": str(parsed.get("summary", "") or "").strip(),
                "query_context": query,
            }
        if len(dedup) >= max(limit, 10):
            break
    rows = sorted(
        dedup.values(),
        key=lambda item: str(item.get("published_at", "") or ""),
        reverse=True,
    )[:limit]
    return _json_payload(
        {
            "ok": bool(rows),
            "provider": "yfinance",
            "look_back_days": look_back_days,
            "curr_date": current_dt.date().isoformat(),
            "queries_used": search_queries,
            "items": rows,
        }
    )


def _tool_get_fundamentals(args: dict[str, Any]) -> str:
    ticker = _ticker_arg(args)
    result = fetch_fundamentals_domain(ticker)
    fundamentals = dict(result.get("fundamentals") or {})
    return _json_payload(
        {
            "ok": bool(result.get("ok", False)),
            "ticker": ticker,
            "provider": str(result.get("provider", "") or ""),
            "as_of": str(result.get("as_of", "") or ""),
            "freshness": str(result.get("freshness", "snapshot") or "snapshot"),
            "fundamentals": fundamentals,
            "error": str(result.get("error", "") or ""),
        }
    )


def _financial_statement_csv(ticker: str, statement: str, period: str) -> str:
    if yf is None:
        return _json_payload({"ok": False, "ticker": ticker, "error": "yfinance_not_installed"})
    try:
        ticker_obj = yf.Ticker(ticker)
    except Exception as exc:  # noqa: BLE001
        return _json_payload({"ok": False, "ticker": ticker, "error": f"ticker_init_failed:{exc}"})
    period_key = str(period or "quarterly").strip().lower()
    is_quarterly = period_key.startswith("q")
    try:
        if statement == "balance_sheet":
            table = ticker_obj.quarterly_balance_sheet if is_quarterly else ticker_obj.balance_sheet
        elif statement == "cashflow":
            table = ticker_obj.quarterly_cashflow if is_quarterly else ticker_obj.cashflow
        elif statement == "income_statement":
            table = ticker_obj.quarterly_income_stmt if is_quarterly else ticker_obj.income_stmt
        else:
            return _json_payload({"ok": False, "ticker": ticker, "error": "unsupported_statement"})
    except Exception as exc:  # noqa: BLE001
        return _json_payload({"ok": False, "ticker": ticker, "error": f"statement_fetch_failed:{exc}"})
    if table is None or getattr(table, "empty", True):
        return _json_payload({"ok": False, "ticker": ticker, "error": "statement_unavailable"})
    try:
        csv_data = table.iloc[:, :6].to_csv()
    except Exception as exc:  # noqa: BLE001
        return _json_payload({"ok": False, "ticker": ticker, "error": f"statement_format_failed:{exc}"})
    return _json_payload(
        {
            "ok": True,
            "ticker": ticker,
            "statement": statement,
            "period": "quarterly" if is_quarterly else "annual",
            "as_of": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "csv": csv_data[:18000],
        }
    )


def _tool_get_balance_sheet(args: dict[str, Any]) -> str:
    ticker = _ticker_arg(args)
    period = str(args.get("period") or args.get("freq") or "quarterly")
    return _financial_statement_csv(ticker, "balance_sheet", period)


def _tool_get_cashflow(args: dict[str, Any]) -> str:
    ticker = _ticker_arg(args)
    period = str(args.get("period") or args.get("freq") or "quarterly")
    return _financial_statement_csv(ticker, "cashflow", period)


def _tool_get_income_statement(args: dict[str, Any]) -> str:
    ticker = _ticker_arg(args)
    period = str(args.get("period") or args.get("freq") or "quarterly")
    return _financial_statement_csv(ticker, "income_statement", period)


_LOCAL_TOOLS: dict[str, Callable[[dict[str, Any]], str]] = {
    "get_stock_data": _tool_get_stock_data,
    "get_indicators": _tool_get_indicators,
    "get_news": _tool_get_news,
    "get_global_news": _tool_get_global_news,
    "get_fundamentals": _tool_get_fundamentals,
    "get_balance_sheet": _tool_get_balance_sheet,
    "get_cashflow": _tool_get_cashflow,
    "get_income_statement": _tool_get_income_statement,
}


MARKET_ANALYST_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_stock_data",
        "description": "Fetch recent ticker price bars for technical analysis.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "lookback_days": {"type": "integer", "minimum": 5, "maximum": 365},
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_indicators",
        "description": "Compute technical indicators for the ticker.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "indicators": {
                    "anyOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "string"},
                    ]
                },
                "lookback_days": {"type": "integer", "minimum": 30, "maximum": 365},
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
]


NEWS_ANALYST_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_news",
        "description": "Fetch ticker-specific news headlines and article links.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "start_date": {"type": "string"},
                "end_date": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_global_news",
        "description": "Fetch broad market and macro headline context.",
        "parameters": {
            "type": "object",
            "properties": {
                "curr_date": {"type": "string"},
                "look_back_days": {"type": "integer", "minimum": 1, "maximum": 14},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "additionalProperties": False,
        },
    },
]


FUNDAMENTALS_ANALYST_TOOL_SPECS: list[dict[str, Any]] = [
    {
        "type": "function",
        "name": "get_fundamentals",
        "description": "Fetch company fundamentals and valuation metrics.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "as_of": {"type": "string"},
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_balance_sheet",
        "description": "Fetch company balance sheet data.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "period": {"type": "string", "enum": ["quarterly", "annual"]},
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_cashflow",
        "description": "Fetch company cash flow statement data.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "period": {"type": "string", "enum": ["quarterly", "annual"]},
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "get_income_statement",
        "description": "Fetch company income statement data.",
        "parameters": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "period": {"type": "string", "enum": ["quarterly", "annual"]},
            },
            "required": ["ticker"],
            "additionalProperties": False,
        },
    },
]


def tool_specs_for_role(role_id: str) -> list[dict[str, Any]]:
    if role_id == "market_analyst":
        return list(MARKET_ANALYST_TOOL_SPECS)
    if role_id == "news_analyst":
        return list(NEWS_ANALYST_TOOL_SPECS)
    if role_id == "macro_economist":
        return list(NEWS_ANALYST_TOOL_SPECS)
    if role_id == "fundamentals_analyst":
        return list(FUNDAMENTALS_ANALYST_TOOL_SPECS)
    return []


def execute_local_tool(name: str, arguments: dict[str, Any]) -> str:
    tool = _LOCAL_TOOLS.get(str(name or "").strip())
    if tool is None:
        return _json_payload({"ok": False, "error": f"unknown_tool:{name}"})
    try:
        return str(tool(dict(arguments or {})))
    except Exception as exc:  # noqa: BLE001
        return _json_payload({"ok": False, "error": f"tool_execution_failed:{exc}"})
