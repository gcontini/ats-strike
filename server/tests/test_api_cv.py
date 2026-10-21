"""The CV endpoints end to end over HTTP, with fake models.

``POST /v1/cv`` answers before the work is done, so almost nothing here
asserts on its response: it starts a job, and the test follows the job's
session to its last event. The fakes reply instantly, so a job may well be
finished before the stream opens — which is fine, because the session is
files and the stream replays them from the first. Where a test needs the job
to be provably *still running*, it closes the model's gate first.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cv_contracts import CVFailure, CVResult, CVStatus, RenderedCV

from cv_server.api.app import create_app
from cv_server.api.multipart import MAX_IMAGES
from cv_server.jobstore import JobStore
from cv_server.pipeline.cv_generator import ARTIFACTS

from server_helpers import (
    EXAMPLE_CANDIDATE,
    SAMPLE_BODY,
    of_type,
    raw_events,
    read_events,
    run_cv,
    script_cv,
    start_cv,
    user_text,
)

needs_latex = pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
STEPS = ["jd_evaluation", "profile_scoring", "format_selection", "cv", "validation", "highlight"]


def a_good_run(fake_llm, **script):
    """Every step answers; the validators find nothing to fix."""
    script_cv(fake_llm, **script)


def a_quick_failure(fake_llm):
    """The first step never gets a usable answer: the job ends at once, with an error."""
    fake_llm["jd_evaluation"].replies = ["never valid json"]


def hold(fake_llm) -> threading.Event:
    """Keep the job provably running: its first model call waits for the gate."""
    fake_llm["jd_evaluation"].gate = threading.Event()
    return fake_llm["jd_evaluation"].gate


def result_of(events) -> CVResult:
    number, kind, data = events[-1]
    assert kind == "result", data
    return CVResult.model_validate(data)


# --- accepting ---------------------------------------------------------------
def test_the_job_is_accepted_not_waited_for(client, fake_llm, parts):
    """202 and an id: the only thing the caller gets up front is the handle."""
    a_good_run(fake_llm)
    gate = hold(fake_llm)
    try:
        response = client.post("/v1/cv", data={"jd_text": "jd"}, files=parts)
        assert response.status_code == 202
        assert response.json()["data"] == {"reattached": False}
        assert response.headers["x-request-id"] == response.json()["request_id"]
    finally:
        gate.set()


def test_a_job_in_flight_is_a_session_that_has_started(client, fake_llm, parts, app_state):
    """The first step is in the session before the POST answers, so a client
    that follows it at once never finds it empty."""
    a_good_run(fake_llm)
    gate = hold(fake_llm)
    try:
        request_id = start_cv(client, parts)
        events = app_state.jobs.open(request_id).events()
        assert events[0] == (1, "status", {"status": "jd_evaluation", "detail": ""})
    finally:
        gate.set()
    read_events(client, request_id)


@needs_latex
def test_a_repeated_step_is_not_reported_twice(client, fake_llm, parts):
    """The generator announces "jd_evaluation" too; once is enough."""
    a_good_run(fake_llm)
    statuses = of_type(run_cv(client, parts), "status")
    assert statuses[0]["status"] == "jd_evaluation"
    assert all(a != b for a, b in zip(statuses, statuses[1:]))


@needs_latex
def test_following_leaves_nothing_behind(client, fake_llm, parts, app_state):
    """A stream is not a request that did anything, so it must not cost a
    directory — and neither does the job's scratch space, which is not here."""
    a_good_run(fake_llm)
    request_id = start_cv(client, parts)
    for _ in range(3):
        read_events(client, request_id)

    assert [p.name for p in app_state.settings.work_root.iterdir()] == [request_id]
    assert [p.name for p in (app_state.settings.work_root / request_id).iterdir()] == ["events"]


# --- what comes back ---------------------------------------------------------
@needs_latex
def test_the_session_ends_with_the_document_the_tex_and_the_pdf(client, fake_llm, parts):
    """One event, everything the client files — no second round trip to turn
    the content into a PDF."""
    a_good_run(fake_llm)
    rendered = result_of(run_cv(client, parts)).cv
    assert rendered.document["summary"] == f"**{SAMPLE_BODY['summary']}**"     # highlighted
    assert rendered.document["job_title"] == SAMPLE_BODY["job_title"]
    assert rendered.pdf_bytes().startswith(b"%PDF")
    assert r"\documentclass" in rendered.tex


@needs_latex
def test_your_own_data_comes_back_in_the_document(client, fake_llm, parts, candidate_data):
    """The server was sent it so it could count the pages; it is part of the
    CV, so it is part of what comes back."""
    a_good_run(fake_llm)
    document = result_of(run_cv(client, parts)).cv.document
    assert document["name"] == candidate_data["name"]
    assert document["email"] == candidate_data["email"]


@needs_latex
def test_a_field_the_schema_never_declared_is_handed_back_too(client, fake_llm, parts):
    """The reply is parsed, not filtered: your prompt, your schema and your
    template can agree on something this server has never heard of."""
    a_good_run(fake_llm, body={**SAMPLE_BODY, "availability": "Immediate"})
    assert result_of(run_cv(client, parts)).cv.document["availability"] == "Immediate"


@needs_latex
def test_each_step_is_reported_and_the_end_says_what_it_cost(client, fake_llm, parts):
    a_good_run(fake_llm)
    events = run_cv(client, parts)

    steps = [s["status"] for s in of_type(events, "status")]
    assert steps == STEPS
    assert [n for n, _, _ in events] == list(range(1, len(events) + 1))
    result = result_of(events)
    assert "tokens used=" in result.detail
    assert "page(s)" in result.detail


@needs_latex
def test_the_session_hands_over_each_intermediate_result_and_only_the_last_pdf(
    client, fake_llm, parts
):
    a_good_run(fake_llm)
    events = run_cv(client, parts)
    assert [a["name"] for a in of_type(events, "artifact")] == list(ARTIFACTS)
    assert [kind for _, kind, data in events if "pdf_base64" in json.dumps(data)] == ["result"]


@needs_latex
def test_an_extra_body_is_all_its_step_sends_and_no_other_step_changes(client, fake_llm, parts):
    a_good_run(fake_llm)
    for role in ("cv", "validation"):
        fake_llm._chat[role].temperature = 0.4
        fake_llm._chat[role].reasoning_effort = "high"
    run_cv(client, parts, cv_extra_body=json.dumps({"top_p": 0.9}))
    for call in fake_llm["cv"].calls:
        assert call["top_p"] == 0.9
        assert "temperature" not in call and "reasoning_effort" not in call
    for call in fake_llm["validation"].calls:
        assert (call["temperature"], call["reasoning_effort"]) == (0.4, "high")
        assert "top_p" not in call


@pytest.mark.parametrize("sent", ["not json", "[0.9]"])
def test_an_extra_body_that_is_not_a_json_object_is_a_400(client, parts, sent):
    response = client.post("/v1/cv", data={"jd_text": "jd", "highlight_extra_body": sent},
                           files=parts)
    assert response.status_code == 400
    assert "highlight_extra_body" in response.json()["error"]


@needs_latex
def test_pages_is_forwarded_to_the_page_count(client, fake_llm, parts):
    """A ``pages`` override reaches the render's page count, not just the
    default of 2 — proving the whole client-to-render chain is wired."""
    a_good_run(fake_llm)
    lines = of_type(run_cv(client, parts, pages=1), "log")
    assert any("past page 1" in line["message"] for line in lines)


@needs_latex
def test_the_slot_comes_back_when_the_job_ends(client, fake_llm, parts, app_state):
    """Released by the worker, not by the request — so a leak only shows here."""
    a_good_run(fake_llm)
    run_cv(client, parts)

    taken = [app_state.job_slots.acquire(blocking=False)
             for _ in range(app_state.settings.max_concurrent_jobs)]
    for _ in [t for t in taken if t]:
        app_state.job_slots.release()
    assert all(taken)


# --- what the run said, and what it cost --------------------------------------
@needs_latex
def test_every_line_the_job_logged_is_in_its_session(client, fake_llm, parts):
    a_good_run(fake_llm)
    lines = of_type(run_cv(client, parts), "log")

    stages = {line["stage"] for line in lines}
    assert {f"cv.{step}" for step in STEPS} | {"render"} <= stages
    assert any("attempt 1: OK" in line["message"] for line in lines)
    costs = [line["message"] for line in lines if "⏱" in line["message"]]
    assert costs and all("input=" in line and "thinking=" in line for line in costs)


@needs_latex
def test_every_model_call_is_reported_and_added_up(client, fake_llm, parts):
    """The fake transport reports 11 tokens in and 22 out, 2 of them thinking."""
    a_good_run(fake_llm)
    events = run_cv(client, parts)
    calls = of_type(events, "usage")
    total = result_of(events).total

    assert {call["role"] for call in calls} == set(STEPS)
    assert all((c["input_tokens"], c["thinking_tokens"], c["output_tokens"]) == (11, 2, 20)
               for c in calls)
    assert total.calls == len(calls)
    assert total.input_tokens == 11 * len(calls)
    assert total.output_tokens == 20 * len(calls)


def test_an_unknown_session_is_a_404(client):
    for response in (client.get("/v1/cv/never-happened/events"),
                     client.delete("/v1/cv/never-happened")):
        assert response.status_code == 404
        assert "unknown_request" in response.json()["error"]


# --- the same posting again --------------------------------------------------
@needs_latex
def test_the_same_id_again_replays_the_session_and_starts_nothing(client, fake_llm, parts):
    a_good_run(fake_llm)
    first = read_events(client, start_cv(client, parts, request_id="posting-1"))
    paid = len(fake_llm["cv"].calls)

    again = client.post("/v1/cv", data={"jd_text": "x" * 1200}, files=parts,
                        headers={"X-Request-Id": "posting-1"})
    assert again.status_code == 202
    assert again.json()["data"] == {"reattached": True}
    assert read_events(client, "posting-1") == first
    assert len(fake_llm["cv"].calls) == paid


@needs_latex
def test_a_job_still_running_is_joined_not_restarted(client, fake_llm, parts):
    a_good_run(fake_llm)
    gate = hold(fake_llm)
    try:
        start_cv(client, parts, request_id="posting-2")
        again = client.post("/v1/cv", data={"jd_text": "x" * 1200}, files=parts,
                            headers={"X-Request-Id": "posting-2"})
        assert again.json()["data"] == {"reattached": True}
    finally:
        gate.set()
    assert result_of(read_events(client, "posting-2"))
    assert len(fake_llm["jd_evaluation"].calls) == 1


@needs_latex
def test_last_event_id_sends_only_what_was_missed(client, fake_llm, parts):
    a_good_run(fake_llm)
    request_id = start_cv(client, parts)
    everything = read_events(client, request_id)
    assert read_events(client, request_id, after=everything[2][0]) == everything[3:]


def test_a_stream_past_the_end_closes_at_once(client, fake_llm, parts):
    """A client that already has the last event, and reconnects anyway,
    must not be left waiting for one that will never come."""
    a_quick_failure(fake_llm)
    request_id = start_cv(client, parts)
    last = read_events(client, request_id)[-1][0]
    assert raw_events(client, request_id, after=last) == []


def test_a_quiet_stream_says_it_is_still_there(client, fake_llm, parts, monkeypatch):
    monkeypatch.setattr("cv_server.api.routers.cv.PING_SECONDS", 0.05)
    a_quick_failure(fake_llm)
    gate = hold(fake_llm)
    request_id = start_cv(client, parts)
    threading.Timer(0.5, gate.set).start()

    events = raw_events(client, request_id)
    assert ("ping" in {kind for _, kind, _ in events})
    assert events[-1][1] == "error"


# --- acknowledging, resetting, expiring --------------------------------------
@needs_latex
def test_acknowledging_deletes_the_session_and_frees_the_id(client, fake_llm, parts):
    a_good_run(fake_llm)
    read_events(client, start_cv(client, parts, request_id="posting-3"))

    response = client.delete("/v1/cv/posting-3")
    assert response.status_code == 200
    assert response.json() == {"request_id": "posting-3", "ok": True}
    assert client.get("/v1/cv/posting-3/events").status_code == 404

    again = client.post("/v1/cv", data={"jd_text": "x" * 1200}, files=parts,
                        headers={"X-Request-Id": "posting-3"})
    assert again.json()["data"] == {"reattached": False}
    assert result_of(read_events(client, "posting-3"))


def test_a_running_session_cannot_be_acknowledged(client, fake_llm, parts):
    """Its worker is still writing there, and a new session for the same
    posting would get the same directory."""
    a_good_run(fake_llm)
    gate = hold(fake_llm)
    try:
        request_id = start_cv(client, parts)
        response = client.delete(f"/v1/cv/{request_id}")
        assert response.status_code == 409
        assert "still running" in response.json()["error"]
    finally:
        gate.set()
    read_events(client, request_id)


def test_a_reset_deletes_every_session_but_a_running_one(client, fake_llm, parts, app_state):
    a_quick_failure(fake_llm)                               # quick to finish
    for name in ("done-1", "done-2"):
        read_events(client, start_cv(client, parts, request_id=name))
    gate = hold(fake_llm)
    try:
        start_cv(client, parts, request_id="busy")
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


def test_an_expired_session_is_not_replayed(client, fake_llm, parts, app_state):
    """Older than the TTL, it is pruned before the lookup: a new job starts."""
    a_quick_failure(fake_llm)
    read_events(client, start_cv(client, parts, request_id="old-posting"))
    day_and_more = time.time() - 86400 * app_state.settings.session_ttl_days - 60
    for path in (app_state.settings.work_root / "old-posting" / "events").iterdir():
        os.utime(path, (day_and_more, day_and_more))

    again = client.post("/v1/cv", data={"jd_text": "x" * 1200}, files=parts,
                        headers={"X-Request-Id": "old-posting"})
    assert again.json()["data"] == {"reattached": False}
    read_events(client, "old-posting")


def test_a_job_a_previous_process_left_running_ends_with_an_error(app_state):
    """A fresh process owns no workers, so the session is closed for it — a
    client following it stops waiting."""
    job = JobStore(app_state.settings.work_root).create("orphan")
    job.append("status", CVStatus(status="validation").model_dump(mode="json"))

    with TestClient(create_app(state=app_state)) as fresh:
        events = read_events(fresh, "orphan")
    failure = CVFailure.model_validate(events[-1][2])
    assert events[-1][1] == "error"
    assert failure.http_status == 503
    assert "restarted" in failure.error


# --- what is sent ------------------------------------------------------------
@needs_latex
def test_an_uploaded_prompt_is_the_one_used(client, fake_llm, parts):
    a_good_run(fake_llm)
    files = {**parts, "sys_prompt_cv_body": ("sys_prompt_cv_body.txt", "MY OWN PROMPT", "text/plain")}
    run_cv(client, files)

    body = next(c for c in fake_llm["cv"].calls if "DUTY CAP:" not in user_text(c))
    assert body["messages"][0]["content"] == "MY OWN PROMPT"
    duties = [c for c in fake_llm["cv"].calls if "DUTY CAP:" in user_text(c)]
    assert all(c["messages"][0]["content"] != "MY OWN PROMPT" for c in duties)


@needs_latex
def test_an_uploaded_schema_is_the_one_the_writer_is_shown(client, fake_llm, parts):
    a_good_run(fake_llm)
    files = {**parts, "cv_duties_schema": ("cv_duties_schema.json", '{"MY": "SCHEMA"}', "application/json")}
    run_cv(client, files)

    duties = [c for c in fake_llm["cv"].calls if "DUTY CAP:" in user_text(c)]
    assert duties and all('SCHEMA:\n{"MY": "SCHEMA"}' in user_text(c) for c in duties)


@needs_latex
def test_without_an_override_the_shipped_prompt_is_used(client, fake_llm, parts, bundle):
    a_good_run(fake_llm)
    run_cv(client, parts)
    for call in fake_llm["profile_scoring"].calls:
        assert call["messages"][0]["content"] == bundle.sys_prompt_profile_scoring


@needs_latex
def test_no_model_is_ever_shown_your_candidate_data(client, fake_llm, parts):
    """It is sent so the page count is real, and it goes to the template only."""
    a_good_run(fake_llm)
    files = {**parts, "candidate_data": (
        "candidate_data.json",
        json.dumps({"name": "Jordan Rivera", "email": "SENTINEL@nowhere.invalid"}),
        "application/json",
    )}
    events = run_cv(client, files)

    for model in fake_llm.roles.values():
        sent = json.dumps([call["messages"] for call in model.calls])
        assert "SENTINEL" not in sent
    # It did reach the template, though — that is what it is for.
    assert result_of(events).cv.document["email"] == "SENTINEL@nowhere.invalid"


def test_a_missing_jd_is_a_400_naming_the_part(client, parts):
    body = client.post("/v1/cv", files=parts).json()
    assert body["ok"] is False
    assert "missing_part" in body["error"]
    assert "jd" in body["error"]


@pytest.mark.parametrize("dropped", ["candidate_profile", "candidate_data"])
def test_a_missing_personal_part_is_a_400(client, parts, dropped):
    files = {k: v for k, v in parts.items() if k != dropped}
    response = client.post("/v1/cv", data={"jd_text": "jd"}, files=files)
    assert response.status_code == 400
    assert dropped in response.json()["error"]


@pytest.mark.parametrize("broken", ["candidate_profile", "candidate_data"])
def test_an_unparsable_personal_part_is_a_400(client, parts, broken):
    files = {**parts, broken: ("p.json", "{not json", "application/json")}
    response = client.post("/v1/cv", data={"jd_text": "jd"}, files=files)
    assert response.status_code == 400
    assert "bad_part" in response.json()["error"]


def test_a_non_positive_pages_is_a_400(client, parts):
    response = client.post("/v1/cv", data={"jd_text": "jd", "pages": 0}, files=parts)
    assert response.status_code == 400
    assert "bad_part" in response.json()["error"]


def test_an_oversized_part_is_a_413(client, app_state, parts):
    files = {**parts, "sys_prompt_cv_body": ("p.txt", "x" * (app_state.settings.max_part_bytes + 1))}
    response = client.post("/v1/cv", data={"jd_text": "jd"}, files=files)
    assert response.status_code == 413


def test_a_request_id_cannot_name_a_directory_of_its_own_choosing(
    client, fake_llm, parts, app_state
):
    """The id is a path component, so anything that is not one is replaced."""
    a_quick_failure(fake_llm)
    response = client.post("/v1/cv", data={"jd_text": "jd"}, files=parts,
                           headers={"X-Request-Id": "../../evil"})
    assert response.json()["request_id"] != "../../evil"
    assert not (app_state.settings.work_root.parent / "evil").exists()
    read_events(client, response.json()["request_id"])


# --- failing -----------------------------------------------------------------
def test_a_failed_job_ends_its_session_with_the_cause_and_the_bill(client, fake_llm, parts):
    a_quick_failure(fake_llm)
    events = run_cv(client, parts)

    assert events[-1][1] == "error"
    failure = CVFailure.model_validate(events[-1][2])
    assert failure.http_status == 502
    assert "model_output" in failure.error
    assert "cv.jd_evaluation" in failure.error
    # The attempts that were paid for are all there, and added up.
    assert failure.total.calls == len(of_type(events, "usage")) >= 1
    assert sum("rejected" in line["message"] for line in of_type(events, "log")) >= 1


def test_a_full_server_says_429_rather_than_queueing(client, app_state, parts):
    taken = [app_state.job_slots.acquire(blocking=False)
             for _ in range(app_state.settings.max_concurrent_jobs)]
    try:
        response = client.post("/v1/cv", data={"jd_text": "jd"}, files=parts)
    finally:
        for _ in [t for t in taken if t]:
            app_state.job_slots.release()
    assert response.status_code == 429
    assert response.headers["retry-after"] == "30"


# --- rendering ---------------------------------------------------------------
@needs_latex
def test_render_returns_the_tex_and_the_pdf(client, sample_document):
    files = {"document": ("cv.json", json.dumps(sample_document), "application/json")}
    body = client.post("/v1/cv/render", files=files).json()

    rendered = RenderedCV.model_validate(body["data"])
    assert rendered.pdf_bytes().startswith(b"%PDF")
    assert r"\documentclass" in rendered.tex
    # Nothing echoed back: whoever asked for this render already has the data.
    assert rendered.document is None


@needs_latex
def test_render_compiles_a_hand_edited_tex(client, sample_document):
    tex = client.post(
        "/v1/cv/render",
        files={"document": ("cv.json", json.dumps(sample_document), "application/json")},
    ).json()["data"]["tex"].replace("Staff Platform Engineer", "Edited By Hand")

    body = client.post("/v1/cv/render", files={"tex": ("cv.tex", tex, "text/plain")}).json()
    assert body["ok"] is True
    assert "Edited By Hand" in body["data"]["tex"]


def test_render_needs_something_to_render(client):
    response = client.post("/v1/cv/render")
    assert response.status_code == 400
    assert "missing_part" in response.json()["error"]


def test_render_refuses_both_inputs_at_once(client, sample_document):
    files = {
        "document": ("cv.json", json.dumps(sample_document), "application/json"),
        "tex": ("cv.tex", "x", "text/plain"),
    }
    assert client.post("/v1/cv/render", files=files).status_code == 400


def test_a_render_keeps_nothing(client, sample_document, app_state):
    """Only a CV job has a session; a render's scratch space is gone with it."""
    files = {"document": ("cv.json", json.dumps(sample_document), "application/json")}
    client.post("/v1/cv/render", files=files)
    assert list(app_state.settings.work_root.iterdir()) == []


@needs_latex
def test_a_broken_template_is_422_with_the_tex_log_in_the_reply(client, sample_document):
    """A failed render is nearly always this, and only the TeX log says where."""
    files = {
        "document": ("cv.json", json.dumps(sample_document), "application/json"),
        "template": ("t.tex.jinja", r"\documentclass{article}\begin{document}\nope\end{document}"),
    }
    response = client.post("/v1/cv/render", files=files)
    body = response.json()
    assert response.status_code == 422
    assert body["error"].startswith("latex_compile [compile]")
    assert "Undefined control sequence" in body["error"]
    # The scratch directory is scrubbed out of it.
    assert str(Path(tempfile.gettempdir()) / "ats-strike-") not in json.dumps(body)


# --- images ------------------------------------------------------------------
IMAGE_TEMPLATE = r"""\documentclass{article}
\usepackage[pdftex]{graphicx}
\begin{document}\VAR{job_title}
\includegraphics[width=2cm]{logo.png}
\includegraphics[width=2cm]{candidate_signature.png}
\end{document}"""


@needs_latex
def test_an_uploaded_image_is_there_under_its_own_name(client, sample_document):
    """The file name is the interface: the template includes what you sent,
    under the name you sent it with."""
    logo = (EXAMPLE_CANDIDATE / "candidate_signature.png").read_bytes()
    files = [
        ("document", ("cv.json", json.dumps(sample_document), "application/json")),
        ("template", ("t.tex.jinja", IMAGE_TEMPLATE, "text/plain")),
        ("images", ("logo.png", logo, "image/png")),
    ]
    body = client.post("/v1/cv/render", files=files).json()

    # candidate_signature.png is not sent and still resolves: the shipped
    # blank one is a default, not a special case.
    assert body["ok"] is True, body.get("error")
    assert RenderedCV.model_validate(body["data"]).pdf_bytes().startswith(b"%PDF")


def test_an_image_named_like_a_path_cannot_escape_the_work_dir(client, sample_document):
    files = [
        ("document", ("cv.json", json.dumps(sample_document), "application/json")),
        ("template", ("t.tex.jinja", IMAGE_TEMPLATE, "text/plain")),
        ("images", ("../../etc/logo.png", b"\x89PNG", "image/png")),
    ]
    # Not a 400: the name is reduced to 'logo.png', which is a name like any
    # other. What must never happen is a write outside the scratch directory.
    response = client.post("/v1/cv/render", files=files)
    assert response.status_code in (200, 422)
    assert not Path("/etc/logo.png").exists()


def test_a_request_cannot_carry_unlimited_images(client, sample_document):
    files = [("document", ("cv.json", json.dumps(sample_document), "application/json"))]
    files += [("images", (f"img{n}.png", b"x", "image/png")) for n in range(MAX_IMAGES + 1)]

    response = client.post("/v1/cv/render", files=files)
    assert response.status_code == 400
    assert "too many images" in response.json()["error"]
