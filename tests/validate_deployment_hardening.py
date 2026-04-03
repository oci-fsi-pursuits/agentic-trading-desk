from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import health_payload
from runtime.common.env_validation import collect_environment_status

REQUIRED_KEYS = [
    "OCI_REGION",
    "OCI_COMPARTMENT_OCID",
    "OCI_GENAI_ENDPOINT",
    "OCI_GENAI_MODEL_ID",
]


@contextmanager
def temp_env(overrides: dict[str, str | None]):
    original = {key: os.environ.get(key) for key in overrides}
    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> None:
    clear_required = {key: None for key in REQUIRED_KEYS}
    clear_required["STRICT_ENV_VALIDATION"] = "0"
    with temp_env(clear_required):
        env_status = collect_environment_status()
        assert env_status["status"] == "degraded"
        assert set(REQUIRED_KEYS).issubset(set(env_status["missing_required"]))

    configured = {
        "OCI_REGION": "us-chicago-1",
        "OCI_COMPARTMENT_OCID": "ocid1.compartment.oc1..demo",
        "OCI_GENAI_ENDPOINT": "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com",
        "OCI_GENAI_MODEL_ID": "cohere.command-r-plus",
        "STRICT_ENV_VALIDATION": "1",
    }
    with temp_env(configured):
        env_status = collect_environment_status()
        assert env_status["status"] == "ok"
        assert not env_status["missing_required"]
        payload = health_payload()
        assert payload["status"] == "ok"
        check_names = {item["name"] for item in payload["checks"]}
        assert {"environment", "filesystem", "scenarios", "llm.oci_genai", "runtime.wayflow", "runtime.langgraph"}.issubset(check_names)

    print('{"status": "ok"}')


if __name__ == "__main__":
    main()
