"""Following one CV job: replay, reconnection, and when a session is let go."""

from __future__ import annotations

from dataclasses import replace

import pytest
from cv_contracts import Failure

from cv_client.api import CVError
from cv_client.cvjob import session_id, write_cv

from conftest import JD_TEXT, LOG_LINE, PDF, TOTAL_LINE, render_session

JOB = {"jd.txt": JD_TEXT.encode()}


def follow(api, server, job=JOB, **kw):
    """write_cv, collecting what it said and what it was handed to store."""
    said, stored = [], []
    write_cv(api, server, job, say=said.append, store=stored.append, **kw)
    return said, stored


def test_a_posting_is_its_own_session():
    """The same text, the same session; another text, another one. A hex
    SHA-256 is 64 characters, exactly what the server takes as an id."""
    assert session_id(JOB["jd.txt"]) == session_id(JD_TEXT.encode())
    assert session_id(JOB["jd.txt"]) != session_id(JOB["jd.txt"] + b" ")
    assert len(session_id(JOB["jd.txt"])) == 64


def test_the_pack_the_job_and_the_settings_are_sent_under_the_session_id(api, server):
    server = replace(server, settings={"pages": 2})
    follow(api, server)
    assert api.seen_request_id == session_id(JOB["jd.txt"])
    assert api.seen_job == JOB
    assert api.seen_settings == {"pages": 2}
    assert "candidate_profile.json" in api.seen_pack and "server.toml" not in api.seen_pack


def test_every_thing_to_store_is_handed_over_in_order_then_the_session_acknowledged(
        api, server):
    _, stored = follow(api, server)
    assert [(i.type, i.name) for i in stored] == [
        ("log", ""), ("step", "steps/key_points.json"), ("step", "cv.json"),
        ("step", "cv.tex"), ("cv", "cv.pdf"), ("log", "")]
    assert stored[-2].content() == PDF
    assert (stored[0].text, stored[-1].text) == (LOG_LINE, TOTAL_LINE)
    assert api.calls == ["create_cv", "events", "acknowledge"]


def test_a_cv_that_could_not_be_stored_is_not_acknowledged(api, server):
    """The server keeps it, so running the posting again gets it back for free."""
    def full_disk(item):
        raise OSError("No space left on device")

    with pytest.raises(OSError):
        write_cv(api, server, JOB, say=lambda line: None, store=full_disk)
    assert "acknowledge" not in api.calls


def test_a_failed_job_is_acknowledged_then_raised_with_its_cause(api, server):
    api.session = api.session[:3] + [("error", Failure(
        http_status=504, error="budget_exceeded [cv.cv]: out of time"))]
    with pytest.raises(CVError) as failure:
        follow(api, server)
    assert failure.value.status == 504
    assert "budget_exceeded" in str(failure.value)
    assert failure.value.request_id == session_id(JOB["jd.txt"])
    assert api.calls[-1] == "acknowledge"


def test_each_status_is_said_and_its_detail_only_when_verbose(api, server):
    said, _ = follow(api, server)
    assert said == ["   started", "   jd_evaluation", "   profile_scoring"]

    said, _ = follow(api, server, verbose=True)
    assert "1 key points, tokens used=30" in said
    assert "done, 1 model call, 1 page(s)" in said
    assert any(line.startswith("   session: ") for line in said)


def test_a_dropped_stream_is_reopened_after_the_last_event_it_delivered(api, server):
    api.break_after = 3
    said, stored = follow(api, server)

    assert api.seen_after == [0, 3]
    assert [i.name for i in stored].count("cv.json") == 1, "nothing stored twice"
    assert any("reconnecting" in line for line in said)
    assert said.count("   jd_evaluation") == 1, "nothing said twice"


def test_a_stream_that_never_reaches_the_end_gives_up_at_the_timeout(api, server):
    """Without acknowledging: the job may still finish, and the next run of
    the same posting will find it."""
    api.session = api.session[:2]                     # never ends
    with pytest.raises(CVError) as failure:
        follow(api, replace(server, timeout=0))
    assert "run the same posting again" in str(failure.value)
    assert "acknowledge" not in api.calls


def test_a_session_the_server_has_lost_is_not_reconnected_to(api, server):
    class Gone(type(api)):
        def events(self, request_id, after=0):
            self._record("events")
            raise CVError("unknown_request: no CV session", status=404)
            yield  # a generator, like the real one

    gone = Gone()
    with pytest.raises(CVError) as failure:
        follow(gone, server)
    assert failure.value.status == 404
    assert gone.calls.count("events") == 1


def test_a_replayed_run_says_so(api, server):
    api.reattached = True
    said, _ = follow(api, server)
    assert any("replaying it (--new starts over)" in line for line in said)


def test_a_job_holding_its_cv_is_rendered_by_the_server(api, server):
    """What the job carries decides: the client only sends it."""
    _, stored = follow(api, server, {**JOB, "cv.json": b"{}"})
    assert [i.name for i in stored if i.type != "log"] == ["cv.tex", "cv.pdf"]
    assert len(stored) == len([e for e in render_session() if e[0] == "store"])


def test_new_acknowledges_the_old_session_first(api, server):
    follow(api, server, new=True)
    assert api.calls == ["acknowledge", "create_cv", "events", "acknowledge"]


def test_new_with_nothing_on_the_server_just_starts(api, server):
    api.acknowledge_errors = [404]
    follow(api, server, new=True)
    assert api.calls[-1] == "acknowledge"


def test_new_while_the_old_run_is_still_being_written_is_refused(api, server):
    """Its worker is still writing the session a new run would have to use."""
    api.acknowledge_errors = [409]
    with pytest.raises(CVError) as failure:
        follow(api, server, new=True)
    assert failure.value.status == 409
    assert "still being written" in str(failure.value)
    assert "create_cv" not in api.calls


def test_a_failed_acknowledgement_does_not_undo_a_stored_cv(api, server):
    api.acknowledge_errors = [503]
    said, stored = follow(api, server)
    assert stored[-2].content() == PDF
    assert any("could not be acknowledged" in line for line in said)
