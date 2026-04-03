# OCI Deployment Hardening Guide

Date: 2026-03-31

## Objective
Provide a reliable, customer-facing demo deployment baseline on OCI with health checks, environment validation, and audit visibility.

## Startup validation

The app validates OCI-related environment configuration at startup.

Default required variables:
- `OCI_REGION`
- `OCI_COMPARTMENT_OCID`
- `OCI_GENAI_ENDPOINT`
- `OCI_GENAI_MODEL_ID`

Controls:
- `STRICT_ENV_VALIDATION=1`: startup fails if required vars are missing.
- `REQUIRED_ENV_VARS`: optional comma-separated override for required keys.
- `OCI_GENAI_API_KEY`: optional Bearer mode credential. Not required in instance-principal/no-key environments.
- `OCI_GENAI_CHAT_PATH`: chat endpoint path override (default `/v1/chat/completions`).
- `OCI_GENAI_TIMEOUT_S`: request timeout in seconds (default `8`).
- `OCI_GENAI_ENABLE`: set to `0` to disable live LLM calls and force deterministic fallback.
- `OCI_GENAI_USE_OCI_SDK`: when `1` (default), no-key mode attempts OCI SDK signed requests (instance/resource principal) before unsigned fallbacks.

Example:

```bash
export OCI_REGION=us-chicago-1
export OCI_COMPARTMENT_OCID=ocid1.compartment.oc1..<redacted>
export OCI_GENAI_ENDPOINT=https://inference.generativeai.us-chicago-1.oci.oraclecloud.com
export OCI_GENAI_MODEL_ID=cohere.command-r-plus
export STRICT_ENV_VALIDATION=1
python3 app.py
```

## Health endpoints

- `GET /api/health`
  - High-level readiness for load balancers and uptime checks.
  - Returns `200` when healthy and `503` when degraded.
- `GET /api/health?verbose=1`
  - Includes detailed environment status (required/missing/present keys).

Checks currently included:
- Environment completeness
- Runtime adapter initialization (`wayflow`, `langgraph`)
- Writable run storage under `var/runs`
- Scenario catalog loading
- OCI GenAI configuration mode (`api_key`, instance-principal SDK, or unsigned fallback mode)

## Audit visibility

Cross-run audit feed:
- `GET /api/audit?limit=50`

Captured audit event types:
- `run.started`
- `approval.requested`
- `approval.resolved`
- `ticket.updated`
- `run.completed`
- `run.failed`

Storage:
- `var/audit/audit-log.jsonl`

## Container hardening baseline

The Docker image includes a container `HEALTHCHECK` against `/api/health`.

Recommended OCI deployment settings:
- Run with non-root user in production image variants.
- Mount writeable storage for `var/` if persistence is required.
- Set `STRICT_ENV_VALIDATION=1` in non-dev environments.
- Route logs and audit artifacts to OCI Logging / Object Storage retention workflows.

## Operational checklist

1. Set required OCI env vars.
2. Enable `STRICT_ENV_VALIDATION=1`.
3. Deploy container and verify `/api/health`.
4. Execute one committee run and verify `/api/audit`.
5. Confirm replay load works through `/api/runs`.
6. Verify run-level `summary.llm.live_count > 0` for live LLM generation in customer demos.
