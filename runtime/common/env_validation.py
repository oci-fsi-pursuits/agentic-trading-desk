from __future__ import annotations

import os
from pathlib import Path
from typing import Any

DEFAULT_REQUIRED_ENV_VARS = [
    "OCI_REGION",
    "OCI_COMPARTMENT_OCID",
    "OCI_GENAI_ENDPOINT",
    "OCI_GENAI_MODEL_ID",
]

OPTIONAL_ENV_VARS = [
    "OCI_TENANCY_OCID",
    "OCI_USER_OCID",
    "OCI_FINGERPRINT",
    "OCI_KEY_FILE",
    "OCI_CONFIG_PROFILE",
    "OCI_VAULT_SECRET_OCID",
    "OCI_LOG_GROUP_OCID",
    "OCI_LOG_OCID",
    "OCI_GENAI_API_KEY",
    "OCI_GENAI_CHAT_PATH",
    "OCI_GENAI_RESPONSES_PATH",
    "OCI_OPENAI_PROJECT",
    "OCI_GENAI_TIMEOUT_S",
    "OCI_GENAI_ENABLE",
    "OCI_GENAI_AUTH_MODE",
    "OCI_CONFIG_FILE",
    "ATD_ENABLE_LIVE_CONTEXT",
    "ATD_PROVIDER_CHART",
    "ATD_PROVIDER_MARKET",
    "ATD_PROVIDER_NEWS",
    "ATD_PROVIDER_MACRO",
    "ATD_PROVIDER_GEOPOLITICAL",
    "ATD_PROVIDER_FUNDAMENTALS",
    "ATD_PROVIDER_SOCIAL",
    "ATD_DATA_LOG",
    "ATD_X_SEARCH_LOOKBACK_DAYS",
    "FINNHUB_API_KEY",
    "FRED_API_KEY",
    "STOCKTWITS_ACCESS_TOKEN",
    "ACLED_ACCESS_TOKEN",
    "ACLED_EMAIL",
    "ACLED_PASSWORD",
    "ATD_ACLED_COUNTRIES",
]


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and ((value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'"))):
        return value[1:-1]
    return value


def load_env_file(path: str | Path = ".env", override: bool = False) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return {"path": str(target), "loaded": [], "skipped": [], "exists": False}

    loaded: list[str] = []
    skipped: list[str] = []
    for raw_line in target.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = _strip_wrapping_quotes(value.strip())
        if not override and key in os.environ:
            skipped.append(key)
            continue
        os.environ[key] = value
        loaded.append(key)

    return {"path": str(target), "loaded": loaded, "skipped": skipped, "exists": True}


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def required_env_vars() -> list[str]:
    configured = os.environ.get("REQUIRED_ENV_VARS", "")
    if not configured.strip():
        return list(DEFAULT_REQUIRED_ENV_VARS)
    return [item.strip() for item in configured.split(",") if item.strip()]


def collect_environment_status() -> dict[str, Any]:
    required = required_env_vars()
    missing_required = [name for name in required if not os.environ.get(name)]
    present_required = [name for name in required if os.environ.get(name)]
    present_optional = [name for name in OPTIONAL_ENV_VARS if os.environ.get(name)]

    if missing_required:
        status = "degraded"
        message = f"Missing required env vars: {', '.join(missing_required)}"
    else:
        status = "ok"
        message = "Environment configuration is complete for OCI demo mode."

    return {
        "status": status,
        "message": message,
        "required": required,
        "missing_required": missing_required,
        "present_required": present_required,
        "present_optional": present_optional,
        "strict_mode": parse_bool(os.environ.get("STRICT_ENV_VALIDATION"), default=False),
    }
