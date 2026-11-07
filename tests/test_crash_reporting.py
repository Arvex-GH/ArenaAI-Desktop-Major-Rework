"""A failure inside a reply must say what happened.

Before this, an exception anywhere in a streaming reply reached the window as
"The app hit an internal error." with nothing else — no exception name in the
UI and no traceback in desktop.log — so a report like that could not be acted
on. These tests pin both halves of the fix, plus the engine-bridge hardening
that removes one known way of crashing a turn before it starts.
"""

import json
import logging

import pytest

from arena.web import app as appmod

streammod = appmod  # stream.py runs inside app.py's namespace


def _events(payload: str):
    out = []
    for line in payload.splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: "):]))
    return out


def test_a_crash_reports_the_exception_and_writes_a_traceback(caplog):
    def broken():
        yield 'data: {"type": "text"}\n\n'
        raise AttributeError("'str' object has no attribute 'get'")

    with caplog.at_level(logging.ERROR, logger="arena.desktop"):
        payload = "".join(streammod._sse_wrap(broken(), chat_id=None))

    events = _events(payload)
    error = next(event for event in events if event["type"] == "error")
    # the friendly sentence stays first, the exception travels beside it
    assert error["message"] == "The app hit an internal error."
    assert error["detail"] == "AttributeError: 'str' object has no attribute 'get'"
    assert events[-1]["type"] == "done"

    crashed = [r for r in caplog.records if "generation crashed" in r.getMessage()]
    assert crashed, "the crash was not logged"
    assert crashed[0].exc_info is not None, "the log line has no traceback"


class _DeadEngine:
    """The arena.ai window: closed, crashed, or simply not answering."""

    def wait_ready(self, *_args, **_kwargs):
        raise RuntimeError("window destroyed")

    def native_preflight(self, *_args, **_kwargs):
        raise RuntimeError("window destroyed")


def test_an_engine_hiccup_is_not_an_internal_error(monkeypatch):
    monkeypatch.setattr(appmod, "ENGINE", _DeadEngine())
    assert appmod._engine_ready(1) is False
    assert appmod._engine_cx("submit") == {}


def test_a_closed_window_reads_as_not_ready(monkeypatch):
    monkeypatch.setattr(appmod, "ENGINE", _DeadEngine())

    gen = appmod._native_create_session("hi", "Europe/Berlin", [])
    with pytest.raises(StopIteration) as stop:
        while True:
            next(gen)
    boot, error = stop.value.value
    assert boot is None
    assert "arena.ai window" in error
