"""Where a job folder goes: every rule of the routing table, in its order."""

from __future__ import annotations

import pytest

from resumix_client.stages.jobfolder import (
    APPROVAL_FILENAME,
    classify,
    read_approval,
    render_source,
    route,
    write_approval,
)
from resumix_client.workspace import STEPS_DIRNAME

from conftest import analysis

JOB = {"jd.txt", "analysis.json", "log.log"}


@pytest.mark.parametrize(
    "names, should_apply, approval, expected",
    [
        # 1. no jd.txt or no valid analysis.json -> error, whatever else is there
        ({"analysis.json"}, "YES", None, "error"),
        ({"jd.txt"}, None, None, "error"),
        ({"analysis.json", "cv_Jordan_Rivera.json"}, "YES", "APPROVED", "error"),
        # 2. a CV to re-render wins over everything below it
        (JOB | {"cv_Jordan_Rivera.json"}, "CHECK", None, "render"),
        (JOB | {"cv_Jordan_Rivera.tex"}, "NO", None, "render"),
        (JOB | {"cv_Jordan_Rivera.json"}, "CHECK", "DISCARDED", "render"),
        (JOB | {"notes.JSON"}, "CHECK", None, "render"),
        # 3. nobody has decided yet: should_apply decides
        (JOB, "YES", None, "generate"),
        (JOB, "CHECK", None, "approve"),
        (JOB, "NO", None, "discard"),
        # 4-5. you decided
        (JOB | {APPROVAL_FILENAME}, "CHECK", "APPROVED", "generate"),
        (JOB | {APPROVAL_FILENAME}, "CHECK", "DISCARDED", "discard"),
        (JOB | {APPROVAL_FILENAME}, "CHECK", " approved\n", "generate"),
        # 6. asked and not answered yet: asked again, whatever should_apply says
        (JOB | {APPROVAL_FILENAME}, "YES", "PENDING\n", "approve"),
        (JOB | {APPROVAL_FILENAME}, "NO", "PENDING", "approve"),
        # 7. anything else in approval_status.txt
        (JOB | {APPROVAL_FILENAME}, "YES", "maybe", "error"),
        (JOB | {APPROVAL_FILENAME}, "YES", "", "error"),
    ],
)
def test_the_routing_table(names, should_apply, approval, expected):
    parsed = analysis(should_apply=should_apply) if should_apply else None
    where, why = route(names, parsed, approval)
    assert where == expected, why
    assert why, "every route says why"


def test_analysis_json_alone_is_not_a_cv_to_render():
    assert route(JOB, analysis(should_apply="YES"), None)[0] == "generate"


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


def test_a_cv_that_failed_half_way_is_written_again_not_rendered(tmp_path):
    """The steps it finished are JSON, but under steps/: not a CV to render."""
    folder = job_folder(tmp_path, jd__txt="a posting",
                        analysis__json=analysis(should_apply="YES").model_dump_json())
    (folder / STEPS_DIRNAME).mkdir()
    (folder / STEPS_DIRNAME / "key_points.json").write_text("[]")
    assert classify(folder)[0] == "generate"
    assert render_source(folder) is None


def test_a_folder_holding_only_a_subfolder_is_an_error(tmp_path):
    folder = job_folder(tmp_path)
    (folder / "jd.txt").mkdir()
    assert classify(folder)[0] == "error"


def test_the_document_is_rendered_before_the_tex(tmp_path):
    folder = job_folder(tmp_path, jd__txt="x", analysis__json="{}",
                        cv_Jordan_Rivera__tex="tex", cv_Jordan_Rivera__json="{}")
    assert render_source(folder).name == "cv_Jordan_Rivera.json"
    (folder / "cv_Jordan_Rivera.json").unlink()
    assert render_source(folder).name == "cv_Jordan_Rivera.tex"
    (folder / "cv_Jordan_Rivera.tex").unlink()
    assert render_source(folder) is None
