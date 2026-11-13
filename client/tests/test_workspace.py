"""The output tree: naming, moves, and what recovery finds."""

from __future__ import annotations

import pytest

from cv_contracts import StoreItem

from cv_client.joblog import JobLog
from cv_client.workspace import (
    Artifacts,
    JobFiles,
    Workspace,
    day,
    sanitize_name,
)

from conftest import NAME, PDF, make_job, make_server


@pytest.fixture
def ws(tmp_path) -> Workspace:
    return Workspace(tmp_path / "out").ensure()


def loose(ws, name="26-09-28-16-13-16_posting.txt", text="a posting"):
    """A loose file in working/, as older clients left them."""
    path = ws.working / name
    path.write_text(text)
    return path


def test_ensure_builds_the_four_folders(ws):
    assert sorted(p.name for p in ws.root.iterdir()) == ["CV", "discarded", "error", "working"]


def test_delivery_goes_into_a_daily_folder(ws):
    job = ws.new_job("Acme Corp", "Head of IT")
    delivered = ws.deliver(job)
    assert delivered.parent.name == day()
    assert delivered.parent.parent == ws.cv
    assert delivered.name == "Acme_Corp_Head_of_IT"


def test_a_second_run_of_the_same_job_does_not_overwrite_the_first(ws):
    first = ws.deliver(ws.new_job("Acme", "Head of IT"))
    second = ws.deliver(ws.new_job("Acme", "Head of IT"))
    assert first.exists() and second.exists()
    assert first != second


def test_a_new_job_never_replaces_one_in_flight(ws):
    """A CV is written while the next posting is filed: a clash of names must
    not delete the folder that got there first."""
    first = ws.new_job("Acme Corp", "Head of IT")
    (first / "jd.txt").write_text("the first posting")
    second = ws.new_job("Acme Corp", "Head of IT")

    assert first.name == "Acme_Corp_Head_of_IT"
    assert second.name == "Acme_Corp_Head_of_IT_2"
    assert (first / "jd.txt").read_text() == "the first posting"
    assert second.is_dir() and not any(second.iterdir())


def test_working_jobs_lists_only_analysed_folders(ws):
    analysed = ws.new_job("Acme", "Head of IT")
    (analysed / "analysis.json").write_text("{}")
    ws.new_job("Other", "Job")  # no analysis.json yet
    loose(ws)

    assert ws.working_jobs() == [analysed]


def test_error_goes_into_a_daily_folder(ws):
    failed = ws.to_error(loose(ws))
    assert failed.parent.name == day()
    assert failed.parent.parent == ws.error


def test_error_keeps_the_name_it_has(ws):
    posting = loose(ws)
    failed = ws.to_error(posting)
    assert failed.name == posting.name


def test_error_replaces_a_file_of_the_same_name(ws):
    """The same posting failing again: one copy, the latest, and no ``_2``."""
    ws.to_error(loose(ws, text="first try"))
    failed = ws.to_error(loose(ws, text="second try"))
    assert [p.name for p in failed.parent.iterdir()] == [failed.name]
    assert failed.read_text() == "second try"


def test_error_replaces_a_folder_of_the_same_name(ws):
    stamped = "26-09-28-16-13-16_Acme_Head_of_IT"
    first = ws.working / stamped
    first.mkdir()
    (first / "old.txt").write_text("stale")
    ws.to_error(first)
    second = ws.working / stamped
    second.mkdir()
    (second / "jd.txt").write_text("fresh")

    failed = ws.to_error(second)

    assert [p.name for p in failed.parent.iterdir()] == [stamped]
    assert sorted(p.name for p in failed.iterdir()) == ["jd.txt"], "not nested, not merged"


def test_error_adds_no_timestamp(ws):
    job = ws.new_job("Acme", "Head of IT")
    failed = ws.to_error(job)
    assert failed.name == "Acme_Head_of_IT"


def test_pending_separates_loose_files_from_job_folders(ws):
    loose(ws)
    ws.new_job("Acme", "Head of IT")
    files, folders = ws.pending()
    assert [f.name for f in folders] == ["Acme_Head_of_IT"]
    assert len(files) == 1


def test_pending_ignores_hidden_entries(ws):
    """They are no run's leftovers, and resuming would never clear them: they
    must not bring the question back at every start."""
    loose(ws, ".DS_Store")
    (ws.working / ".cache").mkdir()
    assert ws.pending() == ([], [])


def test_clean_empties_the_working_folder(ws):
    loose(ws)
    ws.new_job("Acme", "Head of IT")
    assert ws.clean() == 2
    assert ws.pending() == ([], [])


@pytest.mark.parametrize(
    "raw, expected",
    [("Acme Corp.", "Acme_Corp"), ("  ", "unknown"), ("Head of IT / Ops", "Head_of_IT_Ops")],
)
def test_names_are_made_filesystem_safe(raw, expected):
    assert sanitize_name(raw) == expected


def test_artifacts_are_named_after_the_candidate_and_the_job():
    artifacts = Artifacts("Jordan Rivera", "Head of IT / Ops")
    assert artifacts.stem == "cv_jordan_rivera_head_of_it_ops"
    assert artifacts.pdf() == "cv_jordan_rivera_head_of_it_ops.pdf"
    assert artifacts.pdf("v05") == "cv_jordan_rivera_head_of_it_ops_v05.pdf"


# --- one server's files in a job folder -------------------------------------------
STEM = Artifacts(NAME, "Head of IT").stem


@pytest.fixture
def job(tmp_path):
    return make_job(tmp_path / "working")


@pytest.fixture
def v11(tmp_path):
    return make_server(tmp_path / "data", "v11", default=True)


@pytest.fixture
def v05(tmp_path):
    return make_server(tmp_path / "data", "v05")


def item(location, type, name="", content="x"):
    return StoreItem.from_content(location, type, name, content)


def test_a_log_line_goes_into_the_job_log_under_the_server_s_name(job, v05):
    log = JobLog()
    assert JobFiles(job, v05).store(item("CV", "log", content="a line"), log) is None
    assert log.lines == ["  v05 a line"]


def test_a_step_goes_into_the_server_s_own_subfolder(job, v05):
    target = JobFiles(job, v05).store(item("CV", "step", "steps/key_points.json", "[]"),
                                      JobLog())
    assert target == job / "v05" / "steps" / "key_points.json"
    assert target.read_text() == "[]"


def test_the_default_server_s_pdf_has_no_suffix(job, v11):
    log = JobLog()
    target = JobFiles(job, v11).store(item("CV", "cv", "cv.pdf", PDF), log)
    assert target == job / f"{STEM}.pdf"
    assert target.read_bytes() == PDF
    assert any(f"CV Path: {STEM}.pdf" in line for line in log.lines)


def test_any_other_server_s_pdf_ends_in_its_name(job, v05):
    target = JobFiles(job, v05).store(item("CV", "cv", "whatever.pdf", PDF), JobLog())
    assert target == job / f"{STEM}_v05.pdf"


def test_after_the_pdf_the_pack_s_images_go_beside_the_tex(job, v11):
    (v11.folder / "logo.jpeg").write_bytes(b"logo")
    (v11.folder / "cache").mkdir()
    (v11.folder / "cache" / "deep.png").write_bytes(b"not top level")
    JobFiles(job, v11).store(item("CV", "cv", "cv.pdf", PDF), JobLog())
    assert sorted(p.name for p in (job / "v11").iterdir()) == [
        "candidate_signature.png", "logo.jpeg"]


def test_a_cache_step_goes_into_the_server_folder(job, v05):
    target = JobFiles(job, v05).store(item("CACHE", "step", "steps/pool.json", "[]"),
                                      JobLog())
    assert target == v05.folder / "cache" / "steps" / "pool.json"


def test_a_cache_log_line_is_appended_to_the_cache_log(job, v05):
    files = JobFiles(job, v05)
    files.store(item("CACHE", "log", content="one"), JobLog())
    files.store(item("CACHE", "log", content="two"), JobLog())
    assert (v05.folder / "cache" / "log.log").read_text() == "one\ntwo\n"


@pytest.mark.parametrize("location, type", [("CV", "audio"), ("ELSEWHERE", "step"),
                                            ("CACHE", "cv")])
def test_anything_else_is_skipped_with_a_line(job, v05, location, type):
    log = JobLog()
    assert JobFiles(job, v05).store(item(location, type, "a.json"), log) is None
    assert "skipped" in log.lines[0] and f"{location}/{type}" in log.lines[0]
    assert sorted(p.name for p in job.iterdir()) == ["analysis.json", "jd.txt"]


@pytest.mark.parametrize("name", ["../escape.json", "steps/../../escape.json",
                                  "/etc/passwd", "C:/x.json", "a\\b.json", ""])
def test_a_name_that_would_leave_its_folder_is_skipped(job, v05, name):
    log = JobLog()
    assert JobFiles(job, v05).store(item("CV", "step", name), log) is None
    assert "skipped" in log.lines[0]
    assert not (job / "v05").exists()


def test_what_a_server_is_sent_is_the_posting_its_analysis_and_its_own_files(job, v05):
    (job / "v05" / "steps").mkdir(parents=True)
    (job / "v05" / "cv.json").write_text("{}")
    (job / "v05" / "steps" / "key_points.json").write_text("[]")
    (job / "v11").mkdir()
    (job / "v11" / "cv.json").write_text("not v05's")
    (job / "log.log").write_text("not sent")

    assert sorted(JobFiles(job, v05).outgoing()) == [
        "analysis.json", "cv.json", "jd.txt", "steps/key_points.json"]


def test_a_bare_posting_is_sent_as_jd_txt_alone(tmp_path, v05):
    job = make_job(tmp_path / "working", with_analysis=False)
    assert list(JobFiles(job, v05).outgoing()) == ["jd.txt"]
