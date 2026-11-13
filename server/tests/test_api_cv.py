"""The CV endpoints end to end over HTTP, with a stand-in pipeline.

``POST /v1/cv`` answers before the work is done, so almost nothing here
asserts on its response: it starts a job, and the test follows the job's
session to its last event. The stub answers at once, so a job may well be
finished before the stream opens — which is fine, because the session is
files and the stream replays them from the first. Where a test needs the job
to be provably *still running*, it closes the stub's gate first.
"""

from __future__ import annotations

import base64
import json
import os
import threading
import time

import pytest
from fastapi.testclient import TestClient

from cv_contracts import Done, Failure, Status, StoreItem

from cv_server.api.app import create_app
from cv_server.jobstore import JobStore

from server_helpers import (
    POSTING,
    Failing,
    a_job,
    files,
    logs,
    of_type,
    raw_events,
    read_events,
    run_cv,
    start_cv,
)


def hold(stub) -> threading.Event:
    """Keep the job provably running: it waits for the gate."""
    stub.gate = threading.Event()
    return stub.gate


def done_of(events) -> Done:
    number, kind, data = events[-1]
    assert kind == "done", data
    return Done.model_validate(data)


def failure_of(events) -> Failure:
    number, kind, data = events[-1]
    assert kind == "error", data
    return Failure.model_validate(data)


# --- accepting ---------------------------------------------------------------
def test_the_job_is_accepted_not_waited_for(client, stub):
    """202 and an id: the only thing the caller gets up front is the handle."""
    gate = hold(stub)
    try:
        response = client.post("/v1/cv", files=a_job())
        assert response.status_code == 202
        assert response.json()["data"] == {"reattached": False}
        assert response.headers["x-request-id"] == response.json()["request_id"]
    finally:
        gate.set()


def test_a_job_in_flight_is_a_session_that_has_started(client, stub, app_state):
    """The first event is in the session before the POST answers, so a client
    that follows it at once never finds it empty."""
    gate = hold(stub)
    try:
        request_id = start_cv(client)
        events = app_state.jobs.open(request_id).events()
        assert events == [(1, "status", {"status": "started", "detail": ""})]
    finally:
        gate.set()
    read_events(client, request_id)


def test_a_repeated_status_is_not_reported_twice(client):
    statuses = [s["status"] for s in of_type(run_cv(client), "status")]
    assert statuses == ["started", "writing", "rendering"]


def test_following_leaves_nothing_behind(client, stub, app_state):
    """A stream is not a request that did anything, so it must not cost a
    directory — and neither does the job's scratch space, which is not here."""
    request_id = start_cv(client)
    for _ in range(3):
        read_events(client, request_id)

    assert stub.calls[0][-1] is True, "the job had a scratch directory"
    assert [p.name for p in app_state.settings.work_root.iterdir()] == [request_id]
    assert [p.name for p in (app_state.settings.work_root / request_id).iterdir()] == ["events"]


def test_a_bad_request_starts_no_session(client, stub, app_state):
    response = client.post("/v1/cv", files=a_job(), data={"settings": "[1]"})
    assert response.status_code == 400
    assert stub.calls == []
    assert list(app_state.settings.work_root.iterdir()) == []


# --- what comes back ---------------------------------------------------------
def test_the_pipeline_is_handed_the_whole_request(client, stub):
    parts = files(pack={"candidate_profile.json": b"{}", "cache/pool.json": b"[]"},
                  job={"jd.txt": POSTING, "steps/key_points.json": b"[]"})
    run_cv(client, parts=parts, settings={"pages": 1, "extra_body": {"cv": {"top_p": 1}}})
    name, pack, job, settings, _ = stub.calls[-1]
    assert name == "cv"
    assert pack == {"candidate_profile.json": b"{}", "cache/pool.json": b"[]"}
    assert job == {"jd.txt": POSTING.encode(), "steps/key_points.json": b"[]"}
    assert settings == {"pages": 1, "extra_body": {"cv": {"top_p": 1}}}


def test_each_output_is_stored_as_it_comes_and_the_session_ends_done(client, stub):
    events = run_cv(client)
    assert [n for n, _, _ in events] == list(range(1, len(events) + 1))
    stored = [StoreItem.model_validate(item) for item in of_type(events, "store")]
    outputs = [item for item in stored if item.type != "log"]
    assert [(o.location, o.type, o.name) for o in outputs] == [
        ("CV", "step", "steps/key_points.json"), ("CV", "step", "cv.json"), ("CV", "cv", "cv.pdf"),
    ]
    assert outputs[0].text == '[{"id": 1}]'
    assert outputs[2].content() == b"%PDF-1.7 \x00\xff"
    assert done_of(events).detail == "done, 1 model call"


def test_a_binary_output_travels_as_base64_and_a_text_one_does_not(client):
    stored = of_type(run_cv(client), "store")
    pdf = next(item for item in stored if item["type"] == "cv")
    step = next(item for item in stored if item["type"] == "step")
    assert set(pdf) == {"location", "type", "name", "base64"}
    assert base64.b64decode(pdf["base64"]) == b"%PDF-1.7 \x00\xff"
    assert set(step) == {"location", "type", "name", "text"}


def test_every_line_the_job_logged_is_stored_and_the_last_adds_them_up(client):
    events = run_cv(client)
    lines = logs(of_type(events, "store"))
    assert any("[stub] stub-model 0.5s" in line for line in lines)
    assert "Σ 1 model call(s), 0.5s | input=11, thinking=2, output=20" in lines[-1]
    # Nothing comes between the total and the end.
    assert [kind for _, kind, _ in events[-2:]] == ["store", "done"]


def test_the_slot_comes_back_when_the_job_ends(client, app_state):
    """Released by the worker, not by the request — so a leak only shows here."""
    run_cv(client)

    taken = [app_state.job_slots.acquire(blocking=False)
             for _ in range(app_state.settings.max_concurrent_jobs)]
    for _ in [t for t in taken if t]:
        app_state.job_slots.release()
    assert all(taken)


def test_an_unknown_session_is_a_404(client):
    for response in (client.get("/v1/cv/never-happened/events"),
                     client.delete("/v1/cv/never-happened")):
        assert response.status_code == 404
        assert "unknown_request" in response.json()["error"]


# --- the same posting again --------------------------------------------------
def test_the_same_id_again_replays_the_session_and_starts_nothing(client, stub):
    first = read_events(client, start_cv(client, request_id="posting-1"))

    again = client.post("/v1/cv", files=a_job(), headers={"X-Request-Id": "posting-1"})
    assert again.status_code == 202
    assert again.json()["data"] == {"reattached": True}
    assert read_events(client, "posting-1") == first
    assert len(stub.calls) == 1


def test_a_job_still_running_is_joined_not_restarted(client, stub):
    gate = hold(stub)
    try:
        start_cv(client, request_id="posting-2")
        again = client.post("/v1/cv", files=a_job(), headers={"X-Request-Id": "posting-2"})
        assert again.json()["data"] == {"reattached": True}
    finally:
        gate.set()
    assert done_of(read_events(client, "posting-2"))
    assert len(stub.calls) == 1


def test_last_event_id_sends_only_what_was_missed(client):
    request_id = start_cv(client)
    everything = read_events(client, request_id)
    assert read_events(client, request_id, after=everything[2][0]) == everything[3:]


def test_a_stream_past_the_end_closes_at_once(client):
    """A client that already has the last event, and reconnects anyway,
    must not be left waiting for one that will never come."""
    request_id = start_cv(client)
    last = read_events(client, request_id)[-1][0]
    assert raw_events(client, request_id, after=last) == []


def test_a_quiet_stream_says_it_is_still_there(client, stub, monkeypatch):
    monkeypatch.setattr("cv_server.api.routers.cv.PING_SECONDS", 0.05)
    gate = hold(stub)
    request_id = start_cv(client)
    threading.Timer(0.5, gate.set).start()

    events = raw_events(client, request_id)
    assert "ping" in {kind for _, kind, _ in events}
    assert events[-1][1] == "done"


# --- acknowledging, resetting, expiring --------------------------------------
def test_acknowledging_deletes_the_session_and_frees_the_id(client, stub):
    read_events(client, start_cv(client, request_id="posting-3"))

    response = client.delete("/v1/cv/posting-3")
    assert response.status_code == 200
    assert response.json() == {"request_id": "posting-3", "ok": True}
    assert client.get("/v1/cv/posting-3/events").status_code == 404

    again = client.post("/v1/cv", files=a_job(), headers={"X-Request-Id": "posting-3"})
    assert again.json()["data"] == {"reattached": False}
    assert done_of(read_events(client, "posting-3"))
    assert len(stub.calls) == 2


def test_a_running_session_cannot_be_acknowledged(client, stub):
    """Its worker is still writing there, and a new session for the same
    posting would get the same directory."""
    gate = hold(stub)
    try:
        request_id = start_cv(client)
        response = client.delete(f"/v1/cv/{request_id}")
        assert response.status_code == 409
        assert "still running" in response.json()["error"]
    finally:
        gate.set()
    read_events(client, request_id)


def test_a_reset_deletes_every_session_but_a_running_one(client, stub, app_state):
    for name in ("done-1", "done-2"):
        read_events(client, start_cv(client, request_id=name))
    gate = hold(stub)
    try:
        start_cv(client, request_id="busy")
        response = client.delete("/v1/cv?all")
        assert response.status_code == 200
        assert response.json()["data"] == {"deleted": 2, "running": 1}
        assert [p.name for p in app_state.settings.work_root.iterdir()] == ["busy"]
    finally:
        gate.set()
    read_events(client, "busy")


def test_a_reset_has_to_be_asked_for(client):
    response = client.delete("/v1/cv")
    assert response.status_code == 400
    assert "?all" in response.json()["error"]


def test_an_expired_session_is_not_replayed(client, app_state):
    """Older than the TTL, it is pruned before the lookup: a new job starts."""
    read_events(client, start_cv(client, request_id="old-posting"))
    day_and_more = time.time() - 86400 * app_state.settings.session_ttl_days - 60
    for path in (app_state.settings.work_root / "old-posting" / "events").iterdir():
        os.utime(path, (day_and_more, day_and_more))

    again = client.post("/v1/cv", files=a_job(), headers={"X-Request-Id": "old-posting"})
    assert again.json()["data"] == {"reattached": False}
    read_events(client, "old-posting")


def test_a_job_a_previous_process_left_running_ends_with_an_error(app_state):
    """A fresh process owns no workers, so the session is closed for it — a
    client following it stops waiting."""
    job = JobStore(app_state.settings.work_root).create("orphan")
    job.append("status", Status(status="validation").model_dump(mode="json"))

    with TestClient(create_app(state=app_state)) as fresh:
        events = read_events(fresh, "orphan")
    failure = failure_of(events)
    assert failure.http_status == 503
    assert "restarted" in failure.error


def test_a_request_id_cannot_name_a_directory_of_its_own_choosing(client, app_state):
    """The id is a path component, so anything that is not one is replaced."""
    response = client.post("/v1/cv", files=a_job(), headers={"X-Request-Id": "../../evil"})
    assert response.json()["request_id"] != "../../evil"
    assert not (app_state.settings.work_root.parent / "evil").exists()
    read_events(client, response.json()["request_id"])


# --- failing -----------------------------------------------------------------
@pytest.mark.parametrize("failure, status, message", [
    (Failing("model_output", "cv: no usable reply", stage="cv.jd_evaluation"), 502,
     "model_output [cv.jd_evaluation]: cv: no usable reply"),
    (Failing("missing_part", "file 'candidate_data.json' is required"), 400,
     "missing_part: file 'candidate_data.json' is required"),
    (Failing("bad_part", "setting 'pages' must be ..."), 400, "bad_part: setting 'pages'"),
    (Failing("budget_exceeded", "time budget exhausted"), 504, "budget_exceeded: "),
    (Failing("provider_timeout", "the model endpoint failed: X"), 504, "provider_timeout: "),
    (Failing("provider_error", "the model endpoint failed: X"), 502, "provider_error: "),
    (RuntimeError("a bug"), 500, "internal_error: RuntimeError: a bug"),
])
def test_a_failed_job_ends_with_the_cause_after_its_total(client, stub, failure, status,
                                                          message):
    stub.failure = failure
    events = run_cv(client)

    ending = failure_of(events)
    assert ending.http_status == status
    assert ending.error.startswith(message)
    # The attempt that was paid for is there, and added up, just before the end.
    lines = logs(of_type(events, "store"))
    assert "Σ 1 model call(s)" in lines[-1]
    assert events[-2][1] == "store"
    said = "unhandled error on POST /v1/cv" if status == 500 else f"-> {status}"
    assert any(said in line for line in lines), "the failure is in the log too"


def test_a_full_server_says_429_rather_than_queueing(client, app_state):
    taken = [app_state.job_slots.acquire(blocking=False)
             for _ in range(app_state.settings.max_concurrent_jobs)]
    try:
        response = client.post("/v1/cv", files=a_job())
    finally:
        for _ in [t for t in taken if t]:
            app_state.job_slots.release()
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"


# --- rendering ---------------------------------------------------------------
def test_render_returns_what_to_store(client, stub):
    parts = files(pack={"resume.tex.jinja": "x"}, job={"cv.json": b"{}"})
    data = client.post("/v1/cv/render", files=parts).json()["data"]
    outputs = [StoreItem.model_validate(item) for item in data["items"] if item["type"] != "log"]
    assert [(o.type, o.name) for o in outputs] == [("step", "cv.json"), ("cv", "cv.pdf")]
    assert outputs[1].content() == b"%PDF-1.7 \x00\xff"
    assert stub.calls[-1] == ("render", {"resume.tex.jinja": b"x"}, {"cv.json": b"{}"}, True)


def test_a_render_logs_but_adds_up_nothing_when_no_model_was_called(client):
    items = client.post("/v1/cv/render", files=files(job={"cv.tex": "x"})).json()["data"]["items"]
    lines = logs(items)
    assert any("rendered" in line for line in lines)
    assert not any("Σ" in line for line in lines)


def test_a_render_keeps_nothing(client, app_state):
    """Only a CV job has a session; a render's scratch space is gone with it."""
    client.post("/v1/cv/render", files=files(job={"cv.json": b"{}"}))
    assert list(app_state.settings.work_root.iterdir()) == []


def test_a_broken_template_is_422_with_the_tex_log_in_the_reply(client, stub):
    """A failed render is nearly always this, and only the TeX log says where."""
    failure = Failing("latex_compile", "pdflatex failed", stage="compile")
    failure.log_tail = "! Undefined control sequence."
    stub.failure = failure
    response = client.post("/v1/cv/render", files=files(job={"cv.json": b"{}"}))
    assert response.status_code == 422
    assert response.json()["error"] == (
        "latex_compile [compile]: pdflatex failed\n! Undefined control sequence."
    )


def test_render_takes_its_parts_like_every_route(client, stub):
    response = client.post("/v1/cv/render", files=files(job={"../cv.json": b"{}"}))
    assert response.status_code == 400
    assert stub.calls == []
    assert json.loads(response.text)["ok"] is False
