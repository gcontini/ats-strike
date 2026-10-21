"""The six steps of a CV and the render that trims it, without a model or LaTeX."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from dataclasses import replace

import pytest

from cv_server.bundle import CandidateInputs
from cv_server.pipeline.cv_generator import (
    ARTIFACTS,
    FORMATS,
    CVGenerator,
    capped_coverage,
    keep_words,
    score_targets,
    scores_of,
    without_scores,
)
from cv_server.pipeline.cv_renderer import CVRenderer, RenderResult
from cv_server.pipeline.errors import BudgetExceededError, ModelOutputError

from server_helpers import (
    KEY_POINTS,
    SAMPLE_BODY,
    FakeLLM,
    score_items,
    script_cv,
    section,
    tool_completion,
    user_text,
)

STEPS = ["jd_evaluation", "profile_scoring", "format_selection", "cv", "validation", "highlight"]


@pytest.fixture
def renders(monkeypatch):
    """No LaTeX: every render is recorded, and runs to one page per five duties,
    plus one — so the trim has something to cut."""
    seen = []

    def fake_render(self, document, *, stem="cv"):
        seen.append(document)
        duties = sum(len(e["duties"]) for e in document["experiences"])
        pages = 1 + duties // 5
        return RenderResult(tex="\\documentclass", pdf=b"%PDF", pages=pages,
                            overflow_lines=(pages - self.page_limit) * 10 if pages > self.page_limit else 0)

    monkeypatch.setattr(CVRenderer, "render_document", fake_render)
    return seen


def generator(bundle, candidate, tmp_path, llm, **kw) -> CVGenerator:
    return CVGenerator(llm=llm, bundle=bundle, candidate=candidate, work_dir=tmp_path, **kw)


def run(bundle, candidate, tmp_path, llm=None, **kw):
    """The whole run on a scripted fake, with what it reported."""
    llm = llm or script_cv(FakeLLM())
    progress, artifacts = [], []
    out = generator(bundle, candidate, tmp_path, llm, **kw,
                    progress=lambda s, d: progress.append((s, d)),
                    artifact=lambda name, data: artifacts.append((name, data))).generate("A JD")
    return out, progress, dict(artifacts), [name for name, _ in artifacts], llm


# --- the run -----------------------------------------------------------------
def test_returns_the_document_the_latex_and_the_pdf(bundle, candidate, tmp_path, renders):
    (document, tex, pdf, summary), *_ = run(bundle, candidate, tmp_path)
    assert (tex, pdf) == ("\\documentclass", b"%PDF")
    assert document["job_title"] == SAMPLE_BODY["job_title"]
    assert [e["company"] for e in document["experiences"]] == [
        e["company"] for e in candidate.profile["experiences"][:3]
    ]
    assert summary.startswith("done, ") and "2 page(s)" in summary and "removed" not in summary


def test_the_document_has_no_scores_and_your_data_over_the_top(bundle, candidate, tmp_path, renders):
    """A field a model invents can never replace a real name or email."""
    llm = script_cv(FakeLLM(), body={**SAMPLE_BODY, "name": "Someone Else"})
    (document, *_), *_ = run(bundle, candidate, tmp_path, llm)
    assert document["name"] == candidate.data["name"]
    assert "|1-80" not in json.dumps(document)
    assert all(document["name"] == candidate.data["name"] for document in renders)


def test_each_step_is_reported_with_what_the_one_before_produced_and_cost(
    bundle, candidate, tmp_path, renders
):
    _, progress, *_ = run(bundle, candidate, tmp_path)
    assert [step for step, _ in progress] == STEPS
    details = dict(progress)
    assert details["jd_evaluation"] == ""
    assert details["profile_scoring"].startswith(f"{len(KEY_POINTS)} key points, tokens used=")
    assert details["cv"].startswith("format A: experiences [0, 1, 2], duties [2, 2, 2]")
    assert details["highlight"].startswith("body and 3 experience(s) validated")
    assert all("elapsed=" in detail for step, detail in progress[1:])


def test_every_intermediate_result_is_handed_over_once_in_order(bundle, candidate, tmp_path, renders):
    _, _, artifacts, names, _ = run(bundle, candidate, tmp_path)
    assert names == list(ARTIFACTS)
    assert artifacts["key_points"] == KEY_POINTS
    assert artifacts["format_selection"]["format"] == "A"
    assert artifacts["highlighted_resume"]["summary"] == f"**{SAMPLE_BODY['summary']}**"
    assert "|1-80" in json.dumps(artifacts["checked_experiences"]), "the scores are only dropped at the render"


# --- profile_scoring ----------------------------------------------------------
def test_every_list_of_the_profile_is_scored_in_a_call_of_its_own(bundle, candidate, tmp_path, renders):
    *_, llm = run(bundle, candidate, tmp_path)
    labels = [label for label, *_ in score_targets(json.loads(json.dumps(dict(candidate.profile))))]
    assert len(llm["profile_scoring"].calls) == len(labels) == 9
    assert labels[:4] == ["job_title", "summaries", "skills", "certifications"]
    assert labels[4] == "experiences[0].duties"
    contexts = [user_text(call) for call in llm["profile_scoring"].calls]
    assert sum("EXPERIENCE:" in text for text in contexts) == 5


def test_the_scores_land_on_their_own_list_whatever_order_the_calls_finish(
    bundle, candidate, tmp_path, renders
):
    """Each list gets a score of its own; the long lists answer first."""
    def by_length(body):
        items = section(body, "ITEMS")
        time.sleep(0.2 / len(items))
        score = 30 + len(items)
        return json.dumps([{"reason": "r", "matches": [{"key_point": 2, "score": score, "evidence": "e"}]}
                           for _ in items])

    llm = script_cv(FakeLLM())
    llm["profile_scoring"].replies = [by_length]
    _, _, artifacts, *_ = run(bundle, candidate, tmp_path, llm)
    scored = artifacts["scored_profile"]
    for key in ("job_title", "skills", "summaries"):
        assert all(item.endswith(f"|2-{30 + len(scored[key])}") for item in scored[key]), key
    assert scored["certifications"][0]["score"] == f"2-{30 + len(scored['certifications'])}"
    for exp in scored["experiences"]:
        assert all(duty.endswith(f"|2-{30 + len(exp['duties'])}") for duty in exp["duties"])
    assert artifacts["score_reasons"]["skills"][0]["matches"][0]["evidence"] == "e"


def test_a_failure_in_a_parallel_call_fails_the_run_with_its_cause(bundle, candidate, tmp_path, renders):
    llm = script_cv(FakeLLM())
    llm["profile_scoring"].replies = [lambda body: score_items(body)
                                      if "EXPERIENCE:" in user_text(body) else "not json"]
    with pytest.raises(ModelOutputError) as caught:
        run(bundle, candidate, tmp_path, llm)
    assert caught.value.stage == "cv.profile_scoring"


def test_the_calls_of_a_step_run_side_by_side(bundle, candidate, tmp_path, renders, monkeypatch):
    """Nine scoring calls that each wait for the others can only finish together."""
    # LangGraph's pool has min(32, cpus + 4) threads: a 4-vCPU runner has 8, one
    # short of the nine this test needs. What it checks is that the calls are
    # not serialised, not how big the machine is.
    monkeypatch.setattr(os, "cpu_count", lambda: 32)
    barrier = threading.Barrier(9, timeout=10)

    def waits(body):
        barrier.wait()
        return score_items(body)

    llm = script_cv(FakeLLM())
    llm["profile_scoring"].replies = [waits]
    run(bundle, candidate, tmp_path, llm)
    assert not barrier.broken


def test_every_call_of_every_step_is_counted(bundle, candidate, tmp_path, renders):
    llm = script_cv(FakeLLM())
    gen = generator(bundle, candidate, tmp_path, llm)
    gen.generate("A JD")
    assert gen.usage.calls == sum(len(role.calls) for role in llm.roles.values()) == 1 + 9 + 1 + 4 + 4 + 1
    assert gen.usage.input_tokens == 11 * gen.usage.calls


# --- format_selection and cv --------------------------------------------------
def test_the_format_choice_sees_the_formats_and_the_scored_experiences(bundle, candidate, tmp_path, renders):
    *_, llm = run(bundle, candidate, tmp_path)
    assert section(llm["format_selection"].calls[0], "FORMATS") == FORMATS
    shown = section(llm["format_selection"].calls[0], "WORK EXPERIENCES")
    assert shown[0]["duties"][0].endswith("|1-80")


def test_the_writer_gets_the_body_and_each_chosen_experience_with_its_evidence(
    bundle, candidate, tmp_path, renders
):
    *_, llm = run(bundle, candidate, tmp_path)
    calls = [user_text(call) for call in llm["cv"].calls]
    body = [text for text in calls if "DUTY CAP:" not in text]
    duties = sorted((text for text in calls if "DUTY CAP:" in text),
                    key=lambda text: section({"messages": [{"role": "user", "content": text}]},
                                             "EXPERIENCE")["id"])
    assert len(body) == 1 and "MASTER PROFILE:" in body[0] and "WRITING STYLE:" in body[0]
    assert f"SCHEMA:\n{bundle.cv_body_schema}" in body[0]
    assert len(duties) == 3 and all("DUTY CAP: 2" in text for text in duties)
    evidence = section({"messages": [{"role": "user", "content": duties[0]}]}, "EVIDENCE")
    assert evidence["1"] == [{"key_point": 1, "score": 80, "evidence": "it"}]


def test_an_overridden_prompt_and_schema_are_what_the_writer_sees(bundle, candidate, tmp_path, renders):
    own = replace(bundle, sys_prompt_cv_duties="MY DUTIES PROMPT", cv_duties_schema="MY SCHEMA")
    *_, llm = run(own, candidate, tmp_path)
    duties = [call for call in llm["cv"].calls if "DUTY CAP:" in user_text(call)]
    assert all(call["messages"][0]["content"] == "MY DUTIES PROMPT" for call in duties)
    assert all("SCHEMA:\nMY SCHEMA" in user_text(call) for call in duties)
    body = next(call for call in llm["cv"].calls if "DUTY CAP:" not in user_text(call))
    assert body["messages"][0]["content"] == bundle.sys_prompt_cv_body


# --- validation and highlight -------------------------------------------------
def test_the_validators_fix_what_they_find(bundle, candidate, tmp_path, renders):
    """One replacement in the second experience; everything else passes."""
    second = candidate.profile["experiences"][1]["company"]
    first_duty = candidate.profile["experiences"][1]["duties"][0]

    def finds(body):
        text = user_text(body)
        if f'"company": "{second}"' in text and "ATTEMPT: 1" in text and "GENERATED EXPERIENCE" in text:
            return tool_completion("fake-validation", [
                ("replace_in_duties", {"old": first_duty[:10], "new": "FIXED", "duty": 1}),
            ])
        return "OK"

    llm = script_cv(FakeLLM())
    llm["validation"].replies = [finds]
    _, _, artifacts, *_ = run(bundle, candidate, tmp_path, llm)
    checked = artifacts["checked_experiences"]
    assert checked[1]["duties"][0].startswith("FIXED")
    assert checked[0] == artifacts["cv_experiences"][0]
    assert len(llm["validation"].calls) == 1 + 3 + 1


def test_the_validators_check_against_the_profile_without_the_scores(bundle, candidate, tmp_path, renders):
    *_, llm = run(bundle, candidate, tmp_path)
    texts = [user_text(call) for call in llm["validation"].calls]
    body = next(text for text in texts if "GENERATED CV BODY" in text)
    assert "MASTER PROFILE:" in body and "|1-80" not in body.split("GENERATED CV BODY")[0]
    assert "writing_style" not in body.split("GENERATED CV BODY")[0]
    assert all("ATTEMPT: 1" in text for text in texts)


def test_the_highlighter_is_shown_the_cv_and_the_evidence(bundle, candidate, tmp_path, renders):
    *_, llm = run(bundle, candidate, tmp_path)
    call = llm["highlight"].calls[0]
    cv = section(call, "CV")
    assert set(cv) == set(SAMPLE_BODY) | {"experiences"}
    assert len(section(call, "EVIDENCE")) == 3


def test_a_highlight_that_changed_a_text_keeps_its_original(bundle, candidate, tmp_path, renders):
    def rewords(body):
        cv = section(body, "CV")
        cv["summary"] = "Something else entirely."
        cv["skills"][0] = f"**{cv['skills'][0]}**"
        return json.dumps(cv)

    llm = script_cv(FakeLLM())
    llm["highlight"].replies = [rewords]
    (document, *_), *_ = run(bundle, candidate, tmp_path, llm)
    assert document["summary"] == SAMPLE_BODY["summary"]
    assert document["skills"][0] == f"**{SAMPLE_BODY['skills'][0]}**"


def test_a_highlighter_that_never_answers_leaves_the_cv_unmarked(bundle, candidate, tmp_path, renders):
    llm = script_cv(FakeLLM())
    llm["highlight"].replies = ["not json"]
    (document, *_), *_ = run(bundle, candidate, tmp_path, llm)
    assert document["summary"] == SAMPLE_BODY["summary"]


# --- your data ------------------------------------------------------------------
def test_no_model_is_ever_shown_your_candidate_data(bundle, candidate, tmp_path, renders):
    mine = CandidateInputs(profile=candidate.profile,
                           data={**candidate.data, "email": "SENTINEL@nowhere.invalid"})
    (document, *_), *_, llm = run(bundle, mine, tmp_path)
    for role in llm.roles.values():
        assert "SENTINEL" not in json.dumps([call["messages"] for call in role.calls])
    assert document["email"] == "SENTINEL@nowhere.invalid"


# --- the render ---------------------------------------------------------------
def test_an_overlong_cv_loses_its_least_useful_duties_until_it_fits(bundle, candidate, tmp_path, renders):
    """Every duty covers the same key point, so the cut goes by age: the oldest
    experience's last duties first, never below the format's minimum."""
    llm = script_cv(FakeLLM(), caps=(5, 5, 5), fmt="B")
    (document, _, _, summary), *_ = run(bundle, candidate, tmp_path, llm)
    assert [len(e["duties"]) for e in document["experiences"]] == [5, 2, 2]
    assert "6 duty(s) removed to fit" in summary
    assert [1 + sum(len(e["duties"]) for e in d["experiences"]) // 5 for d in renders] == [
        4, 3, 3, 3, 3, 3, 2,
    ]


def test_a_cv_that_cannot_lose_a_duty_is_delivered_long(bundle, candidate, tmp_path, renders):
    """Format A keeps three duties an experience: at 9 duties nothing more can go."""
    llm = script_cv(FakeLLM(), caps=(4, 4, 3), fmt="A")
    (document, _, _, summary), *_ = run(bundle, candidate, tmp_path, llm, page_limit=1)
    assert [len(e["duties"]) for e in document["experiences"]] == [3, 3, 3]
    assert "2 page(s), 2 duty(s) removed to fit" in summary


# --- budget -------------------------------------------------------------------
def test_the_time_budget_is_checked_before_a_step_starts(bundle, candidate, tmp_path, renders):
    llm = script_cv(FakeLLM())
    with pytest.raises(BudgetExceededError) as caught:
        generator(bundle, candidate, tmp_path, llm, deadline=time.monotonic() - 1).generate("A JD")
    assert caught.value.stage == "cv.jd_evaluation"
    assert all(role.calls == [] for role in llm.roles.values())


# --- the pure helpers ---------------------------------------------------------
def test_scores_are_read_and_removed():
    assert scores_of("Led the team.|3-80,7-40") == {3: 80, 7: 40}
    assert scores_of("No scores here.") == {}
    assert without_scores({"duties": ["Led the team.|3-80"], "score": "3-80", "n": 1}) == {
        "duties": ["Led the team."], "n": 1,
    }


def test_coverage_counts_a_key_points_two_best_duties_up_to_twice_its_importance():
    importance = {1: 50, 2: 100}
    assert capped_coverage(["a|1-80", "b|1-70", "c|1-60"], importance) == 100   # capped at 2 x 50
    assert capped_coverage(["a|2-40", "b|2-30", "c|2-20"], importance) == 70    # two best only
    assert capped_coverage(["a|9-90"], importance) == 0                          # no such key point


def test_keep_words_takes_markers_and_nothing_else():
    old = {"summary": "A Kubernetes engineer.", "duties": ["Ran Go.|1-80"]}
    marked = {"summary": "A **Kubernetes** engineer.", "duties": ["Ran *Go*.|1-80"]}
    assert keep_words(marked, old) == marked
    assert keep_words({"summary": "A K8s engineer.", "duties": ["Ran *Go*.|1-80"]}, old)["summary"] == (
        "A Kubernetes engineer."
    )
    assert keep_words({"summary": "A engineer.", "duties": ["Ran Go.|1-80|2-10"]}, old)["duties"] == [
        "Ran Go.|1-80"
    ]
    assert keep_words({"summary": "x"}, old)["duties"] == old["duties"]


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_end_to_end_with_a_real_compile(bundle, candidate, tmp_path):
    document, tex, pdf, summary = generator(bundle, candidate, tmp_path,
                                            script_cv(FakeLLM())).generate("A JD")
    assert pdf.startswith(b"%PDF")
    assert candidate.data["name"] in tex
    assert "\\textbf{" in tex, "the bold summary reached the LaTeX"
