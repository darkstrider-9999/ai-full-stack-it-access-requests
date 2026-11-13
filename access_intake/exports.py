"""Publish a pair of deliverables from one SQLite read snapshot.

Consistent consumers resolve OUTPUT/current once, then open both files inside
that pinned directory. Generations remain available after pointer changes.
Readers using the top-level aliases across a pointer switch must compare each
record/queue entry's generation_id and retry if the generations differ. Empty
exports are identified by the pinned directory name.
"""
import json
import os
from pathlib import Path
from uuid import uuid4

from .storage import Store


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def export_generation(store: Store, output: str | Path) -> Path:
    """Stage complete files, then atomically replace one relative symlink.

    No database transaction is held during file creation or publication. The
    aliases always point through 'current'; only that pointer changes per run.
    """
    output = Path(output).absolute()
    generations = output / "generations"
    generations.mkdir(parents=True, exist_ok=True)
    generation_id = uuid4().hex
    generation = generations / generation_id
    generation.mkdir()
    records = [{**record, "generation_id": generation_id} for record in store.current_records()]
    fields = ("document_id", "attempt_id", "source_sha256", "status", "owner", "next_action", "generation_id")
    queue = [{field: record[field] for field in fields} for record in records]
    _write(generation / "extractions.jsonl", "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records))
    _write(generation / "queue.json", json.dumps(queue, indent=2, ensure_ascii=False) + "\n")
    _sync_directory(generation)
    _sync_directory(generations)

    for name in ("extractions.jsonl", "queue.json"):
        alias = output / name
        target = Path("current") / name
        try:
            alias.symlink_to(target)
        except FileExistsError:
            if not alias.is_symlink() or alias.readlink() != target:
                raise ValueError(f"export path is not a managed generation alias: {alias}") from None

    temporary = output / f".current-{generation_id}"
    temporary.symlink_to(Path("generations") / generation_id)
    try:
        os.replace(temporary, output / "current")
        _sync_directory(output)
    finally:
        temporary.unlink(missing_ok=True)
    return generation
