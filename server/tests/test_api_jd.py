"""POST /v1/jd/detect and /v1/jd/analysis."""

from __future__ import annotations

import json
from dataclasses import replace

from cv_contracts import Analysed, static_jd_guess

from server_helpers import POSTING, Failing, a_job, files, logs


def detect(client, text):
    return client.post("/v1/jd/detect", files=files(job={"jd.txt": text}))


def test_detect_rejects_short_text_without_calling_the_pipeline(client, stub):
    body = detect(client, "too short").json()
    assert body["data"] == {"is_job_description": False}
    assert stub.calls == [], "the free check must come first"


def test_detect_asks_the_pipeline_when_the_text_is_plausible(client, stub):
    assert static_jd_guess(POSTING)
    body = detect(client, POSTING).json()
    assert body["data"] == {"is_job_description": True}
    assert stub.calls == [("detect", POSTING)]


def test_detect_takes_the_pipeline_at_its_word(client, stub):
    stub.verdict = False
    assert detect(client, POSTING).json()["data"]["is_job_description"] is False


def test_detect_carries_no_items(client):
    assert set(detect(client, POSTING).json()["data"]) == {"is_job_description"}


def test_the_free_check_uses_the_servers_length_band(app_state, stub):
    from fastapi.testclient import TestClient

    from cv_server.api.app import create_app

    app_state.settings = replace(app_state.settings, jd_min_chars=5)
    with TestClient(create_app(state=app_state)) as client:
        assert detect(client, "a short posting").json()["data"]["is_job_description"] is True
    assert stub.calls == [("detect", "a short posting")]


def test_an_undecodable_byte_in_the_posting_is_replaced(client, stub):
    detect(client, POSTING.encode() + b"\xff")
    assert stub.calls[-1][1].endswith("�")


def test_detect_needs_a_posting(client, stub):
    response = client.post("/v1/jd/detect", files=files(pack={"candidate_profile.json": b"{}"}))
    assert response.status_code == 400
    assert "missing_part" in response.json()["error"]
    assert "jd.txt" in response.json()["error"]


def test_analysis_returns_the_pipelines_object_untouched(client, stub):
    stub.analysis = {"job_title": "Head of IT", "company_evaluation": "a field 1.0 never had"}
    body = client.post("/v1/jd/analysis", files=a_job()).json()
    reply = Analysed.model_validate(body["data"])
    assert reply.analysis == stub.analysis


def test_analysis_carries_its_log_lines_then_the_total(client):
    items = client.post("/v1/jd/analysis", files=a_job()).json()["data"]["items"]
    lines = logs(items)
    assert all(item["location"] == "CV" for item in items)
    assert "[stub] stub-model 0.5s" in lines[0]
    assert lines[-1].endswith(
        "INFO    [run] Σ 1 model call(s), 0.5s | input=11, thinking=2, output=20"
    )


def test_a_log_line_is_dated_levelled_and_staged(client):
    line = logs(client.post("/v1/jd/analysis", files=a_job()).json()["data"]["items"])[0]
    ts, level, stage = line.split(" ", 2)[0], line[26:33], line[34:].split("]")[0]
    assert ts.endswith("+00:00") and len(ts) == 25, "ISO-8601 to the second, UTC"
    assert (level, stage) == ("INFO   ", "[-")


def test_analysis_hands_the_pipeline_the_pack_the_job_and_the_settings(client, stub):
    client.post("/v1/jd/analysis", files=a_job(), data={"settings": json.dumps({"pages": 1})})
    name, pack, job, settings = stub.calls[-1]
    assert name == "analyze"
    assert pack == {"candidate_profile.json": b"{}"}
    assert job == {"jd.txt": POSTING.encode()}
    assert settings == {"pages": 1}


def test_a_missing_file_the_pipeline_needs_is_a_400(client, stub):
    stub.failure = Failing("missing_part", "file 'candidate_preferences.md' is required")
    response = client.post("/v1/jd/analysis", files=a_job())
    assert response.status_code == 400
    assert response.json()["error"] == (
        "missing_part: file 'candidate_preferences.md' is required"
    )


def test_a_bad_analysis_is_a_502_naming_its_stage(client, stub):
    stub.failure = Failing("model_output", "analysis: no usable reply", stage="jd.analysis")
    response = client.post("/v1/jd/analysis", files=a_job())
    assert response.status_code == 502
    assert response.json()["error"].startswith("model_output [jd.analysis]: ")
