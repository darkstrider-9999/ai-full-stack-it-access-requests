"""Ordered office policy and human-readable review records.

This module routes validated facts. It does not extract facts, grant access,
record approval or execute instructions found in source messages.
"""
from pathlib import Path
from typing import Any, Mapping

from .response_gate import Provider, extract_checked
from .sources import Source


REQUIRED = (
    "request_id", "request_date", "requester", "team", "system", "environment",
    "permission", "business_purpose", "start_date", "end_date", "manager",
)
OWNER = ("review", "System Owner", "review-request")
SECURITY = ("review", "Security Review", "review-risk-then-owner")
CLARIFY = ("needs-information", "IT Service Desk", "clarify")
WITHDRAW = ("review", "IT Service Desk", "confirm-withdrawal")
INSPECT = ("review", "IT Service Desk", "inspect-message")
HUMAN_GUIDANCE = "Await human review. A named manager is a contact; approval is an explicit human decision."
SECURITY_GUIDANCE = (
    "Await human review. Security Review examines risk and forwards its "
    "recommendation to the system owner for a decision. A named manager is a contact."
)


def _add_issue(issues: list[str], issue: str) -> None:
    if issue not in issues:
        issues.append(issue)


def _policy_route(extraction: dict[str, Any], source: Source) -> tuple[str, str, str]:
    """Enrich diagnostics, then apply the five policy rules in order.

    Issue prefixes are the Q-43/Q-51 conventions approved by the user. Other
    warnings are retained without inventing a blanket clarification rule.
    """
    issues = extraction["issues"]
    for field in REQUIRED:
        if extraction[field] is None:
            explained = any(
                field in issue and issue.startswith(("missing ", "conflicting ", "unresolved "))
                for issue in issues
            )
            if not explained:
                _add_issue(issues, "missing " + field)
    if extraction["kind"] is None and not any("kind" in issue for issue in issues):
        _add_issue(issues, "unresolved kind")

    request_id = extraction["request_id"]
    mismatch = request_id is not None and request_id != Path(source.document_id).stem
    if mismatch and not any(issue.startswith("identifier mismatch") for issue in issues):
        _add_issue(issues, f"identifier mismatch: request_id {request_id} differs from document filename {source.document_id}")

    start, end = extraction["start_date"], extraction["end_date"]
    reversed_dates = start is not None and end is not None and start > end
    if reversed_dates:
        _add_issue(issues, "start_date after end_date")

    # Recognize the explicit override form supplied by the policy examples.
    # Broader quoted/negated/obfuscated classification remains open under Q-45;
    # provider-reported override issues are also honored as extraction facts.
    marker = source.message.find("SYSTEM OVERRIDE:")
    override = marker >= 0 or any(issue.startswith("instruction-override attempt") for issue in issues)
    if override:
        _add_issue(issues, "instruction-override attempt")
        if marker >= 0:
            attempted_instruction = source.message[marker:]
            if not any(attempted_instruction in quote for quote in extraction["supporting_quotes"]):
                extraction["supporting_quotes"].append(attempted_instruction)

    # Rule 1 precedes even withdrawal or missing-key clarification.
    if override:
        return INSPECT
    # Rule 2 permits all other fields to be absent or have issues.
    if extraction["kind"] == "withdrawal" and request_id is not None and extraction["request_date"] is not None:
        return WITHDRAW

    clarification_issue = any(
        issue.startswith(("conflicting ", "identifier mismatch", "start_date after end_date"))
        for issue in issues
    )
    # Rule 3, including the clarified incomplete-withdrawal fallback (Q-49).
    if (
        extraction["kind"] in (None, "other", "withdrawal")
        or any(extraction[field] is None for field in REQUIRED)
        or mismatch or reversed_dates or clarification_issue
    ):
        return CLARIFY
    # Rule 4 is production OR elevated permission, after completeness checks.
    if extraction["environment"] == "production" or extraction["permission"] in ("write", "admin"):
        return SECURITY
    # Schema validation plus the earlier checks leave complete staging/read.
    return OWNER


def prepare_review(
    source: Source,
    provider: Provider,
    *,
    attempt_id: str,
    configuration: Mapping[str, Any],
) -> dict[str, Any]:
    """Run the shared response gate and prepare provenance, routing and evidence."""
    record = extract_checked(source, provider, attempt_id=attempt_id, configuration=configuration)
    record.update(
        document_id=source.document_id,
        source_sha256=source.source_sha256,
        attempt_id=attempt_id,
        evidence=[],
        review_guidance=HUMAN_GUIDANCE,
    )
    if record["extraction"] is None:
        # Refusal/errors and Q-80 grounding clarification keep the gate's route.
        return record

    status, owner, action = _policy_route(record["extraction"], source)
    record.update(
        status=status,
        owner=owner,
        next_action=action,
        evidence=list(record["extraction"]["supporting_quotes"]),
        review_guidance=SECURITY_GUIDANCE if owner == "Security Review" else HUMAN_GUIDANCE,
    )
    return record


def render_review(record: Mapping[str, Any]) -> str:
    """Display a review record as text, preserving exact source evidence."""
    lines = [
        f"Document: {record['document_id']}",
        f"Source SHA-256: {record['source_sha256']}",
        f"Attempt: {record['attempt_id']}",
        f"Response source: {record['response_source'] or 'no response observed'}",
        f"Status: {record['status']}",
        f"Owner: {record['owner']}",
        f"Next action: {record['next_action']}",
        record["review_guidance"],
    ]
    extraction = record["extraction"]
    if extraction is not None:
        lines.append("Extracted facts:")
        for field in ("request_id", "request_date", "kind", *REQUIRED[2:]):
            value = extraction[field]
            lines.append(f"  {field}: {value if value is not None else 'null'}")
        lines.append("Issues:")
        lines.extend(f"  {issue}" for issue in extraction["issues"])
        lines.append("Source evidence (untrusted message data):")
        lines.extend(record["evidence"])
    if record["error"] is not None:
        error = record["error"]
        lines.append(f"{error['code']}: {error['description']}")
        if "replay_command" in error:
            lines.append(error["replay_command"])
    return "\n".join(lines) + "\n"
