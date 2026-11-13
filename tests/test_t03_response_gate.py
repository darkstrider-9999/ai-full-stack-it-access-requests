"""T03: completion, parsing, schema and date-presentation gates, before routing.

Structural variants are authored mutations of B001, not measured outputs.
Valid enum/null controls exercise schema only, without claiming source accuracy.
"""
import json
import unittest
from pathlib import Path

from support.offline_guard import install
from access_intake.providers import MockProvider
from access_intake.sources import load_source

install()
ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "tests/mock_responses"
SCALARS = (
    "request_id", "request_date", "kind", "requester", "team", "system",
    "environment", "permission", "business_purpose", "start_date", "end_date", "manager",
)
ARRAYS = ("supporting_quotes", "issues")
ENUMS = {
    "kind": ("new", "change", "withdrawal", "other", None),
    "environment": ("staging", "production", None),
    "permission": ("read", "write", "admin", None),
}
ATTEMPT = "attempt-t03"
CONFIG = {"provider": "mock"}


class CaseMock(MockProvider):
    """Select a saved envelope from a fixture collection without modifying it."""

    def __init__(self, name):
        super().__init__(MOCKS, script=[{"response": "schema-cases.json"}])
        self.name = name

    def extract(self, request):
        return super().extract(request)["cases"][self.name]


class T03ResponseGate(unittest.TestCase):
    def checked(self, provider, filename="IT-001.json"):
        from access_intake.response_gate import extract_checked

        return extract_checked(
            load_source(ROOT / "materials/requests" / filename), provider,
            attempt_id=ATTEMPT, configuration=CONFIG,
        )

    def assert_failure(self, result, code, response_source="mock"):
        self.assertIsNone(result["extraction"])
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["owner"], "IT Service Desk")
        self.assertEqual(result["next_action"], "retry-extraction")
        self.assertEqual(result["response_source"], response_source)
        self.assertEqual(result["attempted_configuration"], CONFIG)
        error = result["error"]
        self.assertEqual(error["code"], code)
        self.assertTrue(error["description"].strip())
        self.assertEqual(error["attempt_id"], ATTEMPT)
        self.assertIn("retry --attempt " + ATTEMPT, error["replay_command"])
        self.assertNotIn("dummy-test-secret", json.dumps(result))
        self.assertNotIn("raw private provider detail", json.dumps(result))

    def test_t03a_provider_failure_then_unaffected_success(self):
        provider = MockProvider(MOCKS, script=[
            {"error": RuntimeError("raw private provider detail: dummy-test-secret")},
            {"response": "it-002.json"},
        ])
        self.assert_failure(self.checked(provider), "provider_error", response_source=None)
        self.assertEqual(provider.call_count, 1, "no implicit retry")
        result = self.checked(provider, "IT-002.json")
        self.assertIsNone(result["error"])
        self.assertEqual(result["response_source"], "mock")
        # Independently transcribed B002 facts, rather than comparison to the reply.
        self.assertEqual(result["extraction"], {
            "request_id": "IT-002", "request_date": "2026-10-22", "kind": "new",
            "requester": "Owen Reed", "team": "Payments", "system": "Ledger",
            "environment": "production", "permission": "write",
            "business_purpose": "We have several settlement entries associated with the wrong reconciliation batch.",
            "start_date": "2026-10-24", "end_date": "2026-10-25", "manager": "Elena Park",
            "supporting_quotes": [load_source(ROOT / "materials/requests/IT-002.json").message],
            "issues": [],
        })
        self.assertEqual(provider.call_count, 2)

    def test_t03b_refusal_wins_over_plausible_output(self):
        for fixture in ("refusal.json", "refusal-with-text.json"):
            with self.subTest(fixture=fixture):
                provider = MockProvider(MOCKS, script=[{"response": fixture}])
                self.assert_failure(self.checked(provider), "refused")
                self.assertEqual(provider.call_count, 1)

    def test_t03c_interruption_wins_over_partial_or_complete_text(self):
        for fixture in ("interrupted.json", "interrupted-complete.json"):
            with self.subTest(fixture=fixture):
                provider = MockProvider(MOCKS, script=[{"response": fixture}])
                self.assert_failure(self.checked(provider), "interrupted")
                self.assertEqual(provider.call_count, 1)

    def test_t03d_invalid_structures_are_rejected_before_use(self):
        from access_intake.response_gate import ResponseValidationError, validate_structure

        parse_errors = ("malformed", "partial", "prose", "fenced", "two_objects", "trailing_prose", "nan")
        invalid = {name: "invalid_json" for name in parse_errors}
        invalid.update({name: "invalid_schema" for name in ("array_root", "null_root", "string_root", "number_root", "bool_root", "extra_key")})
        for field in (*SCALARS, *ARRAYS):
            invalid["missing_" + field] = "invalid_schema"
        for field in SCALARS:
            for kind in ("integer", "float", "boolean", "array", "object"):
                invalid[f"type_{field}_{kind}"] = "invalid_schema"
        for field in ENUMS:
            invalid["enum_" + field] = "invalid_schema"
        for field in ARRAYS:
            for kind in ("null", "string", "integer", "boolean", "object"):
                invalid[f"array_type_{field}_{kind}"] = "invalid_schema"
            for kind in ("null", "integer", "boolean", "array", "object"):
                invalid[f"array_item_{field}_{kind}"] = "invalid_schema"

        saved = json.loads((MOCKS / "schema-cases.json").read_text())["cases"]
        for name, code in invalid.items():
            with self.subTest(case=name):
                with self.assertRaises(ResponseValidationError) as caught:
                    validate_structure(saved[name]["output_text"])
                self.assertEqual(caught.exception.code, code)
                provider = CaseMock(name)
                self.assert_failure(self.checked(provider), code)
                self.assertEqual(provider.call_count, 1)

    def test_t03d_valid_schema_controls_do_not_require_completeness(self):
        from access_intake.response_gate import validate_structure

        saved = json.loads((MOCKS / "schema-cases.json").read_text())["cases"]
        # All missing facts remain null; completeness belongs to the ordered router.
        all_null = validate_structure(saved["valid_all_null"]["output_text"])
        self.assertEqual(all_null, {**dict.fromkeys(SCALARS), "supporting_quotes": [], "issues": []})
        for field, values in ENUMS.items():
            for index, value in enumerate(values):
                with self.subTest(field=field, value=value):
                    result = validate_structure(saved[f"valid_{field}_{index}"]["output_text"])
                    self.assertEqual(result[field], value)
                    self.assertEqual(set(result), set((*SCALARS, *ARRAYS)))
        for field in SCALARS:
            with self.subTest(null_field=field):
                self.assertIsNone(validate_structure(saved["valid_null_" + field]["output_text"])[field])
        result = validate_structure(saved["valid_empty_arrays"]["output_text"])
        self.assertEqual(result["supporting_quotes"], [])
        self.assertEqual(result["issues"], [])
        result = validate_structure(saved["valid_issue_strings"]["output_text"])
        self.assertEqual(result["issues"], ["missing end_date"])

    def test_t03d_date_presentation_is_separate_from_schema(self):
        from access_intake.response_gate import validate_structure

        saved = json.loads((MOCKS / "schema-cases.json").read_text())["cases"]
        for field in ("request_date", "start_date", "end_date"):
            for style in ("unpadded", "slash", "datetime", "relative"):
                name = f"date_{field}_{style}"
                with self.subTest(case=name):
                    # These strings satisfy the unchanged schema, but not YYYY-MM-DD.
                    self.assertIsInstance(validate_structure(saved[name]["output_text"])[field], str)
                    self.assert_failure(self.checked(CaseMock(name)), "invalid_date")

    def test_t03d_completion_status_is_required_before_parsing(self):
        for name in ("missing_status", "unknown_status"):
            with self.subTest(case=name):
                self.assert_failure(self.checked(CaseMock(name)), "invalid_completion")

    def test_t03e_ungrounded_response_requires_human_clarification(self):
        """User Q-80: needs-information / human clarification, not extraction retry."""
        from access_intake.response_gate import validate_structure

        for fixture in ("inexact-quote.json", "unsupported-fact.json"):
            with self.subTest(fixture=fixture):
                saved = json.loads((MOCKS / fixture).read_text())
                # Both pass the structural gate; source grounding is independent.
                self.assertEqual(set(validate_structure(saved["output_text"])), set((*SCALARS, *ARRAYS)))
                provider = MockProvider(MOCKS, script=[{"response": fixture}])
                result = self.checked(provider)
                self.assertIsNone(result["extraction"], "ungrounded facts must not be exposed as usable extraction")
                self.assertEqual(result["status"], "needs-information")
                self.assertEqual(result["owner"], "IT Service Desk")
                self.assertEqual(result["next_action"], "clarify")
                self.assertEqual(result["response_source"], "mock")
                self.assertEqual(result["error"]["code"], "grounding_mismatch")
                self.assertTrue(result["error"]["description"].strip())
                self.assertEqual(result["error"]["attempt_id"], ATTEMPT)
                self.assertEqual(provider.call_count, 1, "no automatic repair or retry")
