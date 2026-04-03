from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from runtime.common.agent_prompts import AGENT_SYSTEM_PROMPTS
from runtime.common.env_validation import parse_bool


def _llm_log_enabled() -> bool:
    return parse_bool(os.environ.get("ATD_LOG_LLM"), default=True)


def _llm_log(message: str) -> None:
    if _llm_log_enabled():
        print(f"[LLM] {message}", flush=True)


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


def _extract_native_chat_text(payload: dict[str, Any]) -> str | None:
    chat_response = payload.get("chatResponse")
    if chat_response is None:
        chat_response = payload.get("chat_response")
    if isinstance(chat_response, dict):
        text = _extract_text(chat_response)
        if text:
            return text
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


def _squash_error(exc: Exception | str, max_len: int = 220) -> str:
    text = str(exc).strip()
    if not text:
        text = exc.__class__.__name__ if isinstance(exc, Exception) else "unknown_error"
    return text


def _http_error_detail(exc: urllib.error.HTTPError) -> str:
    detail = f"HTTP {exc.code}"
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        body = ""
    if body:
        detail = f"{detail} body={body}"
    return detail


class OciGenAIClient:
    def __init__(self) -> None:
        self.endpoint = (os.environ.get("OCI_GENAI_ENDPOINT", "") or "").rstrip("/")
        self.model_id = (os.environ.get("OCI_GENAI_MODEL_ID", "") or "").strip()
        self.api_key = (os.environ.get("OCI_GENAI_API_KEY", "") or "").strip()
        self.chat_path = (os.environ.get("OCI_GENAI_CHAT_PATH", "/v1/chat/completions") or "/v1/chat/completions").strip()
        self.responses_path = (os.environ.get("OCI_GENAI_RESPONSES_PATH", "/openai/v1/responses") or "/openai/v1/responses").strip()
        self.timeout_s = float(os.environ.get("OCI_GENAI_TIMEOUT_S", "8"))
        self.enabled = parse_bool(os.environ.get("OCI_GENAI_ENABLE"), default=True)
        self.region = os.environ.get("OCI_REGION", "")
        self.use_oci_sdk = parse_bool(os.environ.get("OCI_GENAI_USE_OCI_SDK"), default=True)
        self.compartment_id = os.environ.get("OCI_COMPARTMENT_OCID", "")
        self.openai_project = (os.environ.get("OCI_OPENAI_PROJECT", "") or "").strip()
        self.last_error = ""
        self.last_attempts: list[str] = []
        self.last_mode = "none"

    def ready(self) -> bool:
        return self.enabled and bool(self.endpoint and self.model_id)

    def _effective_region(self) -> str:
        region = (self.region or "").strip()
        if region:
            return region
        marker = ".generativeai."
        idx = self.endpoint.find(marker)
        if idx >= 0:
            tail = self.endpoint[idx + len(marker) :]
            region_guess = tail.split(".oci.oraclecloud.com", 1)[0].strip().strip("/")
            if region_guess:
                return region_guess
        return "us-chicago-1"

    def _oci_sdk_available(self) -> bool:
        try:
            import oci  # noqa: F401

            return True
        except Exception:  # noqa: BLE001
            return False

    def capability_profile(self) -> dict[str, Any]:
        if self.api_key:
            auth_mode = "api_key"
        elif self.use_oci_sdk and self._oci_sdk_available():
            auth_mode = "instance_principal_sdk"
        elif self.use_oci_sdk:
            auth_mode = "unsigned_no_sdk"
        else:
            auth_mode = "unsigned"
        return {
            "ready": self.ready(),
            "enabled": self.enabled,
            "auth_mode": auth_mode,
            "oci_sdk_available": self._oci_sdk_available(),
        }

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 420, temperature: float = 0.2) -> str | None:
        if not self.ready():
            self.last_mode = "disabled_or_unconfigured"
            self.last_error = "OCI GenAI is disabled or missing endpoint/model."
            self.last_attempts = []
            return None
        self.last_mode = "attempting"
        self.last_error = ""
        self.last_attempts = []

        strategies: list[tuple[str, Any]] = []
        if self.api_key:
            strategies = [
                ("openai_compatible", self._complete_openai),
                ("oci_native_chat", self._complete_oci_native),
            ]
        else:
            if self.use_oci_sdk:
                strategies.append(("oci_sdk_signed", self._complete_oci_sdk_signed))
            strategies.extend(
                [
                    ("openai_compatible", self._complete_openai),
                    ("oci_native_chat", self._complete_oci_native),
                ]
            )

        attempts: list[str] = []
        for mode, fn in strategies:
            text, error = fn(system_prompt, user_prompt, max_tokens, temperature)
            if text:
                self.last_mode = mode
                self.last_error = ""
                self.last_attempts = attempts
                return text
            if error:
                attempts.append(f"{mode}: {error}")

        self.last_mode = "failed"
        self.last_attempts = attempts
        if attempts:
            self.last_error = " | ".join(attempts)
        else:
            self.last_error = "No text returned from any OCI GenAI request shape."
        return None

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        # API key is optional; instance principal / environment-auth flows can work without Bearer.
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.compartment_id:
            headers["opc-compartment-id"] = self.compartment_id
            headers["CompartmentId"] = self.compartment_id
        if self.openai_project:
            headers["OpenAI-Project"] = self.openai_project
        return headers

    def _complete_openai(self, system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> tuple[str | None, str | None]:
        url = f"{self.endpoint}{self.chat_path}"
        body = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url=url, data=payload, method="POST", headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                parsed = json.loads(response.read().decode("utf-8"))
                text = _extract_text(parsed)
                if text:
                    return text, None
                return None, "response did not contain text"
        except urllib.error.HTTPError as exc:
            return None, _http_error_detail(exc)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return None, f"network/timeout/parse failure ({_squash_error(exc)})"

    def _log_responses_request(self, mode: str, endpoint: str, body: dict[str, Any]) -> None:
        serialized = json.dumps(body, ensure_ascii=True, separators=(",", ":"))
        _llm_log(f"responses mode={mode} endpoint={endpoint}")
        _llm_log(f"responses mode={mode} body={serialized}")

    def _inference_root(self) -> str:
        root = (self.endpoint or "").rstrip("/")
        if root.endswith("/20231130"):
            root = root[: -len("/20231130")]
        return root.rstrip("/")

    def _responses_base_url(self) -> str:
        path_or_url = str(self.responses_path or "").strip() or "/openai/v1/responses"
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            parsed = urllib.parse.urlsplit(path_or_url)
            path = parsed.path or "/openai/v1/responses"
            if path.endswith("/responses"):
                path = path[: -len("/responses")]
            if not path:
                path = "/openai/v1"
            return f"{parsed.scheme}://{parsed.netloc}{path.rstrip('/')}"
        path = path_or_url
        if not path.startswith("/"):
            path = "/" + path
        if path.startswith("/20231130/"):
            path = path[len("/20231130") :]
        if path.endswith("/responses"):
            path = path[: -len("/responses")]
        if not path:
            path = "/openai/v1"
        return f"{self._inference_root()}{path.rstrip('/')}"


    def complete_with_responses(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 420,
        temperature: float = 0.2,
    ) -> str | None:
        if not self.ready():
            self.last_mode = "disabled_or_unconfigured"
            self.last_error = "OCI GenAI is disabled or missing endpoint/model."
            self.last_attempts = []
            return None
        self.last_mode = "attempting"
        self.last_error = ""
        self.last_attempts = []

        body: dict[str, Any] = {
            "model": self.model_id,
            "input": f"System instructions:\n{system_prompt}\n\nUser request:\n{user_prompt}",
            "temperature": float(temperature),
            "max_output_tokens": int(max_tokens),
        }
        if tools:
            body["tools"] = tools

        attempts: list[str] = []
        strategies: list[tuple[str, Any]] = [("responses_openai_client", self._responses_openai_client)]
        for mode, fn in strategies:
            payload, error = fn(body)
            if isinstance(payload, dict):
                text = _extract_responses_text(payload)
                if text:
                    self.last_mode = mode
                    self.last_error = ""
                    self.last_attempts = attempts
                    return text
                attempts.append(f"{mode}: response did not contain text")
                continue
            if error:
                attempts.append(f"{mode}: {error}")

        self.last_mode = "failed"
        self.last_attempts = attempts
        self.last_error = " | ".join(attempts) if attempts else "No response from OCI Responses API."
        return None

    def _responses_openai_client(self, body: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        if not self.openai_project:
            return None, "missing_openai_project"
        try:
            from openai import OpenAI
        except Exception:
            return None, "python package 'openai' not installed for OCI Responses API client"
        headers: dict[str, str] = {}
        if self.compartment_id:
            headers["opc-compartment-id"] = self.compartment_id
            headers["CompartmentId"] = self.compartment_id
        try:
            base_url = self._responses_base_url()
            endpoint = f"{base_url.rstrip('/')}/responses"
            self._log_responses_request("openai_client", endpoint, body)
            http_client = None
            if self.api_key:
                client = OpenAI(
                    base_url=base_url,
                    api_key=self.api_key,
                    project=self.openai_project,
                    default_headers=headers or None,
                    timeout=self.timeout_s,
                )
            else:
                try:
                    import httpx
                    try:
                        from oci_openai import OciInstancePrincipalAuth
                    except Exception:
                        from oci_openai_auth import OciInstancePrincipalAuth
                except Exception:
                    return None, "python packages providing OciInstancePrincipalAuth (for example 'oci-openai') and 'httpx' are required for instance-principal Responses auth"
                http_client = httpx.Client(auth=OciInstancePrincipalAuth(), headers=headers)
                client = OpenAI(
                    api_key="OCI",
                    base_url=base_url,
                    project=self.openai_project,
                    http_client=http_client,
                    timeout=self.timeout_s,
                )
            response = client.responses.create(**body)
            if hasattr(response, "model_dump"):
                payload = response.model_dump()
            elif hasattr(response, "to_dict"):
                payload = response.to_dict()
            elif isinstance(response, dict):
                payload = response
            else:
                payload = {
                    "output_text": str(getattr(response, "output_text", "") or "").strip(),
                }
            return payload if isinstance(payload, dict) else None, "response_not_object"
        except Exception as exc:  # noqa: BLE001
            return None, f"openai_client_failed ({_squash_error(exc)})"
        finally:
            try:
                if "http_client" in locals() and locals()["http_client"] is not None:
                    locals()["http_client"].close()
            except Exception:
                pass

    def _native_chat_payload(self, system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> dict[str, Any]:
        prompt = f"System instructions:\n{system_prompt}\n\nUser request:\n{user_prompt}"
        return {
            "compartmentId": self.compartment_id,
            "servingMode": {
                "servingType": "ON_DEMAND",
                "modelId": self.model_id,
            },
            "chatRequest": {
                "apiFormat": "GENERIC",
                "isStream": False,
                "temperature": temperature,
                "maxCompletionTokens": max_tokens,
                "messages": [
                    {
                        "role": "USER",
                        "content": [{"type": "TEXT", "text": prompt}],
                    }
                ],
            },
        }

    def _complete_oci_native(self, system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> tuple[str | None, str | None]:
        if not self.compartment_id:
            return None, "missing OCI_COMPARTMENT_OCID"
        url = f"{self.endpoint}/20231130/actions/chat"
        body = self._native_chat_payload(system_prompt, user_prompt, max_tokens, temperature)
        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url=url, data=payload, method="POST", headers=self._headers())
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                parsed = json.loads(response.read().decode("utf-8"))
                text = _extract_native_chat_text(parsed)
                if text:
                    return text, None
                return None, "response did not contain text"
        except urllib.error.HTTPError as exc:
            return None, _http_error_detail(exc)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            return None, f"network/timeout/parse failure ({_squash_error(exc)})"

    def _genai_client(self) -> Any:
        import oci

        region = self._effective_region()
        signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
        return oci.generative_ai_inference.GenerativeAiInferenceClient(
            config={"region": region},
            signer=signer,
            service_endpoint=f"https://inference.generativeai.{region}.oci.oraclecloud.com",
        )

    def _complete_oci_sdk_signed(self, system_prompt: str, user_prompt: str, max_tokens: int, temperature: float) -> tuple[str | None, str | None]:
        if not self.compartment_id:
            return None, "missing OCI_COMPARTMENT_OCID"
        try:
            import oci
        except Exception:
            return None, "python package 'oci' not installed for instance-principal signing"
        try:
            client = self._genai_client()
            models = oci.generative_ai_inference.models
            chat_details = models.ChatDetails(
                compartment_id=self.compartment_id,
                serving_mode=models.OnDemandServingMode(
                    model_id=self.model_id,
                ),
                chat_request=models.GenericChatRequest(
                    api_format="GENERIC",
                    is_stream=False,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    messages=[
                        models.Message(
                            role="SYSTEM",
                            content=[models.TextContent(text=system_prompt)],
                        ),
                        models.Message(
                            role="USER",
                            content=[models.TextContent(text=user_prompt)],
                        ),
                    ],
                ),
            )
            response = client.chat(chat_details=chat_details)
            parsed = response.data
            if not isinstance(parsed, dict):
                try:
                    parsed = oci.util.to_dict(parsed)
                except Exception:  # noqa: BLE001
                    if hasattr(parsed, "to_dict"):
                        parsed = parsed.to_dict()
                    else:
                        parsed = {}
            text = _extract_native_chat_text(parsed) or _extract_text(parsed)
            if text:
                return text, None
            return None, "response did not contain text"
        except Exception as exc:  # noqa: BLE001
            return None, f"signed call failed ({_squash_error(exc)})"


class AgentTextService:
    def __init__(self) -> None:
        self.client = OciGenAIClient()
        self.cache: dict[tuple[str, str, str, str, str], str] = {}
        self.cache_version = "v3"
        self.live_count = 0
        self.fallback_count = 0
        self.last_error = ""
        self.last_attempts: list[str] = []
        self.last_mode = "none"

    def generate(
        self,
        role_id: str,
        phase_id: str,
        task: str,
        context: dict[str, Any],
        fallback: str,
        max_words: int = 200,
    ) -> str:
        system_prompt = AGENT_SYSTEM_PROMPTS.get(role_id)
        if not system_prompt:
            return fallback
        safe_max_words = max(80, min(int(max_words or 200), 800))
        # Allocate enough completion tokens so responses do not get cut off mid-sentence.
        max_tokens = max(320, min(1800, int(safe_max_words * 2.5)))
        context_json = json.dumps(context, sort_keys=True, ensure_ascii=True)
        cache_key = (role_id, phase_id, task, context_json, f"{self.cache_version}:{safe_max_words}:{max_tokens}")
        if cache_key in self.cache:
            return self.cache[cache_key]

        user_prompt = (
            f"Phase: {phase_id}\n"
            f"Task: {task}\n"
            f"Context: {context_json}\n"
            "Output format (strict):\n"
            "STANCE: long|short|neutral\n"
            "CONFIDENCE: 0-100\n"
            f"TEXT: one concise paragraph up to {safe_max_words} words. End with a complete sentence."
        )
        candidate = self.client.complete(system_prompt, user_prompt, max_tokens=max_tokens)
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
        collapsed = " ".join(candidate.split())
        if not collapsed:
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
        _llm_log(f"role={role_id} phase={phase_id} mode={self.last_mode} words={len(collapsed.split())}")
        self.cache[cache_key] = collapsed
        return collapsed

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
