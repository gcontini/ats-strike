"""What a request's files and settings mean: pure functions, hand-made values."""

from __future__ import annotations

import json

import pytest

from cv_pipeline import pack
from cv_pipeline.errors import BadPartError, MissingPartError
from cv_pipeline.jd_validator import JD_SYSTEM_PROMPT

PROFILE = json.dumps({"skills": ["Go"]}).encode()
DATA = json.dumps({"name": "Jordan Rivera"}).encode()


# --- the pack -----------------------------------------------------------------
def test_a_prompt_file_replaces_its_bundle_field():
    overrides = pack.bundle_overrides({"sys_prompt_cv.txt": b"MY PROMPT"})
    assert overrides["sys_prompt_cv"] == "MY PROMPT"
    assert overrides["sys_prompt_highlight"] is None, "absent means keep the default"


def test_every_prompt_field_of_the_bundle_has_its_file():
    assert set(pack.PROMPT_FIELDS) == {
        "sys_prompt_cv", "sys_prompt_review", "sys_prompt_highlight", "sys_prompt_letter",
    }


def test_the_template_is_resume_tex_jinja():
    assert pack.bundle_overrides({"resume.tex.jinja": b"\\VAR{x}"})["template_source"] == "\\VAR{x}"


def test_only_top_level_images_are_assets():
    overrides = pack.bundle_overrides({
        "logo.png": b"P", "photo.jpg": b"J", "photo2.jpeg": b"E",
        "cache/old.png": b"C", "anim.gif": b"G", "notes.txt": b"N",
    })
    assert overrides["images"] == {"logo.png": b"P", "photo.jpg": b"J", "photo2.jpeg": b"E"}


def test_a_bom_is_stripped():
    assert pack.bundle_overrides({"sys_prompt_letter.txt": "﻿HI".encode()})[
        "sys_prompt_letter"] == "HI"


def test_a_prompt_that_is_not_utf8_is_a_bad_part():
    with pytest.raises(BadPartError, match="sys_prompt_letter.txt"):
        pack.bundle_overrides({"sys_prompt_letter.txt": b"\xff\xfe\x00"})


def test_the_cache_and_unknown_files_are_ignored():
    overrides = pack.bundle_overrides({
        "cache/pool.json": b"[]", "README.md": b"\xff",
        # A 1.x pack's files mean nothing here.
        "sys_prompt_cv_body.txt": b"1.x", "cv_body_schema.json": b"{}",
    })
    assert all(value is None for key, value in overrides.items() if key != "images")
    assert overrides["images"] == {}


def test_the_analysis_prompt_has_a_file_of_its_own():
    assert pack.analysis_prompt({"sys_prompt_analysis.txt": b"SCORE"}) == "SCORE"
    assert pack.analysis_prompt({}) is None


def test_the_profile_is_always_needed():
    with pytest.raises(MissingPartError, match="candidate_profile.json"):
        pack.candidate({})


def test_the_data_only_when_asked():
    bare = pack.candidate({"candidate_profile.json": PROFILE})
    assert (bare.profile, bare.data) == ({"skills": ["Go"]}, {})
    with pytest.raises(MissingPartError, match="candidate_data.json"):
        pack.candidate({"candidate_profile.json": PROFILE}, data=True)
    full = pack.candidate({"candidate_profile.json": PROFILE, "candidate_data.json": DATA},
                          data=True)
    assert full.data["name"] == "Jordan Rivera"


@pytest.mark.parametrize("blob, why", [
    (b"{not json", "not valid JSON"),
    (b"[1, 2]", "must be a JSON object"),
    (b"  ", "is empty"),
    (b"\xff", "not UTF-8"),
])
def test_a_profile_that_is_not_a_json_object_is_a_bad_part(blob, why):
    with pytest.raises(BadPartError, match=why):
        pack.candidate({"candidate_profile.json": blob})


# --- the prompt's inputs ------------------------------------------------------
PROMPT = """You write CVs.

Inputs:
CANDIDATE_PROFILE: the profile
PRECOMPILED_CV1: a CV written before: by hand
JOB_DESCRIPTION: the posting

Rules: CANDIDATE_PROFILE: is not an input.
"""
FILES = {
    "candidate_profile.json": b'{"skills": ["Go"]}',
    "precompiled_cv1.md": b"# My CV",
}
POSTING = {"job_description.txt": b"Staff engineer"}


def test_the_inputs_are_the_names_of_the_block_in_order_up_to_the_blank_line():
    assert pack.declared_inputs(PROMPT) == [
        "CANDIDATE_PROFILE", "PRECOMPILED_CV1", "JOB_DESCRIPTION",
    ]


def test_a_prompt_without_an_inputs_block_is_a_bad_part():
    with pytest.raises(BadPartError, match="Inputs:"):
        pack.declared_inputs("You write CVs.")


def test_each_input_is_its_file_s_text_in_the_declared_order():
    assert pack.prompt_inputs(PROMPT, FILES, POSTING) == (
        "--------------------------------------------\n"
        'CANDIDATE_PROFILE:\n{"skills": ["Go"]}\n'
        "--------------------------------------------\n"
        "PRECOMPILED_CV1:\n# My CV\n"
        "--------------------------------------------\n"
        "JOB_DESCRIPTION:\nStaff engineer\n"
    )


def test_an_input_may_come_from_the_cache():
    """Where a client keeps a server's CACHE items: ``<server folder>/cache``."""
    files = {"cache/precompiled_cv1.txt": b"cached", "candidate_profile.json": b"{}"}
    assert "PRECOMPILED_CV1:\ncached\n" in pack.prompt_inputs(PROMPT, files, POSTING)


@pytest.mark.parametrize("path", [
    "cache/old/precompiled_cv1.md", "steps/precompiled_cv1.json", "v05/precompiled_cv1.md",
])
def test_a_deeper_file_is_not_an_input(path):
    files = {"candidate_profile.json": b"{}", path: b"too deep"}
    with pytest.raises(MissingPartError, match=r"precompiled_cv1\.\*"):
        pack.prompt_inputs(PROMPT, files, POSTING)


def test_an_input_with_no_file_is_a_missing_part():
    with pytest.raises(MissingPartError, match=r"'job_description\.\*'.*JOB_DESCRIPTION"):
        pack.prompt_inputs(PROMPT, FILES, {})


@pytest.mark.parametrize("other", ["precompiled_cv1.txt", "cache/precompiled_cv1.json"])
def test_two_files_for_one_input_are_a_bad_part(other):
    with pytest.raises(BadPartError, match="several files"):
        pack.prompt_inputs(PROMPT, {**FILES, other: b"another"}, POSTING)


def test_an_input_file_is_checked_like_any_other_text():
    with pytest.raises(BadPartError, match="precompiled_cv1.md"):
        pack.prompt_inputs(PROMPT, {**FILES, "precompiled_cv1.md": b"  "}, POSTING)


@pytest.mark.parametrize("prompt, inputs", [
    ("sys_prompt_cv", ["CANDIDATE_PROFILE", "JOB_DESCRIPTION"]),
    ("sys_prompt_letter", ["CANDIDATE_PROFILE", "CANDIDATE_DATA", "ANALYSIS", "JOB_DESCRIPTION"]),
])
def test_the_shipped_prompts_declare_their_inputs(bundle, prompt, inputs):
    assert pack.declared_inputs(getattr(bundle, prompt)) == inputs


def test_the_shipped_analysis_prompt_declares_its_inputs():
    assert pack.declared_inputs(JD_SYSTEM_PROMPT) == [
        "CANDIDATE_PROFILE", "CANDIDATE_PREFERENCES", "JOB_DESCRIPTION",
    ]


# --- the settings -------------------------------------------------------------
def test_pages_defaults_to_none():
    assert pack.page_limit({}) is None
    assert pack.page_limit({"pages": 1}) == 1


@pytest.mark.parametrize("pages", [0, -1, "2", 1.5])
def test_pages_must_be_a_positive_integer(pages):
    with pytest.raises(BadPartError, match="pages"):
        pack.page_limit({"pages": pages})


def test_sampling_takes_the_temperature_and_the_presence_penalty():
    assert pack.sampling({"temperature": 0.4, "presence_penalty": 1}) == {
        "temperature": 0.4, "presence_penalty": 1.0,
    }


def test_absent_sampling_settings_keep_the_configured_values():
    assert pack.sampling({}) == {"temperature": None, "presence_penalty": None}


def test_unknown_settings_are_ignored():
    """A 1.x server folder's settings, sent to this server, change nothing."""
    settings = {"extra_body": {"cv": {"temperature": 2}}, "mix": {}}
    assert pack.sampling(settings) == {"temperature": None, "presence_penalty": None}
    assert pack.page_limit(settings) is None


@pytest.mark.parametrize("settings", [
    {"temperature": "hot"},
    {"temperature": True},
    {"presence_penalty": [1]},
])
def test_a_sampling_setting_that_is_not_a_number_is_a_bad_part(settings):
    with pytest.raises(BadPartError, match=next(iter(settings))):
        pack.sampling(settings)


# --- the job ------------------------------------------------------------------
def test_the_posting_is_needed():
    with pytest.raises(MissingPartError, match="job_description.txt"):
        pack.jd_text({})


def test_an_undecodable_byte_in_the_posting_is_replaced():
    assert pack.jd_text({"job_description.txt": b"caf\xe9"}) == "caf�"


@pytest.mark.parametrize("names, action", [
    ({"job_description.txt"}, "generate"),
    ({"job_description.txt", "cv.tex"}, "render_tex"),
    ({"job_description.txt", "cv.json"}, "render_document"),
    ({"job_description.txt", "cv.json", "cv.tex"}, "render_document"),
])
def test_what_a_cv_request_does_is_decided_by_the_job(names, action):
    assert pack.job_action({name: b"" for name in names}) == action
