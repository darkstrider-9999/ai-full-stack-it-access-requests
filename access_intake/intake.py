"""Reusable intake and explicit replay through the shared validation/routing path."""
import json
import shlex
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .response_gate import Provider
from .review import prepare_review
from .sources import Source, load_source
from .storage import Store


def _finish(store: Store, attempt_id: str, source: Source, provider: Provider,
            configuration: Mapping[str, Any]) -> dict[str, Any]:
    contract = store.attempt(attempt_id)["execution_contract"]
    record = prepare_review(source, provider, attempt_id=attempt_id, configuration=configuration,
                            request_contract=contract)
    if record["error"] is not None and "replay_command" in record["error"]:
        record["error"]["replay_command"] += " --state " + shlex.quote(str(store.path))
    store.complete(attempt_id, record)
    return store.attempt(attempt_id)["result"]


def intake_sources(store: Store, paths: Iterable[str | Path], provider: Provider, *,
                   configuration: Mapping[str, Any], policy_hash: str) -> list[dict[str, Any]]:
    """Process supplied documents without altering documents omitted from a batch.

    The injected provider is called only for work that has no reusable terminal
    result. Relevant changes cause full extraction and routing, even for policy
    changes. Prior successful/failed attempts remain immutable in history.
    """
    records = []
    for path in paths:
        source = load_source(path)
        attempt_id, saved = store.register_intake(source, configuration=configuration, policy_hash=policy_hash)
        if saved is None:
            archived = store.attempt(attempt_id)
            saved = _finish(store, attempt_id, source, provider, archived["configuration"])
        records.append(saved)
    return records


def retry_attempt(store: Store, attempt_id: str, provider: Provider) -> dict[str, Any]:
    """Replay a named archived attempt; never open its original source path."""
    replay_id = store.register_replay(attempt_id)
    archived = store.attempt(replay_id)
    raw = archived["raw_bytes"]
    export = json.loads(raw.decode("utf-8"))
    source = Source(
        document_id=archived["document_id"], raw_bytes=raw,
        source_sha256=archived["source_sha256"], message=export["message"],
        metadata=MappingProxyType({key: value for key, value in export.items() if key != "message"}),
    )
    return _finish(store, replay_id, source, provider, archived["configuration"])
