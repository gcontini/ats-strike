"""The /v2 routes: the CV and its render run v2; the job description and the letter run v1, because
v2 has neither. One client server entry (a URL ending in /v2) then works for every command."""

from __future__ import annotations

import pytest
from server_helpers import POSTING, StubPipeline, a_job, files, read_events


@pytest.fixture
def v2(app_state) -> StubPipeline:
    other = StubPipeline()
    other.text = "Dear v2"
    other.info = lambda: {"models": {"compile": "stub-compile"}, "pdflatex": True}
    app_state.pipelines["v2"] = other
    return other


@pytest.fixture
def client(app_state, v2):
    from fastapi.testclient import TestClient

    from cv_server.api.app import create_app

    with TestClient(create_app(state=app_state)) as test_client:
        yield test_client


def test_the_cv_and_its_render_run_v2(client, stub, v2):
    response = client.post("/v2/cv", files=a_job(), headers={"X-Request-Id": "v2-posting"})
    assert response.status_code == 202
    assert read_events(client, "v2-posting", prefix="/v2")[-1][1] == "done"
    assert client.post("/v2/cv/render", files=files(job={"cv.json": b"{}"})).status_code == 200
    assert [c[0] for c in v2.calls] == ["cv", "render"]
    assert stub.calls == []


def test_the_job_description_and_the_letter_run_v1(client, stub, v2):
    detect = client.post("/v2/jd/detect", files=files(job={"job_description.txt": POSTING}))
    assert detect.json()["data"] == {"is_job_description": True}
    assert client.post("/v2/jd/analysis", files=a_job()).json()["data"]["analysis"] == stub.analysis
    assert client.post("/v2/letter", files=a_job()).json()["data"]["text"] == stub.text
    assert [c[0] for c in stub.calls] == ["detect", "analyze", "letter"]
    assert v2.calls == []


def test_an_unknown_prefix_is_a_404(client):
    assert client.post("/v3/letter", files=a_job()).status_code == 404


def test_health_reports_both_pipelines(client):
    models = client.get("/healthz").json()["data"]["models"]
    assert models == {"v1.cv": "stub-cv", "v1.detect": "stub-detect", "v2.compile": "stub-compile"}


def test_the_same_posting_on_v1_and_v2_is_two_sessions(client, stub, v2):
    """The client keys a session by the posting alone, so each pipeline keeps its own."""
    for prefix in ("/v1", "/v2"):
        response = client.post(f"{prefix}/cv", files=a_job(), headers={"X-Request-Id": "same-posting"})
        assert response.json()["data"] == {"reattached": False}, prefix
    assert read_events(client, "same-posting")[-1][1] == "done"
    assert read_events(client, "same-posting", prefix="/v2")[-1][1] == "done"
    assert [c[0] for c in stub.calls] == ["cv"] and [c[0] for c in v2.calls] == ["cv"]
    assert client.get("/v2/cv/same-posting/events").status_code == 200
