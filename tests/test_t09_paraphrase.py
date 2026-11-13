"""T16: the plan's 21st, independently authored message stays isolated.

This verifies the shared mocked evaluation path, not live extraction accuracy.
Facts below are transcribed from the authored T16 message in planning/plan.md.
"""
import contextlib
import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support.offline_guard import install
from access_intake.intake import intake_jobs
from access_intake.prompts import build_request
from access_intake.providers import MockProvider
from access_intake.sources import load_source

install()
ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "tests/mock_responses"
INPUT = ROOT / "tests/inputs/IT-001.json"
MESSAGE = "Please log request IT-001, submitted 2026-10-22. I am Maya Chen in Analytics, reporting to Priya Shah. For checking the weekly conversion dashboard with the new event definition changes, I need a new read-only permission for Atlas's staging environment. Please set the window to begin 2026-10-23 and end 2026-11-06."
FACTS = {
    "request_id": "IT-001", "request_date": "2026-10-22", "kind": "new",
    "requester": "Maya Chen", "team": "Analytics", "system": "Atlas",
    "environment": "staging", "permission": "read",
    "business_purpose": "checking the weekly conversion dashboard with the new event definition changes",
    "start_date": "2026-10-23", "end_date": "2026-11-06", "manager": "Priya Shah",
    "supporting_quotes": [MESSAGE], "issues": [],
}


class T16FreshWording(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def cli(self, *arguments, success=True):
        result = subprocess.run([sys.executable, "-m", "access_intake", *map(str, arguments)],
                                cwd=ROOT, text=True, capture_output=True, timeout=30)
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, "evaluation accepted a production destination")
        return result

    def evaluation_args(self, state, output, manifest="manifest-paraphrase.json"):
        return ["evaluate-paraphrase", "--input", str(INPUT), "--state", str(state),
                "--output", str(output), "--provider", "mock", "--mock-responses", str(MOCKS),
                "--mock-script", str(MOCKS / manifest)]

    def snapshot(self, directory):
        return {
            str(path.relative_to(directory)): ("symlink", str(path.readlink())) if path.is_symlink() else ("file", path.read_bytes())
            for path in directory.rglob("*") if path.is_file() or path.is_symlink()
        }

    def assert_paraphrase(self, record):
        self.assertEqual(record["document_id"], "IT-001.json")
        self.assertEqual(record["source_sha256"], hashlib.sha256(INPUT.read_bytes()).hexdigest())
        self.assertEqual(record["extraction"], FACTS)
        self.assertEqual(tuple(record[key] for key in ("status", "owner", "next_action")), ("review", "System Owner", "review-request"))
        self.assertIsNone(record["error"])
        self.assertEqual(record["response_source"], "mock")
        self.assertEqual(record["evidence"], [MESSAGE])
        self.assertIn("human", record["review_guidance"].casefold())

    def test_t16_new_cli_entry_point_exports_only_the_isolated_paraphrase(self):
        # Check the new entry point first, before any setup that already works.
        self.cli("evaluate-paraphrase", "--help")
        production_state = self.directory / "production.sqlite3"
        production_output = self.directory / "production-exports"
        self.cli("intake", ROOT / "materials/requests", "--state", production_state,
                 "--provider", "mock", "--mock-script", MOCKS / "manifest-m20.json")
        self.cli("export", "--state", production_state, "--output", production_output)
        before_records = json.loads(self.cli("queue", "--state", production_state, "--json").stdout)
        self.assertEqual(len(before_records), 20)
        before_database = production_state.read_bytes()
        before_exports = self.snapshot(production_output)

        state, output = self.directory / "evaluation.sqlite3", self.directory / "evaluation-exports"
        self.cli(*self.evaluation_args(state, output))
        isolated = json.loads(self.cli("queue", "--state", state, "--json").stdout)
        self.assertEqual(len(isolated), 1)
        self.assert_paraphrase(isolated[0])
        generation = (output / "current").resolve()
        records = [json.loads(line) for line in (generation / "extractions.jsonl").read_text().splitlines()]
        queue = json.loads((generation / "queue.json").read_text())
        self.assertEqual(len(records), 1)
        self.assertEqual(len(queue), 1)
        self.assert_paraphrase(records[0])
        for field in ("document_id", "source_sha256", "attempt_id", "status", "owner", "next_action", "generation_id"):
            self.assertEqual(queue[0][field], records[0][field])
        self.assertEqual(records[0]["generation_id"], generation.name)
        self.assertEqual(production_state.read_bytes(), before_database)
        self.assertEqual(self.snapshot(production_output), before_exports)
        self.assertEqual(json.loads(self.cli("queue", "--state", production_state, "--json").stdout), before_records)
        self.assertEqual(json.loads(INPUT.read_text())["message"], MESSAGE)
        self.assertNotEqual(MESSAGE, json.loads((ROOT / "materials/requests/IT-001.json").read_text())["message"])

    def test_t16_shared_input_prompt_gate_and_router_handle_success_and_refusal(self):
        from access_intake.cli import main

        requests = []
        class RecordingMock(MockProvider):
            def extract(self, request):
                requests.append(request)
                return super().extract(request)

        for name, manifest in (("success", "manifest-paraphrase.json"), ("refusal", "manifest-refusal.json")):
            with self.subTest(case=name):
                state, output = self.directory / f"{name}.sqlite3", self.directory / name
                with mock.patch("access_intake.cli.intake_jobs", wraps=intake_jobs) as shared, \
                     mock.patch("access_intake.cli.MockProvider", RecordingMock), \
                     contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(main(self.evaluation_args(state, output, manifest)), 0)
                    self.assertEqual(shared.call_count, 1, "evaluation must use the shared intake path")
                record = json.loads(self.cli("queue", "--state", state, "--json").stdout)[0]
                if name == "success":
                    self.assert_paraphrase(record)
                else:
                    self.assertIsNone(record["extraction"])
                    self.assertEqual(record["status"], "error")
                    self.assertEqual(record["owner"], "IT Service Desk")
                    self.assertEqual(record["next_action"], "retry-extraction")
                    self.assertEqual(record["error"]["code"], "refused")
        self.assertEqual(len(requests), 2)
        expected_request = build_request(load_source(INPUT))
        for request in requests:
            self.assertEqual(request["input"], {"message": MESSAGE})
            self.assertEqual(request["instructions"], expected_request["instructions"])
            self.assertEqual(request["prompt_version"], expected_request["prompt_version"])

    def test_t16_production_state_or_output_cannot_be_used_as_evaluation_destination(self):
        self.cli("evaluate-paraphrase", "--help")
        production_state = self.directory / "production.sqlite3"
        production_output = self.directory / "production-exports"
        self.cli("intake", ROOT / "materials/requests/IT-001.json", "--state", production_state, "--provider", "mock")
        self.cli("export", "--state", production_state, "--output", production_output)
        before_database, before_exports = production_state.read_bytes(), self.snapshot(production_output)
        self.cli(*self.evaluation_args(production_state, self.directory / "other-output"), success=False)
        self.assertEqual(production_state.read_bytes(), before_database)
        self.cli(*self.evaluation_args(self.directory / "other-state.sqlite3", production_output), success=False)
        self.assertEqual(self.snapshot(production_output), before_exports)
        self.assertEqual(production_state.read_bytes(), before_database)
