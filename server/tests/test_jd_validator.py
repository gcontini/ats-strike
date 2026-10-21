"""JD detection and analysis -- and the shape every JD source has to produce."""

import json

import pytest
from pydantic import ValidationError

from cv_contracts import JDAnalysis
from cv_server.pipeline.jd_validator import JDValidator
from server_helpers import FakeLLM

VALID = {
    "match_percentage": 82,
    "match_rationale": "Strong overlap.",
    "job_title": "Staff Platform Engineer",
    "work_location": "Lisbon, Portugal",
    "work_mode": "hybrid",
    "expected_salary": "EUR 70k-90k",
    "max_salary": 90000,
    "hard_skills": ["Kubernetes", "Go"],
    "soft_skills": ["Communication"],
    "company_name": "Globex",
    "posting_url": "https://example.com/job",
    "gaps": "No formal people management.",
    "pers_preferences": "Hybrid is acceptable.",
    "pers_preference_score": 1.5,
    "should_apply": "YES",
    "should_apply_reason": "Salary above threshold and 82% match.",
}


def analyze(candidate, *replies):
    llm = FakeLLM()
    llm["analysis"].replies = list(replies)
    return llm, JDValidator(llm).analyze("a posting", candidate)


def test_parses_plain_json(candidate):
    assert analyze(candidate, json.dumps(VALID))[1].company_name == "Globex"


def test_strips_markdown_fences(candidate):
    fenced = "```json\n" + json.dumps(VALID) + "\n```"
    assert analyze(candidate, fenced)[1].match_percentage == 82


@pytest.mark.parametrize("field, value", [
    ("should_apply", "MAYBE"),
    ("should_apply_reason", "x" * 301),
])
def test_rejects_an_invalid_should_apply(field, value):
    bad = dict(VALID, **{field: value})
    with pytest.raises(ValidationError):
        JDAnalysis.model_validate(bad)


def test_optional_fields_may_be_absent():
    minimal = {k: v for k, v in VALID.items()
               if k not in ("work_location", "expected_salary",
                            "max_salary", "posting_url")}
    analysis = JDAnalysis.model_validate(minimal)
    assert analysis.posting_url is None
    assert analysis.max_salary == -1
    assert analysis.company_evaluation is None


def test_retry_names_the_missing_field(candidate):
    """The second round is told which field was missing, not handed a raw dump."""
    no_title = {k: v for k, v in VALID.items() if k != "job_title"}
    llm, analysis = analyze(candidate, json.dumps(no_title), json.dumps(VALID))

    assert analysis.job_title == "Staff Platform Engineer"
    correction = llm["analysis"].calls[1]["messages"][-1]["content"]
    assert "- job_title: Field required" in correction
