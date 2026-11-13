"""T01: expected facts are authored from B001, not from provider output."""
import json
import os
import socket
import subprocess
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from support.offline_guard import install

install()
ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "tests" / "mock_responses"
MESSAGE = json.loads((ROOT / "materials/requests/IT-001.json").read_text())["message"]

# Independently transcribed from planning/plan.md B001 and the original message.
EXPECTED = {
    "request_id": "IT-001",
    "request_date": "2026-10-22",
    "kind": "new",
    "requester": "Maya Chen",
    "team": "Analytics",
    "system": "Atlas",
    "environment": "staging",
    "permission": "read",
    "business_purpose": "I'm checking the weekly conversion dashboard with the new event definition changes (before next reporting cycle).",
    "start_date": "2026-10-23",
    "end_date": "2026-11-06",
    "manager": "Priya Shah",
    "supporting_quotes": [MESSAGE],
    "issues": [],
}


class T01MockBoundary(unittest.TestCase):
    def test_t01_reply_barrier_error_and_call_counts(self):
        from access_intake.providers import MockProvider

        entered, release = Event(), Event()
        provider = MockProvider(MOCKS, script=[
            {"response": "it-001.json"},
            {"response": "it-001.json", "entered": entered, "release": release},
            {"error": RuntimeError("scripted provider outage")},
        ])
        self.assertEqual(provider.call_count, 0)
        reply = provider.extract({"message": MESSAGE})
        self.assertEqual(reply["response_source"], "mock")
        self.assertEqual(reply["status"], "completed")
        self.assertEqual(json.loads(reply["output_text"]), EXPECTED)
        self.assertEqual(reply, json.loads((MOCKS / "it-001.json").read_text()))
        self.assertEqual(provider.call_count, 1)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(provider.extract, {"message": MESSAGE})
            try:
                self.assertTrue(entered.wait(timeout=3), "mock never reached barrier")
                self.assertFalse(future.done(), "reply escaped before barrier release")
                self.assertEqual(provider.call_count, 2)
            finally:
                release.set()
            self.assertEqual(future.result(timeout=3), reply)

        with self.assertRaisesRegex(RuntimeError, "scripted provider outage"):
            provider.extract({"message": MESSAGE})
        self.assertEqual(provider.call_count, 3)

    def test_t01_process_isolation(self):
        # These probes must be blocked before reaching the environment or network.
        with self.assertRaisesRegex(AssertionError, "API_KEY lookup forbidden"):
            os.getenv("API_KEY")
        with self.assertRaisesRegex(AssertionError, "network access forbidden"):
            socket.create_connection(("example.invalid", 443))

    def test_t01_child_entry_point_is_also_offline(self):
        child = subprocess.run([
            sys.executable, "-c",
            "import sys, runpy; sys.path.insert(0, 'tests'); "
            "runpy.run_path('tests/test_t01_mock_provider.py', run_name='__offline_probe__'); "
            "from access_intake.providers import MockProvider; "
            "from pathlib import Path; "
            "p = MockProvider(Path('tests/mock_responses'), script=[{'response':'it-001.json'}]); "
            "r = p.extract({'message':'child offline probe'}); "
            "import json; print(json.dumps(r))",
        ], cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(child.returncode, 0, child.stderr)
        reply = json.loads(child.stdout)
        self.assertEqual(json.loads(reply["output_text"]), EXPECTED)
        self.assertEqual(reply["response_source"], "mock")


if __name__ == "__offline_probe__":
    # unittest does not run here; explicitly check startup guards in the child.
    suite = unittest.TestSuite([T01MockBoundary("test_t01_process_isolation")])
    result = unittest.TextTestRunner().run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
