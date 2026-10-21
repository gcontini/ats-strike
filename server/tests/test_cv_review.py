"""The validation loop: replacements the model calls, applied by the code."""

from __future__ import annotations

from cv_server.models import Usage
from cv_server.pipeline.cv_review import (
    MAX_VALIDATIONS,
    ReplaceInBody,
    ReplaceInDuties,
    apply_body,
    apply_duties,
    review,
)

from server_helpers import FakeLLM, tool_completion, user_text


def experience() -> dict:
    return {"company": "Northwind", "duties": ["Led a team of four.|1-80", "Wrote the tests.|2-40"]}


# --- applying one call -------------------------------------------------------
def test_a_replacement_in_one_duty_leaves_the_others_alone():
    doc = {"company": "Northwind", "duties": ["the team.", "the team."]}
    assert apply_duties(doc, {"old": "team", "new": "squad", "duty": 2}) is None
    assert doc["duties"] == ["the team.", "the squad."]


def test_a_replacement_without_a_duty_goes_everywhere():
    doc = {"company": "Northwind", "duties": ["the team.", "the team."]}
    apply_duties(doc, {"old": "team", "new": "squad"})
    assert doc["duties"] == ["the squad.", "the squad."]


def test_a_replacement_that_finds_nothing_says_where_it_looked():
    doc = experience()
    assert apply_duties(doc, {"old": "nowhere", "new": "x", "duty": 1}) == "'nowhere' not found in duty 1"
    assert apply_duties(doc, {"old": "nowhere", "new": "x"}) == "'nowhere' not found in any duty"
    assert doc == experience()


def test_a_body_replacement_reaches_text_fields_only():
    doc = {"job_title": "Lead", "summary": "A Lead.", "skills": ["Lead"]}
    assert apply_body(doc, {"old": "Lead", "new": "Senior"}) is None
    assert doc == {"job_title": "Senior", "summary": "A Senior.", "skills": ["Lead"]}
    assert apply_body(doc, {"old": "x", "new": "y", "field": "summary"}) == "'x' not found in summary"


# --- the loop ----------------------------------------------------------------
def checker(*replies) -> FakeLLM:
    llm = FakeLLM()
    llm["validation"].replies = list(replies)
    return llm


def text_for(doc: dict, attempt: int) -> str:
    return f"ATTEMPT: {attempt}\n\nGENERATED EXPERIENCE:\n{doc['duties']}"


def run(llm, doc):
    return review(llm, ReplaceInDuties, apply_duties, "check", doc, text_for, "Northwind",
                  usage=Usage())


def test_a_reply_with_no_call_ends_the_review():
    llm = checker("OK")
    assert run(llm, experience()) == experience()
    assert len(llm["validation"].calls) == 1


def test_the_calls_of_one_reply_are_applied_in_order_then_checked_again():
    """The second replacement only finds its text once the first has run."""
    llm = checker(tool_completion("fake-validation", [
        ("replace_in_duties", {"old": "Led", "new": "Coached", "duty": 1}),
        ("replace_in_duties", {"old": "Coached a team", "new": "Coached the team", "duty": 1}),
    ]), "OK")
    doc = run(llm, experience())
    assert doc["duties"] == ["Coached the team of four.|1-80", "Wrote the tests.|2-40"]
    assert len(llm["validation"].calls) == 2
    assert "Coached the team" in user_text(llm["validation"].calls[1])


def test_a_replacement_that_failed_goes_back_to_the_model():
    llm = checker(tool_completion("fake-validation", [
        ("replace_in_duties", {"old": "Managed", "new": "Led", "duty": 1}),
    ]), "OK")
    run(llm, experience())
    retry = user_text(llm["validation"].calls[1])
    assert "FAILED REPLACEMENTS:\n'Managed' not found in duty 1 (your fix: 'Led')" in retry
    assert "ATTEMPT: 2" in retry


def test_a_review_that_never_settles_stops_and_keeps_the_last_edits():
    llm = checker(tool_completion("fake-validation", [
        ("replace_in_duties", {"old": "tests", "new": "tests", "duty": 2}),
    ]))
    assert run(llm, experience()) == experience()
    assert len(llm["validation"].calls) == MAX_VALIDATIONS


def test_the_tools_are_named_and_described_for_the_model():
    llm = checker("OK")
    review(llm, ReplaceInBody, apply_body, "check", {"summary": "x"}, lambda d, a: "x", "body",
           usage=Usage())
    tool = llm["validation"].calls[0]["tools"][0]["function"]
    assert tool["name"] == "replace_in_body"
    assert tool["description"].startswith("Replace the exact text `old` with `new` in the text field")
    assert set(tool["parameters"]["properties"]) == {"old", "new", "field", "reason"}
    assert tool["parameters"]["required"] == ["old", "new"]
