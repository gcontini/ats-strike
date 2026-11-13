"""The generate -> validate -> regenerate loop, without a model or LaTeX."""

from __future__ import annotations

import shutil

import pytest

from cv_pipeline.cv_generator import CVGenerator
from cv_pipeline.cv_renderer import CVRenderer, RenderResult
from cv_pipeline.cv_validator import length_advice
from cv_pipeline.errors import ModelOutputError

from pipeline_helpers import FakeLLM, completion, sample_cv_data

OK_REVIEW = "OK"                        # the reviewer's "nothing to fix"
TOO_LONG = length_advice(3, 2, 1, 0)    # a 3-page CV, the first time round


def cv_json(**overrides) -> str:
    return sample_cv_data(**overrides).model_dump_json()


def fake(*cv, review=(OK_REVIEW,), highlight=None, **cv_spec) -> FakeLLM:
    """A selector whose roles answer in order. The reviewer approves unless a
    test says otherwise, so a test about writing need not script the review."""
    llm = FakeLLM(cv=cv_spec)
    llm["cv"].replies = list(cv)
    llm["review"].replies = list(review)
    if highlight is not None:
        llm["highlight"].replies = [highlight]
    return llm


def build(bundle, candidate, tmp_path, llm, highlight=False, **kw) -> CVGenerator:
    """A generator over fakes; the highlight pass only where a test asks."""
    return CVGenerator(
        llm=llm, bundle=bundle, candidate=candidate, work_dir=tmp_path,
        highlight=highlight, **kw,
    )


def tailor_calls(model):
    """The CV-writing requests, in order."""
    return model["cv"].calls


@pytest.fixture
def no_latex(monkeypatch):
    """Skip the compile: the page check is exercised separately."""
    pages = {"n": 1}

    def fake_render(self, document, *, stem="cv"):
        return RenderResult(tex="", pdf=b"%PDF", pages=pages["n"],
                            overflow_lines=0 if pages["n"] <= 2 else 1)

    monkeypatch.setattr(CVRenderer, "render_document", fake_render)
    return pages


def test_returns_the_document_the_latex_and_the_pdf(bundle, candidate, tmp_path, no_latex):
    """One call, everything a client needs to file: no second round trip to
    turn the content into a PDF."""
    model = fake(cv_json())
    document, tex, pdf, summary = build(bundle, candidate, tmp_path, model).generate("JD text")

    assert document["job_title"] == "Staff Platform Engineer"
    assert pdf == b"%PDF"
    assert "tokens used=" in summary


def test_your_own_data_wins_over_the_model_s(bundle, candidate, tmp_path, no_latex):
    """The two halves are one namespace now, so a field the model invents must
    not be able to replace a real contact detail."""
    model = fake(cv_json())
    document, *_ = build(bundle, candidate, tmp_path, model).generate("JD")
    assert document["name"] == candidate.data["name"]


def test_progress_names_each_step_and_what_the_one_before_cost(
    bundle, candidate, tmp_path, no_latex
):
    steps = []
    model = fake(cv_json())
    build(bundle, candidate, tmp_path, model,
          progress=lambda status, detail: steps.append((status, detail))).generate("JD")

    # The review and the render are one step: the render takes a second, and
    # a status nobody can poll in time is not worth reporting. Later rounds,
    # which skip the review, are the ones that say "page_check".
    assert [status for status, _ in steps] == ["generate", "review", "highlight"]
    assert steps[0][1] == ""                                  # nothing has finished yet
    assert "generation finished, tokens used=" in steps[1][1]
    assert "page check passed: 1 page(s)" in steps[2][1]


def test_a_rejected_review_reports_re_generate_with_the_violations(
    bundle, candidate, tmp_path, no_latex
):
    """Verbatim: the client is shown the words the model is about to be given."""
    steps = []
    model = fake(cv_json(), review=("invented a job at NASA", OK_REVIEW))
    build(bundle, candidate, tmp_path, model,
          progress=lambda status, detail: steps.append((status, detail))).generate("JD")

    status, detail = next(s for s in steps if s[0] == "re-generate")
    assert "CV rejected: 1 violation(s), 1 page(s)" in detail
    assert detail.endswith("- invented a job at NASA")


def test_a_review_that_never_passes_still_produces_a_cv(
    bundle, candidate, tmp_path, no_latex
):
    """Consistent with the page-check loop: running out of attempts is not a
    reason to fail a job that produced a usable CV."""
    model = fake(cv_json(), cv_json(job_title="Second"),
                 review=("invented a job at NASA", "still invented a job at NASA"))
    steps = []
    document, *_ = build(
        bundle, candidate, tmp_path, model, max_attempts=2,
        progress=lambda status, detail: steps.append((status, detail)),
    ).generate("JD")
    assert document["job_title"] == "Second"
    # Giving up is still a step the client is told about, not a silent jump.
    status, detail = steps[-1]
    assert status == "highlight"
    assert "out of attempts with 1 violation(s) open" in detail


def test_an_overlong_pdf_reports_the_condense_instruction(
    bundle, candidate, tmp_path, no_latex
):
    """A CV that never fits is still delivered, at the length the last round
    reached — the page limit is best-effort."""
    steps = []
    no_latex["n"] = 3
    model = fake(cv_json())
    document, *_ = build(bundle, candidate, tmp_path, model, max_attempts=2,
                         progress=lambda status, detail: steps.append((status, detail))
                         ).generate("JD")
    assert document["job_title"] == "Staff Platform Engineer"

    # Too long is a violation like any other now, quoted verbatim.
    _, detail = next(s for s in steps if s[0] == "re-generate")
    assert "CV rejected: 1 violation(s), 3 page(s)" in detail
    assert detail.endswith(f"- {TOO_LONG}")


def test_a_bad_and_over_long_cv_is_told_about_both_at_once(
    bundle, candidate, tmp_path, no_latex
):
    """Why every attempt renders: the round that catches an overstatement
    catches the overflow too, instead of spending one attempt on each."""
    steps = []
    no_latex["n"] = 3
    model = fake(cv_json(), cv_json(job_title="Second"),
                 review=("invented a job at NASA", OK_REVIEW))
    build(bundle, candidate, tmp_path, model, max_attempts=2,
          progress=lambda status, detail: steps.append((status, detail))).generate("JD")

    _, detail = next(s for s in steps if s[0] == "re-generate")
    assert "CV rejected: 2 violation(s), 3 page(s)" in detail
    assert "- invented a job at NASA" in detail
    assert detail.endswith(f"- {TOO_LONG}")          # length complaint last

    # And the model is handed both in the one prompt it gets next.
    second = tailor_calls(model)[-1]
    retry = second["messages"][-1]["content"]
    assert "- invented a job at NASA" in retry and f"- {TOO_LONG}" in retry


def test_a_cv_still_too_long_is_asked_for_a_bigger_cut_next_round(
    bundle, candidate, tmp_path, no_latex
):
    """The validator outlives a round, so each attempt that is still too long
    is told the cut before it was not enough, instead of hearing the same
    instruction again."""
    no_latex["n"] = 3
    model = fake(cv_json(), cv_json(), cv_json())
    build(bundle, candidate, tmp_path, model, max_attempts=3).generate("JD")

    prompts = [c["messages"][-1]["content"] for c in tailor_calls(model)]
    assert "DELETE 1 whole duty" in prompts[1]
    assert "DELETE 2 whole duty" in prompts[2]


def test_the_prompt_carries_the_system_prompt_profile_and_jd(
    bundle, candidate, tmp_path, no_latex
):
    model = fake(cv_json())
    build(bundle, candidate, tmp_path, model).generate("SENTINEL JD")

    first = model["cv"].calls[0]
    # On json_mode the schema rides in the system prompt, once: the endpoint
    # never sees it through response_format.
    assert first["messages"][0]["content"].startswith(bundle.sys_prompt_cv)
    assert '"job_title"' in first["messages"][0]["content"]
    assert first["response_format"] == {"type": "json_object"}
    user = first["messages"][1]["content"]
    assert candidate.profile["skills"][0] in user
    assert "SENTINEL JD" in user


def test_an_endpoint_that_takes_the_schema_is_not_sent_it_twice(
    bundle, candidate, tmp_path, no_latex
):
    model = fake(cv_json(), structured_output="json_schema")
    build(bundle, candidate, tmp_path, model).generate("JD")

    first = model["cv"].calls[0]
    assert first["response_format"]["json_schema"]["strict"] is True
    assert first["messages"][0]["content"] == bundle.sys_prompt_cv


def test_an_overridden_prompt_is_what_the_model_sees(bundle, candidate, tmp_path, no_latex):
    custom = bundle.with_overrides(sys_prompt_cv="WRITE IT MY WAY")
    model = fake(cv_json())
    build(custom, candidate, tmp_path, model).generate("JD")
    assert model["cv"].calls[0]["messages"][0]["content"].startswith("WRITE IT MY WAY")


def test_invalid_json_is_fed_back_and_retried(bundle, candidate, tmp_path, no_latex):
    model = fake("not json at all", cv_json())
    build(bundle, candidate, tmp_path, model).generate("JD")
    assert "rejected" in model["cv"].calls[1]["messages"][-1]["content"]


def test_giving_up_names_the_stage(bundle, candidate, tmp_path, no_latex):
    model = fake("never valid")
    with pytest.raises(ModelOutputError) as excinfo:
        build(bundle, candidate, tmp_path, model).generate("JD")
    assert excinfo.value.stage == "cv.generate"


def test_review_violations_are_fed_back_and_the_cv_regenerated(
    bundle, candidate, tmp_path, no_latex
):
    model = fake(cv_json(), cv_json(job_title="Rewritten"),
                 review=("invented a job at NASA", OK_REVIEW))
    document, *_ = build(bundle, candidate, tmp_path, model).generate("JD")
    assert document["job_title"] == "Rewritten"
    assert any("invented a job at NASA" in (m["content"] or "")
               for call in model["cv"].calls for m in call["messages"])


def test_a_regeneration_does_not_carry_the_previous_round_s_conversation(
    bundle, candidate, tmp_path, no_latex
):
    """Each attempt is one fresh exchange. Accumulating them put three
    near-identical CVs in front of the model, which then copy-edited its own
    last reply — garbling and all — instead of writing a CV."""
    model = fake(
        cv_json(job_title="First"), cv_json(job_title="Second"), cv_json(job_title="Third"),
        review=("invented a job at NASA", "still invented a job at NASA", OK_REVIEW),
    )
    build(bundle, candidate, tmp_path, model).generate("JD")

    third = tailor_calls(model)[-1]
    assert len(third["messages"]) == 2          # system + one user turn, always
    # Only the CV it has to improve on, never the round before that.
    # (Two mentions expected: the section header, plus the instruction's own
    # reference back to it.)
    last = third["messages"][-1]["content"]
    assert last.count("YOUR PREVIOUS ATTEMPT") == 2
    assert "Second" in last and "First" not in last


def test_an_empty_review_is_asked_again_not_a_pass(bundle, candidate, tmp_path, no_latex):
    model = fake(cv_json(), review=("", OK_REVIEW))
    build(bundle, candidate, tmp_path, model).generate("JD")
    assert len(model["cv"].calls) == 1
    assert len(model["review"].calls) == 2


CUT_OFF = completion("fake-review", "Location: Summ", "length")


def test_an_unreviewed_cv_is_reviewed_again_not_rewritten(bundle, candidate, tmp_path, no_latex):
    """A review that never answered says nothing is wrong and nothing is
    right: the same CV goes back to the reviewer, not to the writer."""
    steps = []
    model = fake(cv_json(), review=(CUT_OFF, CUT_OFF, CUT_OFF, OK_REVIEW))
    build(bundle, candidate, tmp_path, model,
          progress=lambda status, detail: steps.append((status, detail))).generate("JD")

    assert len(model["cv"].calls) == 1
    assert len(model["review"].calls) == 4
    assert [status for status, _ in steps] == ["generate", "review", "review", "highlight"]


def test_an_unreviewed_cv_is_delivered_on_the_last_round(bundle, candidate, tmp_path, no_latex):
    model = fake(cv_json(), review=(CUT_OFF,))
    document, *_ = build(bundle, candidate, tmp_path, model, max_attempts=2).generate("JD")
    assert document["job_title"] == "Staff Platform Engineer"
    assert len(model["cv"].calls) == 1


def test_a_violation_on_the_second_round_regenerates_again(
    bundle, candidate, tmp_path, no_latex
):
    """Every round the reviewer rejects costs a round. What it complains about
    is not the loop's business: the violations are text to hand back."""
    model = fake(
        cv_json(), cv_json(job_title="Second"), cv_json(job_title="Third"),
        review=(
            "Location: X. Offending quote: q. FIX: f. Reason: r.",
            "Location: X. Offending quote: still bad. FIX: f. Reason: r.",
            OK_REVIEW,
        ),
    )
    document, *_ = build(bundle, candidate, tmp_path, model).generate("JD")
    assert document["job_title"] == "Third"


def test_a_violation_too_short_to_act_on_is_not_a_rejection(
    bundle, candidate, tmp_path, no_latex
):
    """The list decides: a reply with nothing actionable in it is a pass, or
    the loop spends every attempt on a reviewer that cannot say why."""
    model = fake(cv_json(), review=("too short",))
    document, *_ = build(bundle, candidate, tmp_path, model).generate("JD")
    assert document["job_title"] == "Staff Platform Engineer"


def test_highlighting_failure_keeps_the_unhighlighted_cv(bundle, candidate, tmp_path, no_latex):
    model = fake(cv_json(), highlight="}{ not json")
    document, *_ = build(bundle, candidate, tmp_path, model, highlight=True).generate("JD")
    assert document["job_title"] == "Staff Platform Engineer"


def test_highlighting_replaces_the_content_when_it_works(bundle, candidate, tmp_path, no_latex):
    model = fake(cv_json(), highlight=cv_json(summary="**Bold** summary."))
    document, *_ = build(bundle, candidate, tmp_path, model, highlight=True).generate("JD")
    assert document["summary"] == "**Bold** summary."


def test_the_time_budget_stops_the_loop(bundle, candidate, tmp_path, no_latex):
    from cv_pipeline.errors import BudgetExceededError

    model = fake(cv_json())
    gen = build(bundle, candidate, tmp_path, model, deadline=0.0)
    with pytest.raises(BudgetExceededError):
        gen.generate("JD")


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_end_to_end_with_a_real_compile(bundle, candidate, tmp_path):
    """No model, but a real render: the page check runs on a real PDF."""
    model = fake(cv_json())
    document, tex, pdf, _ = build(bundle, candidate, tmp_path, model).generate("JD")
    assert document["job_title"] == "Staff Platform Engineer"
    assert (tmp_path / "attempt_1.pdf").is_file()
    # The one that is kept is rendered after highlighting, so it is its own pass.
    assert (tmp_path / "cv.pdf").is_file()
    assert pdf.startswith(b"%PDF") and tex.startswith("\\documentclass")
