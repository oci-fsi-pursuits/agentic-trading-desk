from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Callable, Iterator, Mapping

from runtime.common.agent_prompts import AGENT_SYSTEM_PROMPTS
from runtime.common.analyst_tools import execute_local_tool
from runtime.common.env_validation import parse_bool

_DEFAULT_OCI_CONFIG_FILE = os.path.expanduser("~/.oci/config")
_DEFAULT_OCI_CONFIG_PROFILE = "DEFAULT"

# ---------------------------------------------------------------------------
# Self-contained OCI httpx auth classes (mirrors SDTest.py)
# ---------------------------------------------------------------------------
try:
    import httpx
    import requests
    import oci
    from oci.config import DEFAULT_LOCATION, DEFAULT_PROFILE
    from openai import (
        DEFAULT_MAX_RETRIES,
        NOT_GIVEN,
        OpenAI,
        DefaultHttpxClient,
        Timeout,
        NotGiven,
    )

    class _HttpxOCIAuth(httpx.Auth):
        """Base OCI request-signing auth for httpx."""

        def auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
            try:
                content = request.content
            except httpx.RequestNotRead:
                content = request.read()

            req = requests.Request(
                method=request.method,
                url=str(request.url),
                headers=dict(request.headers),
                data=content,
            )
            prepared = req.prepare()
            self.signer.do_request_sign(prepared)
            request.headers.update(prepared.headers)
            yield request

    class _OCIInstancePrincipalAuth(_HttpxOCIAuth):
        def __init__(self):
            self.signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()

    class _OCIUserPrincipalAuth(_HttpxOCIAuth):
        def __init__(self, config_file=DEFAULT_LOCATION, profile_name=DEFAULT_PROFILE):
            config = oci.config.from_file(config_file, profile_name)
            oci.config.validate_config(config)
            self.signer = oci.signer.Signer(
                tenancy=config["tenancy"],
                user=config["user"],
                fingerprint=config["fingerprint"],
                private_key_file_location=config.get("key_file"),
                pass_phrase=oci.config.get_config_value_or_default(config, "pass_phrase"),
                private_key_content=config.get("key_content"),
            )

    class _OCIOpenAI(OpenAI):
        """OpenAI client wired to the OCI Generative AI Responses endpoint."""

        def __init__(
            self,
            *,
            service_endpoint: str,
            auth: httpx.Auth,
            compartment_id: str,
            openai_project: str,
            timeout: float | Timeout | None | NotGiven = NOT_GIVEN,
            max_retries: int = DEFAULT_MAX_RETRIES,
            default_headers: Mapping[str, str] | None = None,
        ) -> None:
            super().__init__(
                api_key="<NOTUSED>",
                # Mirror SDTest.py: base_url = {endpoint}/20231130/actions/v1
                base_url=f"{service_endpoint.rstrip('/')}/20231130/actions/v1",
                timeout=timeout,
                max_retries=max_retries,
                default_headers={
                    "OpenAI-Project": openai_project,
                    **(default_headers or {}),
                },
                http_client=DefaultHttpxClient(
                    auth=auth,
                    headers={"CompartmentId": compartment_id},
                ),
            )

    _OCI_CLASSES_AVAILABLE = True
except Exception:  # noqa: BLE001
    _OCI_CLASSES_AVAILABLE = False


def _llm_log_enabled() -> bool:
    return parse_bool(os.environ.get("ATD_LOG_LLM"), default=True)


def _llm_log(message: str) -> None:
    if _llm_log_enabled():
        print(f"[LLM] {message}", flush=True)


def _llm_log_verbose_enabled() -> bool:
    return parse_bool(os.environ.get("ATD_LOG_LLM_VERBOSE"), default=True)


def _local_function_tools_enabled() -> bool:
    return parse_bool(os.environ.get("ATD_LOCAL_FUNCTION_TOOLS_ENABLE"), default=True)


def _responses_state_mode() -> str:
    raw = str(os.environ.get("ATD_RESPONSES_STATE_MODE", "user_managed") or "").strip().lower()
    if raw in {"api_managed", "user_managed", "hybrid"}:
        return raw
    return "user_managed"


def _is_response_not_found_error(detail: str) -> bool:
    text = str(detail or "").lower()
    return "response with id=" in text and "not found" in text


def _is_store_disallowed_error(detail: str) -> bool:
    text = str(detail or "").lower()
    return "unable to store messages when zdr is enabled" in text


def _prompt_value_is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, Mapping):
        return not any(not _prompt_value_is_empty(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return not any(not _prompt_value_is_empty(item) for item in value)
    return False


def _format_prompt_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def _mapping_is_flat(value: Mapping[str, Any]) -> bool:
    for item in value.values():
        if _prompt_value_is_empty(item):
            continue
        if isinstance(item, Mapping):
            return False
        if isinstance(item, (list, tuple, set)):
            for nested in item:
                if _prompt_value_is_empty(nested):
                    continue
                if isinstance(nested, (Mapping, list, tuple, set)):
                    return False
    return True


def _render_prompt_context_lines(value: Any, *, indent: int = 0) -> list[str]:
    prefix = "  " * indent
    if isinstance(value, Mapping):
        lines: list[str] = []
        for key, item in value.items():
            if _prompt_value_is_empty(item):
                continue
            label = str(key)
            if isinstance(item, Mapping):
                if _mapping_is_flat(item):
                    flat = ", ".join(
                        f"{sub_key}={_format_prompt_scalar(sub_value)}"
                        for sub_key, sub_value in item.items()
                        if not _prompt_value_is_empty(sub_value)
                    )
                    lines.append(f"{prefix}{label}: {flat}")
                else:
                    lines.append(f"{prefix}{label}:")
                    lines.extend(_render_prompt_context_lines(item, indent=indent + 1))
                continue
            if isinstance(item, (list, tuple, set)):
                items = [entry for entry in item if not _prompt_value_is_empty(entry)]
                if not items:
                    continue
                if all(not isinstance(entry, (Mapping, list, tuple, set)) for entry in items):
                    lines.append(
                        f"{prefix}{label}: " + ", ".join(_format_prompt_scalar(entry) for entry in items)
                    )
                    continue
                lines.append(f"{prefix}{label}:")
                for entry in items:
                    if isinstance(entry, Mapping):
                        if _mapping_is_flat(entry):
                            flat = ", ".join(
                                f"{sub_key}={_format_prompt_scalar(sub_value)}"
                                for sub_key, sub_value in entry.items()
                                if not _prompt_value_is_empty(sub_value)
                            )
                            lines.append(f"{prefix}  - {flat}")
                        else:
                            lines.append(f"{prefix}  -")
                            lines.extend(_render_prompt_context_lines(entry, indent=indent + 2))
                        continue
                    if isinstance(entry, (list, tuple, set)):
                        lines.append(f"{prefix}  -")
                        lines.extend(_render_prompt_context_lines({"items": list(entry)}, indent=indent + 2))
                        continue
                    lines.append(f"{prefix}  - {_format_prompt_scalar(entry)}")
                continue
            lines.append(f"{prefix}{label}: {_format_prompt_scalar(item)}")
        return lines
    return [f"{prefix}{_format_prompt_scalar(value)}"]


def _render_prompt_context(context: dict[str, Any]) -> str:
    lines = _render_prompt_context_lines(context)
    return "\n".join(lines) if lines else "(no context provided)"


def build_agent_prompt_preview(role_id: str, context: dict[str, Any], *, max_words: int = 200) -> str:
    base_prompt = AGENT_SYSTEM_PROMPTS.get(role_id)
    if not base_prompt:
        raise KeyError(f"Unknown role_id for prompt preview: {role_id}")
    safe_max_words = max(80, min(int(max_words or 200), 800))
    context_block = _render_prompt_context(context)
    return (
        f"{base_prompt}\n\n"
        f"Response budget: {safe_max_words} words maximum.\n\n"
        f"Context:\n{context_block}\n\n"
        "Use only this context. If required data is missing or uncertain, state that explicitly."
    )


def _extract_text(payload: dict[str, Any]) -> str | None:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message", {})
        content = message.get("content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            chunks = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    chunks.append(item["text"].strip())
            combined = " ".join(part for part in chunks if part)
            return combined or None
    output_text = payload.get("output_text")
    if isinstance(output_text, str):
        return output_text.strip()
    return None


def _extract_responses_text(payload: dict[str, Any]) -> str | None:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()
    output = payload.get("output")
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, str) and content.strip():
                chunks.append(content.strip())
                continue
            if isinstance(content, list):
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    text = part.get("text")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text.strip())
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                chunks.append(text.strip())
        if chunks:
            return " ".join(chunks)
    return _extract_text(payload)


def _extract_responses_function_calls(payload: dict[str, Any]) -> list[dict[str, str]]:
    output = payload.get("output")
    if not isinstance(output, list):
        return []
    calls: list[dict[str, str]] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type", "") or "").strip().lower()
        name = str(item.get("name", "") or "").strip()
        arguments = item.get("arguments")
        call_id = str(item.get("call_id", "") or "").strip()
        item_id = str(item.get("id", "") or "").strip()
        if item_type not in {"function_call", "tool_call"} and (not name or arguments in (None, "")):
            continue
        if not name:
            continue
        if isinstance(arguments, (dict, list)):
            arguments_text = json.dumps(arguments, ensure_ascii=True, separators=(",", ":"))
        else:
            arguments_text = str(arguments or "").strip()
        calls.append(
            {
                "name": name,
                "arguments": arguments_text,
                "call_id": call_id,
                "id": item_id,
            }
        )
    return calls


def _parse_function_arguments(raw: str) -> dict[str, Any]:
    value = str(raw or "").strip()
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _squash_error(exc: Exception | str, max_len: int = 220) -> str:
    text = str(exc).strip()
    if not text:
        text = exc.__class__.__name__ if isinstance(exc, Exception) else "unknown_error"
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


class OciGenAIClient:
    def __init__(self) -> None:
        self.endpoint = (os.environ.get("OCI_GENAI_ENDPOINT", "") or "").rstrip("/")
        self.model_id = (os.environ.get("OCI_GENAI_MODEL_ID", "") or "").strip()
        self.api_key = (os.environ.get("OCI_GENAI_API_KEY", "") or "").strip()
        self.timeout_s = float(os.environ.get("OCI_GENAI_TIMEOUT_S", "120"))
        self.enabled = parse_bool(os.environ.get("OCI_GENAI_ENABLE"), default=True)
        self.compartment_id = os.environ.get("OCI_COMPARTMENT_OCID", "")
        self.openai_project = (os.environ.get("OCI_OPENAI_PROJECT", "") or "").strip()
        self.auth_mode = self._normalize_auth_mode(os.environ.get("OCI_GENAI_AUTH_MODE", "instance_principal"))
        self.oci_config_file = (os.environ.get("OCI_CONFIG_FILE", "") or "").strip() or _DEFAULT_OCI_CONFIG_FILE
        self.oci_config_profile = (os.environ.get("OCI_CONFIG_PROFILE", "") or "").strip() or _DEFAULT_OCI_CONFIG_PROFILE
        self.last_error = ""
        self.last_attempts: list[str] = []
        self.last_mode = "none"

    @staticmethod
    def _normalize_auth_mode(value: str | None) -> str:
        raw = str(value or "").strip().lower()
        if raw in {"", "instance", "instance_principal", "instance-principal"}:
            return "instance_principal"
        if raw in {"user", "user_principal", "user-principal"}:
            return "user_principal"
        return "instance_principal"

    def ready(self) -> bool:
        return self.enabled and bool(self.endpoint and self.model_id)

    def capability_profile(self) -> dict[str, Any]:
        if self.api_key:
            auth_mode = "api_key"
        else:
            auth_mode = self.auth_mode
        return {
            "ready": self.ready(),
            "enabled": self.enabled,
            "auth_mode": auth_mode,
            "oci_sdk_available": _OCI_CLASSES_AVAILABLE,
        }

    def _httpx_auth(self) -> tuple[Any | None, str | None]:
        if self.auth_mode == "instance_principal":
            return _OCIInstancePrincipalAuth(), None
        if self.auth_mode == "user_principal":
            return (
                _OCIUserPrincipalAuth(
                    config_file=self.oci_config_file,
                    profile_name=self.oci_config_profile,
                ),
                None,
            )
        return None, f"unsupported_auth_mode ({self.auth_mode})"

    def _log_responses_request(self, mode: str, endpoint: str, body: dict[str, Any]) -> None:
        _llm_log(f"responses mode={mode} endpoint={endpoint}")
        if _llm_log_verbose_enabled():
            serialized = json.dumps(body, ensure_ascii=True, separators=(",", ":"))
            _llm_log(f"responses mode={mode} body={serialized}")
        else:
            input_items = body.get("input") if isinstance(body.get("input"), list) else []
            prompt_chars = 0
            for item in input_items:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, str):
                    prompt_chars += len(content)
            _llm_log(
                "responses mode="
                + mode
                + f" body_summary=model={body.get('model')} input_items={len(input_items)} "
                + f"prompt_chars={prompt_chars} tools={len(body.get('tools') or [])}"
            )

    def _log_responses_debug(self, mode: str, message: str) -> None:
        _llm_log(f"responses mode={mode} debug={message}")

    def _inference_root(self) -> str:
        root = (self.endpoint or "").rstrip("/")
        if root.endswith("/20231130"):
            root = root[: -len("/20231130")]
        return root.rstrip("/")


    def complete_with_responses(
        self,
        prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 420,
        temperature: float = 0.2,
        local_tool_executor: Callable[[str, dict[str, Any]], str] | None = None,
        max_tool_rounds: int = 3,
        max_tool_calls: int = 6,
    ) -> str | None:
        if not self.ready():
            self.last_mode = "disabled_or_unconfigured"
            self.last_error = "OCI GenAI is disabled or missing endpoint/model."
            self.last_attempts = []
            return None
        self.last_mode = "attempting"
        self.last_error = ""
        self.last_attempts = []
        attempts: list[str] = []
        normalized_tools = list(tools or [])
        local_function_tools = [
            tool
            for tool in normalized_tools
            if isinstance(tool, dict)
            and str(tool.get("type", "") or "").strip().lower() == "function"
            and str(tool.get("name", "") or "").strip()
        ]
        has_local_tool_loop = bool(local_function_tools and local_tool_executor and _local_function_tools_enabled())
        local_tool_names = {str(tool.get("name", "") or "").strip() for tool in local_function_tools}
        input_items: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": str(prompt or "").strip(),
            },
        ]
        state_mode_config = _responses_state_mode()
        state_mode = "user_managed" if state_mode_config == "user_managed" else "api_managed"
        allow_state_fallback = state_mode_config == "hybrid"
        previous_response_id = ""
        tool_call_count = 0
        strategies: list[tuple[str, Any]] = [("responses_openai_client", self._responses_openai_client)]
        max_rounds = max(0, int(max_tool_rounds))
        for mode, fn in strategies:
            # Initial response pass + bounded local tool rounds.
            for round_idx in range(max_rounds + 1):
                body: dict[str, Any] = {
                    "model": self.model_id,
                    "input": input_items,
                    "temperature": float(temperature),
                    "max_output_tokens": int(max_tokens),
                }
                if normalized_tools:
                    body["tools"] = normalized_tools
                if previous_response_id and state_mode == "api_managed":
                    body["previous_response_id"] = previous_response_id
                self._log_responses_debug(
                    mode,
                    f"attempt_start auth_mode={self.auth_mode} api_key={'set' if self.api_key else 'unset'} "
                    f"tools={len(normalized_tools)} model={self.model_id} round={round_idx} state_mode={state_mode}",
                )
                payload, error = fn(body)
                if not isinstance(payload, dict):
                    if error:
                        self._log_responses_debug(mode, f"attempt_error detail={error}")
                        if (
                            has_local_tool_loop
                            and state_mode == "api_managed"
                            and allow_state_fallback
                            and round_idx > 0
                            and (_is_response_not_found_error(error) or _is_store_disallowed_error(error))
                        ):
                            attempts.append(f"{mode}: api_managed_state_fallback_to_user_managed")
                            state_mode = "user_managed"
                            previous_response_id = ""
                            continue
                        attempts.append(f"{mode}: {error}")
                    break

                response_id = str(payload.get("id", "") or "").strip()
                if response_id and state_mode == "api_managed":
                    previous_response_id = response_id
                text = _extract_responses_text(payload)
                function_calls = _extract_responses_function_calls(payload) if has_local_tool_loop else []
                pending_local_calls = [
                    call
                    for call in function_calls
                    if str(call.get("name", "") or "").strip() in local_tool_names
                ]
                if pending_local_calls:
                    if round_idx >= max_rounds:
                        attempts.append(f"{mode}: local_tool_round_limit_reached")
                        break
                    outputs: list[dict[str, Any]] = []
                    call_items_for_user_managed: list[dict[str, Any]] = []
                    for call in pending_local_calls:
                        if tool_call_count >= int(max_tool_calls):
                            break
                        name = str(call.get("name", "") or "").strip()
                        call_id = str(call.get("call_id", "") or call.get("id", "") or "").strip()
                        if not call_id:
                            call_id = f"local_call_{tool_call_count + 1}"
                        args = _parse_function_arguments(str(call.get("arguments", "") or ""))
                        result_text = str(local_tool_executor(name, args)) if local_tool_executor else ""
                        outputs.append(
                            {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": result_text,
                            }
                        )
                        if state_mode == "user_managed":
                            call_items_for_user_managed.append(
                                {
                                    "type": "function_call",
                                    "call_id": call_id,
                                    "name": name,
                                    "arguments": json.dumps(args, ensure_ascii=True, separators=(",", ":")),
                                }
                            )
                        tool_call_count += 1
                    if not outputs:
                        attempts.append(f"{mode}: local_tool_call_limit_reached")
                        break
                    if state_mode == "user_managed":
                        input_items = call_items_for_user_managed + outputs
                        previous_response_id = ""
                    else:
                        input_items = outputs
                    continue

                if text:
                    self.last_mode = mode
                    self.last_error = ""
                    self.last_attempts = attempts
                    return text
                attempts.append(f"{mode}: response did not contain text")
                break

        self.last_mode = "failed"
        self.last_attempts = attempts
        self.last_error = " | ".join(attempts) if attempts else "No response from OCI Responses API."
        self._log_responses_debug("final", f"failed attempts={attempts}")
        return None

    def _responses_openai_client(self, body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        if not self.openai_project:
            return None, "missing_openai_project"
        if not _OCI_CLASSES_AVAILABLE:
            return None, (
                "Required packages (oci, httpx, openai, requests) are not installed. "
                "Install them and retry."
            )
        inference_root = self._inference_root()
        endpoint_log = f"{inference_root}/20231130/actions/v1/responses"
        body_for_log = dict(body)
        self._log_responses_request("openai_client", endpoint_log, body_for_log)
        try:
            if self.api_key:
                # API-key path: standard OpenAI client pointed at OCI
                from openai import OpenAI
                client = OpenAI(
                    base_url=f"{inference_root}/20231130/actions/v1",
                    api_key=self.api_key,
                    default_headers={
                        "OpenAI-Project": self.openai_project,
                        "CompartmentId": self.compartment_id,
                    },
                    timeout=self.timeout_s,
                )
                self._log_responses_debug(
                    "openai_client",
                    f"client_init api_key endpoint={inference_root}/20231130/actions/v1 timeout_s={self.timeout_s}",
                )
            else:
                # Principal-based path: self-contained auth matching OCI signer mode.
                auth, auth_error = self._httpx_auth()
                if auth is None:
                    return None, auth_error or "auth_initialization_failed"
                client = _OCIOpenAI(
                    service_endpoint=inference_root,
                    auth=auth,
                    compartment_id=self.compartment_id,
                    openai_project=self.openai_project,
                    timeout=self.timeout_s,
                )
                self._log_responses_debug(
                    "openai_client",
                    f"client_init principal auth_mode={self.auth_mode} config_profile={self.oci_config_profile}",
                )

            if _llm_log_verbose_enabled():
                self._log_responses_debug(
                    "openai_client",
                    f"request input={body.get('input')}",
                )
            else:
                self._log_responses_debug(
                    "openai_client",
                    f"request input_items={len(body.get('input') or [])} tools={len(body.get('tools') or [])}",
                )

            create_kwargs: dict[str, Any] = {
                "model": body["model"],
                "input": body["input"],
                "temperature": body.get("temperature"),
                "max_output_tokens": body.get("max_output_tokens"),
                "tools": body.get("tools"),
            }
            previous_response_id = body.get("previous_response_id")
            if previous_response_id:
                create_kwargs["previous_response_id"] = previous_response_id
                create_kwargs["store"] = True
            response = client.responses.create(**create_kwargs)

            # Avoid model_dump()/to_dict() here: OpenAI SDK typed models may emit
            # Pydantic serializer warnings for OCI-only tool variants (for example
            # x_search) even when the request/response is valid.
            if isinstance(response, dict):
                return response, None

            payload: dict[str, Any] = {}
            response_id = str(getattr(response, "id", "") or "").strip()
            if response_id:
                payload["id"] = response_id
            output_text = str(getattr(response, "output_text", "") or "").strip()
            if output_text:
                payload["output_text"] = output_text

            # Build a lightweight dict from response.output so downstream text
            # extraction can still recover content when output_text is empty.
            output_items = getattr(response, "output", None)
            if isinstance(output_items, list):
                normalized_output: list[dict[str, Any]] = []
                for item in output_items:
                    item_dict: dict[str, Any] = {}
                    item_type = str(getattr(item, "type", "") or "").strip()
                    if item_type:
                        item_dict["type"] = item_type
                    item_id = str(getattr(item, "id", "") or "").strip()
                    if item_id:
                        item_dict["id"] = item_id
                    item_name = str(getattr(item, "name", "") or "").strip()
                    if item_name:
                        item_dict["name"] = item_name
                    item_call_id = str(getattr(item, "call_id", "") or "").strip()
                    if item_call_id:
                        item_dict["call_id"] = item_call_id
                    item_arguments = getattr(item, "arguments", None)
                    if isinstance(item_arguments, str) and item_arguments.strip():
                        item_dict["arguments"] = item_arguments.strip()

                    item_text = str(getattr(item, "text", "") or "").strip()
                    if item_text:
                        item_dict["text"] = item_text

                    content_blocks = getattr(item, "content", None)
                    if isinstance(content_blocks, list):
                        normalized_blocks: list[dict[str, str]] = []
                        for block in content_blocks:
                            block_type = str(getattr(block, "type", "") or "").strip()
                            block_text = str(getattr(block, "text", "") or "").strip()
                            if block_text:
                                block_payload = {"text": block_text}
                                if block_type:
                                    block_payload["type"] = block_type
                                normalized_blocks.append(block_payload)
                        if normalized_blocks:
                            item_dict["content"] = normalized_blocks

                    if item_dict:
                        normalized_output.append(item_dict)

                if normalized_output:
                    payload["output"] = normalized_output

            if not payload:
                self._log_responses_debug("openai_client", "empty_response_object_no_output_text_or_output_items")
            return (payload if payload else None), None
        except Exception as exc:  # noqa: BLE001
            status_code = getattr(exc, "status_code", None)
            response_obj = getattr(exc, "response", None)
            if status_code is None and response_obj is not None:
                status_code = getattr(response_obj, "status_code", None)
            detail = _squash_error(exc)
            if status_code is not None:
                return None, f"openai_client_failed status={status_code} detail=({_squash_error(detail)})"
            return None, f"openai_client_failed ({detail})"


class AgentTextService:
    def __init__(self) -> None:
        self.client = OciGenAIClient()
        self.cache: dict[tuple[str, str, str, str, str], str] = {}
        self.cache_version = "v5"
        self.live_count = 0
        self.fallback_count = 0
        self.last_error = ""
        self.last_attempts: list[str] = []
        self.last_mode = "none"

    def generate(
        self,
        role_id: str,
        phase_id: str,
        context: dict[str, Any],
        fallback: str,
        max_words: int = 300,
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
    ) -> str:
        base_prompt = AGENT_SYSTEM_PROMPTS.get(role_id)
        if not base_prompt:
            return fallback
        safe_max_words = max(80, min(int(max_words or 200), 800))
        # Allocate enough completion tokens so responses do not get cut off mid-sentence.
        max_tokens = max(320, min(1800, int(safe_max_words * 2.5)))
        prompt_fingerprint = hashlib.sha1(base_prompt.encode("utf-8")).hexdigest()[:12]
        context_json = json.dumps(context, sort_keys=True, ensure_ascii=True)
        tools_json = json.dumps(tools or [], sort_keys=True, ensure_ascii=True)
        context_block = _render_prompt_context(context)
        cache_key = (
            role_id,
            phase_id,
            context_json,
            f"{self.cache_version}:{prompt_fingerprint}:{safe_max_words}:{max_tokens}:{temperature:.3f}:{tools_json}",
        )
        if cache_key in self.cache:
            return self.cache[cache_key]

        prompt = (
            f"{base_prompt}\n\n"
            f"Response budget: {safe_max_words} words maximum.\n\n"
            f"Context:\n{context_block}\n\n"
            "Use only this context. If required data is missing or uncertain, state that explicitly."
        )
        candidate = self.client.complete_with_responses(
            prompt,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            local_tool_executor=execute_local_tool,
            max_tool_rounds=3,
            max_tool_calls=6,
        )
        if not candidate:
            self.fallback_count += 1
            self.last_mode = "fallback"
            self.last_error = self.client.last_error
            self.last_attempts = list(self.client.last_attempts)
            _llm_log(
                f"role={role_id} phase={phase_id} mode=fallback "
                f"error={self.last_error or 'unknown'} attempts={self.last_attempts}"
            )
            return fallback
        normalized = str(candidate or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized:
            self.fallback_count += 1
            self.last_mode = "fallback"
            self.last_error = "LLM returned empty response after normalization."
            self.last_attempts = []
            _llm_log(f"role={role_id} phase={phase_id} mode=fallback error={self.last_error}")
            return fallback
        self.live_count += 1
        self.last_mode = self.client.last_mode or "live"
        self.last_error = ""
        self.last_attempts = []
        _llm_log(
            f"role={role_id} phase={phase_id} mode={self.last_mode} "
            f"words={len(normalized.split())} tools={len(tools or [])}"
        )
        self.cache[cache_key] = normalized
        return normalized

    def diagnostics(self) -> dict[str, Any]:
        capability = self.client.capability_profile()
        return {
            "live_count": self.live_count,
            "fallback_count": self.fallback_count,
            "last_mode": self.last_mode,
            "last_error": self.last_error,
            "last_attempts": self.last_attempts,
            "ready": capability["ready"],
            "enabled": capability["enabled"],
            "auth_mode": capability["auth_mode"],
            "oci_sdk_available": capability["oci_sdk_available"],
        }
