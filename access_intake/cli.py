"""Intake, inspection, immutable history and export for a local SQLite state."""
import argparse
import base64
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from .exports import export_generation
from .intake import intake_sources, retry_attempt
from .prompts import EXTRACTION_INSTRUCTIONS, PROMPT_VERSION
from .providers import MockProvider
from .review import render_review
from .storage import Store


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare access requests for human review.")
    commands = parser.add_subparsers(dest="command", required=True)
    intake = commands.add_parser("intake", help="Process UTF-8 JSON exports into durable records.")
    intake.add_argument("input", type=Path)
    intake.add_argument("--provider", choices=("mock",), default="mock")
    intake.add_argument("--mock-responses", type=Path, default=ROOT / "tests/mock_responses")
    intake.add_argument("--mock-script", type=Path, help="Saved JSON list of mock response actions, in sorted input order.")
    queue = commands.add_parser("queue", help="Inspect current completed/latest-failed results.")
    inspect = commands.add_parser("inspect", help="Inspect one current document.")
    history = commands.add_parser("history", help="Inspect archived attempts and source snapshots.")
    retry = commands.add_parser("retry", help="Replay archived bytes/settings for a named attempt.")
    retry.add_argument("--attempt", required=True)
    export = commands.add_parser("export", help="Publish a consistent extraction/queue generation.",
                                 description="Pin the returned generation directory when reading both files. Top-level readers must retry if generation IDs differ.")
    export.add_argument("--output", type=Path, required=True)
    for command in (inspect, history):
        command.add_argument("--document", required=True)
    for command in (queue, inspect, history):
        command.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    for command in (intake, queue, inspect, history, retry, export):
        command.add_argument("--state", type=Path, required=True, help="Local SQLite database path.")
    return parser


def _intake(arguments, store: Store) -> None:
    paths = sorted(arguments.input.glob("*.json")) if arguments.input.is_dir() else [arguments.input]
    if not paths:
        raise ValueError("no JSON exports found")
    if arguments.mock_script:
        script = json.loads(arguments.mock_script.read_text(encoding="utf-8"))
        if not isinstance(script, list) or len(script) != len(paths):
            raise ValueError("mock script must contain one action per input export")
    else:
        # Fixture selection is a mock transport setting, never extraction logic.
        script = [{"response": path.name.lower()} for path in paths]
    configuration = {
        "provider": "mock", "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(EXTRACTION_INSTRUCTIONS.encode("utf-8")).hexdigest(),
        "schema_sha256": hashlib.sha256((ROOT / "snapshot.schema.json").read_bytes()).hexdigest(),
        "routing_sha256": hashlib.sha256((ROOT / "access_intake/review.py").read_bytes()).hexdigest(),
        "mock_responses": str(arguments.mock_responses.resolve()),
    }
    policy_hash = hashlib.sha256((ROOT / "materials/office-policy.md").read_bytes()).hexdigest()
    for path, action in zip(paths, script):
        # Per-document transport snapshots prevent batch omission from changing
        # a result's fingerprint or shifting a reused document's scripted reply.
        settings = {**configuration, "mock_script": [action]}
        if "response" in action:
            fixture = (arguments.mock_responses / action["response"]).resolve()
            if not fixture.is_relative_to(arguments.mock_responses.resolve()):
                raise ValueError("mock response must be inside the fixture directory")
            settings["mock_response_sha256"] = hashlib.sha256(fixture.read_bytes()).hexdigest()
        provider = MockProvider(arguments.mock_responses, script=[action])
        record = intake_sources(store, [path], provider, configuration=settings, policy_hash=policy_hash)[0]
        print(render_review(record), end="")


def main(argv=None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        with Store(arguments.state) as store:
            if arguments.command == "intake":
                _intake(arguments, store)
            elif arguments.command == "retry":
                archived = store.attempt(arguments.attempt)
                configuration = archived["configuration"]
                if configuration.get("provider") != "mock":
                    raise ValueError("this build supports mock replay only")
                script = configuration.get("mock_script")
                if not isinstance(script, list) or len(script) != 1:
                    raise ValueError("archived replay requires a single-document mock script")
                provider = MockProvider(configuration["mock_responses"], script=script)
                print(render_review(retry_attempt(store, arguments.attempt, provider)), end="")
            elif arguments.command == "export":
                print(export_generation(store, arguments.output))
            elif arguments.command == "history":
                rows = store.history(arguments.document)
                if arguments.json:
                    archived = []
                    for row in rows:
                        value = {key: item for key, item in row.items() if key != "raw_bytes"}
                        value["source_bytes_base64"] = base64.b64encode(row["raw_bytes"]).decode("ascii")
                        archived.append(value)
                    print(json.dumps(archived, ensure_ascii=False))
                else:
                    for row in rows:
                        print(f"Archived attempt: {row['attempt_id']} ({row['processing_state']})")
                        if row["result"] is not None:
                            print(render_review(row["result"]), end="")
            else:
                records = store.current_records()
                if arguments.command == "inspect":
                    records = [record for record in records if record["document_id"] == arguments.document]
                    if not records:
                        raise ValueError("no completed current result for that document")
                if arguments.json:
                    print(json.dumps(records[0] if arguments.command == "inspect" else records, ensure_ascii=False))
                else:
                    for record in records:
                        print(render_review(record), end="")
    except (OSError, ValueError, sqlite3.Error) as error:
        print(f"access-intake: {error}", file=sys.stderr)
        return 1
    return 0
