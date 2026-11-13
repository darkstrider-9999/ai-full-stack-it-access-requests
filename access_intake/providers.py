"""Provider boundaries; the file-backed mock performs no external operations."""
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from threading import Lock
from typing import Any


class MockProvider:
    """Consume a finite script of fixture replies, barriers and exceptions.

    Each action contains either ``response`` (a fixture filename) or ``error``
    (an exception). Optional ``entered`` and ``release`` threading events let
    tests control when an action completes. Calls claim actions in lock order;
    waiting and file reads happen outside the lock so other calls can proceed.
    """

    def __init__(
        self,
        fixtures: str | Path,
        *,
        script: Iterable[Mapping[str, Any]],
    ) -> None:
        self._fixtures = Path(fixtures).resolve()
        self._script = tuple(dict(action) for action in script)
        self._lock = Lock()
        self._call_count = 0

    @property
    def call_count(self) -> int:
        """Count attempted calls, including failures and blocked calls."""
        with self._lock:
            return self._call_count

    def extract(self, request: Mapping[str, Any]) -> dict[str, Any]:
        """Return the next saved envelope unchanged, without interpreting input.

        Exhaustion is an error rather than an implicit repeat or fallback to a
        live provider. Response parsing/validation belongs to the response gate.
        """
        with self._lock:
            index = self._call_count
            self._call_count += 1
            if index >= len(self._script):
                raise RuntimeError("mock provider script exhausted")
            action = self._script[index]

        if "entered" in action:
            action["entered"].set()
        if "release" in action:
            action["release"].wait()
        if "error" in action:
            raise action["error"]

        path = (self._fixtures / action["response"]).resolve()
        if not path.is_relative_to(self._fixtures):
            raise ValueError("mock response must be inside the fixture directory")
        return json.loads(path.read_text(encoding="utf-8"))
