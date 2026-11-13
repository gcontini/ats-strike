"""What is wrong with one CV: the content review and the page check together.

No model and no LaTeX — the reviewer is a :class:`FakeLLM` answering in
plain text, one violation per line, and the render is replaced by a fake that
lets a test choose how long the PDF came out.
"""

from __future__ import annotations

import pytest

from cv_pipeline.models import ATTEMPTS, Usage
from cv_pipeline.cv_renderer import CVRenderer, RenderResult
from cv_pipeline.cv_validator import (
    MAX_VIOLATIONS,
    CVValidator,
    length_advice,
)

from pipeline_helpers import FakeLLM, sample_cv_data

OK_REVIEW = "OK"                        # nothing to fix
REJECT = "invented a job at NASA"
#: What the fake render below earns a 3-page CV the first time round.
TOO_LONG = length_advice(3, 2, 1, 0)


@pytest.fixture
def renders(monkeypatch):
    """Count the renders and choose their length, without pdflatex."""
    state = {"pages": 1, "calls": 0, "stems": [], "document": None}

    def fake_render(self, document, *, stem="cv"):
        state["calls"] += 1
        state["stems"].append(stem)
        state["document"] = dict(document)
        pages = state["pages"]
        return RenderResult(
            tex="", pdf=b"%PDF", pages=pages,
            overflow_lines=0 if pages <= 2 else 1,
        )

    monkeypatch.setattr(CVRenderer, "render_document", fake_render)
    return state


def reviewer(*replies, finish_reason="stop", **spec) -> FakeLLM:
    """A selector whose ``review`` role answers ``replies`` in order."""
    llm = FakeLLM(review=spec)
    llm["review"].replies = list(replies)
    llm["review"].finish_reason = finish_reason
    return llm


def build(bundle, candidate, tmp_path, model) -> CVValidator:
    renderer = CVRenderer(
        template_source=bundle.template_source,
        template_name=bundle.template_name,
        work_dir=tmp_path,
    )
    return CVValidator(
        llm=model,
        renderer=renderer,
        master_profile=candidate.profile,
        review_prompt=bundle.sys_prompt_review,
        usage=Usage(),
    )


def cv_and_document(candidate_data, **overrides):
    """What the model wrote, and the same CV with the real contact details on
    top — the reviewer gets the first, the renderer the second."""
    cv_data = sample_cv_data(**overrides)
    return cv_data, {**cv_data.model_dump(), **dict(candidate_data)}


def test_a_cv_that_reads_well_and_fits_has_nothing_wrong_with_it(
    bundle, candidate, candidate_data, tmp_path, renders
):
    model = reviewer(OK_REVIEW)
    cv_data, document = cv_and_document(candidate_data)

    result = build(bundle, candidate, tmp_path, model).validate(
        cv_data, document, attempt=0
    )

    assert result.violations == []
    assert result.pages == 1
    assert renders["stems"] == ["attempt_1"]


def test_a_cv_the_reviewer_rejected_is_rendered_anyway(
    bundle, candidate, candidate_data, tmp_path, renders
):
    """The whole point: the page count is measured even when the reviewer has
    already said no, so one round finds everything wrong with the CV."""
    model = reviewer(REJECT)
    cv_data, document = cv_and_document(candidate_data)

    result = build(bundle, candidate, tmp_path, model).validate(
        cv_data, document, attempt=0
    )

    assert result.violations == ["invented a job at NASA"]
    assert renders["calls"] == 1


def test_too_long_is_a_violation_in_the_condenser_s_own_words(
    bundle, candidate, candidate_data, tmp_path, renders
):
    renders["pages"] = 3
    model = reviewer(OK_REVIEW)
    cv_data, document = cv_and_document(candidate_data)

    result = build(bundle, candidate, tmp_path, model).validate(
        cv_data, document, attempt=0
    )

    assert result.violations == [TOO_LONG]
    assert result.pages == 3


def test_the_length_complaint_comes_last(
    bundle, candidate, candidate_data, tmp_path, renders
):
    """Content first, length appended: the model is told what to say before it
    is told how much room it has to say it in."""
    renders["pages"] = 3
    model = reviewer(REJECT)
    cv_data, document = cv_and_document(candidate_data)

    result = build(bundle, candidate, tmp_path, model).validate(
        cv_data, document, attempt=0
    )

    assert result.violations == ["invented a job at NASA", TOO_LONG]


def test_the_review_does_not_run_again_once_it_has_passed(
    bundle, candidate, candidate_data, tmp_path, renders
):
    """A CV shortened for length is the reviewed CV with less in it. Asking the
    most expensive call in the pipeline to re-confirm that buys nothing."""
    model = reviewer(OK_REVIEW)
    cv_data, document = cv_and_document(candidate_data)
    validator = build(bundle, candidate, tmp_path, model)

    validator.validate(cv_data, document, attempt=0)
    assert validator.content_reviewed

    renders["pages"] = 3
    result = validator.validate(cv_data, document, attempt=1)

    assert len(model["review"].calls) == 1                      # no second review
    assert result.violations == [TOO_LONG]
    assert renders["stems"] == ["attempt_1", "attempt_2"]


@pytest.mark.parametrize("lines, duties", [(0, 1), (1, 1), (2, 1), (3, 2), (6, 3), (14, 7)])
def test_the_length_advice_asks_for_whole_duties_in_proportion(lines, duties):
    """Whole duties, never characters: told to remove characters, the model
    shortens a sentence, and a paragraph that loses no line frees no room."""
    advice = length_advice(3, 2, lines, 0)

    assert f"DELETE {duties} whole duty" in advice
    assert "2-page limit" in advice
    assert "character" not in advice


def test_a_serious_overflow_asks_for_whole_experiences():
    assert "remove one or more work experiences" in length_advice(4, 2, 30, 0)


def test_each_round_still_too_long_asks_for_one_more_duty(
    bundle, candidate, candidate_data, tmp_path, renders
):
    """The line count misses an image or a gap that moved over with the text,
    so the same "1 line" can come back round after round. Each time it does,
    the cut before it was too small, and the next one asks for more."""
    renders["pages"] = 3
    model = reviewer(OK_REVIEW)
    cv_data, document = cv_and_document(candidate_data)
    validator = build(bundle, candidate, tmp_path, model)

    advice = [validator.validate(cv_data, document, attempt=n).violations[-1]
              for n in range(3)]

    assert "DELETE 1 whole duty" in advice[0] and "not enough" not in advice[0]
    assert "DELETE 2 whole duty" in advice[1] and "2 times in a row" in advice[1]
    assert "DELETE 3 whole duty" in advice[2] and "3 times in a row" in advice[2]


def test_a_round_that_fits_starts_the_count_again(
    bundle, candidate, candidate_data, tmp_path, renders
):
    """Too long, then a fit rejected for its content, then too long again: the
    round in between made no cut that could have been too small."""
    model = reviewer(REJECT, REJECT, REJECT)
    cv_data, document = cv_and_document(candidate_data)
    validator = build(bundle, candidate, tmp_path, model)

    renders["pages"] = 3
    validator.validate(cv_data, document, attempt=0)
    renders["pages"] = 2
    validator.validate(cv_data, document, attempt=1)
    renders["pages"] = 3
    result = validator.validate(cv_data, document, attempt=2)

    assert result.violations[-1] == TOO_LONG


def test_a_rejection_leaves_the_review_running_next_round(
    bundle, candidate, candidate_data, tmp_path, renders
):
    model = reviewer(REJECT, OK_REVIEW)
    cv_data, document = cv_and_document(candidate_data)
    validator = build(bundle, candidate, tmp_path, model)

    validator.validate(cv_data, document, attempt=0)
    assert not validator.content_reviewed

    assert validator.validate(cv_data, document, attempt=1).violations == []
    assert validator.content_reviewed
    assert len(model["review"].calls) == 2


def test_the_reviewer_is_never_shown_your_candidate_data(
    bundle, candidate, candidate_data, tmp_path, renders
):
    """Why validate() takes the CV twice: the reviewer sees what the model
    wrote, the renderer sees the merged document — or it measures a page count
    that is not the page count of the CV you will send."""
    model = reviewer(OK_REVIEW)
    cv_data, document = cv_and_document(candidate_data)

    build(bundle, candidate, tmp_path, model).validate(cv_data, document, attempt=0)

    assert candidate_data["email"] not in model["review"].last_prompt
    assert renders["document"]["email"] == candidate_data["email"]


def test_each_line_is_one_violation_and_blank_or_short_lines_are_none(
    bundle, candidate, candidate_data, tmp_path, renders
):
    """A line too short to say where and what is not an instruction — which
    also disposes of a stray code fence or a bare "OK"."""
    model = reviewer(
        "Location: Summary. Offending quote: 'led'. FIX: say 'worked on'.\n"
        "\n"
        "```\n"
        "OK\n"
        "  Location: Skills. Offending quote: 'Rust'. FIX: remove it.  \n"
    )
    cv_data, document = cv_and_document(candidate_data)

    result = build(bundle, candidate, tmp_path, model).validate(
        cv_data, document, attempt=0
    )

    assert result.violations == [
        "Location: Summary. Offending quote: 'led'. FIX: say 'worked on'.",
        "Location: Skills. Offending quote: 'Rust'. FIX: remove it.",
    ]


@pytest.mark.parametrize("reply", ["OK", "ok", "Ok.", "  OK  "])
def test_a_bare_ok_is_an_approval_not_a_violation(
    bundle, candidate, candidate_data, tmp_path, renders, reply
):
    """"OK" in any case or with a full stop is an approval, not a one-word
    violation."""
    model = reviewer(reply)
    cv_data, document = cv_and_document(candidate_data)

    result = build(bundle, candidate, tmp_path, model).validate(
        cv_data, document, attempt=0
    )

    assert result.violations == []


def test_a_review_longer_than_the_cap_keeps_the_most_important(
    bundle, candidate, candidate_data, tmp_path, renders
):
    """The prompt asks for the most important first, so the cap keeps the head:
    a runaway review must not bloat the next round's prompt."""
    lines = [f"Location: duty {n}. Offending quote: q. FIX: f." for n in range(1, 13)]
    model = reviewer("\n".join(lines))
    cv_data, document = cv_and_document(candidate_data)

    result = build(bundle, candidate, tmp_path, model).validate(
        cv_data, document, attempt=0
    )

    assert result.violations == lines[:MAX_VIOLATIONS]


def test_an_empty_review_is_asked_again_not_read_as_a_pass(
    bundle, candidate, candidate_data, tmp_path, renders
):
    model = reviewer("", OK_REVIEW)
    cv_data, document = cv_and_document(candidate_data)

    result = build(bundle, candidate, tmp_path, model).validate(cv_data, document, attempt=0)

    assert len(model["review"].calls) == 2
    assert result.reviewed and result.violations == []


def test_a_review_that_never_finishes_leaves_the_cv_unreviewed(
    bundle, candidate, candidate_data, tmp_path, renders
):
    """Cut off every time, the review says nothing about the CV — not that it
    passed. The half-line never becomes a violation, and the next round
    reviews again."""
    model = reviewer("Location: Summary. Offending quo", finish_reason="length")
    cv_data, document = cv_and_document(candidate_data)
    validator = build(bundle, candidate, tmp_path, model)

    result = validator.validate(cv_data, document, attempt=0)

    assert len(model["review"].calls) == ATTEMPTS
    assert "cut off" in model["review"].calls[1]["messages"][-1]["content"]
    assert (result.reviewed, result.violations) == (False, [])
    assert not validator.content_reviewed


def test_the_reviewer_is_asked_for_text_not_json(
    bundle, candidate, candidate_data, tmp_path, renders
):
    model = reviewer(OK_REVIEW, structured_output="json_schema")
    cv_data, document = cv_and_document(candidate_data)

    build(bundle, candidate, tmp_path, model).validate(cv_data, document, attempt=0)

    assert "response_format" not in model["review"].calls[0]
