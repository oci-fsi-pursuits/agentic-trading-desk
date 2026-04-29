# Agentic Trading Desk

Buy-side investment committee simulator with:
- authored desk and flow definitions,
- canonical typed object contracts,
- canonical AG-UI event envelopes,
- local `wayflow` and `langgraph` runtime adapters,
- conformance tests for stage and artifact parity,
- a minimal Python web app and static frontend.

## What is real vs simulated

Real in this repo:
- authored role and flow definitions under `authoring/`
- exported Oracle Open Agent Spec-shaped artifact under `spec/exported/`
- Agent and Flow components with explicit per-role tool declarations
- typed contracts under `contracts/`
- runtime adapters that emit canonical events and materialize objects
- quant execution via a bounded Python subprocess
- conformance tests across both runtimes

Simulated in this repo:
- `wayflow` and `langgraph` are local adapter shims, not vendored copies of the official external packages
- no live market data feed
- no real OCI deployment from inside this sandbox

That is deliberate. The repo exports and validates the canonical Agent Spec artifact, then runs it through local adapters so the demo stays runnable without external runtime packages.

## Repo layout

```text
authoring/           Authored desk, roles, and flow definitions
contracts/           Versioned object and AG-UI schemas
data/demo/           Timestamped demo datasets
frontend/app/        Static HTML + vanilla JS UI
runtime/             Runtime adapters and shared engine code
scenarios/           Scenario definitions
spec/exported/       Exported desk spec artifact
tests/conformance/   Parity fixtures and conformance harness
```

## Quick start

Export the authored spec:

```bash
python3 authoring/export_spec.py
```

Run conformance across both runtimes:

```bash
python3 tests/conformance/run_conformance.py
```

Run the local app:

```bash
python3 app.py
```

Then open `http://127.0.0.1:8000`.

Note: in some sandboxed environments, binding a local TCP port is blocked. If that happens, run the same command outside the sandbox.

## Current product slice

Implemented:
- one starter scenario: `single_name_earnings`
- full 16-role registry in contracts and authored spec
- exported Agent Spec artifact with Agent and Flow components
- spec-driven runtime stage order: gather → quantify → debate → synthesize → risk_review → pm_review → trade_finalize → monitor
- core desk execution path
- optional seat selection in the UI contract
- server-side participation-matrix enforcement for required, optional, and suppressed seats
- runtime fail-fast contract validation for AG-UI envelopes and typed objects
- role-specific prompt registry wired into runtime agent text generation
- role-specific tool declarations exported into the Agent Spec artifact
- OCI GenAI integration for agent narratives with deterministic fallback when not configured
- event log and object-store materialization under `var/runs/`
- replay retrieval endpoint: `/api/runs?run_id=<id>`
- cross-run audit feed for runtime choice, PM approvals, and ticket lifecycle: `/api/audit`
- deployment readiness endpoint: `/api/health` (and `/api/health?verbose=1`)
- OCI environment validation with optional strict startup enforcement (`STRICT_ENV_VALIDATION=1`)

Not implemented yet:
- direct execution through installed external Oracle/PyAgentSpec, WayFlow, or LangGraph packages
- OCI-specific deployment automation
- live licensed data integrations

## Deployment shape

This repo includes a container-friendly Python app. For real customer demos:
- replace the local runtime shims with the official runtimes,
- replace demo datasets with approved feeds,
- wire secrets through OCI Vault,
- deploy the container to OCI Compute, OKE, or another OCI target.

## Health and OCI env validation

Runtime health:
- `GET /api/health` returns high-level health (`ok` or `degraded`).
- `GET /api/health?verbose=1` includes OCI environment details and missing keys.
- Status code is `200` when healthy, `503` when degraded.

OCI env validation:
- Required by default:
  - `OCI_REGION`
  - `OCI_COMPARTMENT_OCID`
  - `OCI_GENAI_ENDPOINT`
  - `OCI_GENAI_MODEL_ID`
- Override required list with `REQUIRED_ENV_VARS` (comma-separated).
- Set `STRICT_ENV_VALIDATION=1` to fail fast at startup if required vars are missing.

OCI GenAI runtime integration:
- `OCI_GENAI_ENDPOINT` and `OCI_GENAI_MODEL_ID` are required for configured mode.
- `OCI_GENAI_API_KEY` is optional. If present, runtime uses Bearer auth mode.
- Without API key, runtime first attempts OCI SDK signed calls using `OCI_GENAI_AUTH_MODE` (`instance_principal` or `user_principal`; default `instance_principal`) when Python package `oci` is installed, then falls back to unsigned request shapes.
- Optional:
  - `OCI_GENAI_CHAT_PATH` (default `/v1/chat/completions`)
  - `OCI_GENAI_TIMEOUT_S` (default `120`)
  - `OCI_GENAI_ENABLE` (`1`/`0`, default enabled)
  - `OCI_GENAI_USE_OCI_SDK` (`1`/`0`, default enabled)
  - `OCI_GENAI_AUTH_MODE` (`instance_principal` or `user_principal`)
  - `OCI_CONFIG_FILE` (default `~/.oci/config`; used by `user_principal` mode)
  - `OCI_CONFIG_PROFILE` (default `DEFAULT`; used by `user_principal` mode)
- If calls fail, runtime falls back to deterministic local text so the demo remains runnable.

LLM diagnostics:
- Each run summary now includes `summary.llm` with:
  - `live_count`, `fallback_count`
  - `last_mode`, `last_error`, `last_attempts`
  - `auth_mode`, `oci_sdk_available`
- The UI status line surfaces this after each run/replay.

For deployment-oriented setup and hardening details, see:
- `docs/oci-deployment-hardening.md`
