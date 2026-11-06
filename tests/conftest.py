"""Shared fixtures for the Arena Desktop test suite."""

import os
import tempfile

# Must run before any arena import: config.CONFIG_DIR is fixed at import time.
# Without this the suite wrote fake accounts into the real ~/.arena-personal.
os.environ["ARENA_CONFIG_DIR"] = tempfile.mkdtemp(prefix="arena-tests-")
os.environ["ARENA_PROJECTS_DIR"] = tempfile.mkdtemp(prefix="arena-tests-projects-")

import pytest
from flask.testing import FlaskClient

import arena.crypto as crypto
import arena.web.app as web


class _TokenClient(FlaskClient):
    """Injects X-Arena-Token on every request so the per-launch guard passes."""

    def open(self, *args, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("X-Arena-Token", web._API_TOKEN)
        kwargs["headers"] = headers
        return super().open(*args, **kwargs)


@pytest.fixture(autouse=True)
def _inject_api_token():
    prev = web.app.test_client_class
    web.app.test_client_class = _TokenClient
    yield
    web.app.test_client_class = prev


@pytest.fixture(autouse=True)
def _bypass_dpapi(monkeypatch):
    """Replace DPAPI encrypt/decrypt with identity so tests see plaintext."""
    monkeypatch.setattr(crypto, "encrypt", lambda data: data)
    monkeypatch.setattr(crypto, "decrypt", lambda data: data)
