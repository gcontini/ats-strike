"""The wire format itself: what both sides have to agree on."""

from __future__ import annotations

import base64

import pytest

import cv_contracts
from cv_contracts import (
    EVENT_PAYLOADS,
    PROTOCOL,
    TERMINAL_EVENTS,
    Analysed,
    Done,
    Envelope,
    Failure,
    Letter,
    Rendered,
    ServerStatus,
    Status,
    StoreItem,
    static_jd_guess,
)


def test_this_is_protocol_two():
    assert PROTOCOL == 2


# --- a thing to store --------------------------------------------------------
def test_text_travels_as_text():
    item = StoreItem.from_content("CV", "step", "steps/key_points.json", '{"à": 1}')
    assert (item.text, item.base64) == ('{"à": 1}', None)
    assert item.content() == '{"à": 1}'.encode("utf-8")


def test_bytes_travel_as_base64():
    item = StoreItem.from_content("CV", "cv", "cv.pdf", b"%PDF-1.7\x00\xff")
    assert item.text is None
    assert base64.b64decode(item.base64) == b"%PDF-1.7\x00\xff"
    assert item.content() == b"%PDF-1.7\x00\xff"


def test_a_log_line_has_no_name():
    item = StoreItem(location="CV", type="log", text="2026-11-13T09:00:00+00:00 INFO    [-] hi")
    assert item.name == ""


def test_an_unknown_location_or_type_is_still_read():
    """A client skips what it does not know; it must be able to read it first."""
    item = StoreItem.model_validate({"location": "ELSEWHERE", "type": "video", "name": "x"})
    assert (item.location, item.type) == ("ELSEWHERE", "video")


@pytest.mark.parametrize("item", [
    StoreItem.from_content("CACHE", "step", "pool.json", "[]"),
    StoreItem.from_content("CV", "cv", "cv.pdf", b"%PDF"),
])
def test_an_item_round_trips_as_json(item):
    assert StoreItem.model_validate_json(item.model_dump_json()) == item


# --- the events a CV job streams ---------------------------------------------
def test_every_event_type_names_its_payload():
    assert EVENT_PAYLOADS == {"status": Status, "store": StoreItem, "done": Done, "error": Failure}
    assert TERMINAL_EVENTS == ("done", "error")


def test_a_status_is_free_text():
    """The step names are the server's: a client shows them and knows none."""
    assert Status(status="thinking-about-it").detail == ""


def test_the_end_carries_no_bill():
    """Token usage is a log line now; the last events carry only what they say."""
    assert set(Done.model_fields) == {"detail"}
    assert set(Failure.model_fields) == {"http_status", "error"}


@pytest.mark.parametrize("payload", [
    Status(status="validation", detail="cv finished\n  on two lines"),
    StoreItem.from_content("CV", "log", "", "a line"),
    Done(detail="done, 2 model calls"),
    Failure(http_status=502, error="model_output [cv.cv]: no usable reply"),
])
def test_an_event_round_trips_as_json(payload):
    kind = next(k for k, model in EVENT_PAYLOADS.items() if isinstance(payload, model))
    assert EVENT_PAYLOADS[kind].model_validate_json(payload.model_dump_json()) == payload


# --- the REST replies --------------------------------------------------------
def test_a_server_that_does_not_say_its_protocol_is_one():
    status = ServerStatus.model_validate({
        "status": "ok", "version": "1.0.4", "pdflatex": True, "models": {}, "auth_required": False,
    })
    assert status.protocol == 1


def test_an_analysis_is_an_opaque_object():
    """Whatever fields the server's pipeline writes, they come through."""
    reply = Analysed(analysis={"job_title": "Head of IT", "company_evaluation": "fine"})
    back = Analysed.model_validate_json(reply.model_dump_json())
    assert back.analysis["company_evaluation"] == "fine"
    assert back.items == []


def test_replies_carry_things_to_store():
    log = StoreItem.from_content("CV", "log", "", "Σ 1 model call(s), 0.5s")
    pdf = StoreItem.from_content("CV", "cv", "cv.pdf", b"%PDF")
    assert Rendered(items=[pdf, log]).items[0].content() == b"%PDF"
    assert Letter(text="Dear", items=[log]).items == [log]


# --- the envelope -----------------------------------------------------------
def test_an_envelope_round_trips_with_its_payload():
    envelope = Envelope[Rendered](request_id="abc", ok=False)
    assert Envelope[Rendered].model_validate_json(envelope.model_dump_json()).ok is False


def test_an_envelope_carries_only_the_payload():
    assert set(Envelope[Rendered].model_fields) == {"request_id", "ok", "data", "error"}


def test_the_contracts_know_no_pipeline():
    """No analysis schema, no step names, no rendered-CV shape."""
    for gone in ("JDAnalysis", "JDDetection", "RenderedCV", "CVStatus", "CVArtifact",
                 "CVResult", "CVFailure", "ModelCall", "TokenUsage", "LogEntry", "CoverLetter"):
        assert not hasattr(cv_contracts, gone), gone


# --- the free JD check ------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected, why",
    [
        ("x" * 1500, True, "plausible"),
        ("x" * 900, True, "a terse posting: a title and a list of requirements"),
        ("x" * 18500, True, "a long posting: a selection notice with its legal text"),
        ("x" * 499, False, "too short"),
        ("x" * 30001, False, "too long"),
        ("x" * 1500 + "\x00", False, "binary"),
    ],
)
def test_the_static_check_decides_what_it_can(text, expected, why):
    assert static_jd_guess(text) is expected, why


def test_the_bounds_are_adjustable():
    assert static_jd_guess("x" * 100, min_chars=50)


def test_a_control_character_dump_is_not_text():
    assert not static_jd_guess("\x01\x02\x03" * 500 + "x" * 500)
