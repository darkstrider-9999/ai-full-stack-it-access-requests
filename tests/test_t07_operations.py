"""T11–T14: deterministic concurrency, real worker death, fencing and mock load."""
import copy
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Lock

from support.offline_guard import install
from access_intake.providers import MockProvider
from access_intake.review import prepare_review
from access_intake.sources import load_source
from access_intake.storage import Store

install()
ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "tests/mock_responses"
POLICY = hashlib.sha256((ROOT / "materials/office-policy.md").read_bytes()).hexdigest()
CONFIG = {"provider": "mock", "model": "operational-test", "workers": 4}
B001 = json.loads((ROOT / "tests/expected/step4.json").read_text())["cases"]["B001"]["facts"]


class PerMessageMock:
    """Select explicitly saved replies independent of worker scheduling."""
    def __init__(self, *, blocked=None, error_id=None, error=None):
        self.blocked, self.error_id, self.error = blocked, error_id, error
        self.entered, self.release = Event(), Event()
        self.lock = Lock()
        self.calls = []

    def extract(self, request):
        request_id = re.search(r"IT-\d{3}", request["input"]["message"]).group()
        with self.lock:
            self.calls.append(request_id)
        if request_id == self.blocked:
            self.entered.set()
            if not self.release.wait(timeout=15):
                raise TimeoutError("test did not release provider barrier")
        if request_id == self.error_id:
            raise self.error
        provider = MockProvider(MOCKS, script=[{"response": request_id.lower() + ".json"}])
        return provider.extract(request)


class LoadMock:
    def __init__(self):
        self.saved = json.loads((MOCKS / "it-001.json").read_text())
        self.lock = Lock()
        self.entered, self.release = Event(), Event()
        self.active = self.peak = self.calls = 0

    def extract(self, request):
        message = request["input"]["message"]
        request_id = re.search(r"IT-LOAD-\d+", message).group()
        with self.lock:
            self.active += 1
            self.calls += 1
            self.peak = max(self.peak, self.active)
            if self.active == 4:
                self.entered.set()
        try:
            if not self.release.wait(timeout=15):
                raise TimeoutError("test did not release initial worker wave")
            envelope = copy.deepcopy(self.saved)
            # The plan's authored transformation changes only ID and its quote.
            extraction = json.loads(envelope["output_text"])
            extraction["request_id"] = request_id
            extraction["supporting_quotes"] = [message]
            envelope["output_text"] = json.dumps(extraction)
            return envelope
        finally:
            with self.lock:
                self.active -= 1


class T11ToT14Operations(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.state = self.directory / "state.sqlite3"

    def wait_for(self, predicate, *, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            Event().wait(0.02)
        self.fail("deterministic operational checkpoint was not reached")

    def cli(self, *args):
        result = subprocess.run([sys.executable, "-m", "access_intake", *map(str, args), "--state", str(self.state)],
                                cwd=ROOT, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def start_worker(self, control, *args):
        path = self.directory / f"control-{time.monotonic_ns()}.json"
        path.write_text(json.dumps(control))
        # Write stdout/stderr to a file: a large review display must not fill a
        # pipe and block persistence while the parent inspects the database.
        log = path.with_suffix(".log").open("w+")
        self.addCleanup(log.close)
        process = subprocess.Popen([
            sys.executable, str(ROOT / "tests/support/controlled_worker.py"), str(path),
            *map(str, args), "--state", str(self.state),
        ], cwd=ROOT, text=True, stdout=log, stderr=log)
        self.addCleanup(self.stop_worker, process)
        return process, log

    @staticmethod
    def stop_worker(process):
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)

    def finish_worker(self, process, log):
        code = process.wait(timeout=12)
        log.seek(0)
        self.assertEqual(code, 0, log.read())

    def wait_worker_checkpoint(self, process, log, checkpoint):
        self.wait_for(lambda: checkpoint.exists() or process.poll() is not None)
        if not checkpoint.exists():
            log.seek(0)
            self.fail("worker exited before the checkpoint: " + log.read())

    def copy_source(self, number, directory=None):
        path = (directory or self.directory) / f"IT-{number:03}.json"
        path.write_bytes((ROOT / "materials/requests" / path.name).read_bytes())
        return path

    def assert_pair(self, output):
        generation = (output / "current").resolve()
        records = [json.loads(line) for line in (generation / "extractions.jsonl").read_text().splitlines()]
        queue = json.loads((generation / "queue.json").read_text())
        self.assertEqual(len(records), len(queue))
        by_document = {row["document_id"]: row for row in records}
        self.assertEqual(len(by_document), len(records))
        for entry in queue:
            for field in ("attempt_id", "source_sha256", "status", "owner", "next_action", "generation_id"):
                self.assertEqual(entry[field], by_document[entry["document_id"]][field])
            self.assertEqual(entry["generation_id"], generation.name)
        return records

    def test_t11_slow_reply_does_not_hide_other_results_or_independent_error(self):
        from access_intake.intake import intake_sources
        from access_intake.providers import ProviderError

        error = json.loads((MOCKS / "operational-errors.json").read_text())["unavailable"]
        provider = PerMessageMock(blocked="IT-001", error_id="IT-010", error=ProviderError(error["code"]))
        paths = [ROOT / "materials/requests" / f"IT-{n:03}.json" for n in (1, 2, 10)]
        with Store(self.state) as store, ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(intake_sources, store, paths, provider, configuration=CONFIG,
                                     policy_hash=POLICY, workers=3, lease_seconds=60)
            try:
                self.assertTrue(provider.entered.wait(timeout=5))
                self.wait_for(lambda: len(store.current_records()) == 2)
                rows = json.loads(self.cli("queue", "--include-pending", "--json"))
                by_document = {row["document_id"]: row for row in rows}
                self.assertEqual(len(by_document), 3)
                slow = by_document["IT-001.json"]
                self.assertEqual(slow["processing_state"], "running")
                self.assertIsNone(slow["status"], "processing state is not a policy status")
                self.assertEqual(slow["version_label"], "pending attempt")
                self.assertEqual(by_document["IT-002.json"]["owner"], "Security Review")
                self.assertEqual(by_document["IT-010.json"]["error"]["code"], "unavailable")
                self.assertFalse(future.done())
            finally:
                provider.release.set()
            self.assertEqual(len(future.result(timeout=10)), 3)
            self.assertEqual(len(store.current_records()), 3)
            self.assertCountEqual(provider.calls, ["IT-001", "IT-002", "IT-010"])

    def test_t11_pending_new_revision_labels_earlier_result_explicitly(self):
        from access_intake.intake import intake_sources

        path = self.copy_source(1)
        with Store(self.state) as store:
            earlier = intake_sources(store, [path], MockProvider(MOCKS, script=[{"response": "it-001.json"}]),
                                     configuration=CONFIG, policy_hash=POLICY)[0]
            path.write_bytes(path.read_bytes().replace(b"2026-11-06", b"2026-11-09"))
            source = load_source(path)
            pending = store.register(source, configuration=CONFIG, policy_hash=POLICY)
            claim = store.claim(pending, worker_id="pending-worker", now=10, lease_seconds=100)
            self.assertTrue(claim)
            rows = store.current_records(include_pending=True, now=11)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["attempt_id"], pending)
            self.assertEqual(rows[0]["version_label"], "pending attempt")
            self.assertEqual(rows[0]["previous_result"]["attempt_id"], earlier["attempt_id"])
            self.assertEqual(rows[0]["previous_result"]["version_label"], "earlier result")
            self.assertEqual(rows[0]["previous_result"]["extraction"]["end_date"], "2026-11-06")

    def test_t12_two_cli_operators_share_one_claim_for_identical_input(self):
        path = self.copy_source(1)
        entered, release, calls = (self.directory / name for name in ("entered", "release", "calls.jsonl"))
        first, first_log = self.start_worker({"calls": str(calls), "before_reply": {"IT-001": {"entered": str(entered), "release": str(release)}}},
                                              "intake", path, "--provider", "mock", "--workers", 1)
        try:
            self.wait_worker_checkpoint(first, first_log, entered)
            second, second_log = self.start_worker({"calls": str(calls)}, "intake", path, "--provider", "mock", "--workers", 1)
            self.finish_worker(second, second_log)
            rows = json.loads(self.cli("queue", "--include-pending", "--json"))
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["processing_state"], "running")
            self.assertEqual(len(calls.read_text().splitlines()), 1)
        finally:
            release.touch()
        self.finish_worker(first, first_log)
        history = json.loads(self.cli("history", "--document", path.name, "--json"))
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["processing_state"], "succeeded")
        self.assertEqual(len(calls.read_text().splitlines()), 1)

    def test_t12_newer_cli_revision_wins_when_older_reply_finishes_last(self):
        path = self.copy_source(1)
        original_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        entered, release = self.directory / "old-entered", self.directory / "old-release"
        old, old_log = self.start_worker({"before_reply": {"IT-001": {"entered": str(entered), "release": str(release)}}},
                                        "intake", path, "--provider", "mock", "--workers", 1)
        output = self.directory / "overlap-exports"
        try:
            self.wait_worker_checkpoint(old, old_log, entered)
            path.write_bytes(path.read_bytes().replace(b"2026-11-06", b"2026-11-09"))
            new, new_log = self.start_worker({}, "intake", path, "--provider", "mock", "--workers", 1,
                                             "--mock-script", MOCKS / "manifest-extended.json")
            self.finish_worker(new, new_log)
            self.cli("export", "--output", output)
            published = self.assert_pair(output)
            self.assertEqual(len(published), 1)
            self.assertEqual(published[0]["extraction"]["end_date"], "2026-11-09")
            newer_attempt = published[0]["attempt_id"]
        finally:
            release.touch()
        self.finish_worker(old, old_log)
        self.cli("export", "--output", output)
        current = self.assert_pair(output)[0]
        self.assertEqual(current["attempt_id"], newer_attempt)
        self.assertEqual(current["source_sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
        history = json.loads(self.cli("history", "--document", path.name, "--json"))
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["source_sha256"], original_hash)
        self.assertEqual(history[0]["result"]["extraction"]["end_date"], "2026-11-06")

    def test_t13_killed_worker_resumes_expired_claim_without_recalling_completed_work(self):
        for phase in ("before_reply", "before_persistence"):
            with self.subTest(phase=phase):
                work = self.directory / phase
                work.mkdir()
                self.state = work / "state.sqlite3"
                inputs = work / "inputs"
                inputs.mkdir()
                self.copy_source(1, inputs)
                self.copy_source(2, inputs)
                entered, release, calls = (work / name for name in ("entered", "release", "calls.jsonl"))
                key = "IT-001" if phase == "before_reply" else "IT-001.json"
                worker, log = self.start_worker({"calls": str(calls), phase: {key: {"entered": str(entered), "release": str(release)}}},
                                                "intake", inputs, "--provider", "mock", "--workers", 2, "--lease-seconds", 0.5)
                try:
                    self.wait_worker_checkpoint(worker, log, entered)
                    with Store(self.state) as store:
                        self.wait_for(lambda: any(row["document_id"] == "IT-002.json" for row in store.current_records()))
                        completed = next(row for row in store.current_records() if row["document_id"] == "IT-002.json")
                        pending = next(row for row in store.current_records(include_pending=True) if row["document_id"] == "IT-001.json")
                    worker.kill()
                    worker.wait(timeout=5)
                    with Store(self.state) as store:
                        self.wait_for(lambda: any(row["processing_state"] == "interrupted" for row in store.pending_attempts()))
                    resumed, resumed_log = self.start_worker({"calls": str(calls)}, "resume", "--workers", 2, "--lease-seconds", 5)
                    self.finish_worker(resumed, resumed_log)
                    with Store(self.state) as store:
                        records = {row["document_id"]: row for row in store.current_records()}
                        self.assertEqual(len(records), 2)
                        self.assertEqual(records["IT-002.json"], completed)
                        self.assertEqual(records["IT-001.json"]["attempt_id"], pending["attempt_id"])
                        self.assertEqual(records["IT-001.json"]["extraction"]["requester"], "Maya Chen")
                        self.assertEqual(store.pending_attempts(), [])
                    call_ids = [json.loads(line)["request_id"] for line in calls.read_text().splitlines()]
                    self.assertEqual(call_ids.count("IT-002"), 1)
                    self.assertEqual(call_ids.count("IT-001"), 2, "interrupted remote calls can be duplicated")
                finally:
                    release.touch()
                    self.stop_worker(worker)

    def test_t13_expired_worker_is_fenced_after_reclaim(self):
        with Store(self.state) as store:
            source = load_source(ROOT / "materials/requests/IT-001.json")
            attempt = store.register(source, configuration=CONFIG, policy_hash=POLICY)
            first = store.claim(attempt, worker_id="worker-one", now=10, lease_seconds=5)
            self.assertTrue(first)
            self.assertIsNone(store.claim(attempt, worker_id="worker-two", now=12, lease_seconds=5))
            self.assertEqual(store.pending_attempts(now=16)[0]["processing_state"], "interrupted")
            second = store.claim(attempt, worker_id="worker-two", now=16, lease_seconds=5)
            self.assertTrue(second)
            self.assertNotEqual(first, second)
            provider = MockProvider(MOCKS, script=[{"response": "it-001.json"}])
            record = prepare_review(source, provider, attempt_id=attempt, configuration=CONFIG)
            with self.assertRaisesRegex(ValueError, "stale|claim|lease"):
                store.complete(attempt, record, claim_token=first, now=16)
            self.assertEqual(store.current_records(), [])
            store.complete(attempt, record, claim_token=second, now=16)
            self.assertEqual(len(store.current_records()), 1)

    def test_t14_rate_limit_timeout_unavailability_require_explicit_retry(self):
        from access_intake.intake import intake_sources, retry_attempt
        from access_intake.providers import ProviderError

        errors = json.loads((MOCKS / "operational-errors.json").read_text())
        paths = [ROOT / "materials/requests/IT-001.json", ROOT / "materials/requests/IT-002.json"]
        for name, error in errors.items():
            with self.subTest(error=name), Store(self.directory / f"{name}.sqlite3") as store:
                provider = PerMessageMock(error_id="IT-001", error=ProviderError(error["code"]))
                records = intake_sources(store, paths, provider, configuration=CONFIG, policy_hash=POLICY, workers=2)
                failed = next(row for row in records if row["document_id"] == "IT-001.json")
                unaffected = next(row for row in records if row["document_id"] == "IT-002.json")
                self.assertEqual(failed["error"]["code"], error["code"])
                self.assertEqual(failed["status"], "error")
                self.assertIsNone(failed["extraction"])
                self.assertEqual(unaffected["owner"], "Security Review")
                self.assertCountEqual(provider.calls, ["IT-001", "IT-002"])
                retry = MockProvider(MOCKS, script=[{"response": "it-001.json"}])
                recovered = retry_attempt(store, failed["attempt_id"], retry)
                self.assertIsNone(recovered["error"])
                self.assertEqual(recovered["owner"], "System Owner")
                self.assertEqual(retry.call_count, 1)
                self.assertIn(unaffected, store.current_records())

    def test_t14_two_thousand_mock_messages_use_at_most_four_workers(self):
        from access_intake.intake import intake_sources

        original = json.loads((ROOT / "materials/requests/IT-001.json").read_text())
        paths = []
        for n in range(1, 2001):
            export = {**original, "message": original["message"].replace("IT-001", f"IT-LOAD-{n}")}
            path = self.directory / f"IT-LOAD-{n}.json"
            path.write_text(json.dumps(export))
            paths.append(path)
        provider = LoadMock()
        started = time.monotonic()
        with Store(self.state) as store, ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(intake_sources, store, paths, provider, configuration=CONFIG,
                                     policy_hash=POLICY, workers=4, lease_seconds=60)
            try:
                self.wait_for(lambda: provider.entered.is_set() or future.done())
                if future.done():
                    future.result()
                self.assertTrue(provider.entered.is_set(), "four independent workers never reached the barrier")
                self.assertFalse(future.done())
            finally:
                provider.release.set()
            records = future.result(timeout=90)
            self.assertEqual(len(records), 2000)
            self.assertEqual(len(store.current_records()), 2000)
            self.assertEqual(provider.calls, 2000)
            self.assertEqual(provider.peak, 4)
            self.assertEqual({row["document_id"] for row in records}, {f"IT-LOAD-{n}.json" for n in range(1, 2001)})
            for row in records:
                request_id = Path(row["document_id"]).stem
                self.assertIsNone(row["error"])
                self.assertEqual(row["response_source"], "mock")
                self.assertEqual(row["owner"], "System Owner")
                for field, value in {**B001, "request_id": request_id}.items():
                    self.assertEqual(row["extraction"][field], value)
                self.assertEqual(row["extraction"]["supporting_quotes"], [original["message"].replace("IT-001", request_id)])
                self.assertEqual(row["source_sha256"], hashlib.sha256((self.directory / row["document_id"]).read_bytes()).hexdigest())
        print(json.dumps({"observation": "T14 mock workload", "messages": 2000,
                          "workers": 4, "peak_active_provider_calls": provider.peak,
                          "elapsed_seconds": round(time.monotonic() - started, 3),
                          "configuration": CONFIG, "response_source": "mock"}))
