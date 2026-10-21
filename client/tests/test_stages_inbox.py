"""Stage 1: what the inbox does with each thing dropped into it."""

from __future__ import annotations

import threading

import pytest

from cv_client.stages.calls import Calls
from cv_client.stages.inbox import InputProcessor
from cv_client.workspace import day

from conftest import JD_TEXT, analysis, make_job


@pytest.fixture
def inbox(tmp_path):
    folder = tmp_path / "in"
    folder.mkdir()
    return folder


@pytest.fixture
def reader(inbox, api, config, processor, console):
    return InputProcessor(inbox, Calls(api), config, processor, console,
                          poll_seconds=0, settle_seconds=0)


def scan(reader, stop=None):
    reader.scan(stop or threading.Event())


def filed(workspace, name):
    """The one entry under error/ that ends in ``name``."""
    [entry] = workspace.error.glob(f"*/*_{name}")
    assert entry.parent.name == day()
    return entry


def test_a_posting_is_analysed_handed_over_then_deleted(reader, inbox, workspace, processor,
                                                         api):
    (inbox / "posting.txt").write_text(JD_TEXT)
    scan(reader)

    assert api.calls == ["detect", "analyze"]
    [folder] = workspace.working_jobs()
    assert (folder / "jd.txt").read_text() == JD_TEXT
    assert processor.approvals.get_nowait() == folder, "the fake's analysis says CHECK"
    assert not any(inbox.iterdir())


def test_an_upper_case_suffix_is_still_a_posting(reader, inbox, api):
    (inbox / "POSTING.TXT").write_text(JD_TEXT)
    scan(reader)
    assert api.calls == ["detect", "analyze"]


def test_junk_never_reaches_the_server(reader, inbox, workspace, api):
    (inbox / "short.txt").write_text("too short to be a posting")
    scan(reader)

    assert api.calls == []
    assert filed(workspace, "short.txt").is_file()
    assert "nothing sent" in filed(workspace, "short.txt.log").read_text()
    assert not any(inbox.iterdir())


def test_a_posting_the_server_says_no_to_is_rejected(reader, inbox, workspace, api, processor):
    api.is_jd = False
    (inbox / "posting.txt").write_text(JD_TEXT)
    scan(reader)

    assert api.calls == ["detect"]
    assert "not a job description" in filed(workspace, "posting.txt.log").read_text()
    assert processor.failures == 0, "a rejection is not a failure"


def test_any_other_file_goes_to_error_without_a_call(reader, inbox, workspace, api):
    (inbox / "posting.pdf").write_bytes(b"%PDF-1.7")
    scan(reader)

    assert api.calls == []
    assert filed(workspace, "posting.pdf").read_bytes() == b"%PDF-1.7"
    assert "not a .txt file" in filed(workspace, "posting.pdf.log").read_text()


def test_a_failed_analysis_is_filed_with_its_cause(reader, inbox, workspace, api, processor):
    api.fail_on = "analyze"
    (inbox / "posting.txt").write_text(JD_TEXT)
    scan(reader)

    log = filed(workspace, "posting.txt.log").read_text()
    assert "analyze failed" in log
    assert "request id: failed-request" in log
    assert processor.failures == 1
    assert not any(workspace.working.iterdir())


def test_a_posting_seen_before_is_still_removed(reader, inbox, workspace, capsys):
    (inbox / "first.txt").write_text(JD_TEXT)
    (inbox / "second.txt").write_text(JD_TEXT)
    scan(reader)

    assert len(workspace.working_jobs()) == 1
    assert not any(inbox.iterdir())
    assert "already in progress" in capsys.readouterr().out


@pytest.mark.parametrize("where", ["cv", "discarded"])
def test_a_known_job_posting_url_is_deleted_without_a_call(reader, inbox, workspace, api,
                                                           capsys, where):
    earlier = make_job(getattr(workspace, where) / day())
    (earlier / "analysis.json").write_text(analysis(
        posting_url="https://it.linkedin.com/jobs/view/4470889642/?refId=x").model_dump_json())
    (inbox / "posting.txt").write_text(
        "JOB_POSTING: https://www.linkedin.com/jobs/view/4470889642/\n" + JD_TEXT)
    scan(reader)

    assert api.calls == []
    assert not any(inbox.iterdir())
    assert not any(workspace.working.iterdir())
    out = capsys.readouterr().out
    assert f"already {'applied' if where == 'cv' else 'discarded'}" in out
    assert "dropped" in out


def test_a_known_job_posting_url_in_progress_is_deleted(reader, inbox, workspace, api):
    (inbox / "first.txt").write_text(
        "JOB_POSTING: https://www.linkedin.com/jobs/view/4470889642/\n" + JD_TEXT)
    scan(reader)
    [folder] = workspace.working_jobs()
    (folder / "analysis.json").write_text(analysis(
        posting_url="https://www.linkedin.com/jobs/search/?currentJobId=4470889642").model_dump_json())

    (inbox / "second.txt").write_text(
        "JOB_POSTING: https://www.linkedin.com/jobs/view/4470889642\n" + JD_TEXT)
    api.calls.clear()
    scan(reader)

    assert api.calls == []
    assert not any(inbox.iterdir())


def test_an_unknown_job_posting_url_is_analysed(reader, inbox, workspace, api):
    make_job(workspace.cv / day())
    (inbox / "posting.txt").write_text(
        "JOB_POSTING: https://www.linkedin.com/jobs/view/4470889642/\n" + JD_TEXT)
    scan(reader)

    assert api.calls == ["detect", "analyze"]


def test_an_analysed_folder_is_handed_over_whole_then_removed(reader, inbox, workspace,
                                                              processor, api):
    make_job(inbox, "old_job", should_apply="YES", **{"log.log": "an earlier run\n"})
    scan(reader)

    assert api.calls == [], "already analysed"
    [folder] = workspace.working_jobs()
    assert processor.generations.get_nowait() == folder
    assert (folder / "log.log").read_text().startswith("an earlier run")
    assert not any(inbox.iterdir())


def test_a_folder_without_jd_txt_goes_to_error_with_why_inside(reader, inbox, workspace, api):
    job = make_job(inbox, "old_job")
    (job / "jd.txt").rename(job / "posting.txt")
    scan(reader)

    assert api.calls == []
    assert "no jd.txt" in (filed(workspace, "old_job") / "log.log").read_text()
    assert not any(inbox.iterdir())


def test_a_folder_with_a_bad_analysis_goes_to_error(reader, inbox, workspace):
    job = make_job(inbox, "old_job")
    (job / "analysis.json").write_text("{}")
    scan(reader)
    assert "analysis.json is not valid" in (filed(workspace, "old_job") / "log.log").read_text()


def test_hidden_entries_are_left_alone(reader, inbox, workspace, api):
    """A dot folder is one ``cv analysis`` is still writing."""
    (inbox / ".posting.txt").write_text(JD_TEXT)
    make_job(inbox, ".Acme_Corp_Head_of_IT")
    scan(reader)
    assert api.calls == []
    assert workspace.working_jobs() == []
    assert (inbox / ".posting.txt").exists()
    assert (inbox / ".Acme_Corp_Head_of_IT" / "analysis.json").exists()


def test_a_file_still_being_written_waits(reader, inbox, api, monkeypatch):
    sizes = iter(range(100))
    monkeypatch.setattr("cv_client.stages.inbox._footprint",
                        lambda path: (1, next(sizes)))
    (inbox / "posting.txt").write_text(JD_TEXT)
    scan(reader)
    assert api.calls == []
    assert (inbox / "posting.txt").exists()


def test_stop_leaves_the_rest_for_later(reader, inbox, processor, workspace):
    """q: the entry in hand is finished, the rest stays in the inbox."""
    stop = threading.Event()
    handed_over = processor.submit

    def submit_then_stop(*args, **kwargs):
        stop.set()
        return handed_over(*args, **kwargs)

    processor.submit = submit_then_stop
    (inbox / "a.txt").write_text(JD_TEXT)
    (inbox / "b.txt").write_text(JD_TEXT.replace("Head of IT", "CTO"))
    scan(reader, stop)

    assert len(workspace.working_jobs()) == 1
    assert sorted(p.name for p in inbox.iterdir()) == ["b.txt"]
