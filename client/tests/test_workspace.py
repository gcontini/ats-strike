"""The output tree: naming, moves, and what recovery finds."""

from __future__ import annotations

import re

import pytest

from cv_client.workspace import (
    Artifacts,
    Workspace,
    day,
    sanitize_name,
)


@pytest.fixture
def ws(tmp_path) -> Workspace:
    return Workspace(tmp_path / "out").ensure()


def loose(ws, name="26-09-28-16-13-16_posting.txt", text="a posting"):
    """A loose file in working/, as older clients left them."""
    path = ws.working / name
    path.write_text(text)
    return path


def test_ensure_builds_the_four_folders(ws):
    assert sorted(p.name for p in ws.root.iterdir()) == ["cv", "discarded", "error", "working"]


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


def test_error_keeps_an_existing_timestamp(ws):
    """A file dropped back from error/ keeps the time it first arrived."""
    stamped = loose(ws)
    failed = ws.to_error(stamped)
    assert failed.name == stamped.name, "the arrival time is the useful one"


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


def test_error_adds_a_timestamp_when_there_is_none(ws):
    job = ws.new_job("Acme", "Head of IT")
    failed = ws.to_error(job)
    assert re.fullmatch(r"\d{2}(-\d{2}){5}_Acme_Head_of_IT", failed.name)


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
    assert (artifacts.pdf, artifacts.tex, artifacts.document) == (
        "cv_jordan_rivera_head_of_it_ops.pdf", "cv_jordan_rivera_head_of_it_ops.tex",
        "cv_jordan_rivera_head_of_it_ops.json")
