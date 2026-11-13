"""What a request's files and settings mean: pure functions, hand-made values."""

from __future__ import annotations

import json

import pytest

from cv_pipeline import pack
from cv_pipeline.errors import BadPartError, MissingPartError

PROFILE = json.dumps({"skills": ["Go"]}).encode()
DATA = json.dumps({"name": "Jordan Rivera"}).encode()


# --- the pack -----------------------------------------------------------------
def test_a_prompt_file_replaces_its_bundle_field():
    overrides = pack.bundle_overrides({"sys_prompt_cv_body.txt": b"MY PROMPT"})
    assert overrides["sys_prompt_cv_body"] == "MY PROMPT"
    assert overrides["sys_prompt_highlight"] is None, "absent means keep the default"


def test_every_prompt_field_of_the_bundle_has_its_file():
    assert set(pack.PROMPT_FIELDS) == {
        "sys_prompt_jd_evaluation", "sys_prompt_profile_scoring", "sys_prompt_format_selection",
        "sys_prompt_cv_body", "sys_prompt_cv_duties", "sys_prompt_validation_body",
        "sys_prompt_validation_duties", "sys_prompt_highlight", "sys_prompt_letter",
    }


def test_a_schema_file_is_json_named_and_kept_as_text():
    overrides = pack.bundle_overrides({"cv_duties_schema.json": b'{"MY": "SCHEMA"}'})
    assert overrides["cv_duties_schema"] == '{"MY": "SCHEMA"}'


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
    overrides = pack.bundle_overrides({"cache/pool.json": b"[]", "README.md": b"\xff"})
    assert all(value is None for key, value in overrides.items() if key != "images")
    assert overrides["images"] == {}


def test_the_analysis_prompt_has_a_file_of_its_own():
    assert pack.analysis_prompt({"sys_prompt_analysis.txt": b"SCORE"}) == "SCORE"
    assert pack.analysis_prompt({}) is None


def test_the_profile_is_always_needed():
    with pytest.raises(MissingPartError, match="candidate_profile.json"):
        pack.candidate({})


def test_the_data_and_the_preferences_only_when_asked():
    bare = pack.candidate({"candidate_profile.json": PROFILE})
    assert (bare.profile, bare.data, bare.preferences) == ({"skills": ["Go"]}, {}, "")
    with pytest.raises(MissingPartError, match="candidate_data.json"):
        pack.candidate({"candidate_profile.json": PROFILE}, data=True)
    with pytest.raises(MissingPartError, match="candidate_preferences.md"):
        pack.candidate({"candidate_profile.json": PROFILE}, preferences=True)
    full = pack.candidate({"candidate_profile.json": PROFILE, "candidate_data.json": DATA,
                           "candidate_preferences.md": b"Remote only"},
                          data=True, preferences=True)
    assert (full.data["name"], full.preferences) == ("Jordan Rivera", "Remote only")


@pytest.mark.parametrize("blob, why", [
    (b"{not json", "not valid JSON"),
    (b"[1, 2]", "must be a JSON object"),
    (b"  ", "is empty"),
    (b"\xff", "not UTF-8"),
])
def test_a_profile_that_is_not_a_json_object_is_a_bad_part(blob, why):
    with pytest.raises(BadPartError, match=why):
        pack.candidate({"candidate_profile.json": blob})


# --- the settings -------------------------------------------------------------
def test_pages_defaults_to_none():
    assert pack.page_limit({}) is None
    assert pack.page_limit({"pages": 1}) == 1


@pytest.mark.parametrize("pages", [0, -1, "2", 1.5])
def test_pages_must_be_a_positive_integer(pages):
    with pytest.raises(BadPartError, match="pages"):
        pack.page_limit({"pages": pages})


def test_each_call_takes_only_its_own_roles():
    settings = {"extra_body": {"cv": {"top_p": 0.9}, "analysis": {"temperature": 0},
                               "nobody": {"x": 1}}}
    assert pack.extra_bodies(settings, pack.CV_ROLES) == {"cv": {"top_p": 0.9}}
    assert pack.extra_bodies(settings, pack.ANALYSIS_ROLES) == {"analysis": {"temperature": 0}}
    assert pack.extra_bodies({}, pack.CV_ROLES) == {}


def test_unknown_settings_are_ignored():
    assert pack.extra_bodies({"temperature": 2, "mix": {}}, pack.CV_ROLES) == {}


@pytest.mark.parametrize("settings", [
    {"extra_body": [0.9]},
    {"extra_body": {"highlight": "hot"}},
])
def test_an_extra_body_that_is_not_an_object_is_a_bad_part(settings):
    with pytest.raises(BadPartError, match="extra_body"):
        pack.extra_bodies(settings, pack.CV_ROLES)


# --- the job ------------------------------------------------------------------
def test_the_posting_is_needed():
    with pytest.raises(MissingPartError, match="jd.txt"):
        pack.jd_text({})


def test_an_undecodable_byte_in_the_posting_is_replaced():
    assert pack.jd_text({"jd.txt": b"caf\xe9"}) == "caf�"


def test_the_analysis_is_optional():
    assert pack.job_analysis({}) is None
    assert pack.job_analysis({"analysis.json": b'{"job_title": "CTO"}'}) == {"job_title": "CTO"}


@pytest.mark.parametrize("names, action", [
    ({"jd.txt"}, "generate"),
    ({"jd.txt", "cv.tex"}, "render_tex"),
    ({"jd.txt", "cv.json"}, "render_document"),
    ({"jd.txt", "cv.json", "cv.tex"}, "render_document"),
    ({"jd.txt", "steps/key_points.json"}, "generate"),
])
def test_what_a_cv_request_does_is_decided_by_the_job(names, action):
    assert pack.job_action({name: b"" for name in names}) == action
