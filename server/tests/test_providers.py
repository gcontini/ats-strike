"""What each provider puts on the wire — pinned, because the endpoint reads it."""

import json

import pytest
from resumix_contracts import JDAnalysis

from resumix_server.models import Usage

from server_helpers import FakeLLM, completion

#: A reply the analysis model may give, every key present as a strict
#: json_schema requires: valid, so a structured call succeeds.
ANALYSIS = json.dumps({
    "match_percentage": 82, "match_rationale": "Strong overlap.", "job_title": "Head of IT",
    "work_location": "Milan, Italy", "work_mode": "hybrid", "expected_salary": None,
    "max_salary": -1, "salary_match": None, "hard_skills": ["AWS"],
    "soft_skills": ["Communication"], "company_name": "Acme Corp", "posting_url": None,
    "gaps": "No SAP.", "pers_preferences": "Hybrid is fine.", "pers_preference_score": 1.5,
    "should_apply": "YES", "should_apply_reason": "Strong match.", "company_evaluation": None,
})

USER = [{"role": "user", "content": "go"}]


def sent(llm, role, output_type=str, reply="ok"):
    """The request body ``role`` sends for one call."""
    llm[role].replies = [reply]
    llm.call_llm(role, output_type, "sys", USER)
    return llm[role].calls[-1]


def test_standard_sends_what_the_role_declares():
    llm = FakeLLM(cv=dict(
        thinking="on", reasoning_effort="high", max_output_tokens=24500, temperature=0.2,
        extra_body={"thinking_budget": 7500, "enable_search": True},
    ))
    body = sent(llm, "cv")
    assert body["path"] == "/v1/chat/completions"
    assert body["max_completion_tokens"] == 24500
    assert (body["reasoning_effort"], body["temperature"]) == ("high", 0.2)
    assert (body["enable_thinking"], body["thinking_budget"], body["enable_search"]) == (
        True, 7500, True
    )


@pytest.mark.parametrize("thinking, switch", [("auto", None), ("off", False)])
def test_standard_thinking_switch(thinking, switch):
    assert sent(FakeLLM(cv=dict(thinking=thinking)), "cv").get("enable_thinking") is switch


def test_a_declared_extra_body_key_wins_over_thinking():
    llm = FakeLLM(cv=dict(thinking="on", extra_body={"enable_thinking": False}))
    assert sent(llm, "cv")["enable_thinking"] is False


def test_json_schema_sends_the_schema_strict_and_closed():
    llm = FakeLLM(analysis=dict(structured_output="json_schema"))
    body = sent(llm, "analysis", JDAnalysis, ANALYSIS)
    block = body["response_format"]["json_schema"]
    assert block["strict"] is True
    assert block["schema"]["additionalProperties"] is False
    assert all(d["additionalProperties"] is False for d in block["schema"].get("$defs", {}).values())


def test_a_structured_reply_cut_off_is_retried_and_paid_for():
    llm, usage = FakeLLM(analysis=dict(structured_output="json_schema")), Usage()
    llm["analysis"].replies = [completion("fake-analysis", "{", "length"), ANALYSIS]
    assert llm.call_llm("analysis", JDAnalysis, "sys", USER, usage=usage).company_name == "Acme Corp"
    assert (usage.calls, usage.input_tokens) == (2, 22)
    assert "cut off" in llm["analysis"].calls[-1]["messages"][-1]["content"]


def response(text, status="completed"):
    """A Responses API body: 5 tokens in, 3 out, 1 of them thinking."""
    return {
        "id": "r", "object": "response", "created_at": 0, "model": "fake-letter",
        "status": status,
        "incomplete_details": {"reason": "max_output_tokens"} if status == "incomplete" else None,
        "output": [{
            "type": "message", "id": "m", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }],
        "usage": {
            "input_tokens": 5, "output_tokens": 3, "total_tokens": 8,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 1},
        },
        "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
    }


def test_responses_api_sends_the_same_settings_its_way():
    llm = FakeLLM(letter=dict(
        api="responses", thinking="on", reasoning_effort="low", max_output_tokens=900,
        extra_body={"enable_search": True},
    ))
    body = sent(llm, "letter", reply=response("Dear team"))
    assert body["path"] == "/v1/responses"
    assert body["max_output_tokens"] == 900
    assert body["reasoning"] == {"effort": "low"}
    assert (body["enable_thinking"], body["enable_search"]) == (True, True)


def test_responses_api_incomplete_is_retried():
    llm, usage = FakeLLM(letter=dict(api="responses")), Usage()
    llm["letter"].replies = [response("Dear", "incomplete"), response("Dear team")]
    assert llm.call_llm("letter", str, "sys", USER, usage=usage) == "Dear team"
    assert (usage.calls, usage.thinking_tokens, usage.output_tokens) == (2, 2, 4)


def test_responses_api_structured_output_goes_in_text_format():
    llm = FakeLLM(analysis=dict(api="responses", structured_output="json_schema"))
    body = sent(llm, "analysis", JDAnalysis, response(ANALYSIS))
    assert body["text"]["format"]["type"] == "json_schema"


def test_deepseek_thinking_switch():
    body = sent(FakeLLM(validation=dict(model_provider="deepseek", thinking="on")), "validation")
    assert body["thinking"] == {"type": "enabled"}


def test_deepseek_function_calling_reads_the_tool_call():
    from resumix_contracts import JDDetection

    llm = FakeLLM(detect=dict(
        model_provider="deepseek", thinking="off", structured_output="function_calling",
    ))
    tool_call = completion("fake-detect", "", "tool_calls")
    tool_call["choices"][0]["message"] = {"role": "assistant", "content": None, "tool_calls": [{
        "id": "c", "type": "function",
        "function": {"name": "JDDetection", "arguments": '{"is_job_description": true}'},
    }]}
    body = sent(llm, "detect", JDDetection, tool_call)
    assert body["thinking"] == {"type": "disabled"}
    assert body["tools"][0]["function"]["name"] == "JDDetection"
    assert body["tool_choice"]["function"]["name"] == "JDDetection"
    assert llm.call_llm("detect", JDDetection, "sys", USER) == JDDetection(is_job_description=True)

