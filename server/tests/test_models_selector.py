"""call_llm and call_tools: types in, retries with feedback, usage out — through a real selector."""

import httpx
import openai
import pytest
from resumix_contracts import JDAnalysis, JDDetection

from resumix_server.models import ATTEMPTS, Usage
from resumix_server.observability import stage
from resumix_server.pipeline.cv_review import ReplaceInDuties
from resumix_server.pipeline.errors import ModelOutputError

from server_helpers import FakeLLM, tool_completion

USER = [{"role": "user", "content": "the text"}]


def test_str_is_returned_as_is_after_the_system_prompt():
    llm = FakeLLM()
    llm["letter"].replies = ["  Dear team,  "]
    assert llm.call_llm("letter", str, "be brief", USER) == "  Dear team,  "
    assert llm["letter"].calls[0]["messages"] == [
        {"role": "system", "content": "be brief"}, {"role": "user", "content": "the text"},
    ]


def test_an_empty_system_prompt_sends_no_system_message():
    llm = FakeLLM()
    llm["letter"].replies = ["hi"]
    llm.call_llm("letter", str, "", USER)
    assert [m["role"] for m in llm["letter"].calls[0]["messages"]] == ["user"]


@pytest.mark.parametrize("reply, expected", [("**YES**", True), ("'Yes.'", True), ("No.", False)])
def test_bool_reads_the_first_word(reply, expected):
    llm = FakeLLM()
    llm["detect"].replies = [reply]
    assert llm.call_llm("detect", bool, "is it?", USER) is expected


def test_an_unusable_reply_is_retried_with_what_was_wrong():
    llm = FakeLLM()
    llm["detect"].replies = ["", "maybe", "YES"]
    assert llm.call_llm("detect", bool, "is it?", USER) is True
    last = llm["detect"].calls[-1]["messages"]
    assert "it was empty" in last[2]["content"]
    assert last[3] == {"role": "assistant", "content": "maybe"}
    assert "expected YES or NO" in last[4]["content"]


def test_a_cut_off_reply_is_retried():
    llm = FakeLLM()
    llm["validation"].finish_reason = "length"
    with pytest.raises(ModelOutputError, match="cut off"):
        llm.call_llm("validation", str, "check", USER)
    assert len(llm["validation"].calls) == ATTEMPTS


def test_giving_up_names_the_stage():
    llm = FakeLLM()
    llm["detect"].replies = ["perhaps"]
    with stage("jd.detect"), pytest.raises(ModelOutputError) as caught:
        llm.call_llm("detect", bool, "is it?", USER)
    assert caught.value.stage == "jd.detect"


def test_json_mode_puts_the_schema_in_the_system_prompt_and_lists_what_failed():
    llm = FakeLLM()
    llm["analysis"].replies = ['{"job_title": "x"}']
    with pytest.raises(ModelOutputError):
        llm.call_llm("analysis", JDAnalysis, "analyse", USER)
    system, feedback = (llm["analysis"].calls[-1]["messages"][i]["content"] for i in (0, -1))
    assert system.startswith("analyse") and '"match_percentage"' in system
    assert llm["analysis"].calls[0]["response_format"] == {"type": "json_object"}
    assert "- match_percentage: Field required" in feedback


def test_a_valid_reply_comes_back_as_the_model():
    llm = FakeLLM()
    llm["detect"].replies = ['{"is_job_description": true}']
    assert llm.call_llm("detect", JDDetection, "is it?", USER) == JDDetection(
        is_job_description=True
    )


@pytest.mark.parametrize("reply", [
    '[{"id": 1}]',
    '```json\n[{"id": 1}]\n```',
    'Here they are:\n```json\n[{"id": 1}]\n```',
])
def test_a_json_list_is_parsed_fenced_or_not(reply):
    """Parsed as LangChain's JSON output parser does: no schema, no response_format."""
    llm = FakeLLM()
    llm["jd_evaluation"].replies = [reply]
    assert llm.call_llm("jd_evaluation", list, "extract", USER) == [{"id": 1}]
    assert "response_format" not in llm["jd_evaluation"].calls[0]


def test_json_of_the_wrong_type_is_retried():
    llm = FakeLLM()
    llm["format_selection"].replies = ['["A"]', '{"format": "A"}']
    assert llm.call_llm("format_selection", dict, "choose", USER) == {"format": "A"}
    assert "it is not a JSON object" in llm["format_selection"].calls[-1]["messages"][-1]["content"]


def test_a_reply_that_is_no_json_is_retried_then_given_up():
    llm = FakeLLM()
    llm["cv"].replies = ["I cannot help with that."]
    with pytest.raises(ModelOutputError):
        llm.call_llm("cv", dict, "write", USER)
    assert len(llm["cv"].calls) == ATTEMPTS


def test_tool_calls_come_back_as_their_arguments_in_order():
    llm = FakeLLM()
    llm["validation"].replies = [tool_completion("fake-validation", [
        ("replace_in_duties", {"old": "a", "new": "b", "duty": 1, "reason": "Syntax"}),
        ("replace_in_duties", {"old": "c", "new": "d"}),
    ])]
    calls = llm.call_tools("validation", ReplaceInDuties, "check", USER)
    assert calls == [{"old": "a", "new": "b", "duty": 1, "reason": "Syntax"},
                     {"old": "c", "new": "d"}]
    sent = llm["validation"].calls[0]
    assert sent["tools"][0]["function"]["name"] == "replace_in_duties"
    assert sent["parallel_tool_calls"] is True
    assert "tool_choice" not in sent, "never forced: no call at all is an answer too"


def test_a_reply_without_a_call_is_nothing_to_report():
    llm = FakeLLM()
    llm["validation"].replies = ["OK"]
    assert llm.call_tools("validation", ReplaceInDuties, "check", USER) == []


def test_an_empty_tool_reply_is_retried():
    llm = FakeLLM()
    llm["validation"].replies = ["", "OK"]
    assert llm.call_tools("validation", ReplaceInDuties, "check", USER) == []
    assert len(llm["validation"].calls) == 2


def test_usage_counts_every_attempt():
    llm, usage = FakeLLM(), Usage()
    llm["detect"].replies = ["", "NO"]
    llm.call_llm("detect", bool, "is it?", USER, usage=usage)
    assert (usage.calls, usage.input_tokens, usage.thinking_tokens, usage.output_tokens) == (
        2, 22, 4, 40
    )
    assert (usage - Usage(calls=1)).calls == 1


def test_the_structured_wrapper_is_built_once():
    llm = FakeLLM()
    assert llm._wrapper("analysis", JDAnalysis) is llm._wrapper("analysis", JDAnalysis)


SETTINGS = dict(temperature=0.2, thinking="on", reasoning_effort="high",
                max_output_tokens=900, extra_body={"thinking_budget": 100})


def test_an_extra_body_supersedes_every_setting_of_its_role():
    """What the client sent is all that goes out, besides the model and the messages."""
    llm = FakeLLM(cv=SETTINGS)
    llm.with_extra_body({"cv": {"top_p": 0.9}}).call_llm("cv", str, "", USER)
    sent = llm["cv"].calls[0]
    assert sent["top_p"] == 0.9
    for setting in ("temperature", "reasoning_effort", "max_completion_tokens",
                    "enable_thinking", "thinking_budget"):
        assert setting not in sent, setting


def test_an_extra_body_changes_its_role_for_one_request_only():
    llm = FakeLLM(cv=SETTINGS, validation=dict(temperature=0.3))
    assert llm.with_extra_body({}) is llm
    hot = llm.with_extra_body({"cv": {"temperature": 1.5}})
    hot.call_llm("validation", str, "", USER)
    llm.call_llm("cv", str, "", USER)
    assert llm["validation"].calls[0]["temperature"] == 0.3
    assert llm["cv"].calls[0]["temperature"] == 0.2
    assert hot._chat["cv"].root_client is llm._chat["cv"].root_client


def test_provider_errors_propagate():
    llm = FakeLLM()
    llm["cv"].error = httpx.ConnectError("down")
    with pytest.raises(openai.APIConnectionError):
        llm.call_llm("cv", str, "", USER)
