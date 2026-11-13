"""HttpApi's own plumbing: the protocol check, the three parts, the stream.

The server is faked with an ``httpx.MockTransport`` rather than a real one,
per AGENTS.md — fake at the boundary, not above it.
"""

from __future__ import annotations

import json

import httpx
import pytest

from cv_client.api import CVError, HttpApi


def _health(protocol=None) -> httpx.Response:
    data = {"status": "ok", "version": "1.0.4", "pdflatex": True, "models": {},
            "auth_required": False}
    if protocol is not None:
        data["protocol"] = protocol
    return httpx.Response(200, json={"request_id": "r1", "ok": True, "data": data})


def _client_reporting(protocol) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda request: _health(protocol)),
                        base_url="http://server.invalid")


def test_another_protocol_stops_before_the_first_call():
    api = HttpApi("http://server.invalid", client=_client_reporting(3))
    with pytest.raises(CVError, match="speaks protocol 3, this client speaks protocol 2"):
        api._ensure_healthy()


def test_a_server_that_does_not_say_is_protocol_1_and_refused():
    api = HttpApi("http://server.invalid", client=_client_reporting(None))
    with pytest.raises(CVError, match="speaks protocol 1"):
        api._ensure_healthy()


def test_the_same_protocol_is_quiet(capsys):
    api = HttpApi("http://server.invalid", client=_client_reporting(2))
    api._ensure_healthy()
    assert capsys.readouterr().err == ""


# --- requests and the CV session over HTTP ---------------------------------------
STREAM = (
    b'id: 1\nevent: status\ndata: {"status": "started", "detail": ""}\n\n'
    b": ping\n\n"
    b'id: 2\nevent: something-newer\ndata: {"x": 1}\n\n'
    b'id: 3\nevent: store\ndata: {"location": "CV", "type": "cv", "name": "cv.pdf", '
    b'"base64": "JVBERg=="}\n\n'
)


class Broken(httpx.SyncByteStream):
    """A stream that delivers some events, then loses the connection."""

    def __iter__(self):
        yield STREAM
        raise httpx.ReadError("connection reset by peer")


def _server(seen: list, *, events=None, status: int = 200) -> httpx.Client:
    """/healthz on protocol 2; everything else recorded and answered."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return _health(2)
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
    assert [(number, kind) for number, kind, _ in events] == [(1, "status"), (3, "store")]
    assert events[1][2].content() == b"%PDF"


def test_a_reconnect_says_where_it_got_to():
    seen: list = []
    api = HttpApi("http://server.invalid", client=_server(seen))
    list(api.events("abc", 7))
    assert seen[0].headers["last-event-id"] == "7"


def test_a_stream_the_server_refuses_raises_with_its_status():
    api = HttpApi("http://server.invalid", client=_server([], status=404))
    with pytest.raises(CVError) as failure:
        list(api.events("abc"))
    assert failure.value.status == 404
    assert "unknown_request" in str(failure.value)


def test_a_broken_stream_raises_with_no_status_after_what_it_delivered():
    """No status: nothing answered, so there is something to reconnect to."""
    api = HttpApi("http://server.invalid", client=_server([], events=Broken()))
    delivered = []
    with pytest.raises(CVError) as failure:
        for event in api.events("abc"):
            delivered.append(event[0])
    assert failure.value.status == 0
    assert delivered == [1, 3]


def test_a_cv_job_is_sent_under_its_session_id():
    seen: list = []
    api = HttpApi("http://server.invalid", client=_server(seen))
    api.create_cv({}, {"jd.txt": b"posting"}, {}, "abc123")
    assert seen[0].headers["x-request-id"] == "abc123"


def test_every_file_is_a_part_named_by_its_path_and_settings_go_as_json():
    seen: list = []
    api = HttpApi("http://server.invalid", client=_server(seen))
    api.create_cv({"candidate_profile.json": b"{}", "cache/steps/pool.json": b"[]"},
                  {"jd.txt": b"posting", "steps/key_points.json": b"[]"},
                  {"pages": 2, "extra_body": {"cv": {"temperature": 0.4}}}, "abc123")
    body = seen[0].content
    for part, name in (("pack", "candidate_profile.json"), ("pack", "cache/steps/pool.json"),
                       ("job", "jd.txt"), ("job", "steps/key_points.json")):
        assert f'name="{part}"; filename="{name}"'.encode() in body, name
    settings = body.split(b'name="settings"\r\n\r\n', 1)[1].split(b"\r\n", 1)[0]
    assert json.loads(settings) == {"pages": 2, "extra_body": {"cv": {"temperature": 0.4}}}


def test_no_settings_means_no_settings_part():
    seen: list = []
    api = HttpApi("http://server.invalid", client=_server(seen))
    api.create_cv({}, {"jd.txt": b"posting"}, {}, "abc123")
    assert b'name="settings"' not in seen[0].content


def test_a_reset_has_to_say_all():
    seen: list = []
    api = HttpApi("http://server.invalid", client=_server(seen))
    assert api.reset().data.deleted == 1
    assert (seen[0].method, seen[0].url.path, seen[0].url.query) == ("DELETE", "/v1/cv", b"all=")
