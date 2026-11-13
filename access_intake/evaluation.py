"""Bind evaluation state and exports to an explicit, separate destination.

Markers live beside the database and inside the output directory, keeping the
intake database unchanged. Existing unmarked state/output is never adopted as
evaluation data. Repeated evaluation may reuse its own bound destinations.
"""
import json
from pathlib import Path


def state_marker(state: str | Path) -> Path:
    state = Path(state).resolve()
    return state.with_name(state.name + ".evaluation.json")


def _read_marker(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write_marker(path: Path, binding) -> None:
    try:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(binding, stream, indent=2)
            stream.write("\n")
    except FileExistsError:
        if _read_marker(path) != binding:
            raise ValueError("evaluation destination belongs to another state/output binding") from None


def prepare_evaluation(source: str | Path, state: str | Path, output: str | Path) -> None:
    """Validate both destinations before opening a database or publishing files."""
    source, state, output = Path(source).resolve(), Path(state).resolve(), Path(output).resolve()
    if not source.is_file():
        raise ValueError("evaluation requires one exported JSON message file")
    if state == source or output == source or state == output:
        raise ValueError("evaluation input, state and output must be separate paths")
    binding = {"version": 1, "purpose": "evaluation", "state": str(state), "output": str(output)}
    marker = state_marker(state)
    if marker.exists():
        if _read_marker(marker) != binding:
            raise ValueError("evaluation state is bound to a different output destination")
    elif state.exists() or Path(str(state) + "-wal").exists() or Path(str(state) + "-shm").exists():
        raise ValueError("evaluation requires separate state; existing intake state cannot be used")

    output_marker = output / ".evaluation.json"
    if output.exists():
        if not output.is_dir():
            raise ValueError("evaluation output must be a separate directory")
        if output_marker.exists():
            if _read_marker(output_marker) != binding:
                raise ValueError("evaluation output is bound to a different state destination")
        elif any(output.iterdir()):
            raise ValueError("evaluation requires separate output; existing intake exports cannot be used")

    state.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    _write_marker(marker, binding)
    _write_marker(output_marker, binding)


def protect_intake_state(state: str | Path) -> None:
    """Ordinary intake cannot add production messages to evaluation state."""
    if state_marker(state).exists():
        raise ValueError("this state is reserved for evaluation; use a separate intake state")


def protect_export_destination(state: str | Path, output: str | Path) -> None:
    """Prevent manual export from mixing evaluation and production outputs."""
    state, output = Path(state).resolve(), Path(output).resolve()
    marker, output_marker = state_marker(state), output / ".evaluation.json"
    if marker.exists():
        binding = _read_marker(marker)
        if binding.get("output") != str(output) or not output_marker.exists() or _read_marker(output_marker) != binding:
            raise ValueError("evaluation exports must use their bound isolated output destination")
    elif output_marker.exists():
        raise ValueError("intake state cannot publish into an evaluation output destination")
