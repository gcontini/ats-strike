"""The wire format itself: what both sides have to agree on."""

from __future__ import annotations

import base64
from datetime import datetime

import pytest

from cv_contracts import (
    EVENT_PAYLOADS,
    TERMINAL_EVENTS,
    CVArtifact,
    CVFailure,
    CVResult,
    CVStatus,
    Envelope,
    JDAnalysis,
    LogEntry,
    ModelCall,
    RenderedCV,
    TokenUsage,
    static_jd_guess,
)

# --- what a CV job reports and returns --------------------------------------
def test_a_status_carries_one_step_and_nothing_else():
    """Each step is its own event: the history is the session, not the status."""
    assert set(CVStatus.model_fields) == {"status", "detail"}


def test_a_status_starts_with_no_detail():
    """The first step is announced before any step has finished."""
    assert CVStatus(status="jd_evaluation").detail == ""


def test_the_steps_are_the_six_of_the_pipeline_in_order():
    steps = CVStatus.model_fields["status"].annotation.__args__
    assert steps == ("jd_evaluation", "profile_scoring", "format_selection", "cv",
                     "validation", "highlight")


def test_a_detail_may_span_lines():
    """The format's reason follows the choice, on lines of its own."""
    status = CVStatus(status="cv", detail="format B: experiences [0, 1, 2]\n  most relevant")
    assert status.detail.splitlines()[-1] == "  most relevant"


def test_an_unknown_status_is_refused():
    with pytest.raises(ValueError):
        CVStatus(status="thinking-about-it")


def test_the_end_is_an_event_not_a_status():
    """A job ends with its result or its error, which carry what a status
    could not: the CV, the cause, the bill."""
    with pytest.raises(ValueError):
        CVStatus(status="END")


def test_a_rendered_cv_carries_the_pdf_back_as_bytes():
    rendered = RenderedCV.from_bytes(tex="\\documentclass{article}", pdf=b"%PDF-1.7")
    assert rendered.pdf_bytes() == b"%PDF-1.7"
    assert base64.b64decode(rendered.pdf_base64) == b"%PDF-1.7"


def test_a_re_render_echoes_no_document():
    """Whoever asked for a render already has the data it rendered."""
    assert RenderedCV.from_bytes(tex="x", pdf=b"y").document is None


def test_the_document_has_no_schema():
    """It is whatever the model wrote with your own data over the top, and only
    your template has an opinion about it."""
    rendered = RenderedCV.from_bytes(tex="x", pdf=b"y", document={"hobbies": ["climbing"]})
    assert rendered.document == {"hobbies": ["climbing"]}


# --- the envelope -----------------------------------------------------------
def test_an_envelope_round_trips_with_its_payload():
    envelope = Envelope[JDAnalysis](request_id="abc", ok=False)
    assert Envelope[JDAnalysis].model_validate_json(envelope.model_dump_json()).ok is False


def test_an_envelope_carries_only_the_payload():
    """Logs travel as a CV job's events or stay in the server's log, so
    nothing large rides on every reply."""
    assert set(Envelope[JDAnalysis].model_fields) == {"request_id", "ok", "data", "error"}


def test_a_failure_says_why_in_one_line():
    """One string, not a structure: the detail that would not fit is in the
    log, which is where a reader who wants it is going anyway."""
    envelope = Envelope[JDAnalysis](
        request_id="abc", ok=False, error="model_output [cv.cv]: no usable reply"
    )
    assert "cv.cv" in envelope.error


# --- the events a CV job streams --------------------------------------------
def test_every_event_type_names_its_payload():
    assert set(EVENT_PAYLOADS) == {"status", "artifact", "log", "usage", "result", "error"}
    assert set(TERMINAL_EVENTS) == {"result", "error"}


@pytest.mark.parametrize("payload", [
    CVStatus(status="validation", detail="cv finished"),
    CVArtifact(name="key_points", data=[{"id": 1, "requirement": "Kubernetes",
                                         "keywords": ["K8s"], "importance": 90}]),
    LogEntry(ts=datetime(2026, 1, 15, 9, 0), level="INFO", stage="cv.profile_scoring",
             message="gpt-fake 1.2s | input=10, output=20"),
    ModelCall(role="cv", model="gpt-fake", seconds=1.2, input_tokens=10,
              thinking_tokens=0, output_tokens=20),
])
def test_an_event_round_trips_as_json(payload):
    kind = next(k for k, model in EVENT_PAYLOADS.items() if isinstance(payload, model))
    assert EVENT_PAYLOADS[kind].model_validate_json(payload.model_dump_json()) == payload


def test_a_result_carries_the_cv_and_the_bill():
    result = CVResult(
        cv=RenderedCV.from_bytes(tex="x", pdf=b"%PDF-1.7", document={"name": "Jordan"}),
        detail="done, 2 model calls",
        total=TokenUsage(calls=2, seconds=3.5, input_tokens=100, output_tokens=40),
    )
    back = CVResult.model_validate_json(result.model_dump_json())
    assert back.cv.pdf_bytes() == b"%PDF-1.7"
    assert back.total.calls == 2


def test_a_failure_still_carries_the_bill():
    """A user who paid for four attempts is entitled to see all four."""
    failure = CVFailure(http_status=502, error="model_output [cv.cv]: no usable reply",
                        total=TokenUsage(calls=4))
    assert CVFailure.model_validate_json(failure.model_dump_json()).total.calls == 4


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
