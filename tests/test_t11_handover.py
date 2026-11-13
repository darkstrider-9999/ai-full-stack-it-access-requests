"""T18: executable operator handover and mutually consistent evidence artifacts.

Smoke commands use only saved mocks and temporary state. Setup is audited with
uv offline against the installed environment; no fresh network install is
claimed. The full-suite command is run outside this test to avoid recursion.
Human requirement validation and gold review are never marked by this test.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from support.offline_guard import install

install()
ROOT = Path(__file__).resolve().parents[1]
MOCKS = ROOT / "tests/mock_responses"
GOLD = json.loads((ROOT / "tests/expected/accuracy.json").read_text())
SMOKE_ORDER = ("setup", "intake", "queue", "inspect", "history", "resume", "retry", "export", "evaluate", "accuracy", "tests")
TEST_COMMAND = "env -u API_KEY python3 -m unittest discover -s tests -v"


class T18OperatorHandover(unittest.TestCase):
    def read_pair(self, output):
        generation = (output / "current").resolve()
        records = [json.loads(line) for line in (generation / "extractions.jsonl").read_text(encoding="utf-8").splitlines()]
        queue = json.loads((generation / "queue.json").read_text(encoding="utf-8"))
        self.assertEqual(len(records), 20)
        self.assertEqual(len(queue), 20)
        by_document = {record["document_id"]: record for record in records}
        self.assertEqual(len(by_document), 20)
        self.assertEqual({entry["document_id"] for entry in queue}, set(by_document))
        expectations = {case["document_id"]: case for case in GOLD["cases"] if case["scope"] == "submission"}
        for entry in queue:
            record = by_document[entry["document_id"]]
            for field in ("attempt_id", "source_sha256", "status", "owner", "next_action", "generation_id"):
                self.assertEqual(entry[field], record[field])
            self.assertEqual(record["generation_id"], generation.name)
            self.assertEqual(record["response_source"], "mock")
            self.assertIsNone(record["error"])
            expected = expectations[entry["document_id"]]
            for field, value in expected["expected"].items():
                self.assertEqual(record["extraction"][field], value)
            self.assertEqual(record["extraction"]["supporting_quotes"], expected["source_quotes"])
            self.assertEqual(record["source_sha256"], expected["source_sha256"])
            for field, value in expected["route"].items():
                self.assertEqual(record[field], value)
            self.assertNotIn("raw_bytes", record)
        return by_document

    def run_smoke(self, name, command, directory):
        variables = {
            "INTAKE_STATE": str(directory / "intake.sqlite3"),
            "EVALUATION_STATE": str(directory / "evaluation.sqlite3"),
            "INTAKE_OUTPUT": str(directory / "deliverables"),
            "EVALUATION_OUTPUT": str(directory / "evaluation-exports"),
            "ACCURACY_OUTPUT": str(directory / "accuracy.md"),
            "UV_OFFLINE": "1", "UV_CACHE_DIR": str(directory / "uv-cache"),
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
        }
        result = subprocess.run(["env", *[f"{key}={value}" for key, value in variables.items()],
                                 "bash", "-eu", "-o", "pipefail", "-c", command],
                                cwd=ROOT, text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, f"README smoke:{name}\n{result.stderr}\n{result.stdout}")
        return result.stdout

    def test_t18_documented_commands_and_handover_artifacts_are_reviewable(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        blocks = dict(re.findall(r"<!-- smoke:([a-z]+) -->\s*```bash\n(.*?)```", readme, re.DOTALL))
        self.assertEqual(set(blocks), set(SMOKE_ORDER), "README is missing executable handover blocks")
        self.assertNotIn("Replace this file with your own README", readme)
        self.assertIn("uv sync --frozen", blocks["setup"])
        self.assertIn(TEST_COMMAND, blocks["tests"])
        for term in ("API_KEY", "OpenAI", "Responses", "3.11", "human", "planning/validation.md", "planning/test-run.txt"):
            self.assertIn(term, readme)
        for term in ("unverified", "lease", "duplicate", "single host", "generation", "Q-45", "semantic"):
            self.assertIn(term.casefold(), readme.casefold())
        manifest = tomllib.loads((ROOT / "pyproject.toml").read_text())
        lock = tomllib.loads((ROOT / "uv.lock").read_text())
        self.assertIn("jsonschema==4.26.0", manifest["project"]["dependencies"])
        self.assertTrue(any(package["name"] == "jsonschema" and package["version"] == "4.26.0" for package in lock["package"]))

        with tempfile.TemporaryDirectory(prefix="access-handover-") as temporary:
            directory = Path(temporary)
            displayed = {}
            for name in SMOKE_ORDER:
                if name == "tests":
                    continue  # Executed as the outer full-suite verification.
                displayed[name] = self.run_smoke(name, blocks[name], directory)
            current = self.read_pair(directory / "deliverables")
            for text in ("review", "System Owner", "Security Review", "IT Service Desk", "human"):
                self.assertIn(text, displayed["queue"])
            self.assertIn("attempt", displayed["history"].casefold())
            self.assertIn("inspect-message", displayed["inspect"])
            evaluation = (directory / "evaluation-exports/current").resolve()
            isolated = [json.loads(line) for line in (evaluation / "extractions.jsonl").read_text().splitlines()]
            self.assertEqual(len(isolated), 1)
            self.assertEqual(isolated[0]["extraction"]["business_purpose"], "checking the weekly conversion dashboard with the new event definition changes")
            self.assertEqual(len(current), 20)
            smoke_accuracy = (directory / "accuracy.md").read_text()
            for term in ("252", "mock", "human", "case-sensitive", "null"):
                self.assertIn(term, smoke_accuracy.casefold())

        committed = self.read_pair(ROOT / "deliverables")
        accuracy = (ROOT / "deliverables/accuracy.md").read_text(encoding="utf-8")
        match = re.search(r"## Observation provenance.*?```json\n(.*?)\n```", accuracy, re.DOTALL)
        self.assertIsNotNone(match, "accuracy evidence must retain per-observation source/configuration labels")
        observations = json.loads(match.group(1))
        self.assertEqual(len(observations), 21)
        self.assertEqual(len({(row["scope"], row["document_id"]) for row in observations}), 21)
        for observation in observations:
            self.assertEqual(observation["response_source"], "mock")
            self.assertTrue(observation["configuration"])
            if observation["scope"] == "submission":
                record = committed[observation["document_id"]]
                for field in ("attempt_id", "source_sha256", "configuration", "policy_hash"):
                    self.assertEqual(observation[field], record[field], "accuracy observations must describe committed outputs")

        request = json.loads((ROOT / "deliverables/request-evidence.json").read_text())
        self.assertFalse(request["live_observation"])
        self.assertEqual(request["observation"]["kind"], "fake_http_transport")
        self.assertEqual(request["request"]["headers"]["Authorization"], "Bearer [REDACTED]")
        self.assertNotIn("dummy-test-secret", json.dumps(request))
        provider_fixture = json.loads((MOCKS / "provider-completed.json").read_text())["body"]
        self.assertEqual(request["observation"]["normalized_response_source"], "openai/" + provider_fixture["model"])
        self.assertEqual(request["request"]["body"]["text"]["format"]["schema"], json.loads((ROOT / "snapshot.schema.json").read_text()))
        for reference in request["implementation_references"] + request["test_references"]:
            self.assertTrue((ROOT / reference.split(":")[0]).is_file(), reference)

        verification = (ROOT / "deliverables/verification.md").read_text(encoding="utf-8")
        for term in ("mock", "intended", "observed", "configuration", "limitations", "human", "planning/test-run.txt", "planning/validation.md"):
            self.assertIn(term, verification.casefold())
        for number in range(11, 19):
            self.assertIn(f"T{number}", verification)
        validation = (ROOT / "planning/validation.md").read_text(encoding="utf-8")
        self.assertNotEqual(validation, verification)
        self.assertIn("human", validation.casefold())
        requirement_ids = set(re.findall(r"^\| (R-\d+) \|", (ROOT / "planning/spec.md").read_text(), re.MULTILINE))
        validation_ids = re.findall(r"^\| (R-\d+) \|", validation, re.MULTILINE)
        self.assertEqual(set(validation_ids), requirement_ids)
        self.assertEqual(len(validation_ids), len(requirement_ids), "one human review row per requirement")
