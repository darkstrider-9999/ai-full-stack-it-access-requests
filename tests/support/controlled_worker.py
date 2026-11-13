"""Subprocess test harness: inject file barriers around the real CLI/provider.

Only this test helper interprets barrier files. The application's CLI has no
test-only crash/delay switches. All returned envelopes come from saved mocks.
"""
import json
import re
import sys
import time
from pathlib import Path
from threading import Event

from offline_guard import install

install()
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from access_intake import cli, intake
from access_intake.providers import MockProvider

control = json.loads(Path(sys.argv[1]).read_text())


def wait_file(name):
    deadline = time.monotonic() + 20
    while not Path(name).exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("test barrier was not released")
        Event().wait(0.02)


class ControlledMock(MockProvider):
    def extract(self, request):
        message = request["input"]["message"]
        request_id = re.search(r"IT-\d{3}", message).group()
        if "calls" in control:
            with Path(control["calls"]).open("a") as stream:
                stream.write(json.dumps({"request_id": request_id, "message": message}) + "\n")
        barrier = control.get("before_reply", {}).get(request_id)
        if barrier:
            Path(barrier["entered"]).touch()
            wait_file(barrier["release"])
        return super().extract(request)


original_prepare = intake.prepare_review


def controlled_prepare(source, *args, **kwargs):
    record = original_prepare(source, *args, **kwargs)
    barrier = control.get("before_persistence", {}).get(source.document_id)
    if barrier:
        Path(barrier["entered"]).touch()
        wait_file(barrier["release"])
    return record


cli.MockProvider = ControlledMock
intake.prepare_review = controlled_prepare
raise SystemExit(cli.main(sys.argv[2:]))
