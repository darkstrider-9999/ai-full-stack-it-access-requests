"""Local SQLite snapshots, append-only attempts/results and current pointers."""
import json
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .sources import Source
from .prompts import build_request
from .response_gate import SCHEMA as EXTRACTION_SCHEMA


SCHEMA = """
CREATE TABLE IF NOT EXISTS attempts (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id TEXT NOT NULL UNIQUE,
    document_id TEXT NOT NULL,
    source_sha256 TEXT NOT NULL,
    raw_bytes BLOB NOT NULL,
    configuration_json TEXT NOT NULL,
    policy_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    latest_attempt TEXT NOT NULL REFERENCES attempts(attempt_id)
);
CREATE TABLE IF NOT EXISTS results (
    attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
    record_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS replays (
    attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
    replay_of TEXT NOT NULL REFERENCES attempts(attempt_id)
);
CREATE TABLE IF NOT EXISTS execution_contracts (
    attempt_id TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
    contract_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS attempts_document ON attempts(document_id, sequence);
CREATE TRIGGER IF NOT EXISTS attempts_no_update BEFORE UPDATE ON attempts
BEGIN SELECT RAISE(ABORT, 'attempt snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON attempts
BEGIN SELECT RAISE(ABORT, 'attempt snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS results_no_update BEFORE UPDATE ON results
BEGIN SELECT RAISE(ABORT, 'completed results are immutable'); END;
CREATE TRIGGER IF NOT EXISTS results_no_delete BEFORE DELETE ON results
BEGIN SELECT RAISE(ABORT, 'completed results are immutable'); END;
CREATE TRIGGER IF NOT EXISTS replays_no_update BEFORE UPDATE ON replays
BEGIN SELECT RAISE(ABORT, 'replay links are immutable'); END;
CREATE TRIGGER IF NOT EXISTS replays_no_delete BEFORE DELETE ON replays
BEGIN SELECT RAISE(ABORT, 'replay links are immutable'); END;
CREATE TRIGGER IF NOT EXISTS contracts_no_update BEFORE UPDATE ON execution_contracts
BEGIN SELECT RAISE(ABORT, 'execution contracts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS contracts_no_delete BEFORE DELETE ON execution_contracts
BEGIN SELECT RAISE(ABORT, 'execution contracts are immutable'); END;
"""


def extraction_fingerprint(configuration: Mapping[str, Any], policy_hash: str) -> str:
    """Fingerprint result-affecting settings; worker count is operational."""
    settings = {key: value for key, value in configuration.items() if key != "workers"}
    encoded = json.dumps({"configuration": settings, "policy_hash": policy_hash},
                         sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class Store:
    """Open short-lived connections so provider/file waits hold no transaction.

    Registration commits the document's latest-attempt pointer. Completion adds
    an immutable result; finishing an older attempt cannot change that pointer.
    Processing state is separate from policy status and is derived from whether
    a terminal result exists. Worker claims and leases are added in step 7.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(SCHEMA)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        try:
            yield connection
        finally:
            connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        # No connection or transaction is kept across method calls.
        return False

    @staticmethod
    def _archive_contract(connection, attempt_id: str, source: Source) -> None:
        contract = {"request": build_request(source), "schema": EXTRACTION_SCHEMA}
        connection.execute("INSERT INTO execution_contracts(attempt_id, contract_json) VALUES (?, ?)",
                           (attempt_id, json.dumps(contract, ensure_ascii=False)))

    def register(self, source: Source, *, configuration: Mapping[str, Any], policy_hash: str) -> str:
        """Archive one attempt; intake reuse is a separate, higher-level choice."""
        attempt_id = uuid4().hex
        settings = json.dumps(dict(configuration), ensure_ascii=False, sort_keys=True)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    "INSERT INTO attempts(attempt_id, document_id, source_sha256, raw_bytes, configuration_json, policy_hash) VALUES (?, ?, ?, ?, ?, ?)",
                    (attempt_id, source.document_id, source.source_sha256, source.raw_bytes, settings, policy_hash),
                )
                self._archive_contract(connection, attempt_id, source)
                connection.execute(
                    "INSERT INTO documents(document_id, latest_attempt) VALUES (?, ?) "
                    "ON CONFLICT(document_id) DO UPDATE SET latest_attempt=excluded.latest_attempt",
                    (source.document_id, attempt_id),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return attempt_id

    def register_intake(self, source: Source, *, configuration: Mapping[str, Any], policy_hash: str) -> tuple[str, dict[str, Any] | None]:
        """Atomically inspect the current version or register a new revision.

        Unchanged terminal failures stay visible until explicit retry. Only the
        current attempt is considered: A→B→A always registers a new revision.
        Matching unfinished work retains its existing attempt for resumption;
        durable worker claims are the next build step.
        """
        fingerprint = extraction_fingerprint(configuration, policy_hash)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                current = connection.execute(
                    "SELECT a.*, r.record_json FROM documents d JOIN attempts a ON a.attempt_id=d.latest_attempt "
                    "LEFT JOIN results r ON r.attempt_id=a.attempt_id WHERE d.document_id=?",
                    (source.document_id,),
                ).fetchone()
                if (current is not None and current["source_sha256"] == source.source_sha256
                    and extraction_fingerprint(json.loads(current["configuration_json"]), current["policy_hash"]) == fingerprint):
                    result = json.loads(current["record_json"]) if current["record_json"] is not None else None
                    connection.commit()
                    return current["attempt_id"], result
                attempt_id = uuid4().hex
                connection.execute(
                    "INSERT INTO attempts(attempt_id, document_id, source_sha256, raw_bytes, configuration_json, policy_hash) VALUES (?, ?, ?, ?, ?, ?)",
                    (attempt_id, source.document_id, source.source_sha256, source.raw_bytes,
                     json.dumps(dict(configuration), sort_keys=True, ensure_ascii=False), policy_hash),
                )
                self._archive_contract(connection, attempt_id, source)
                connection.execute(
                    "INSERT INTO documents(document_id, latest_attempt) VALUES (?, ?) "
                    "ON CONFLICT(document_id) DO UPDATE SET latest_attempt=excluded.latest_attempt",
                    (source.document_id, attempt_id),
                )
                connection.commit()
                return attempt_id, None
            except Exception:
                connection.rollback()
                raise

    def register_replay(self, replay_of: str) -> str:
        """Replay archived bytes/settings; promote only a retry of current work.

        Checking the parent and changing the pointer happen in one transaction.
        An obsolete replay remains historical regardless of completion order.
        """
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                archived = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (replay_of,)).fetchone()
                if archived is None:
                    raise ValueError("unknown replay attempt")
                contract = connection.execute("SELECT contract_json FROM execution_contracts WHERE attempt_id=?", (replay_of,)).fetchone()
                if contract is None:
                    raise ValueError("legacy attempt has no archived prompt/schema contract; an exact replay is unavailable")
                attempt_id = uuid4().hex
                connection.execute(
                    "INSERT INTO attempts(attempt_id, document_id, source_sha256, raw_bytes, configuration_json, policy_hash) VALUES (?, ?, ?, ?, ?, ?)",
                    (attempt_id, archived["document_id"], archived["source_sha256"], archived["raw_bytes"],
                     archived["configuration_json"], archived["policy_hash"]),
                )
                connection.execute("INSERT INTO replays(attempt_id, replay_of) VALUES (?, ?)", (attempt_id, replay_of))
                connection.execute("INSERT INTO execution_contracts(attempt_id, contract_json) VALUES (?, ?)",
                                   (attempt_id, contract["contract_json"]))
                connection.execute("UPDATE documents SET latest_attempt=? WHERE document_id=? AND latest_attempt=?",
                                   (attempt_id, archived["document_id"], replay_of))
                connection.commit()
                return attempt_id
            except Exception:
                connection.rollback()
                raise

    def complete(self, attempt_id: str, record: Mapping[str, Any]) -> None:
        """Append a terminal result once, using registered provenance."""
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                attempt = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
                if attempt is None:
                    raise ValueError("unknown attempt")
                if connection.execute("SELECT 1 FROM results WHERE attempt_id=?", (attempt_id,)).fetchone():
                    raise ValueError("attempt already completed")
                if any(record.get(field) != attempt[field] for field in ("attempt_id", "document_id", "source_sha256")):
                    raise ValueError("result provenance does not match the registered attempt")
                saved = dict(record)
                saved.update(
                    configuration=json.loads(attempt["configuration_json"]),
                    policy_hash=attempt["policy_hash"],
                    processing_state="succeeded" if record["error"] is None else "failed",
                    extraction_fingerprint=extraction_fingerprint(json.loads(attempt["configuration_json"]), attempt["policy_hash"]),
                )
                connection.execute("INSERT INTO results(attempt_id, record_json) VALUES (?, ?)",
                                   (attempt_id, json.dumps(saved, ensure_ascii=False)))
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def current_records(self) -> list[dict[str, Any]]:
        """Read completed/latest-failed records in one database snapshot.

        Pending attempts have no exportable result. Source snapshots and older
        attempts are available through history, never mixed into this result.
        """
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT r.record_json FROM documents d JOIN results r "
                "ON r.attempt_id=d.latest_attempt ORDER BY d.document_id"
            ).fetchall()
        return [json.loads(row["record_json"]) for row in rows]

    def history(self, document_id: str) -> list[dict[str, Any]]:
        """Return archived bytes/settings and results in registration order."""
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT a.*, r.record_json, p.replay_of, c.contract_json FROM attempts a LEFT JOIN results r "
                "ON r.attempt_id=a.attempt_id LEFT JOIN replays p ON p.attempt_id=a.attempt_id "
                "LEFT JOIN execution_contracts c ON c.attempt_id=a.attempt_id "
                "WHERE a.document_id=? ORDER BY a.sequence",
                (document_id,),
            ).fetchall()
        history = []
        for row in rows:
            result = json.loads(row["record_json"]) if row["record_json"] is not None else None
            history.append({
                "attempt_id": row["attempt_id"], "document_id": row["document_id"],
                "source_sha256": row["source_sha256"], "raw_bytes": row["raw_bytes"],
                "configuration": json.loads(row["configuration_json"]), "policy_hash": row["policy_hash"],
                "processing_state": result["processing_state"] if result else "pending",
                "result": result,
                "replay_of": row["replay_of"],
                "execution_contract": json.loads(row["contract_json"]) if row["contract_json"] is not None else None,
            })
        return history

    def attempt(self, attempt_id: str) -> dict[str, Any]:
        """Look up any immutable attempt, including obsolete historical work."""
        with self._connection() as connection:
            row = connection.execute("SELECT document_id FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if row is None:
            raise ValueError("unknown attempt")
        return next(attempt for attempt in self.history(row["document_id"]) if attempt["attempt_id"] == attempt_id)
