"""Reading a request: the same three parts on every route, checked before any work.

Exercised through ``POST /v1/jd/analysis``, the simplest route that hands all
three parts to the pipeline untouched.
"""

from __future__ import annotations

import json

import pytest

from cv_server.api.multipart import MAX_FILES

from server_helpers import files


def analyse(client, parts, **data):
    return client.post("/v1/jd/analysis", files=parts, data=data)


def handed(stub) -> tuple:
    """What the pipeline was given: pack, job, settings."""
    _, pack, job, settings = stub.calls[-1]
    return pack, job, settings


def test_the_pack_and_the_job_reach_the_pipeline_apart_and_as_bytes(client, stub):
    parts = files(pack={"candidate_profile.json": b"{}", "logo.png": b"\x89PNG\x00"},
                  job={"jd.txt": "a posting", "analysis.json": b"{}"})
    assert analyse(client, parts).status_code == 200
    pack, job, settings = handed(stub)
    assert pack == {"candidate_profile.json": b"{}", "logo.png": b"\x89PNG\x00"}
    assert job == {"jd.txt": b"a posting", "analysis.json": b"{}"}
    assert settings == {}


def test_a_sub_folder_and_accents_survive_in_a_name(client, stub):
    parts = files(pack={"cache/pool.json": b"[]", "señal/ç.txt": b"x"},
                  job={"steps/key_points.json": b"[]"})
    analyse(client, parts)
    pack, job, _ = handed(stub)
    assert set(pack) == {"cache/pool.json", "señal/ç.txt"}
    assert set(job) == {"steps/key_points.json"}


def test_the_settings_are_forwarded_untouched(client, stub):
    settings = {"pages": 2, "extra_body": {"cv": {"temperature": 0.4}}, "unknown": [1]}
    analyse(client, files(job={"jd.txt": "x"}), settings=json.dumps(settings))
    assert handed(stub)[2] == settings


def test_a_repeated_name_keeps_the_last(client, stub):
    parts = [("job", ("jd.txt", "first")), ("job", ("jd.txt", "second"))]
    analyse(client, parts)
    assert handed(stub)[1] == {"jd.txt": b"second"}


@pytest.mark.parametrize("name", ["/etc/passwd", "../up.txt", "a/../../b", "a\\b.txt"])
def test_a_name_that_could_leave_its_folder_is_refused(client, stub, name):
    response = analyse(client, files(pack={name: b"x"}))
    assert response.status_code == 400
    assert "bad_part" in response.json()["error"]
    assert stub.calls == []


def test_a_file_without_a_name_is_refused(client, stub):
    """Starlette reads a part with an empty file name as a plain field, so
    FastAPI refuses it as a 422 before the reader sees it."""
    response = analyse(client, files(job={"": b"x"}))
    assert response.status_code == 422
    assert stub.calls == []


def test_too_many_files_are_refused_before_any_is_read(client, stub):
    parts = files(pack={f"f{n}.txt": b"x" for n in range(MAX_FILES)}, job={"jd.txt": b"x"})
    response = analyse(client, parts)
    assert response.status_code == 400
    assert "too many files" in response.json()["error"]
    assert stub.calls == []


def test_an_oversized_file_is_a_413(client, app_state, stub):
    big = b"x" * (app_state.settings.max_part_bytes + 1)
    response = analyse(client, files(pack={"sys_prompt_cv_body.txt": big}))
    assert response.status_code == 413
    assert stub.calls == []


@pytest.mark.parametrize("sent", ["not json", "[0.9]", '"pages"'])
def test_settings_that_are_not_a_json_object_are_a_400(client, stub, sent):
    response = analyse(client, files(job={"jd.txt": "x"}), settings=sent)
    assert response.status_code == 400
    assert "settings" in response.json()["error"]
    assert stub.calls == []
