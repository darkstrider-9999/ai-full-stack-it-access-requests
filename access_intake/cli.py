"""Intake, inspection, immutable history and export for a local SQLite state."""
import argparse
import base64
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

from .exports import export_generation
from .intake import intake_jobs, resume_pending, retry_attempt
from .prompts import EXTRACTION_INSTRUCTIONS, PROMPT_VERSION
from .providers import MockProvider
from .openai_provider import DEFAULT_ENDPOINT, HTTPTransport, ResponsesProvider, load_api_key, validate_configuration
from .review import render_review
from .sources import load_source
from .storage import Store


ROOT = Path(__file__).resolve().parents[1]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare access requests for human review.")
    commands = parser.add_subparsers(dest="command", required=True)
    intake = commands.add_parser("intake", help="Process UTF-8 JSON exports into durable records.")
    intake.add_argument("input", type=Path)
    intake.add_argument("--provider", choices=("mock", "openai"), default="mock")
    intake.add_argument("--model", help="Explicit Responses model ID; facilitator-key access remains unverified.")
    intake.add_argument("--endpoint", default=DEFAULT_ENDPOINT, help="Full HTTPS Responses API endpoint.")
    intake.add_argument("--timeout", type=float, default=60, help="Finite provider I/O timeout in seconds (default: 60).")
    intake.add_argument("--output-limit", type=int, default=4096, help="Maximum output tokens (default: 4096).")
    intake.add_argument("--temperature", type=float, help="Optional sampling temperature; omit for models that do not support it.")
    intake.add_argument("--mock-responses", type=Path, default=ROOT / "tests/mock_responses")
    intake.add_argument("--mock-script", type=Path, help="Saved JSON list of mock response actions, in sorted input order.")
    queue = commands.add_parser("queue", help="Inspect current completed/latest-failed results.")
    inspect = commands.add_parser("inspect", help="Inspect one current document.")
    history = commands.add_parser("history", help="Inspect archived attempts and source snapshots.")
    retry = commands.add_parser("retry", help="Replay archived bytes/settings for a named attempt.")
    retry.add_argument("--attempt", required=True)
    resume = commands.add_parser("resume", help="Resume pending work or reclaim expired worker leases.")
    export = commands.add_parser("export", help="Publish a consistent extraction/queue generation.",
                                 description="Pin the returned generation directory when reading both files. Top-level readers must retry if generation IDs differ.")
    export.add_argument("--output", type=Path, required=True)
    for command in (inspect, history):
        command.add_argument("--document", required=True)
    for command in (queue, inspect, history):
        command.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    for command in (queue, inspect):
        command.add_argument("--include-pending", action="store_true", help="Show unfinished attempts and label earlier results.")
    for command in (intake, resume):
        command.add_argument("--workers", type=int, default=4, help="Maximum concurrent provider calls (default: 4).")
        command.add_argument("--lease-seconds", type=float, default=60, help="Renewed worker lease; interrupted work is reclaimable after expiry.")
    for command in (intake, queue, inspect, history, retry, resume, export):
        command.add_argument("--state", type=Path, required=True, help="Local SQLite database path.")
    return parser


def _intake(arguments, store: Store) -> None:
    paths = sorted(arguments.input.glob("*.json")) if arguments.input.is_dir() else [arguments.input]
    if not paths:
        raise ValueError("no JSON exports found")
    configuration = {
        "provider": arguments.provider, "prompt_version": PROMPT_VERSION,
        "prompt_sha256": hashlib.sha256(EXTRACTION_INSTRUCTIONS.encode("utf-8")).hexdigest(),
        "schema_sha256": hashlib.sha256((ROOT / "snapshot.schema.json").read_bytes()).hexdigest(),
        "routing_sha256": hashlib.sha256((ROOT / "access_intake/review.py").read_bytes()).hexdigest(),
    }
    if arguments.provider == "openai":
        configuration.update(validate_configuration({
            "model": arguments.model, "endpoint": arguments.endpoint, "timeout": arguments.timeout,
            "output_limit": arguments.output_limit, "temperature": arguments.temperature,
        }))
        script = [None] * len(paths)
    else:
        configuration["mock_responses"] = str(arguments.mock_responses.resolve())
        if arguments.mock_script:
            script = json.loads(arguments.mock_script.read_text(encoding="utf-8"))
            if not isinstance(script, list) or len(script) != len(paths):
                raise ValueError("mock script must contain one action per input export")
        else:
            script = [{"response": path.name.lower()} for path in paths]
    policy_hash = hashlib.sha256((ROOT / "materials/office-policy.md").read_bytes()).hexdigest()
    def jobs():
        for path, action in zip(paths, script):
            # Per-document snapshots do not change when batch membership changes.
            settings = dict(configuration)
            if action is not None:
                settings["mock_script"] = [action]
            if action is not None and "response" in action:
                fixture = (arguments.mock_responses / action["response"]).resolve()
                if not fixture.is_relative_to(arguments.mock_responses.resolve()):
                    raise ValueError("mock response must be inside the fixture directory")
                settings["mock_response_sha256"] = hashlib.sha256(fixture.read_bytes()).hexdigest()
            yield load_source(path), settings, policy_hash

    for record in intake_jobs(store, jobs(), _configured_provider, workers=arguments.workers, lease_seconds=arguments.lease_seconds):
        print(render_review(record), end="")


def _mock_provider(configuration):
    if configuration.get("provider") != "mock":
        raise ValueError("this build supports mock recovery only")
    script = configuration.get("mock_script")
    if not isinstance(script, list) or len(script) != 1:
        raise ValueError("archived recovery requires a single-document mock script")
    return MockProvider(configuration["mock_responses"], script=script)


def _configured_provider(configuration):
    if configuration.get("provider") == "mock":
        return _mock_provider(configuration)
    if configuration.get("provider") == "openai":
        validate_configuration(configuration)
        return ResponsesProvider(configuration, credential=load_api_key(), transport=HTTPTransport())
    raise ValueError("unsupported configured provider")


def main(argv=None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        with Store(arguments.state) as store:
            if arguments.command == "intake":
                _intake(arguments, store)
            elif arguments.command == "retry":
                archived = store.attempt(arguments.attempt)
                provider = _configured_provider(archived["configuration"])
                print(render_review(retry_attempt(store, arguments.attempt, provider)), end="")
            elif arguments.command == "resume":
                for record in resume_pending(store, _configured_provider, workers=arguments.workers, lease_seconds=arguments.lease_seconds):
                    print(render_review(record), end="")
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
                records = store.current_records(include_pending=arguments.include_pending)
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
