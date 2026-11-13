"""call_llm: types in, retries with feedback, usage out — through a real selector."""

import httpx
import openai
import pytest
from cv_pipeline.schemas import JDAnalysis

from cv_pipeline.models import ATTEMPTS, Usage
from cv_pipeline.observability import stage
from cv_pipeline.errors import ModelOutputError

from pipeline_helpers import FakeLLM

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
    llm["review"].finish_reason = "length"
    with pytest.raises(ModelOutputError, match="cut off"):
        llm.call_llm("review", str, "review", USER)
    assert len(llm["review"].calls) == ATTEMPTS


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


def test_a_valid_reply_comes_back_as_the_model(sample_document):
    from cv_pipeline.cv_schema import TailoredCVData
    from pipeline_helpers import sample_cv_data

    llm = FakeLLM()
    llm["highlight"].replies = [sample_cv_data().model_dump_json()]
    assert llm.call_llm("highlight", TailoredCVData, "mark", USER) == sample_cv_data()


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


def test_overrides_change_one_role_and_share_its_client():
    llm = FakeLLM()
    assert llm.with_overrides("cv") is llm
    hot = llm.with_overrides("cv", temperature=0.9, presence_penalty=0.5)
    hot.call_llm("cv", str, "", USER)
    hot.call_llm("review", str, "", USER)
    assert llm["cv"].calls[0]["temperature"] == 0.9
    assert llm["cv"].calls[0]["presence_penalty"] == 0.5
    assert "temperature" not in llm["review"].calls[0]
    assert hot._chat["cv"].root_client is llm._chat["cv"].root_client
    assert llm._chat["cv"].temperature is None


def test_provider_errors_propagate():
    llm = FakeLLM()
    llm["cv"].error = httpx.ConnectError("down")
    with pytest.raises(openai.APIConnectionError):
        llm.call_llm("cv", str, "", USER)
