"""The pipeline's face, over a real ModelSelector with a fake transport."""

from __future__ import annotations

import json
import logging
import shutil

import httpx
import pytest

from cv_pipeline import Output, Pipeline, PipelineError
from cv_pipeline.cv_generator import ARTIFACTS

from pipeline_helpers import EXAMPLE_CANDIDATE, SAMPLE_BODY, script_cv

needs_latex = pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
STEPS = ["jd_evaluation", "profile_scoring", "format_selection", "cv", "validation", "highlight"]
LETTER = " ".join(["word"] * 200)
ANALYSIS = {
    "match_percentage": 82, "match_rationale": "Strong overlap.",
    "job_title": "Head of IT", "work_location": "Milan, Italy", "work_mode": "hybrid",
    "expected_salary": None, "max_salary": -1, "salary_match": None,
    "hard_skills": ["AWS"], "soft_skills": ["Communication"], "company_name": "Acme Corp",
    "posting_url": None, "gaps": "No SAP.", "pers_preferences": "Hybrid is fine.",
    "pers_preference_score": 1.5, "should_apply": "YES", "should_apply_reason": "Strong match.",
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


def no_model_was_called(fake_llm) -> bool:
    return all(not role.calls for role in fake_llm.roles.values())


# --- writing a CV -------------------------------------------------------------
@needs_latex
def test_a_written_cv_stores_each_step_then_the_document_the_tex_and_the_pdf(
    pipeline, fake_llm, example_pack, tmp_path
):
    script_cv(fake_llm)
    sink = Recorder()
    summary = pipeline.cv(example_pack, {"jd.txt": b"x" * 1200}, {}, tmp_path, sink)

    assert sink.statuses == STEPS
    assert [o.name for o in sink.outputs] == (
        [f"steps/{name}.json" for name in ARTIFACTS] + ["cv.json", "cv.tex", "cv.pdf"]
    )
    assert {(o.location, o.type) for o in sink.outputs[:-1]} == {("CV", "step")}
    pdf = sink.outputs[-1]
    assert (pdf.location, pdf.type) == ("CV", "cv")
    assert pdf.content.startswith(b"%PDF")
    document = json.loads(sink.named("cv.json").content)
    assert document["summary"] == f"**{SAMPLE_BODY['summary']}**"
    assert document["name"] == json.loads(example_pack["candidate_data.json"])["name"]
    assert r"\documentclass" in sink.named("cv.tex").content
    assert "tokens used=" in summary and "page(s)" in summary


@needs_latex
def test_a_stored_step_is_indented_json_with_its_accents_kept(
    pipeline, fake_llm, example_pack, tmp_path
):
    script_cv(fake_llm, body={**SAMPLE_BODY, "summary": "Ingénieur"})
    sink = Recorder()
    pipeline.cv(example_pack, {"jd.txt": b"x" * 1200}, {}, tmp_path, sink)
    text = sink.named("steps/cv_body.json").content
    assert "Ingénieur" in text
    assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False)


@needs_latex
def test_a_cv_runs_on_its_own_roles_extra_bodies_only(pipeline, fake_llm, example_pack, tmp_path):
    script_cv(fake_llm)
    for role in ("cv", "duties"):
        fake_llm._chat[role].temperature = 0.4
    settings = {"extra_body": {"cv": {"top_p": 0.9}, "letter": {"top_p": 0.1}}}
    pipeline.cv(example_pack, {"jd.txt": b"x" * 1200}, settings, tmp_path, Recorder())

    assert all(c["top_p"] == 0.9 and "temperature" not in c for c in fake_llm["cv"].calls)
    assert all(c["temperature"] == 0.4 and "top_p" not in c for c in fake_llm["duties"].calls)


@pytest.fixture
def pipeline_lines():
    """Every line the pipeline logs, whether or not a server set logging up
    (a server stops ``cv_pipeline`` propagating, so ``caplog`` would miss them)."""
    lines: list[str] = []
    handler = logging.Handler(logging.INFO)
    handler.emit = lambda record: lines.append(record.getMessage())
    logger = logging.getLogger("cv_pipeline")
    level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    yield lines
    logger.removeHandler(handler)
    logger.setLevel(level)


@needs_latex
def test_pages_reaches_the_page_count(pipeline, fake_llm, example_pack, tmp_path, pipeline_lines):
    script_cv(fake_llm)
    pipeline.cv(example_pack, {"jd.txt": b"x" * 1200}, {"pages": 1}, tmp_path, Recorder())
    assert any("past page 1" in line for line in pipeline_lines)


def test_a_missing_pack_file_fails_before_any_model_call(pipeline, fake_llm, example_pack,
                                                         tmp_path):
    script_cv(fake_llm)
    pack = {k: v for k, v in example_pack.items() if k != "candidate_data.json"}
    with pytest.raises(PipelineError) as failure:
        pipeline.cv(pack, {"jd.txt": b"x" * 1200}, {}, tmp_path, Recorder())
    assert failure.value.kind == "missing_part"
    assert no_model_was_called(fake_llm)


# --- rendering ----------------------------------------------------------------
@needs_latex
@pytest.mark.parametrize("call", ["render", "cv"])
def test_a_document_is_rendered_into_a_tex_and_a_pdf(
    pipeline, fake_llm, example_pack, tmp_path, sample_document, call
):
    job = {"jd.txt": b"x" * 1200, "cv.json": json.dumps(sample_document).encode()}
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


def test_an_analysis_takes_its_prompt_and_its_extra_body_from_the_request(
    pipeline, fake_llm, example_pack
):
    fake_llm["analysis"].replies = [json.dumps(ANALYSIS)]
    pack = {**example_pack, "sys_prompt_analysis.txt": b"SCORE IT STRICTLY"}
    settings = {"extra_body": {"analysis": {"top_p": 0.5}, "cv": {"top_p": 0.9}}}
    pipeline.analyze(pack, {"jd.txt": b"a jd"}, settings)

    sent = fake_llm["analysis"].calls[-1]
    assert sent["messages"][0]["content"].startswith("SCORE IT STRICTLY")
    assert sent["top_p"] == 0.5


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
    assert info["models"]["cv"] == "fake-cv"
    assert isinstance(info["pdflatex"], bool)
