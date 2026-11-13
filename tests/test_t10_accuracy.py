"""T17: independent 21-case gold, fixed denominator and visible discrepancies.

Gold is source/plan-authored; only measured copies are corrupted. Perfect mock
agreement verifies the fixture path, not a model's extraction performance.
"""
import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support.offline_guard import install
from access_intake.intake import intake_sources
from access_intake.providers import MockProvider
from access_intake.review import prepare_review
from access_intake.sources import load_source
from access_intake.storage import Store

install()
ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "tests/mock_responses"
GOLD_PATH = ROOT / "tests/expected/accuracy.json"
SCALARS = (
    "request_id", "request_date", "kind", "requester", "team", "system", "environment",
    "permission", "business_purpose", "start_date", "end_date", "manager",
)
CONFIG = {"provider": "mock", "model": "accuracy-fixture", "prompt_version": "access-extraction-v2"}
POLICY = hashlib.sha256((ROOT / "materials/office-policy.md").read_bytes()).hexdigest()


class T17Accuracy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.state = cls.directory / "submission.sqlite3"
        cls.evaluation_state = cls.directory / "paraphrase.sqlite3"
        manifest = json.loads((MOCKS / "manifest-m20.json").read_text())
        with Store(cls.state) as store:
            submission = intake_sources(store, sorted((ROOT / "materials/requests").glob("*.json")),
                                        MockProvider(MOCKS, script=manifest), configuration=CONFIG, policy_hash=POLICY)
        with Store(cls.evaluation_state) as store:
            paraphrase = intake_sources(store, [ROOT / "tests/inputs/IT-001.json"],
                                        MockProvider(MOCKS, script=[{"response": "paraphrase.json"}]),
                                        configuration=CONFIG, policy_hash=POLICY)
        cls.observations = {"submission": submission, "paraphrase": paraphrase}

    def setUp(self):
        self.gold = json.loads(GOLD_PATH.read_text())
        self.original_gold = GOLD_PATH.read_bytes()

    def tearDown(self):
        self.assertEqual(GOLD_PATH.read_bytes(), self.original_gold, "measurement/scoring must never rewrite gold")

    def score(self, observations=None):
        from access_intake.accuracy import score_accuracy

        original = copy.deepcopy(self.gold)
        report = score_accuracy(self.gold, self.observations if observations is None else observations)
        self.assertEqual(self.gold, original)
        return report

    def mutated(self):
        return copy.deepcopy(self.observations)

    def test_t17_independent_gold_has_all_252_source_backed_field_slots(self):
        self.assertEqual(self.gold["scalar_fields"], list(SCALARS))
        self.assertEqual(len(self.gold["cases"]), 21)
        self.assertEqual({case["case_id"] for case in self.gold["cases"]}, {f"B{n:03}" for n in range(1, 21)} | {"T16"})
        self.assertEqual(len({(case["scope"], case["document_id"]) for case in self.gold["cases"]}), 21)
        self.assertEqual(self.gold["human_review"]["status"], "pending")
        for case in self.gold["cases"]:
            with self.subTest(case=case["case_id"]):
                source = ROOT / case["source"]
                raw = source.read_bytes()
                self.assertEqual(case["source_sha256"], hashlib.sha256(raw).hexdigest())
                self.assertEqual(set(case["expected"]), set(SCALARS))
                message = json.loads(raw)["message"]
                self.assertEqual(case["source_quotes"], [message])
                for field, value in case["expected"].items():
                    if value is not None and field != "kind":
                        self.assertIn(value.casefold(), message.casefold(), field)
                for fragment in case["required_quote_fragments"]:
                    self.assertIn(fragment, message)
        cases = {case["case_id"]: case for case in self.gold["cases"]}
        self.assertEqual(cases["B006"]["expected"]["permission"], "read")
        self.assertEqual(cases["B006"]["expected"]["environment"], "staging")
        self.assertEqual(cases["B014"]["expected"]["start_date"], "2026-11-05")
        self.assertEqual(cases["B014"]["expected"]["end_date"], "2026-11-02")

    def test_t17_mock_baseline_is_252_of_252_with_separate_checks_and_review_limit(self):
        report = self.score()
        self.assertEqual(report["scalar_score"], {"matched": 252, "total": 252, "ratio": 1.0})
        self.assertEqual(set(report["per_field"]), set(SCALARS))
        for field in SCALARS:
            self.assertEqual(report["per_field"][field], {"matched": 21, "total": 21})
        self.assertEqual(report["discrepancies"], [])
        for check in ("quotes", "issues", "routing"):
            self.assertEqual(report["checks"][check]["passed"], 21)
            self.assertEqual(report["checks"][check]["total"], 21)
            self.assertEqual(report["checks"][check]["discrepancies"], [])
        self.assertEqual(report["gold_review"]["status"], "pending")
        self.assertFalse(report["benchmark_complete"])
        for phrase in ("mock", "fixture", "not live"):
            self.assertIn(phrase, report["interpretation"].casefold())
        self.assertEqual(len(report["observations"]), 21)
        for observed in report["observations"]:
            self.assertEqual(observed["response_source"], "mock")
            self.assertEqual(observed["configuration"], CONFIG)
            self.assertEqual(observed["policy_hash"], POLICY)
            self.assertTrue(observed["attempt_id"])

    def test_t17_wrong_value_and_each_null_mismatch_lose_one_point(self):
        for case, index, field, actual, expected in (
            ("B001", 0, "permission", "admin", "read"),
            ("B009", 8, "manager", "Priya Shah", None),
            ("B001", 0, "manager", None, "Priya Shah"),
        ):
            with self.subTest(case=case, field=field, actual=actual):
                observations = self.mutated()
                observations["submission"][index]["extraction"][field] = actual
                report = self.score(observations)
                self.assertEqual(report["scalar_score"]["matched"], 251)
                self.assertEqual(report["scalar_score"]["total"], 252)
                self.assertEqual(len(report["discrepancies"]), 1)
                mismatch = report["discrepancies"][0]
                self.assertEqual((mismatch["case_id"], mismatch["field"], mismatch["expected"], mismatch["actual"]), (case, field, expected, actual))
                self.assertTrue(mismatch["source_quotes"])

    def test_t17_missing_or_failed_extraction_loses_all_twelve_including_gold_nulls(self):
        for missing in (True, False):
            with self.subTest(missing_record=missing):
                observations = self.mutated()
                if missing:
                    del observations["submission"][2]  # B003 has four null gold fields.
                else:
                    source = load_source(ROOT / "materials/requests/IT-003.json")
                    failure = prepare_review(source, MockProvider(MOCKS, script=[{"response": "refusal.json"}]),
                                             attempt_id="failed-b003", configuration=CONFIG)
                    observations["submission"][2] = {**observations["submission"][2], **failure}
                report = self.score(observations)
                self.assertEqual(report["scalar_score"]["matched"], 240)
                self.assertEqual(report["scalar_score"]["total"], 252)
                discrepancies = report["discrepancies"]
                self.assertEqual(len(discrepancies), 12)
                self.assertEqual({row["case_id"] for row in discrepancies}, {"B003"})
                self.assertEqual({row["field"] for row in discrepancies}, set(SCALARS))
                self.assertEqual({row["reason"] for row in discrepancies}, {"missing_record" if missing else "failed_extraction"})
                for field in SCALARS:
                    self.assertEqual(report["per_field"][field], {"matched": 20, "total": 21})

    def test_t17_absent_scalar_does_not_match_a_gold_null(self):
        observations = self.mutated()
        del observations["submission"][8]["extraction"]["manager"]
        report = self.score(observations)
        self.assertEqual(report["scalar_score"]["matched"], 251)
        self.assertEqual(report["discrepancies"][0]["reason"], "missing_field")

    def test_t17_same_filename_in_evaluation_is_a_separate_scoring_case(self):
        observations = self.mutated()
        observations["paraphrase"] = []
        report = self.score(observations)
        self.assertEqual(report["scalar_score"]["matched"], 240)
        self.assertEqual({row["case_id"] for row in report["discrepancies"]}, {"T16"})

    def test_t17_case_sensitive_purpose_difference_requires_human_semantic_review(self):
        for field, actual in (("requester", "maya chen"), ("business_purpose", "I am checking the weekly conversion dashboard with the new event definition changes (before next reporting cycle).")):
            with self.subTest(field=field):
                observations = self.mutated()
                observations["submission"][0]["extraction"][field] = actual
                report = self.score(observations)
                self.assertEqual(report["scalar_score"]["matched"], 251)
                if field == "business_purpose":
                    self.assertEqual(report["discrepancies"][0]["factual_review"], "human_review_required")

    def test_t17_quote_issue_and_route_errors_are_reported_separately(self):
        observations = self.mutated()
        observations["submission"][0]["extraction"]["supporting_quotes"] = ["Maya owns production"]
        observations["submission"][3]["extraction"]["supporting_quotes"] = ["read access in staging"]
        observations["submission"][5]["extraction"]["issues"] = []
        observations["submission"][1]["owner"] = "System Owner"
        report = self.score(observations)
        self.assertEqual(report["scalar_score"]["matched"], 252)
        for name, failed_ids in (("quotes", {"B001", "B004"}), ("issues", {"B006"}), ("routing", {"B002"})):
            self.assertEqual(report["checks"][name]["passed"], 21 - len(failed_ids))
            self.assertEqual({row["case_id"] for row in report["checks"][name]["discrepancies"]}, failed_ids)

    def test_t17_duplicate_measurements_are_rejected(self):
        observations = self.mutated()
        observations["submission"].append(copy.deepcopy(observations["submission"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.score(observations)

    def test_t17_provider_configuration_provenance_is_not_relabelled_as_live(self):
        observations = self.mutated()
        observations["submission"][0]["response_source"] = "openai/fixture-returned-model"
        observations["submission"][0]["configuration"] = {"provider": "openai", "model": "fixture-requested-model", "temperature": 0.2}
        report = self.score(observations)
        observed = next(row for row in report["observations"] if row["case_id"] == "B001")
        self.assertEqual(observed["response_source"], "openai/fixture-returned-model")
        self.assertEqual(observed["configuration"], observations["submission"][0]["configuration"])
        self.assertIn("unverified", report["interpretation"].casefold())

    def test_t17_cli_prints_score_rules_and_each_discrepancy_without_changing_state(self):
        def cli(*args):
            result = subprocess.run([sys.executable, "-m", "access_intake", "accuracy", "--expected", str(GOLD_PATH),
                                     "--state", str(self.state), "--evaluation-state", str(self.evaluation_state), *args],
                                    cwd=ROOT, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            return result.stdout
        from access_intake.accuracy import render_accuracy

        before = self.state.read_bytes(), self.evaluation_state.read_bytes()
        report = json.loads(cli("--json"))
        self.assertEqual(report["scalar_score"]["matched"], 252)
        rendered = cli()
        for text in ("252", "mock", "case-sensitive", "null", "human"):
            self.assertIn(text, rendered.casefold())
        observations = self.mutated()
        observations["submission"][0]["extraction"]["permission"] = "admin"
        rendered_negative = render_accuracy(self.score(observations))
        for text in ("B001", "permission", "read", "admin", "251", "252"):
            self.assertIn(text, rendered_negative)
        self.assertEqual((self.state.read_bytes(), self.evaluation_state.read_bytes()), before)
