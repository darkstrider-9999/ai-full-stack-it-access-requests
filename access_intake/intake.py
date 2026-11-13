"""Bounded, claimed intake and recovery from archived state.

After worker death, its remote call may still run. Reclamation can duplicate
remote execution/billing; claim tokens fence database completion only.
"""
import json
import shlex
import math
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from threading import Event, Thread
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4

from .response_gate import Provider
from .review import prepare_review
from .sources import Source, load_source
from .storage import StaleClaimError, Store


def _source(archived: Mapping[str, Any]) -> Source:
    raw = archived["raw_bytes"]
    export = json.loads(raw.decode("utf-8"))
    return Source(
        document_id=archived["document_id"], raw_bytes=raw,
        source_sha256=archived["source_sha256"], message=export["message"],
        metadata=MappingProxyType({key: value for key, value in export.items() if key != "message"}),
    )


def _visible(store: Store, attempt_id: str) -> dict[str, Any]:
    archived = store.attempt(attempt_id)
    if archived["result"] is not None:
        return archived["result"]
    for row in store.current_records(include_pending=True):
        if row["attempt_id"] == attempt_id:
            return row
    for row in store.pending_attempts():
        if row["attempt_id"] == attempt_id:
            return row
    return store.attempt(attempt_id)["result"]  # Concurrent completion between reads.


@contextmanager
def _heartbeat(store: Store, token: str, lease_seconds: float):
    stop = Event()

    def renew():
        while not stop.wait(min(lease_seconds / 3, 5)):
            if not store.renew_claim(token, lease_seconds=lease_seconds):
                return

    thread = Thread(target=renew, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join()


def _finish(store: Store, attempt_id: str, source: Source, provider: Provider,
            token: str, lease_seconds: float) -> dict[str, Any]:
    archived = store.attempt(attempt_id)
    try:
        with _heartbeat(store, token, lease_seconds):
            record = prepare_review(source, provider, attempt_id=attempt_id,
                                    configuration=archived["configuration"],
                                    request_contract=archived["execution_contract"])
            if record["error"] is not None and "replay_command" in record["error"]:
                record["error"]["replay_command"] += " --state " + shlex.quote(str(store.path))
            store.complete(attempt_id, record, claim_token=token)
    except StaleClaimError:
        return _visible(store, attempt_id)
    return store.attempt(attempt_id)["result"]


def _run_registered(store, items, provider_factory, *, workers, lease_seconds):
    if not isinstance(workers, int) or isinstance(workers, bool) or workers < 1:
        raise ValueError("workers must be a positive integer")
    if not math.isfinite(lease_seconds) or lease_seconds <= 0:
        raise ValueError("lease_seconds must be finite and positive")
    results = []
    worker_id = uuid4().hex
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}

        def collect(done):
            for future in done:
                results[futures.pop(future)] = future.result()

        for attempt_id, source, saved in items:
            index = len(results)
            results.append(saved)
            if saved is not None:
                continue
            token = store.claim(attempt_id, worker_id=worker_id, lease_seconds=lease_seconds)
            if token is None:
                results[index] = _visible(store, attempt_id)
                continue
            provider = provider_factory(store.attempt(attempt_id)["configuration"])
            if hasattr(provider, "reserve"):
                provider = provider.reserve()
            future = executor.submit(_finish, store, attempt_id, source, provider, token, lease_seconds)
            futures[future] = index
            if len(futures) >= workers:
                done, _ = wait(futures, return_when=FIRST_COMPLETED)
                collect(done)
        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            collect(done)
    return results


def intake_jobs(store: Store, jobs, provider_factory: Callable[[Mapping[str, Any]], Provider], *,
                workers: int = 4, lease_seconds: float = 60) -> list[dict[str, Any]]:
    """Archive the batch in input order, then bound workers/submitted futures."""
    return _run_registered(store, store.register_batch(jobs), provider_factory,
                           workers=workers, lease_seconds=lease_seconds)


def intake_sources(store: Store, paths: Iterable[str | Path], provider: Provider, *,
                   configuration: Mapping[str, Any], policy_hash: str,
                   workers: int = 4, lease_seconds: float = 60) -> list[dict[str, Any]]:
    """Process supplied documents without altering documents omitted from a batch.

    The injected provider is called only for work that has no reusable terminal
    result. Relevant changes cause full extraction and routing, even for policy
    changes. Prior successful/failed attempts remain immutable in history.
    """
    jobs = ((load_source(path), configuration, policy_hash) for path in paths)
    return intake_jobs(store, jobs, lambda settings: provider, workers=workers, lease_seconds=lease_seconds)


def retry_attempt(store: Store, attempt_id: str, provider: Provider, *, lease_seconds: float = 60) -> dict[str, Any]:
    """Replay a named archived attempt; never open its original source path."""
    replay_id = store.register_replay(attempt_id)
    archived = store.attempt(replay_id)
    return _run_registered(store, [(replay_id, _source(archived), None)], lambda settings: provider,
                           workers=1, lease_seconds=lease_seconds)[0]


def resume_pending(store: Store, provider_factory: Callable[[Mapping[str, Any]], Provider], *,
                   workers: int = 4, lease_seconds: float = 60) -> list[dict[str, Any]]:
    """Resume unfinished/expired work; completed failures require explicit retry.

    Live claims are inspected without another call. Reclaimed work keeps its
    original attempt ID, including obsolete work which stays historical.
    """
    def unfinished():
        for pending in store.pending_attempts():
            archived = store.attempt(pending["attempt_id"])
            yield archived["attempt_id"], _source(archived), archived["result"]

    return _run_registered(store, unfinished(), provider_factory, workers=workers, lease_seconds=lease_seconds)
