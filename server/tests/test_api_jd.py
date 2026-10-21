"""POST /v1/jd/detect and /v1/jd/analysis."""

from __future__ import annotations

import json

import pytest
from cv_contracts import JDAnalysis, static_jd_guess

from cv_server.models import ATTEMPTS

ANALYSIS = {
    "match_percentage": 82, "match_rationale": "Strong overlap.",
    "job_title": "Head of IT", "work_location": "Milan, Italy", "work_mode": "hybrid",
    "expected_salary": "EUR 60k-80k", "max_salary": 80000,
    "hard_skills": ["AWS", "Kubernetes", "ITIL", "Budgets", "Extra one"],
    "soft_skills": ["Communication"], "company_name": "Acme Corp",
    "posting_url": None, "gaps": "No SAP.", "pers_preferences": "Hybrid is fine.",
    "pers_preference_score": 1.5,
    "should_apply": "YES", "should_apply_reason": "Salary meets the threshold, strong match.",
}


def jd_parts(candidate, **extra):
    return {
        "candidate_profile": ("p.json", json.dumps(dict(candidate.profile)), "application/json"),
        "pers_preferences": ("prefs.md", candidate.preferences, "text/markdown"),
        **extra,
    }


def test_detect_rejects_short_text_without_calling_the_model(client, fake_llm):
    body = client.post("/v1/jd/detect", data={"jd_text": "too short"}).json()

    assert body["data"] == {"is_job_description": False}
    assert fake_llm["detect"].calls == [], "the free check must come first"


def test_detect_asks_the_model_when_the_text_is_plausible(client, fake_llm):
    fake_llm["detect"].replies = ["YES"]
    text = "Job posting. " * 120
    assert static_jd_guess(text)

    body = client.post("/v1/jd/detect", data={"jd_text": text}).json()

    assert body["data"]["is_job_description"] is True
    assert len(fake_llm["detect"].calls) == 1


def test_detect_takes_the_model_at_its_word_when_it_says_no(client, fake_llm):
    fake_llm["detect"].replies = ["NO, this is a privacy policy"]
    body = client.post("/v1/jd/detect", data={"jd_text": "Job posting. " * 120}).json()
    assert body["data"]["is_job_description"] is False


@pytest.mark.parametrize(
    "reply, expected",
    [
        ("**YES**", True),
        ("'YES'", True),
        ("Yes.", True),
        ("No.", False),
    ],
)
def test_detect_reads_the_first_word_of_the_reply(client, fake_llm, reply, expected):
    fake_llm["detect"].replies = [reply]
    body = client.post("/v1/jd/detect", data={"jd_text": "Job posting. " * 120}).json()
    assert body["data"]["is_job_description"] is expected


def test_detect_without_a_yes_or_no_is_retried_then_reported(client, fake_llm):
    fake_llm["detect"].replies = ["Not a posting, YES it names a role"]
    response = client.post("/v1/jd/detect", data={"jd_text": "Job posting. " * 120})
    assert response.status_code == 502
    assert len(fake_llm["detect"].calls) == ATTEMPTS


def test_detect_sends_the_posting_apart_from_the_instructions(client, fake_llm):
    fake_llm["detect"].replies = ["YES"]
    text = "Job posting. " * 120
    client.post("/v1/jd/detect", data={"jd_text": text})

    system, user = fake_llm["detect"].calls[-1]["messages"]
    assert system["role"] == "system" and "exactly 'YES'" in system["content"]
    assert user == {"role": "user", "content": text}


def test_detect_ignores_a_temperature_sent_by_the_client(client, fake_llm):
    """The client's temperature is for writing; detection keeps its own."""
    fake_llm._chat["detect"].temperature = 0.0
    fake_llm["detect"].replies = ["YES"]
    client.post("/v1/jd/detect",
                data={"jd_text": "Job posting. " * 120, "temperature": "1.5"})
    assert fake_llm["detect"].calls[-1]["temperature"] == 0.0


def test_analysis_runs_on_its_own_extra_body_and_ignores_the_others(client, fake_llm, candidate):
    """A client sends every extra_body it has; each endpoint reads its own."""
    fake_llm._chat["analysis"].temperature = 0.4
    fake_llm["analysis"].replies = [json.dumps(ANALYSIS)]
    client.post("/v1/jd/analysis", data={"jd_text": "a jd", "cv_extra_body": '{"temperature": 1.5}'},
                files=jd_parts(candidate))
    assert fake_llm["analysis"].calls[-1]["temperature"] == 0.4

    client.post("/v1/jd/analysis", data={"jd_text": "a jd", "analysis_extra_body": '{"top_p": 0.5}'},
                files=jd_parts(candidate))
    sent = fake_llm["analysis"].calls[-1]
    assert sent["top_p"] == 0.5 and "temperature" not in sent


def test_analysis_returns_a_validated_analysis(client, fake_llm, candidate):
    fake_llm["analysis"].replies = [json.dumps(ANALYSIS)]

    body = client.post("/v1/jd/analysis", data={"jd_text": "a jd"},
                       files=jd_parts(candidate)).json()

    analysis = JDAnalysis.model_validate(body["data"])
    assert analysis.company_name == "Acme Corp"
    assert len(analysis.hard_skills) == 5, "the list is passed through unclamped"


def test_analysis_prompt_carries_the_profile_and_the_preferences(client, fake_llm, candidate):
    fake_llm["analysis"].replies = [json.dumps(ANALYSIS)]
    client.post("/v1/jd/analysis", data={"jd_text": "SENTINEL"}, files=jd_parts(candidate))

    prompt = fake_llm["analysis"].last_prompt
    assert "SENTINEL" in prompt
    assert candidate.profile["skills"][0] in prompt
    assert candidate.preferences[:40] in prompt


def test_an_overridden_analysis_prompt_is_used(client, fake_llm, candidate):
    fake_llm["analysis"].replies = [json.dumps(ANALYSIS)]
    files = jd_parts(candidate, sys_prompt_analysis=("p.txt", "SCORE IT STRICTLY"))
    client.post("/v1/jd/analysis", data={"jd_text": "a jd"}, files=files)
    system = fake_llm["analysis"].calls[0]["messages"][0]["content"]
    assert system.startswith("SCORE IT STRICTLY"), "json_mode appends the schema after it"


def test_analysis_requires_the_profile(client, candidate):
    response = client.post(
        "/v1/jd/analysis", data={"jd_text": "a jd"},
        files={"pers_preferences": ("prefs.md", candidate.preferences, "text/markdown")},
    )
    assert response.status_code == 400
    assert "candidate_profile" in response.json()["error"]


def test_analysis_requires_the_preferences(client, candidate):
    response = client.post(
        "/v1/jd/analysis", data={"jd_text": "a jd"},
        files={"candidate_profile": ("p.json", json.dumps(dict(candidate.profile)))},
    )
    assert response.status_code == 400
    assert "pers_preferences" in response.json()["error"]


def test_a_bad_analysis_is_retried_then_reported(client, fake_llm, candidate):
    fake_llm["analysis"].replies = ["{}"]
    response = client.post("/v1/jd/analysis", data={"jd_text": "a jd"},
                           files=jd_parts(candidate))
    body = response.json()
    assert response.status_code == 502
    assert "jd.analysis" in body["error"]
    assert len(fake_llm["analysis"].calls) == ATTEMPTS, "it retried before giving up"
