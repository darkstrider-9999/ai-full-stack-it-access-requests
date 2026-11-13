"""Exact comparison against independently authored gold, with no model calls."""
import copy
import json
import sqlite3
from pathlib import Path


SCALARS = (
    "request_id", "request_date", "kind", "requester", "team", "system", "environment",
    "permission", "business_purpose", "start_date", "end_date", "manager",
)


def read_current_records(path: str | Path) -> list[dict]:
    """Open an existing database read-only, without schema initialization."""
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        rows = connection.execute(
            "SELECT r.record_json FROM documents d JOIN results r ON r.attempt_id=d.latest_attempt ORDER BY d.document_id"
        ).fetchall()
        return [json.loads(row[0]) for row in rows]
    finally:
        connection.close()


def _validate_gold(gold):
    if gold.get("scalar_fields") != list(SCALARS):
        raise ValueError("gold must declare the twelve scalar fields in the documented order")
    cases = gold.get("cases")
    if not isinstance(cases, list) or len(cases) != 21:
        raise ValueError("this benchmark requires all 21 cases and 252 scalar slots")
    ids, identities = set(), set()
    for case in cases:
        identity = (case["scope"], case["document_id"])
        if case["case_id"] in ids or identity in identities:
            raise ValueError("duplicate gold case or scoped document")
        ids.add(case["case_id"])
        identities.add(identity)
        if set(case["expected"]) != set(SCALARS):
            raise ValueError("each gold case must contain all twelve scalar expectations")
        if any(value is not None and not isinstance(value, str) for value in case["expected"].values()):
            raise ValueError("gold scalar expectations must be strings or null")
        if not case.get("source_quotes") or not isinstance(case.get("route"), dict):
            raise ValueError("gold must retain source evidence and expected routing")
    return cases


def _quote_problems(case, extraction):
    quotes = extraction.get("supporting_quotes")
    if not isinstance(quotes, list) or not quotes:
        return ["missing_or_invalid_quotes"]
    problems = []
    if any(not isinstance(quote, str) or not any(quote in source for source in case["source_quotes"]) for quote in quotes):
        problems.append("inexact_quote")
    for fragment in case["required_quote_fragments"]:
        if not any(isinstance(quote, str) and fragment in quote for quote in quotes):
            problems.append("missing_required_evidence: " + fragment)
    return problems


def _issue_problems(case, extraction):
    issues = extraction.get("issues")
    if not isinstance(issues, list) or any(not isinstance(issue, str) for issue in issues):
        return ["invalid_issues"]
    # Check explanations of the affected facts, not accidental wording/order.
    return ["missing_issue_explanation: " + ", ".join(tokens)
            for tokens in case["issue_checks"]
            if not any(all(token.casefold() in issue.casefold() for token in tokens) for issue in issues)]


def _candidate_category(case, field, expected, actual):
    """Triage candidates only; human factual adjudication remains separate."""
    if field == "business_purpose":
        return "wording_difference_requires_review"
    if actual is None and expected is not None:
        return "omission"
    if expected is None and actual is not None:
        if any(any("conflict" in token for token in tokens) and field in tokens for tokens in case["issue_checks"]):
            return "unresolved_conflict_resolved"
        return "unsupported_assertion"
    if isinstance(actual, str) and not any(actual.casefold() in source.casefold() for source in case["source_quotes"]):
        return "unsupported_assertion"
    return "explicit_value_or_correction_requires_review"


def score_accuracy(gold: dict, observations: dict[str, list[dict]]) -> dict:
    """Use a fixed denominator; never learn expectations from observations."""
    cases = _validate_gold(gold)
    measured = {}
    for scope, records in observations.items():
        for record in records:
            key = (scope, record["document_id"])
            if key in measured:
                raise ValueError("duplicate measured scoped document")
            measured[key] = record
    per_field = {field: {"matched": 0, "total": 21} for field in SCALARS}
    checks = {name: {"passed": 0, "total": 21, "discrepancies": []} for name in ("quotes", "issues", "routing")}
    discrepancies, provenance = [], []
    usable_cases = 0
    for case in cases:
        record = measured.get((case["scope"], case["document_id"]))
        extraction = None if record is None else record.get("extraction")
        usable = record is not None and isinstance(extraction, dict) and record.get("error") is None
        usable_cases += int(usable)
        base = {key: case[key] for key in ("case_id", "scope", "document_id")}
        provenance.append({**base, **{key: copy.deepcopy(record.get(key)) if record is not None else None for key in (
            "response_source", "configuration", "policy_hash", "attempt_id", "source_sha256",
        )}})
        for field in SCALARS:
            expected = case["expected"][field]
            actual = extraction.get(field) if isinstance(extraction, dict) else None
            if usable and field in extraction and type(expected) is type(actual) and actual == expected:
                per_field[field]["matched"] += 1
                continue
            reason = ("missing_record" if record is None else "failed_extraction" if not usable
                      else "missing_field" if field not in extraction else "field_mismatch")
            discrepancies.append({
                **base, "field": field, "expected": expected, "actual": actual, "reason": reason,
                "candidate_category": _candidate_category(case, field, expected, actual),
                "factual_review": "human_review_required", "source_quotes": copy.deepcopy(case["source_quotes"]),
            })
        problems = {
            "quotes": _quote_problems(case, extraction) if usable else ["missing_or_failed_extraction"],
            "issues": _issue_problems(case, extraction) if usable else ["missing_or_failed_extraction"],
            "routing": [f"{field}: expected {value!r}, observed {record.get(field) if record else None!r}"
                        for field, value in case["route"].items() if record is None or record.get(field) != value],
        }
        for name, reasons in problems.items():
            if reasons:
                checks[name]["discrepancies"].append({**base, "reasons": reasons, "source_quotes": copy.deepcopy(case["source_quotes"])})
            else:
                checks[name]["passed"] += 1

    matched = sum(field["matched"] for field in per_field.values())
    all_mock = (any(row["response_source"] == "mock" for row in provenance)
                and all(row["response_source"] in ("mock", None) for row in provenance))
    interpretation = (
        "Mock fixture-path verification only; not live model extraction performance."
        if all_mock else
        "Mock/provider provenance is preserved per observation; live execution and model access are unverified by this comparison."
    )
    review = copy.deepcopy(gold.get("human_review", {"status": "pending"}))
    expected_identities = {(case["scope"], case["document_id"]) for case in cases}
    return {
        "scalar_score": {"matched": matched, "total": 252, "ratio": matched / 252},
        "per_field": per_field, "discrepancies": discrepancies, "checks": checks,
        "observations": provenance, "gold_review": review,
        "benchmark_complete": bool(review.get("status") == "reviewed" and review.get("reviewer") and usable_cases == 21),
        "interpretation": interpretation, "comparison_rules": gold["counting_rules"],
        "expected_facts": copy.deepcopy(cases), "gold_provenance": gold["provenance"],
        "unexpected_records": [{"scope": scope, "document_id": document} for scope, document in sorted(measured.keys() - expected_identities)],
        "factual_review_note": "Candidate categories require human adjudication, including omissions, unsupported assertions, incorrectly resolved conflicts and correction errors. Purpose wording differences are not automatically hallucinations.",
    }


def _cell(value):
    return json.dumps(value, ensure_ascii=False).replace("|", "\\|").replace("\n", " ")


def render_accuracy(report: dict) -> str:
    """Render procedure, every discrepancy, provenance and all expected facts."""
    score = report["scalar_score"]
    lines = ["# Accuracy comparison", "", report["interpretation"], "",
             f"Scalar exact matches: **{score['matched']} / {score['total']}** ({score['ratio']:.2%}).",
             "", report["comparison_rules"], "",
             f"Human gold review: **{report['gold_review']['status']}**. Complete benchmark: {str(report['benchmark_complete']).lower()}.",
             "No passing threshold is specified. A mock score is not evidence of live extraction accuracy.", "",
             report["factual_review_note"], "", "## Per-field counts", "",
             "| Field | Exact matches | Total |", "|---|---:|---:|"]
    for field, counts in report["per_field"].items():
        lines.append(f"| {field} | {counts['matched']} | {counts['total']} |")
    lines.extend(["", "## Scalar discrepancies", ""])
    if not report["discrepancies"]:
        lines.append("No scalar exact-match discrepancies in these supplied observations.")
    for mismatch in report["discrepancies"]:
        lines.extend([
            f"- {mismatch['case_id']} ({mismatch['scope']}/{mismatch['document_id']}), {mismatch['field']}: expected {_cell(mismatch['expected'])}; observed {_cell(mismatch['actual'])}. Reason: {mismatch['reason']}. Candidate: {mismatch['candidate_category']}; human review required.",
            "", "  Source quotes: " + _cell(mismatch["source_quotes"]), "",
        ])
    lines.extend(["", "## Separate quote, issue and routing checks", ""])
    for name, check in report["checks"].items():
        lines.append(f"{name}: {check['passed']} / {check['total']} cases.")
        for problem in check["discrepancies"]:
            lines.append(f"- {problem['case_id']}: {'; '.join(problem['reasons'])}")
            lines.extend(["", "  Source quotes: " + _cell(problem["source_quotes"]), ""])
        lines.append("")
    if report["unexpected_records"]:
        lines.extend(["Unexpected records (excluded from the fixed gold denominator):", "", "```json",
                      json.dumps(report["unexpected_records"], indent=2), "```", ""])
    lines.extend(["## Observation provenance", "", "Each entry labels mock/provider source, attempt, source hash and the saved configuration. This command makes no provider call.",
                  "", "State inputs: " + _cell(report.get("state_inputs", "provided in-memory observations")),
                  "", "```json", json.dumps(report["observations"], indent=2, ensure_ascii=False), "```", "",
                  "## Independently authored expected facts", "", report["gold_provenance"], ""])
    for case in report["expected_facts"]:
        lines.extend([f"### {case['case_id']} — {case['scope']}/{case['document_id']}", "",
                      f"Source: [{case['source']}](../{case['source']}); SHA-256: `{case['source_sha256']}`.",
                      "", "| Field | Expected value |", "|---|---|"])
        for field in SCALARS:
            lines.append(f"| {field} | {_cell(case['expected'][field])} |")
        lines.extend(["", "Expected routing: " + "; ".join(case["route"].values()) + ".",
                      "Required issue explanations (tokens): " + _cell(case["issue_checks"]),
                      "", "Source quotes (decoded message, JSON-escaped for exact display):", "", "```json",
                      json.dumps(case["source_quotes"], indent=2, ensure_ascii=False), "```", ""])
    return "\n".join(lines).rstrip() + "\n"
