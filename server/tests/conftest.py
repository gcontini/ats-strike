"""Fixtures shared by the server tests.

The server is the protocol and nothing else, so it is tested against a
:class:`StubPipeline` that records what it was handed and answers with canned
values: no key, no network, no model, no LaTeX. The real pipeline is tested
in ``pipeline/tests``, and the two together in ``tests/test_integration.py``.
"""

from __future__ import annotations

from threading import BoundedSemaphore

import pytest

from cv_server.api.deps import AppState
from cv_server.api.settings import Settings

from server_helpers import StubPipeline


@pytest.fixture
def stub() -> StubPipeline:
    return StubPipeline()


@pytest.fixture
def app_state(stub, tmp_path) -> AppState:
    settings = Settings(work_root=tmp_path)
    return AppState(
        settings=settings,
        pipeline=stub,
        job_slots=BoundedSemaphore(settings.max_concurrent_jobs),
    )


@pytest.fixture(autouse=True)
def fast_streams(monkeypatch):
    """A stream looks for new events every second; the stub answers at once."""
    monkeypatch.setattr("cv_server.api.routers.cv.FOLLOW_SECONDS", 0.01)


@pytest.fixture
def client(app_state):
    """A TestClient over the real app, wired to the stub."""
    from fastapi.testclient import TestClient

    from cv_server.api.app import create_app

    with TestClient(create_app(state=app_state)) as test_client:
        yield test_client
