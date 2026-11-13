"""OpenAI Responses API using an injected, non-streaming HTTP boundary.

Contract references are recorded in tests/mock_responses/provider-contract.json.
No facilitator model access or live extraction performance has been observed.
Uses Python's HTTP/TLS stack and the JSON Schema validator pinned in uv.lock;
this adapter uses no provider SDK or implicit retry.
"""
import copy
import json
import math
import os
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .providers import ProviderError
from .response_gate import SCHEMA


DEFAULT_ENDPOINT = "https://api.openai.com/v1/responses"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class ConfigurationError(ValueError):
    """A safe operator-facing configuration problem, without credential values."""


def _redact(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]")
    if isinstance(value, dict):
        return {_redact(key, secret): _redact(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secret) for item in value]
    return value


def load_api_key(*, lookup=None) -> str:
    """Production credential loader; tests must supply their own lookup."""
    value = (os.environ.get if lookup is None else lookup)("API_KEY")
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("API_KEY is required for the OpenAI provider.")
    if "\r" in value or "\n" in value:
        raise ConfigurationError("API_KEY must be a single-line credential.")
    return value


def validate_configuration(configuration: Mapping[str, Any]) -> dict[str, Any]:
    """Validate transport settings before storing or using a provider config."""
    model = configuration.get("model")
    if not isinstance(model, str) or not model.strip():
        raise ConfigurationError("An explicit model ID is required; model access is not verified by this application.")
    endpoint = configuration.get("endpoint", DEFAULT_ENDPOINT)
    try:
        parsed = urlsplit(endpoint)
        valid_endpoint = (parsed.scheme == "https" and bool(parsed.hostname)
                          and parsed.username is None and parsed.password is None
                          and not parsed.query and not parsed.fragment)
        parsed.port  # Validate a configured port without echoing the URL on error.
    except (TypeError, ValueError, AttributeError):
        valid_endpoint = False
    if not valid_endpoint:
        raise ConfigurationError("Provider endpoint must be HTTPS with no embedded credentials, query or fragment.")
    timeout = configuration.get("timeout", 60)
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool)
        or not math.isfinite(timeout) or timeout <= 0):
        raise ConfigurationError("Provider timeout must be finite and positive.")
    output_limit = configuration.get("output_limit", 4096)
    if not isinstance(output_limit, int) or isinstance(output_limit, bool) or output_limit <= 0:
        raise ConfigurationError("Provider output limit must be a positive integer.")
    temperature = configuration.get("temperature")
    if temperature is not None and (
        not isinstance(temperature, (int, float)) or isinstance(temperature, bool)
        or not math.isfinite(temperature) or not 0 <= temperature <= 2
    ):
        raise ConfigurationError("Temperature must be omitted or a finite number between 0 and 2.")
    return {"provider": "openai", "model": model, "endpoint": endpoint,
            "timeout": timeout, "output_limit": output_limit, "temperature": temperature}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # Authentication belongs to the configured destination, not a redirect.
        raise HTTPError(request.full_url, code, "redirect rejected", headers, response)


class HTTPTransport:
    """One HTTPS POST with a finite I/O timeout; no redirects or retries.

    The response body is bounded. The timeout applies to socket operations; it
    is not a total wall-clock SLA. Tests inject an opener and never open sockets.
    """
    def __init__(self, *, opener=None):
        self._opener = opener if opener is not None else build_opener(_NoRedirect()).open

    def post(self, url, *, headers, body, timeout):
        request = Request(url, headers=headers, data=body, method="POST")
        try:
            with self._opener(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                status = response.status
        except HTTPError as error:
            try:
                status, raw = error.code, error.read(MAX_RESPONSE_BYTES + 1)
            finally:
                error.close()
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise TimeoutError("provider request timed out") from None
            raise OSError("provider transport unavailable") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError("invalid_provider_response")
        return status, raw


class ResponsesProvider:
    """Credentials are injected separately and are never part of configuration."""
    def __init__(self, configuration: Mapping[str, Any], *, credential: str, transport=None):
        self.configuration = validate_configuration(configuration)
        if not isinstance(credential, str) or not credential.strip() or "\n" in credential or "\r" in credential:
            raise ConfigurationError("A nonempty single-line provider credential is required.")
        self._credential = credential
        self._transport = HTTPTransport() if transport is None else transport

    def _payload(self, request):
        config = self.configuration
        payload = {
            "model": config["model"], "instructions": request["instructions"],
            "input": [{"role": "user", "content": [{"type": "input_text", "text": request["input"]["message"]}]}],
            "max_output_tokens": config["output_limit"], "stream": False, "store": False,
            "text": {"format": {"type": "json_schema", "name": "access_request", "strict": True,
                                 "schema": copy.deepcopy(request.get("schema", SCHEMA))}},
        }
        if config["temperature"] is not None:
            payload["temperature"] = config["temperature"]
        return payload

    def request_evidence(self, request) -> dict[str, Any]:
        """Construct redacted request evidence without making a model call."""
        evidence = {
            "evidence_kind": "constructed_request", "live_observation": False,
            "configuration": self.configuration,
            "model_access": "unverified; no facilitator-key compatibility claim",
            "request": {"method": "POST", "url": self.configuration["endpoint"],
                        "headers": {"Content-Type": "application/json", "Authorization": "Bearer [REDACTED]"},
                        "body": self._payload(request), "timeout": self.configuration["timeout"]},
            "response_handling": {
                "completion": "access_intake/openai_provider.py:ResponsesProvider._normalize; completed response and assistant message required",
                "refusal": "refusal content takes precedence over accompanying output text; safe refusal category only",
                "interruption": "nonterminal/cancelled/incomplete responses are not usable extraction; no polling or implicit retry",
                "http_errors": "local allowlisted error categories; HTTP bodies and exception details are not logged",
                "schema_validation": "access_intake/response_gate.py:validate_structure; unchanged supplied schema, Draft 2020-12",
                "grounding": "exact quote/literal-fact checks; mismatches require human clarification; semantic accuracy remains separately evaluated",
            },
        }
        # Protect even constructed evidence if a caller put the credential into
        # a source/configuration string. It is never written into this artifact.
        return _redact(evidence, self._credential)

    def extract(self, request) -> dict[str, Any]:
        body = json.dumps(self._payload(request), ensure_ascii=False).encode("utf-8")
        try:
            status, raw = self._transport.post(
                self.configuration["endpoint"],
                headers={"Content-Type": "application/json", "Authorization": "Bearer " + self._credential},
                body=body, timeout=self.configuration["timeout"],
            )
        except AssertionError:
            raise  # Isolation failures must fail tests, not become provider records.
        except ProviderError:
            raise
        except TimeoutError:
            raise ProviderError("timeout") from None
        except OSError:
            raise ProviderError("unavailable") from None
        if not isinstance(status, int) or isinstance(status, bool):
            raise ProviderError("invalid_provider_response")
        if not 200 <= status < 300:
            code = {400: "invalid_request", 401: "authentication_error", 403: "permission_denied",
                    404: "invalid_request", 408: "timeout", 429: "rate_limited"}.get(status)
            if status == 429:
                try:
                    upstream_code = json.loads(raw).get("error", {}).get("code")
                except (ValueError, TypeError, AttributeError):
                    upstream_code = None
                if upstream_code in ("credit_balance_exhausted", "insufficient_quota", "organization_spend_limit_exceeded", "project_spend_limit_exceeded"):
                    code = "quota_exceeded"
            raise ProviderError(code or ("unavailable" if status >= 500 else "invalid_provider_response"))
        try:
            response = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            raise ProviderError("invalid_provider_response") from None
        return self._normalize(response)

    def _normalize(self, response):
        if not isinstance(response, dict):
            raise ProviderError("invalid_provider_response")
        if response.get("error") is not None or response.get("status") == "failed":
            raise ProviderError("provider_error")
        model = response.get("model")
        if not isinstance(model, str) or not model.strip() or self._credential in model:
            raise ProviderError("invalid_provider_response")
        normalized = {"response_source": "openai/" + model, "status": response.get("status"), "output_text": ""}
        output = response.get("output")
        # Refusal is a content item, not a promise that output text is absent.
        messages = [item for item in output if isinstance(item, dict) and item.get("type") == "message" and item.get("role") == "assistant"] if isinstance(output, list) else []
        for message in messages:
            content = message.get("content")
            if isinstance(content, list) and any(isinstance(part, dict) and part.get("type") == "refusal" for part in content):
                return {**normalized, "status": "refused", "refusal": "Provider refused extraction."}
        if normalized["status"] in ("incomplete", "cancelled", "queued", "in_progress"):
            return {**normalized, "status": "incomplete"}
        if normalized["status"] != "completed":
            return {**normalized, "status": "invalid"}  # Shared gate reports invalid_completion.
        if not messages:
            raise ProviderError("invalid_provider_response")
        parts = []
        for message in messages:
            if message.get("status") != "completed":
                return {**normalized, "status": "incomplete"}
            content = message.get("content")
            if not isinstance(content, list):
                raise ProviderError("invalid_provider_response")
            for part in content:
                if not isinstance(part, dict) or part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                    raise ProviderError("invalid_provider_response")
                parts.append(part["text"])
        if not parts:
            raise ProviderError("invalid_provider_response")
        # Never persist a credential echoed by upstream text. Redacted text must
        # still pass the same JSON/schema/grounding gates as every other reply.
        return {**normalized, "output_text": "".join(parts).replace(self._credential, "[REDACTED]")}
