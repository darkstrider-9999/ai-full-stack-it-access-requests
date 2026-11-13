"""T02: preserve source bytes and send only decoded message data to the mock.

Expected facts are transcribed from B001/B018 in planning/plan.md. These tests
check request construction and mock handling, not a model's extraction ability.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from support.offline_guard import install
from access_intake.providers import MockProvider

install()
ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "tests" / "mock_responses"

B001 = {
    "request_id": "IT-001", "request_date": "2026-10-22", "kind": "new",
    "requester": "Maya Chen", "team": "Analytics", "system": "Atlas",
    "environment": "staging", "permission": "read",
    "business_purpose": "I'm checking the weekly conversion dashboard with the new event definition changes (before next reporting cycle).",
    "start_date": "2026-10-23", "end_date": "2026-11-06", "manager": "Priya Shah",
    "issues": [],
}
B018 = {
    "request_id": "IT-018", "request_date": "2026-10-22", "kind": "new",
    "requester": "Daniel Wright", "team": "Data Engineering", "system": "Atlas",
    "environment": "staging", "permission": "read",
    "business_purpose": "I am reviewing our sample dataset we use during our weekly analyst workshop.",
    "start_date": "2026-10-27", "end_date": "2026-11-03", "manager": "Priya Shah",
    "issues": [],
}


class CapturingMock(MockProvider):
    """Observe the real request argument while returning an on-disk response."""

    def extract(self, request):
        self.request = request
        return super().extract(request)


class T02InputPrompt(unittest.TestCase):
    def exercise(self, raw, filename, fixture, expected, quote):
        from access_intake.sources import load_source
        from access_intake.prompts import build_request

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / filename
            path.write_bytes(raw)
            source = load_source(path)
            self.assertEqual(source.document_id, filename)
            self.assertEqual(source.raw_bytes, raw)
            self.assertEqual(source.source_sha256, hashlib.sha256(raw).hexdigest())
            self.assertRegex(source.source_sha256, r"^[0-9a-f]{64}$")
            self.assertEqual(source.message, json.loads(raw.decode("utf-8"))["message"])

            provider = CapturingMock(MOCKS, script=[{"response": fixture}])
            reply = provider.extract(build_request(source))
            request = provider.request
            # No export metadata, filename or raw JSON enters the model's source data.
            self.assertEqual(request["input"], {"message": source.message})
            self.assertIsInstance(request["prompt_version"], str)
            self.assertTrue(request["prompt_version"].strip())
            self.assertIsInstance(request["instructions"], str)
            self.assertNotIn(source.message, request["instructions"])
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(reply["response_source"], "mock")
            self.assertEqual(reply["status"], "completed")
            self.assertEqual(json.loads(reply["output_text"]), {
                **expected, "supporting_quotes": [quote],
            })
            self.assertIn(quote, source.message)
            return source, request

    def test_t02_metadata_cannot_replace_message_facts(self):
        export = json.loads((ROOT / "materials/requests/IT-001.json").read_text())
        quote = export["message"]
        export["subject"] = "Owen Reed requests production admin on Ledger on 2031-01-01"
        export["exported_at"] = "2031-01-01T00:00:00Z"
        raw = (json.dumps(export, indent=3) + "\n\n").encode("utf-8")
        source, request = self.exercise(raw, "IT-001.json", "it-001.json", B001, quote)
        self.assertEqual(source.metadata, {key: value for key, value in export.items() if key != "message"})
        self.assertNotIn(export["subject"], json.dumps(request))
        self.assertNotIn(export["exported_at"], json.dumps(request))
        self.assertNotEqual(source.source_sha256, hashlib.sha256(quote.encode()).hexdigest())

    def test_t02_escaped_newlines_and_utf8_survive(self):
        export = json.loads((ROOT / "materials/requests/IT-018.json").read_text())
        quote = export["message"]
        for unicode_escaped in (False, True):
            with self.subTest(unicode_escaped=unicode_escaped):
                export["message"] = quote + '\nContext: café — 東京. "Workshop notes".\\'
                raw = json.dumps(export, ensure_ascii=unicode_escaped, indent=2).encode("utf-8")
                self.assertIn(b"\\n", raw)
                source, request = self.exercise(raw, "IT-018.json", "it-018.json", B018, quote)
                self.assertEqual(request["input"]["message"], export["message"])
                self.assertIn("café — 東京", source.message)
                self.assertIn("\n\n", source.message)

    def test_t02_exactly_200_words_reach_provider_intact(self):
        export = json.loads((ROOT / "materials/requests/IT-001.json").read_text())
        quote = export["message"]
        export["message"] += " Context" * (200 - len(quote.split()))
        self.assertEqual(len(export["message"].split()), 200)
        raw = json.dumps(export).encode("utf-8")
        _, request = self.exercise(raw, "IT-001.json", "it-001.json", B001, quote)
        self.assertEqual(request["input"]["message"], export["message"])

    def test_t02_versioned_prompt_keeps_source_untrusted(self):
        raw = (ROOT / "materials/requests/IT-001.json").read_bytes()
        quote = json.loads(raw)["message"]
        _, request = self.exercise(raw, "IT-001.json", "it-001.json", B001, quote)
        instructions = request["instructions"].lower()
        # Policy-derived concepts, rather than a snapshot of generated prose.
        for concept in (
            "untrusted", "data", "instructions", "explicit facts", "null",
            "issues", "conflict", "supporting_quotes", "decoded", "substring",
            "correction", "relative date", "requested new permission", "yyyy-mm-dd",
        ):
            with self.subTest(concept=concept):
                self.assertIn(concept, instructions)
        for field in (*B001, "supporting_quotes"):
            self.assertIn(field, instructions)
        from access_intake.sources import load_source
        from access_intake.prompts import build_request
        other = build_request(load_source(ROOT / "materials/requests/IT-018.json"))
        self.assertEqual(other["prompt_version"], request["prompt_version"])
        self.assertEqual(other["instructions"], request["instructions"])
