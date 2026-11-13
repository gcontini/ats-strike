"""The pipeline's face, over a real ModelSelector with a fake transport."""

from __future__ import annotations

import json
import shutil

import httpx
import pytest

from cv_pipeline import Output, Pipeline, PipelineError

from pipeline_helpers import EXAMPLE_CANDIDATE, sample_cv_data

needs_latex = pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
JD = {"jd.txt": b"x" * 1200}
LETTER = " ".join(["word"] * 200)
ANALYSIS = {
    "match_percentage": 82, "match_rationale": "Strong overlap.",
    "job_title": "Head of IT", "work_location": "Milan, Italy", "work_mode": "hybrid",
    "expected_salary": None, "max_salary": -1, "salary_match": None,
    "hard_skills": ["AWS"], "soft_skills": ["Communication"], "company_name": "Acme Corp",
    "posting_url": None, "gaps": "No SAP.", "pers_preferences": "Hybrid is fine.",
    "pers_preference_score": 1.5, "should_apply": "YES", "should_apply_reason": "Strong match.",
    "company_evaluation": None,
}


class Recorder:
    """A sink that keeps everything it is handed."""

    def __init__(self) -> None:
        self.statuses: list[str] = []
        self.outputs: list[Output] = []

    def status(self, status: str, detail: str = "") -> None:
        self.statuses.append(status)

    def store(self, output: Output) -> None:
        self.outputs.append(output)

    def named(self, name: str) -> Output:
        return next(o for o in self.outputs if o.name == name)


@pytest.fixture(scope="module")
def example_pack() -> dict:
    """The example candidate's folder, as a client packs it."""
    return {path.name: path.read_bytes() for path in EXAMPLE_CANDIDATE.iterdir()}


@pytest.fixture
def pipeline(fake_llm, bundle) -> Pipeline:
    return Pipeline(llm=fake_llm, bundle=bundle, budget_seconds=60.0)


def a_good_run(fake_llm, **overrides) -> None:
    """The writer writes, the reviewer approves, the highlighter bolds."""
    fake_llm["cv"].replies = [sample_cv_data(**overrides).model_dump_json()]
    fake_llm["review"].replies = ["OK"]
    fake_llm["highlight"].replies = [
        sample_cv_data(**{**overrides, "summary": "**Bold** summary."}).model_dump_json()
    ]


def no_model_was_called(fake_llm) -> bool:
    return all(not role.calls for role in fake_llm.roles.values())


# --- building it --------------------------------------------------------------
def test_build_reads_the_limits_from_the_environment(monkeypatch, fake_llm):
    monkeypatch.setattr("cv_pipeline.facade.build_selector", lambda: fake_llm)
    monkeypatch.setenv("RESUMIX_LATEX_TIMEOUT", "30")
    monkeypatch.setenv("RESUMIX_REQUEST_BUDGET_SECONDS", "600")
    monkeypatch.setenv("RESUMIX_MAX_ATTEMPTS", "2")
    built = Pipeline.build()
    assert (built.latex_timeout, built.budget_seconds, built.max_attempts) == (30.0, 600.0, 2)


def test_build_defaults_when_the_environment_says_nothing(monkeypatch, fake_llm):
    monkeypatch.setattr("cv_pipeline.facade.build_selector", lambda: fake_llm)
    for name in ("RESUMIX_LATEX_TIMEOUT", "RESUMIX_REQUEST_BUDGET_SECONDS", "RESUMIX_MAX_ATTEMPTS"):
        monkeypatch.delenv(name, raising=False)
    built = Pipeline.build()
    assert (built.latex_timeout, built.budget_seconds, built.max_attempts) == (120.0, 1200.0, 4)


# --- writing a CV -------------------------------------------------------------
@needs_latex
def test_a_written_cv_stores_the_document_the_tex_and_the_pdf_and_no_step(
    pipeline, fake_llm, example_pack, tmp_path
):
    a_good_run(fake_llm)
    sink = Recorder()
    summary = pipeline.cv(example_pack, JD, {}, tmp_path, sink)

    assert sink.statuses[0] == "generate" and "highlight" in sink.statuses
    assert [(o.location, o.type, o.name) for o in sink.outputs] == [
        ("CV", "step", "cv.json"), ("CV", "step", "cv.tex"), ("CV", "cv", "cv.pdf"),
    ]
    assert sink.named("cv.pdf").content.startswith(b"%PDF")
    document = json.loads(sink.named("cv.json").content)
    assert document["summary"] == "**Bold** summary."
    assert document["name"] == json.loads(example_pack["candidate_data.json"])["name"]
    assert r"\documentclass" in sink.named("cv.tex").content
    assert "tokens used=" in summary and "page(s)" in summary


@needs_latex
def test_the_document_is_indented_json_with_its_accents_kept(
    pipeline, fake_llm, example_pack, tmp_path
):
    a_good_run(fake_llm, job_title="Ingénieur")
    sink = Recorder()
    pipeline.cv(example_pack, JD, {}, tmp_path, sink)
    text = sink.named("cv.json").content
    assert "Ingénieur" in text
    assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False)


@needs_latex
def test_the_sampling_settings_reach_the_cv_model_and_no_other(
    pipeline, fake_llm, example_pack, tmp_path
):
    a_good_run(fake_llm)
    for role in ("cv", "review", "highlight"):
        fake_llm._chat[role].temperature = 0.4
    settings = {"temperature": 1.5, "presence_penalty": 0.5}
    pipeline.cv(example_pack, JD, settings, tmp_path, Recorder())

    sent = fake_llm["cv"].calls[0]
    assert (sent["temperature"], sent["presence_penalty"]) == (1.5, 0.5)
    for role in ("review", "highlight"):
        assert all(c["temperature"] == 0.4 and "presence_penalty" not in c
                   for c in fake_llm[role].calls)
    assert fake_llm._chat["cv"].temperature == 0.4, "the override is for one request"


@needs_latex
def test_pages_reaches_the_page_check(pipeline, fake_llm, example_pack, tmp_path):
    a_good_run(fake_llm)
    assert "1 page(s)" in pipeline.cv(example_pack, JD, {"pages": 1}, tmp_path, Recorder())


@needs_latex
@pytest.mark.parametrize("attempts", [1, 2])
def test_max_attempts_bounds_the_rounds(fake_llm, bundle, example_pack, tmp_path, attempts):
    a_good_run(fake_llm)
    fake_llm["review"].replies = ["invented a job at NASA"]
    pipeline = Pipeline(llm=fake_llm, bundle=bundle, budget_seconds=60.0, max_attempts=attempts)
    pipeline.cv(example_pack, JD, {}, tmp_path, Recorder())
    assert len(fake_llm["cv"].calls) == attempts


@needs_latex
def test_the_pack_prompts_are_the_ones_used(pipeline, fake_llm, example_pack, tmp_path):
    a_good_run(fake_llm)
    pack = {**example_pack, "sys_prompt_cv.txt": b"MY OWN PROMPT",
            "sys_prompt_review.txt": b"MY OWN REVIEW"}
    pipeline.cv(pack, JD, {}, tmp_path, Recorder())
    assert fake_llm["cv"].calls[0]["messages"][0]["content"].startswith("MY OWN PROMPT")
    assert fake_llm["review"].calls[0]["messages"][0]["content"].startswith("MY OWN REVIEW")


@needs_latex
def test_no_model_is_ever_shown_the_candidate_data(pipeline, fake_llm, example_pack, tmp_path):
    """It is in the pack so the page count is real, and it goes to the template only."""
    a_good_run(fake_llm)
    data = {"name": "Jordan Rivera", "email": "SENTINEL@nowhere.invalid"}
    pack = {**example_pack, "candidate_data.json": json.dumps(data).encode()}
    sink = Recorder()
    pipeline.cv(pack, JD, {}, tmp_path, sink)

    for role in fake_llm.roles.values():
        assert "SENTINEL" not in json.dumps([call["messages"] for call in role.calls])
    assert json.loads(sink.named("cv.json").content)["email"] == "SENTINEL@nowhere.invalid"


def test_a_missing_pack_file_fails_before_any_model_call(pipeline, fake_llm, example_pack,
                                                         tmp_path):
    a_good_run(fake_llm)
    pack = {k: v for k, v in example_pack.items() if k != "candidate_data.json"}
    with pytest.raises(PipelineError) as failure:
        pipeline.cv(pack, JD, {}, tmp_path, Recorder())
    assert failure.value.kind == "missing_part"
    assert no_model_was_called(fake_llm)


def test_a_bad_setting_fails_before_any_model_call(pipeline, fake_llm, example_pack, tmp_path):
    a_good_run(fake_llm)
    with pytest.raises(PipelineError) as failure:
        pipeline.cv(example_pack, JD, {"temperature": "hot"}, tmp_path, Recorder())
    assert failure.value.kind == "bad_part"
    assert no_model_was_called(fake_llm)


# --- rendering ----------------------------------------------------------------
@needs_latex
@pytest.mark.parametrize("call", ["render", "cv"])
def test_a_document_is_rendered_into_a_tex_and_a_pdf(
    pipeline, fake_llm, example_pack, tmp_path, sample_document, call
):
    job = {**JD, "cv.json": json.dumps(sample_document).encode()}
    sink = Recorder()
    if call == "render":
        pipeline.render(example_pack, job, tmp_path, sink)
    else:
        assert "page(s)" in pipeline.cv(example_pack, job, {}, tmp_path, sink)

    assert [(o.type, o.name) for o in sink.outputs] == [("step", "cv.tex"), ("cv", "cv.pdf")]
    assert sink.outputs[1].content.startswith(b"%PDF")
    assert no_model_was_called(fake_llm)


@needs_latex
def test_a_tex_is_compiled_into_a_pdf_only(pipeline, fake_llm, tmp_path):
    tex = "\\documentclass{article}\\begin{document}Edited By Hand\\end{document}"
    sink = Recorder()
    pipeline.cv({}, {"jd.txt": b"x", "cv.tex": tex.encode()}, {}, tmp_path, sink)
    assert [(o.type, o.name) for o in sink.outputs] == [("cv", "cv.pdf")]
    assert no_model_was_called(fake_llm)


@needs_latex
def test_the_document_wins_over_the_tex(pipeline, tmp_path, sample_document):
    job = {"cv.json": json.dumps(sample_document).encode(), "cv.tex": b"not latex at all"}
    sink = Recorder()
    pipeline.render({}, job, tmp_path, sink)
    assert "Staff Platform Engineer" in sink.named("cv.tex").content


def test_a_render_needs_something_to_render(pipeline, tmp_path):
    with pytest.raises(PipelineError) as failure:
        pipeline.render({}, {"jd.txt": b"x"}, tmp_path, Recorder())
    assert failure.value.kind == "missing_part"


# --- the REST calls -----------------------------------------------------------
def test_detect_is_the_model_call(pipeline, fake_llm):
    fake_llm["detect"].replies = ["YES"]
    assert pipeline.detect("too short for the static check, but it is not asked") is True
    assert len(fake_llm["detect"].calls) == 1


def test_an_analysis_comes_back_as_a_plain_object(pipeline, fake_llm, example_pack):
    fake_llm["analysis"].replies = [json.dumps(ANALYSIS)]
    analysis = pipeline.analyze(example_pack, {"jd.txt": b"SENTINEL"}, {})

    assert analysis == ANALYSIS
    prompt = fake_llm["analysis"].last_prompt
    assert "SENTINEL" in prompt
    assert example_pack["candidate_preferences.md"].decode()[:40] in prompt


def test_an_analysis_takes_its_prompt_from_the_pack_and_ignores_the_sampling(
    pipeline, fake_llm, example_pack
):
    """The sampling settings are the CV's and the letter's; analysis keeps its own."""
    fake_llm._chat["analysis"].temperature = 0.4
    fake_llm["analysis"].replies = [json.dumps(ANALYSIS)]
    pack = {**example_pack, "sys_prompt_analysis.txt": b"SCORE IT STRICTLY"}
    pipeline.analyze(pack, {"jd.txt": b"a jd"}, {"temperature": 1.5})

    sent = fake_llm["analysis"].calls[-1]
    assert sent["messages"][0]["content"].startswith("SCORE IT STRICTLY")
    assert sent["temperature"] == 0.4


def test_an_analysis_needs_the_preferences(pipeline, example_pack):
    pack = {k: v for k, v in example_pack.items() if k != "candidate_preferences.md"}
    with pytest.raises(PipelineError) as failure:
        pipeline.analyze(pack, {"jd.txt": b"a jd"}, {})
    assert failure.value.kind == "missing_part"


def test_a_letter_draws_on_the_analysis_and_the_pack_prompt(pipeline, fake_llm, example_pack):
    fake_llm["letter"].replies = [LETTER]
    pack = {**example_pack, "sys_prompt_letter.txt": b"WRITE IT SHORT"}
    job = {"jd.txt": b"a jd", "analysis.json": json.dumps(ANALYSIS).encode()}

    assert pipeline.letter(pack, job, {}) == LETTER
    sent = fake_llm["letter"].calls[0]
    assert sent["messages"][0]["content"] == "WRITE IT SHORT"
    assert "Acme Corp" in fake_llm["letter"].last_prompt


def test_a_letter_takes_the_sampling_settings(pipeline, fake_llm, example_pack):
    fake_llm._chat["letter"].temperature = 0.4
    fake_llm["letter"].replies = [LETTER]
    pipeline.letter(example_pack, {"jd.txt": b"a jd"}, {"temperature": 1.5})
    assert fake_llm["letter"].calls[-1]["temperature"] == 1.5
    assert fake_llm._chat["letter"].temperature == 0.4, "the override is for one request"


# --- failing ------------------------------------------------------------------
@pytest.mark.parametrize("error, kind", [
    (httpx.ConnectError("http://provider.invalid is down"), "provider_unreachable"),
    (httpx.ReadTimeout("http://provider.invalid is slow"), "provider_timeout"),
])
def test_a_provider_error_leaves_as_a_pipeline_error_without_its_body(
    pipeline, fake_llm, error, kind
):
    """LangChain raises its own subclasses: the message names the type, never the body."""
    fake_llm["detect"].error = error
    with pytest.raises(PipelineError) as failure:
        pipeline.detect("Job posting. " * 120)
    assert failure.value.kind == kind
    assert str(failure.value).startswith("the model endpoint failed: ")
    assert "provider.invalid" not in str(failure.value)


def test_info_names_each_roles_model(pipeline):
    info = pipeline.info()
    assert info["models"]["review"] == "fake-review"
    assert isinstance(info["pdflatex"], bool)
