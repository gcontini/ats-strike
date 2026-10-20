"""POST /v1/letter."""

from __future__ import annotations

import json

LETTER = " ".join(["word"] * 200)


def letter_parts(candidate, **extra):
    return {
        "candidate_profile": ("p.json", json.dumps(dict(candidate.profile)), "application/json"),
        "candidate_data": ("d.json", json.dumps(dict(candidate.data)), "application/json"),
        **extra,
    }


def test_returns_the_letter_and_its_word_count(client, fake_llm, candidate):
    fake_llm["letter"].replies = [LETTER]
    body = client.post("/v1/letter", data={"jd_text": "a jd"},
                       files=letter_parts(candidate)).json()
    assert body["data"]["text"] == LETTER
    assert body["data"]["words"] == 200


def test_the_letter_runs_on_the_extra_body_sent_by_the_client(client, fake_llm, candidate):
    """It supersedes the letter's own settings: what it says is all that is sent."""
    fake_llm._chat["letter"].temperature = 0.4
    fake_llm._chat["letter"].extra_body = {"enable_search": True}
    fake_llm["letter"].replies = [LETTER]
    client.post("/v1/letter", data={"jd_text": "a jd", "letter_extra_body": '{"temperature": 1.5}'},
                files=letter_parts(candidate))
    sent = fake_llm["letter"].calls[-1]
    assert sent["temperature"] == 1.5 and "enable_search" not in sent


def test_the_letters_extra_body_does_not_stick_to_the_model(client, fake_llm, candidate):
    """The override is for one request: the next letter runs on the same model."""
    fake_llm._chat["letter"].temperature = 0.4
    fake_llm["letter"].replies = [LETTER]
    client.post("/v1/letter", data={"jd_text": "a jd", "letter_extra_body": '{"temperature": 1.5}'},
                files=letter_parts(candidate))
    client.post("/v1/letter", data={"jd_text": "a jd"}, files=letter_parts(candidate))
    assert fake_llm["letter"].calls[-1]["temperature"] == 0.4


def test_a_short_letter_is_rejected_and_retried(client, fake_llm, candidate):
    fake_llm["letter"].replies = ["too short", LETTER]
    body = client.post("/v1/letter", data={"jd_text": "a jd"},
                       files=letter_parts(candidate)).json()
    assert body["data"]["words"] == 200
    retry = fake_llm["letter"].calls[1]["messages"][-1]["content"]
    assert "not acceptable as-is" in retry


def test_the_analysis_is_optional(client, fake_llm, candidate):
    fake_llm["letter"].replies = [LETTER]
    analysis = {"company_name": "Acme Corp", "job_title": "Head of IT"}
    files = letter_parts(candidate,
                         analysis=("analysis.json", json.dumps(analysis), "application/json"))
    client.post("/v1/letter", data={"jd_text": "a jd"}, files=files)
    assert "Acme Corp" in fake_llm["letter"].last_prompt


def test_an_overridden_prompt_is_used(client, fake_llm, candidate):
    fake_llm["letter"].replies = [LETTER]
    files = letter_parts(candidate, sys_prompt_letter=("p.txt", "WRITE IT SHORT"))
    client.post("/v1/letter", data={"jd_text": "a jd"}, files=files)
    assert fake_llm["letter"].calls[0]["messages"][0]["content"] == "WRITE IT SHORT"


def test_a_letter_that_never_validates_is_a_502(client, fake_llm, candidate):
    fake_llm["letter"].replies = ["too short"]
    response = client.post("/v1/letter", data={"jd_text": "a jd"}, files=letter_parts(candidate))
    assert response.status_code == 502
    assert "letter.generate" in response.json()["error"]
