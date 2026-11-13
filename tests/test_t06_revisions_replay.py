"""T06–T10: reuse only current versions; replay archived bytes and settings.

All expected business facts are B001/B002 and T07's authored end-date change.
Finite saved-fixture scripts expose unwanted extra provider calls.
"""
import copy
import hashlib
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support.offline_guard import install
from access_intake.exports import export_generation
from access_intake.providers import MockProvider
from access_intake.review import prepare_review
from access_intake.sources import load_source
from access_intake.storage import Store

install()
ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "tests/mock_responses"
B001 = json.loads((ROOT / "tests/expected/step4.json").read_text())["cases"]["B001"]["facts"]
CONFIG = {
    "provider": "mock", "endpoint": "https://provider.invalid/responses", "model": "test-model",
    "temperature": 0, "output_limit": 4096, "prompt_sha256": "prompt-v2",
    "schema_sha256": "schema-v1", "routing_sha256": "routing-v1", "workers": 4,
}
POLICY = hashlib.sha256((ROOT / "materials/office-policy.md").read_bytes()).hexdigest()


class RecordingMock(MockProvider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.requests = []

    def extract(self, request):
        self.requests.append(copy.deepcopy(request))
        return super().extract(request)


class T06ToT10Revisions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.state = self.directory / "state.sqlite3"
        self.source_path = self.directory / "IT-001.json"
        self.original = (ROOT / "materials/requests/IT-001.json").read_bytes()
        self.extended = self.original.replace(b"2026-11-06", b"2026-11-09")
        self.source_path.write_bytes(self.original)

    def intake(self, store, provider, paths=None, configuration=None, policy=POLICY):
        from access_intake.intake import intake_sources

        return intake_sources(store, paths or [self.source_path], provider,
                              configuration=CONFIG if configuration is None else configuration,
                              policy_hash=policy)

    def retry(self, store, attempt, provider):
        from access_intake.intake import retry_attempt

        return retry_attempt(store, attempt, provider)

    def assert_b001(self, record, end="2026-11-06"):
        self.assertIsNone(record["error"])
        self.assertEqual(record["response_source"], "mock")
        self.assertEqual(tuple(record[key] for key in ("status", "owner", "next_action")), ("review", "System Owner", "review-request"))
        for field, value in {**B001, "end_date": end}.items():
            self.assertEqual(record["extraction"][field], value, field)
        self.assertEqual(record["extraction"]["issues"], [])
        expected_message = json.loads(self.extended if end == "2026-11-09" else self.original)["message"]
        self.assertEqual(record["extraction"]["supporting_quotes"], [expected_message])

    def test_t06_unchanged_success_uses_current_result_without_another_call(self):
        provider = MockProvider(MOCKS, script=[{"response": "it-001.json"}])
        with Store(self.state) as store:
            first = self.intake(store, provider)[0]
        with Store(self.state) as reopened:
            second = self.intake(reopened, provider)[0]
            self.assert_b001(second)
            self.assertEqual(second, first)
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(len(reopened.history("IT-001.json")), 1)
            self.assertEqual(reopened.current_records(), [first])

    def test_t06_unchanged_failure_stays_visible_until_explicit_retry(self):
        provider = MockProvider(MOCKS, script=[{"error": RuntimeError("scripted outage")}])
        with Store(self.state) as store:
            first = self.intake(store, provider)[0]
            second = self.intake(store, provider)[0]
            self.assertEqual(first["status"], "error")
            self.assertEqual(first, second)
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(len(store.history("IT-001.json")), 1)

    def test_t07_changed_bytes_and_a_to_b_to_a_create_distinct_attempts(self):
        provider = MockProvider(MOCKS, script=[
            {"response": "it-001.json"}, {"response": "it-001-extended.json"},
            {"response": "it-001.json"}, {"response": "it-001.json"},
        ])
        variants = (self.original, self.extended, self.original, self.original + b"\n  ")
        with Store(self.state) as store:
            records = []
            for index, raw in enumerate(variants):
                self.source_path.write_bytes(raw)
                record = self.intake(store, provider)[0]
                self.assert_b001(record, "2026-11-09" if index == 1 else "2026-11-06")
                self.assertEqual(record["source_sha256"], hashlib.sha256(raw).hexdigest())
                records.append(record)
            self.assertEqual(len({record["attempt_id"] for record in records}), 4)
            self.assertEqual(provider.call_count, 4)
            self.assertEqual(store.current_records(), [records[-1]])
            history = store.history("IT-001.json")
            self.assertEqual([row["raw_bytes"] for row in history], list(variants))
            self.assertEqual([row["result"] for row in history], records)

    def test_t07_stale_completion_cannot_replace_a_newer_registered_revision(self):
        # This storage fence was introduced in step 5; report it honestly if it
        # already passes. It is not a reason to break existing behavior.
        with Store(self.state) as store:
            old_source = load_source(self.source_path)
            old_attempt = store.register(old_source, configuration=CONFIG, policy_hash=POLICY)
            self.source_path.write_bytes(self.extended)
            new_source = load_source(self.source_path)
            new_attempt = store.register(new_source, configuration=CONFIG, policy_hash=POLICY)
            provider = MockProvider(MOCKS, script=[{"response": "it-001-extended.json"}, {"response": "it-001.json"}])
            store.complete(new_attempt, prepare_review(new_source, provider, attempt_id=new_attempt, configuration=CONFIG))
            store.complete(old_attempt, prepare_review(old_source, provider, attempt_id=old_attempt, configuration=CONFIG))
            self.assert_b001(store.current_records()[0], "2026-11-09")
            self.assertEqual(store.current_records()[0]["attempt_id"], new_attempt)
            self.assertEqual(len(store.history("IT-001.json")), 2)

    def test_t08_failed_replay_preserves_success_and_uses_archived_input_settings(self):
        for changed_before_success in (False, True):
            with self.subTest(changed_before_success=changed_before_success), Store(self.directory / f"retry-{changed_before_success}.sqlite3") as store:
                self.source_path.write_bytes(self.original)
                first = self.intake(store, MockProvider(MOCKS, script=[{"response": "it-001.json"}]))[0]
                selected = first
                archived_raw = self.original
                if changed_before_success:
                    self.source_path.write_bytes(self.extended)
                    selected = self.intake(store, MockProvider(MOCKS, script=[{"response": "it-001-extended.json"}]))[0]
                    archived_raw = self.extended
                # Disk content is subsequently different; replay must not read it.
                self.source_path.write_text('{"message":"changed after registration"}')
                provider = RecordingMock(MOCKS, script=[{"error": RuntimeError("scripted outage")}])
                failed = self.retry(store, selected["attempt_id"], provider)
                self.assertEqual(provider.call_count, 1)
                self.assertEqual(provider.requests[0]["input"], {"message": json.loads(archived_raw)["message"]})
                self.assertIsNone(failed["extraction"])
                self.assertEqual(tuple(failed[key] for key in ("status", "owner", "next_action")), ("error", "IT Service Desk", "retry-extraction"))
                self.assertNotEqual(failed["attempt_id"], selected["attempt_id"])
                self.assertEqual(failed["source_sha256"], selected["source_sha256"])
                self.assertEqual(failed["configuration"], selected["configuration"])
                self.assertEqual(failed["policy_hash"], selected["policy_hash"])
                command = shlex.split(failed["error"]["replay_command"])
                self.assertEqual(command[command.index("--attempt") + 1], failed["attempt_id"])
                self.assertEqual(command[command.index("--state") + 1], str(store.path))
                self.assertEqual(store.current_records(), [failed])
                history = store.history("IT-001.json")
                self.assertEqual(history[-1]["replay_of"], selected["attempt_id"])
                self.assertEqual(history[-1]["raw_bytes"], archived_raw)
                self.assertEqual(history[0]["result"], first)
                self.assertIsNone(history[0]["result"]["error"])
                generation = export_generation(store, self.directory / f"failed-export-{changed_before_success}")
                extracted = json.loads((generation / "extractions.jsonl").read_text())
                queue = json.loads((generation / "queue.json").read_text())
                self.assertEqual(extracted["attempt_id"], failed["attempt_id"])
                self.assertIsNone(extracted["extraction"])
                self.assertEqual(queue[0]["attempt_id"], failed["attempt_id"])
                self.assertEqual(queue[0]["status"], "error")
                self.assertEqual(queue[0]["source_sha256"], failed["source_sha256"])

    def test_t08_obsolete_replay_remains_historical(self):
        provider = RecordingMock(MOCKS, script=[{"response": "it-001.json"}, {"response": "it-001-extended.json"}, {"response": "it-001.json"}])
        with Store(self.state) as store:
            original = self.intake(store, provider)[0]
            self.source_path.write_bytes(self.extended)
            current = self.intake(store, provider)[0]
            self.source_path.unlink()
            replay = self.retry(store, original["attempt_id"], provider)
            self.assert_b001(replay)
            self.assertEqual(provider.requests[-1]["input"], {"message": json.loads(self.original)["message"]})
            self.assertEqual(store.current_records(), [current])
            history = store.history("IT-001.json")
            self.assertEqual(len(history), 3)
            self.assertEqual(history[-1]["replay_of"], original["attempt_id"])
            self.assertEqual(history[-1]["result"], replay)
            self.assertEqual(provider.call_count, 3)

    def test_t09_relevant_settings_and_policy_changes_refresh(self):
        changes = {
            "endpoint": "https://other-provider.invalid/responses", "model": "different-model",
            "temperature": 0.25, "output_limit": 8192, "prompt_sha256": "changed-prompt",
            "schema_sha256": "changed-schema", "routing_sha256": "changed-routing-implementation",
            "policy_hash": "changed-policy-fingerprint",
        }
        for index, (key, value) in enumerate(changes.items()):
            with self.subTest(setting=key), Store(self.directory / f"settings-{index}.sqlite3") as store:
                provider = MockProvider(MOCKS, script=[{"response": "it-001.json"}, {"response": "it-001.json"}])
                first = self.intake(store, provider)[0]
                config = dict(CONFIG) if key == "policy_hash" else {**CONFIG, key: value}
                policy = value if key == "policy_hash" else POLICY
                second = self.intake(store, provider, configuration=config, policy=policy)[0]
                self.assert_b001(second)
                self.assertNotEqual(first["attempt_id"], second["attempt_id"])
                self.assertEqual(first["source_sha256"], second["source_sha256"])
                self.assertEqual(second["configuration"], config)
                self.assertEqual(second["policy_hash"], policy)
                self.assertEqual(store.history("IT-001.json")[0]["configuration"], CONFIG)
                self.assertEqual(store.history("IT-001.json")[0]["policy_hash"], POLICY)
                self.assertEqual(provider.call_count, 2)

    def test_t09_worker_count_is_operational_and_does_not_invalidate_result(self):
        provider = MockProvider(MOCKS, script=[{"response": "it-001.json"}])
        with Store(self.state) as store:
            first = self.intake(store, provider)[0]
            second = self.intake(store, provider, configuration={**CONFIG, "workers": 1})[0]
            self.assertEqual(first, second)
            self.assertEqual(provider.call_count, 1)
            self.assertEqual(len(store.history("IT-001.json")), 1)

    def test_t10_omitted_batch_document_is_retained_and_not_withdrawn(self):
        second_path = ROOT / "materials/requests/IT-002.json"
        provider = MockProvider(MOCKS, script=[{"response": "it-001.json"}, {"response": "it-002.json"}])
        with Store(self.state) as store:
            first = self.intake(store, provider, paths=[self.source_path, second_path])
            again = self.intake(store, provider, paths=[second_path])
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(again, [first[1]])
            self.assertEqual(store.current_records(), first)
            self.assert_b001(first[0])
            self.assertEqual(tuple(first[1][key] for key in ("status", "owner", "next_action")), ("review", "Security Review", "review-risk-then-owner"))
            self.assertEqual(len(store.history("IT-001.json")), 1)

    def cli(self, *args):
        result = subprocess.run([sys.executable, "-m", "access_intake", *map(str, args), "--state", str(self.state)],
                                cwd=ROOT, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_t06_t08_cli_reuses_and_replays_archived_attempt_without_source_file(self):
        self.cli("intake", self.source_path, "--provider", "mock")
        first = json.loads(self.cli("queue", "--json"))[0]
        self.cli("intake", self.source_path, "--provider", "mock")
        second = json.loads(self.cli("queue", "--json"))[0]
        self.assertEqual(first["attempt_id"], second["attempt_id"])
        self.source_path.unlink()
        self.cli("retry", "--attempt", first["attempt_id"])
        current = json.loads(self.cli("queue", "--json"))[0]
        self.assertNotEqual(current["attempt_id"], first["attempt_id"])
        self.assert_b001(current)
        self.assertEqual(current["configuration"], first["configuration"])
        history = json.loads(self.cli("history", "--document", "IT-001.json", "--json"))
        self.assertEqual(len(history), 2)
        self.assertEqual(history[-1]["replay_of"], first["attempt_id"])

    def test_t10_cli_batch_omission_does_not_change_per_document_fingerprint(self):
        second_path = self.directory / "IT-002.json"
        second_path.write_bytes((ROOT / "materials/requests/IT-002.json").read_bytes())
        self.cli("intake", self.directory, "--provider", "mock")
        first = json.loads(self.cli("queue", "--json"))
        self.cli("intake", second_path, "--provider", "mock")
        second = json.loads(self.cli("queue", "--json"))
        self.assertEqual(second, first)
        for document in ("IT-001.json", "IT-002.json"):
            self.assertEqual(len(json.loads(self.cli("history", "--document", document, "--json"))), 1)
