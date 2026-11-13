"""Validate normalized provider responses before exposing any extraction."""
import json
import re
import shlex
from pathlib import Path
from typing import Any, Mapping, Protocol

from jsonschema import Draft202012Validator

from .prompts import build_request
from .sources import Source
from .providers import ProviderError


SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "snapshot.schema.json").read_text(encoding="utf-8"))
# The supplied schema has no $schema. Select its dialect explicitly without
# editing it, and never resolve remote schemas from provider-controlled data.
Draft202012Validator.check_schema(SCHEMA)
VALIDATOR = Draft202012Validator(SCHEMA)
DATE_FIELDS = ("request_date", "start_date", "end_date")
DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


class Provider(Protocol):
    def extract(self, request: Mapping[str, Any]) -> dict[str, Any]: ...


class ResponseValidationError(ValueError):
    """A safe category and description, excluding untrusted response contents."""

    def __init__(self, code: str, description: str) -> None:
        super().__init__(description)
        self.code = code


def _reject_constant(value: str) -> None:
    raise ValueError("non-JSON constant")


def validate_structure(text: str, *, schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Parse exactly one JSON value and validate the unchanged extraction schema.

    This function deliberately does not enforce completeness, date presentation
    or factual grounding. Nulls and all schema-valid enum controls pass here.
    """
    try:
        value = json.loads(text, parse_constant=_reject_constant)
    except (ValueError, TypeError, RecursionError):
        raise ResponseValidationError("invalid_json", "Response must contain exactly one complete JSON object.") from None
    validator = VALIDATOR if schema is None else Draft202012Validator(schema)
    if not validator.is_valid(value):
        raise ResponseValidationError("invalid_schema", "Response does not satisfy the extraction schema.")
    return value


def _check_completion(response: Any) -> None:
    if not isinstance(response, dict):
        raise ResponseValidationError("invalid_completion", "Provider returned no usable completion envelope.")
    if response.get("refusal") or response.get("status") == "refused":
        raise ResponseValidationError("refused", "Provider refused the extraction request.")
    if response.get("status") in ("incomplete", "interrupted"):
        raise ResponseValidationError("interrupted", "Provider response was interrupted or incomplete.")
    if response.get("status") != "completed":
        raise ResponseValidationError("invalid_completion", "Provider did not report a completed response.")


def _check_dates(extraction: Mapping[str, Any]) -> None:
    for field in DATE_FIELDS:
        value = extraction[field]
        if value is not None and DATE_PATTERN.fullmatch(value) is None:
            raise ResponseValidationError("invalid_date", "Extracted dates must use YYYY-MM-DD presentation.")
    # Calendar validity and reversed-date routing are separate policy questions.


def _check_grounding(extraction: Mapping[str, Any], message: str) -> None:
    if any(quote not in message for quote in extraction["supporting_quotes"]):
        raise ResponseValidationError("grounding_mismatch", "Supporting quotes do not match the decoded source; human clarification is required.")

    # Presence is a necessary check for literal facts, not proof that a model
    # selected the correct alternative, understood a correction or omitted none.
    # Kind is a semantic category (e.g. 'withdraw' -> 'withdrawal'), so it cannot
    # be checked by literal membership. Purpose may be a summary rather than an
    # exact excerpt and requires semantic evaluation in the accuracy workflow.
    for field in ("request_id", "request_date", "requester", "team", "system",
                  "environment", "permission", "start_date", "end_date", "manager"):
        value = extraction[field]
        if value is not None and value.casefold() not in message.casefold():
            raise ResponseValidationError("grounding_mismatch", "An extracted literal fact lacks source support; human clarification is required.")


def extract_checked(
    source: Source,
    provider: Provider,
    *,
    attempt_id: str,
    configuration: Mapping[str, Any],
    request_contract: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Make one injected provider call and return validated facts or a safe issue.

    Configuration must be credential-free. Raw provider exceptions/output are
    never copied into errors. A transport failure has no observed response
    source; attempted configuration is retained independently. Successful
    extraction still needs policy routing; no approval is implied here.
    """
    result = {
        "extraction": None,
        "error": None,
        "response_source": None,
        "attempted_configuration": dict(configuration),
    }
    try:
        request = build_request(source) if request_contract is None else request_contract["request"]
        response = provider.extract(request)
    except AssertionError:
        # Test isolation failures are programming failures, not provider errors.
        raise
    except ProviderError as error:
        failure = ResponseValidationError(error.code, ProviderError.DESCRIPTIONS[error.code])
    except TimeoutError:
        failure = ResponseValidationError("timeout", ProviderError.DESCRIPTIONS["timeout"])
    except Exception:
        failure = ResponseValidationError("provider_error", "Provider request failed. Inspect the attempted configuration and explicitly retry this attempt.")
    else:
        if isinstance(response, dict):
            result["response_source"] = response.get("response_source")
        try:
            _check_completion(response)
            schema = None if request_contract is None else request_contract["schema"]
            extraction = validate_structure(response.get("output_text"), schema=schema)
            _check_dates(extraction)
            _check_grounding(extraction, source.message)
        except ResponseValidationError as error:
            failure = error
        else:
            result["extraction"] = extraction
            return result

    clarification = failure.code == "grounding_mismatch"
    result.update(
        status="needs-information" if clarification else "error",
        owner="IT Service Desk",
        next_action="clarify" if clarification else "retry-extraction",
        error={
            "code": failure.code,
            "description": str(failure),
            "attempt_id": attempt_id,
        },
    )
    if not clarification:
        result["error"]["replay_command"] = "python3 -m access_intake retry --attempt " + shlex.quote(attempt_id)
    return result
