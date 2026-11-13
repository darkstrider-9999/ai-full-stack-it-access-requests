"""Load source exports without normalizing their bytes or message text."""
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class Source:
    """A source snapshot with display metadata separate from extraction data."""

    document_id: str
    raw_bytes: bytes
    source_sha256: str
    message: str
    metadata: Mapping[str, Any]


def load_source(path: str | Path) -> Source:
    """Read a supplied-format UTF-8 JSON export and hash the original bytes.

    Input read/decode errors propagate to the caller. Their eventual application
    outcome is outside this loader's contract; no extraction route is inferred.
    """
    path = Path(path)
    raw = path.read_bytes()
    export = json.loads(raw.decode("utf-8"))
    if not isinstance(export, dict) or not isinstance(export.get("message"), str):
        raise ValueError("source export must be an object with a message string")
    return Source(
        document_id=path.name,
        raw_bytes=raw,
        source_sha256=hashlib.sha256(raw).hexdigest(),
        message=export["message"],
        metadata=MappingProxyType({key: value for key, value in export.items() if key != "message"}),
    )
