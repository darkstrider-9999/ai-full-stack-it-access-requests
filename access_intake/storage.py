"""Local SQLite snapshots, append-only attempts/results and current pointers."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from .sources import Source


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
CREATE INDEX IF NOT EXISTS attempts_document ON attempts(document_id, sequence);
CREATE TRIGGER IF NOT EXISTS attempts_no_update BEFORE UPDATE ON attempts
BEGIN SELECT RAISE(ABORT, 'attempt snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON attempts
BEGIN SELECT RAISE(ABORT, 'attempt snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS results_no_update BEFORE UPDATE ON results
BEGIN SELECT RAISE(ABORT, 'completed results are immutable'); END;
CREATE TRIGGER IF NOT EXISTS results_no_delete BEFORE DELETE ON results
BEGIN SELECT RAISE(ABORT, 'completed results are immutable'); END;
"""


class Store:
    """Open short-lived connections so provider/file waits hold no transaction.

    Registration commits the document's latest-attempt pointer. Completion adds
    an immutable result; finishing an older attempt cannot change that pointer.
    Processing state is separate from policy status and is derived from whether
    a terminal result exists. Worker claims and leases are added in step 7.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
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
                "SELECT a.*, r.record_json FROM attempts a LEFT JOIN results r "
                "ON r.attempt_id=a.attempt_id WHERE a.document_id=? ORDER BY a.sequence",
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
            })
        return history
