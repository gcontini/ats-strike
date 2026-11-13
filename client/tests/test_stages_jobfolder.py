"""Where a job folder goes: every rule of the routing table, in its order."""

from __future__ import annotations

import json

import pytest

from cv_client.stages.jobfolder import (
    APPROVAL_FILENAME,
    classify,
    read_analysis,
    read_approval,
    route,
    write_analysis,
    write_approval,
)

from conftest import analysis

JOB = {"jd.txt", "analysis.json", "log.log"}


@pytest.mark.parametrize(
    "names, should_apply, approval, expected",
    [
        # 1. no jd.txt or no valid analysis.json -> error, whatever else is there
        ({"analysis.json"}, "YES", None, "error"),
        ({"jd.txt"}, None, None, "error"),
        ({"analysis.json", "cv_Jordan_Rivera.json"}, "YES", "APPROVED", "error"),
        # 2. nobody has decided yet: should_apply decides, whatever CV it holds
        (JOB, "YES", None, "generate"),
        (JOB, "CHECK", None, "approve"),
        (JOB, "NO", None, "discard"),
        (JOB | {"cv_jordan_rivera_head_of_it.pdf"}, "YES", None, "generate"),
        # 3-4. you decided
        (JOB | {APPROVAL_FILENAME}, "CHECK", "APPROVED", "generate"),
        (JOB | {APPROVAL_FILENAME}, "CHECK", "DISCARDED", "discard"),
        (JOB | {APPROVAL_FILENAME}, "CHECK", " approved\n", "generate"),
        # 5. asked and not answered yet: asked again, whatever should_apply says
        (JOB | {APPROVAL_FILENAME}, "YES", "PENDING\n", "approve"),
        (JOB | {APPROVAL_FILENAME}, "NO", "PENDING", "approve"),
        # 6. anything else in approval_status.txt
        (JOB | {APPROVAL_FILENAME}, "YES", "maybe", "error"),
        (JOB | {APPROVAL_FILENAME}, "YES", "", "error"),
    ],
)
def test_the_routing_table(names, should_apply, approval, expected):
    parsed = analysis(should_apply=should_apply) if should_apply else None
    where, why = route(names, parsed, approval)
    assert where == expected, why
    assert why, "every route says why"


def job_folder(tmp_path, **files):
    folder = tmp_path / "job"
    folder.mkdir()
    for name, content in files.items():
        (folder / name.replace("__", ".")).write_text(content, encoding="utf-8")
    return folder


def test_classify_reads_the_folder(tmp_path):
    folder = job_folder(tmp_path, jd__txt="a posting",
                        analysis__json=analysis(should_apply="CHECK").model_dump_json())
    assert classify(folder)[0] == "approve"
    write_approval(folder, "APPROVED")
    assert classify(folder)[0] == "generate"
    assert read_approval(folder) == "APPROVED\n"


def test_an_analysis_that_does_not_validate_is_an_error_that_says_so(tmp_path):
    folder = job_folder(tmp_path, jd__txt="a posting", analysis__json="{}")
    where, why = classify(folder)
    assert where == "error"
    assert "analysis.json is not valid" in why


def test_a_folder_holding_its_cv_is_written_like_any_other(tmp_path):
    """Each server is sent what it stored, and renders it: not the client's call."""
    folder = job_folder(tmp_path, jd__txt="a posting",
                        analysis__json=analysis(should_apply="YES").model_dump_json())
    (folder / "v11").mkdir()
    (folder / "v11" / "cv.json").write_text("{}")
    assert classify(folder)[0] == "generate"


def test_a_folder_holding_only_a_subfolder_is_an_error(tmp_path):
    folder = job_folder(tmp_path)
    (folder / "jd.txt").mkdir()
    assert classify(folder)[0] == "error"


def test_an_analysis_keeps_what_the_client_does_not_know_and_adds_nothing(tmp_path):
    """Another server's analysis has its own fields; writing it back loses none."""
    folder = job_folder(tmp_path, analysis__json=json.dumps({
        "job_title": "Head of IT", "company_name": "Acme", "should_apply": "YES",
        "company_evaluation": "a 0.5 field"}))
    parsed = read_analysis(folder)
    parsed.posting_url = "https://jobs.example/1"
    write_analysis(folder, parsed)

    assert json.loads((folder / "analysis.json").read_text()) == {
        "job_title": "Head of IT", "company_name": "Acme", "should_apply": "YES",
        "company_evaluation": "a 0.5 field", "posting_url": "https://jobs.example/1"}
