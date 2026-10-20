"""HttpApi's own plumbing: the health check and the version compatibility it
now carries. ``version_compat`` is pure, so most of this needs no transport;
the two behavioural tests fake ``/healthz`` with an ``httpx.MockTransport``
rather than a real server, per AGENTS.md — fake at the boundary, not above it.
"""

from __future__ import annotations

import httpx
import pytest

from resumix_client import __version__
from resumix_client.api import HttpApi, ResumixError, version_compat


@pytest.mark.parametrize("client_version, server_version, expected", [
    ("1.2.3", "1.2.9", "ok"),
    ("1.2.3", "1.2.3", "ok"),
    ("1.2.3", "1.3.0", "warn"),
    ("1.2.3", "1.1.0", "warn"),
    ("1.2.3", "2.0.0", "stop"),
    ("2.0.0", "1.9.9", "stop"),
    ("1.2.3", "unknown", "ok"),
    ("unknown", "1.2.3", "ok"),
    ("1.2.3", "1", "ok"),
])
def test_version_compat(client_version, server_version, expected):
    assert version_compat(client_version, server_version) == expected


def _health(version: str) -> httpx.Response:
    return httpx.Response(200, json={
        "request_id": "r1", "ok": True,
        "data": {"status": "ok", "version": version, "pdflatex": True,
                 "models": {}, "auth_required": False},
    })


def _client_reporting(version: str) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: _health(version)),
                        base_url="http://server.invalid")


def _bump(part: int, by: int = 1) -> str:
    pieces = __version__.split(".")
    pieces[part] = str(int(pieces[part]) + by)
    return ".".join(pieces)


def test_a_minor_mismatch_warns_and_continues(capsys):
    api = HttpApi("http://server.invalid", client=_client_reporting(_bump(1)))

    api._ensure_healthy()  # does not raise

    assert "minor versions differ" in capsys.readouterr().err


def test_a_major_mismatch_stops_before_the_first_call():
    api = HttpApi("http://server.invalid", client=_client_reporting(_bump(0)))

    with pytest.raises(ResumixError, match="major versions differ"):
        api._ensure_healthy()


def test_a_matching_version_is_quiet(capsys):
    api = HttpApi("http://server.invalid", client=_client_reporting(__version__))

    api._ensure_healthy()

    assert capsys.readouterr().err == ""


# --- a CV session over HTTP -------------------------------------------------------
STREAM = (
    b'id: 1\nevent: status\ndata: {"status": "jd_evaluation", "detail": ""}\n\n'
    b": ping\n\n"
    b'id: 2\nevent: something-newer\ndata: {"x": 1}\n\n'
    b'id: 3\nevent: usage\ndata: {"role": "cv", "model": "m", "seconds": 1.5, '
    b'"input_tokens": 1, "thinking_tokens": 2, "output_tokens": 3}\n\n'
)


class Broken(httpx.SyncByteStream):
    """A stream that delivers some events, then loses the connection."""

    def __iter__(self):
        yield STREAM
        raise httpx.ReadError("connection reset by peer")


def _server(seen: list, *, events=None, status: int = 200) -> httpx.Client:
    """/healthz at this client's version; everything else recorded and answered."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return _health(__version__)
        seen.append(request)
        if request.url.path.endswith("/events"):
            if status != 200:
                return httpx.Response(status, json={"request_id": "r", "ok": False,
                                                    "error": "unknown_request: no CV session"})
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  stream=events or httpx.ByteStream(STREAM))
        return httpx.Response(200, json={"request_id": "r", "ok": True,
                                         "data": {"reattached": False, "deleted": 1,
                                                  "running": 0}})

    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://server.invalid")


def test_a_stream_is_read_as_typed_events_skipping_pings_and_unknown_types():
    api = HttpApi("http://server.invalid", client=_server([]))
    events = list(api.events("abc"))
    assert [(number, kind) for number, kind, _ in events] == [(1, "status"), (3, "usage")]
    assert events[1][2].thinking_tokens == 2


def test_a_reconnect_says_where_it_got_to():
    seen: list = []
    api = HttpApi("http://server.invalid", client=_server(seen))
    list(api.events("abc", after=7))
    assert seen[0].headers["last-event-id"] == "7"


def test_a_stream_the_server_refuses_raises_with_its_status():
    api = HttpApi("http://server.invalid", client=_server([], status=404))
    with pytest.raises(ResumixError) as failure:
        list(api.events("abc"))
    assert failure.value.status == 404
    assert "unknown_request" in str(failure.value)


def test_a_broken_stream_raises_with_no_status_after_what_it_delivered():
    """No status: nothing answered, so there is something to reconnect to."""
    api = HttpApi("http://server.invalid", client=_server([], events=Broken()))
    delivered = []
    with pytest.raises(ResumixError) as failure:
        for event in api.events("abc"):
            delivered.append(event[0])
    assert failure.value.status == 0
    assert delivered == [1, 3]


def test_a_cv_job_is_sent_under_its_session_id():
    seen: list = []
    api = HttpApi("http://server.invalid", client=_server(seen))
    api.create_cv("posting", request_id="abc123", profile=b"{}", candidate_data=b"{}")
    assert seen[0].headers["x-request-id"] == "abc123"


def test_every_extra_body_goes_as_a_json_field_named_for_its_role():
    """All of them, with every request: each endpoint reads the roles it has."""
    seen: list = []
    api = HttpApi("http://server.invalid", client=_server(seen))
    api.create_cv("posting", request_id="abc123", profile=b"{}", candidate_data=b"{}",
                  extra_body={"cv": {"temperature": 0.7}, "analysis": {"top_p": 0.5}})
    assert b'name="cv_extra_body"\r\n\r\n{"temperature": 0.7}' in seen[0].content
    assert b'name="analysis_extra_body"\r\n\r\n{"top_p": 0.5}' in seen[0].content


def test_a_reset_has_to_say_all():
    seen: list = []
    api = HttpApi("http://server.invalid", client=_server(seen))
    assert api.reset().data.deleted == 1
    assert (seen[0].method, seen[0].url.path, seen[0].url.query) == ("DELETE", "/v1/cv", b"all=")
