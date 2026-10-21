"""Following one CV job: replay, reconnection, and when a session is let go."""

from __future__ import annotations

from dataclasses import replace

import pytest
from cv_contracts import CVFailure, TokenUsage

from cv_client.api import CVError
from cv_client.cvjob import session_id, write_cv

from conftest import JD_TEXT


def follow(api, config, **kw):
    """write_cv, collecting what it said and what it kept."""
    said, kept = [], []
    write_cv(api, config, JD_TEXT, say=said.append, keep=kept.append, **kw)
    return said, kept


def test_a_posting_is_its_own_session():
    """The same text, the same session; another text, another one. A hex
    SHA-256 is 64 characters, exactly what the server takes as an id."""
    assert session_id(JD_TEXT) == session_id(JD_TEXT)
    assert session_id(JD_TEXT) != session_id(JD_TEXT + " ")
    assert len(session_id(JD_TEXT)) == 64


def test_the_session_id_is_what_the_server_is_sent(api, config):
    follow(api, config)
    assert api.seen_request_id == session_id(JD_TEXT)


def test_the_cv_is_kept_then_the_session_acknowledged(api, config):
    said, kept = follow(api, config)
    assert kept == [api.rendered]
    assert api.calls == ["create_cv", "events", "acknowledge"]


def test_a_cv_that_could_not_be_kept_is_not_acknowledged(api, config):
    """The server keeps it, so running the posting again gets it back for free."""
    def full_disk(rendered):
        raise OSError("No space left on device")

    with pytest.raises(OSError):
        write_cv(api, config, JD_TEXT, say=lambda line: None, keep=full_disk)
    assert "acknowledge" not in api.calls


def test_a_failed_job_is_acknowledged_then_raised_with_its_cause(api, config):
    api.session = api.session[:3] + [("error", CVFailure(
        http_status=504, error="budget_exceeded [cv.cv]: out of time",
        total=TokenUsage(calls=3)))]
    said = []
    with pytest.raises(CVError) as failure:
        write_cv(api, config, JD_TEXT, say=said.append, keep=lambda rendered: None)
    assert failure.value.status == 504
    assert "budget_exceeded" in str(failure.value)
    assert failure.value.request_id == session_id(JD_TEXT)
    assert api.calls[-1] == "acknowledge"
    assert any(line.startswith("   Σ 3 model call(s)") for line in said)


def test_a_dropped_stream_is_reopened_after_the_last_event_it_delivered(api, config):
    api.break_after = 2
    said, kept = follow(api, config)

    assert api.seen_after == [0, 2]
    assert kept == [api.rendered]
    assert any("reconnecting" in line for line in said)
    assert said.count("   jd_evaluation") == 1, "nothing delivered twice"


def test_a_stream_that_never_reaches_the_end_gives_up_at_the_timeout(api, config):
    """Without acknowledging: the job may still finish, and the next run of
    the same posting will find it."""
    api.session = api.session[:2]                     # never ends
    with pytest.raises(CVError) as failure:
        follow(api, replace(config, timeout=0))
    assert "run the same posting again" in str(failure.value)
    assert "acknowledge" not in api.calls


def test_a_session_the_server_has_lost_is_not_reconnected_to(api, config):
    class Gone(type(api)):
        def events(self, request_id, *, after=0):
            self._record("events")
            raise CVError("unknown_request: no CV session", status=404)
            yield  # a generator, like the real one

    gone = Gone()
    with pytest.raises(CVError) as failure:
        follow(gone, config)
    assert failure.value.status == 404
    assert gone.calls.count("events") == 1


def test_a_replayed_run_says_so(api, config):
    api.reattached = True
    said, _ = follow(api, config)
    assert any("replaying it (--new starts over)" in line for line in said)


def test_new_acknowledges_the_old_session_first(api, config):
    follow(api, config, new=True)
    assert api.calls == ["acknowledge", "create_cv", "events", "acknowledge"]


def test_new_with_nothing_on_the_server_just_starts(api, config):
    api.acknowledge_errors = [404]
    _, kept = follow(api, config, new=True)
    assert kept == [api.rendered]


def test_new_while_the_old_run_is_still_being_written_is_refused(api, config):
    """Its worker is still writing the session a new run would have to use."""
    api.acknowledge_errors = [409]
    with pytest.raises(CVError) as failure:
        follow(api, config, new=True)
    assert failure.value.status == 409
    assert "still being written" in str(failure.value)
    assert "create_cv" not in api.calls


def test_a_failed_acknowledgement_does_not_undo_a_kept_cv(api, config):
    api.acknowledge_errors = [503]
    said, kept = follow(api, config)
    assert kept == [api.rendered]
    assert any("could not be acknowledged" in line for line in said)
