"""Test infrastructure: reject network access and credential lookups, including in children."""
import os
import socket
from pathlib import Path


def install():
    if getattr(socket, "_intake_test_guard", False):
        return

    def no_network(*args, **kwargs):
        raise AssertionError("network access forbidden in tests")

    original_getitem = os._Environ.__getitem__
    original_contains = os._Environ.__contains__

    def check_key(key):
        if key in ("API_KEY", b"API_KEY"):
            raise AssertionError("API_KEY lookup forbidden in tests")

    def guarded_getitem(environment, key):
        check_key(key)
        return original_getitem(environment, key)

    def guarded_contains(environment, key):
        check_key(key)
        return original_contains(environment, key)

    os._Environ.__getitem__ = guarded_getitem
    os._Environ.__contains__ = guarded_contains
    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            setattr(socket.socket, name, no_network)
    socket.create_connection = no_network
    socket.getaddrinfo = no_network
    socket._intake_test_guard = True

    # Python imports sitecustomize at startup, before a child's application imports.
    support = str(Path(__file__).resolve().parent)
    paths = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    if support not in paths:
        os.environ["PYTHONPATH"] = os.pathsep.join([support, *paths])
