"""Intake: a posting you hand over yourself, analysed, then asked about."""

from __future__ import annotations

import pytest

from cv_client.stages.calls import Calls
from cv_client.stages.intake import Intake
from cv_client.stages.jobfolder import read_analysis
from cv_client.ui import Decision
from cv_client.workspace import day

from conftest import JD_TEXT, ScriptedConfirmer, analysis, make_job


def build(api, config, processor, *decisions):
    confirmer = ScriptedConfirmer(*decisions)
    intake = Intake(Calls(api), config, processor, confirmer, processor.console)
    return intake, confirmer


def approval(folder):
    return (folder / "approval_status.txt").read_text().strip()


# --- before a folder exists ---------------------------------------------------------
def test_junk_never_reaches_the_server(api, config, processor, workspace):
    intake, confirmer = build(api, config, processor)

    assert intake.take("too short to be a posting", "clipboard") is True
    assert api.calls == [], "the free check has to come first"
    assert confirmer.seen == []
    assert not any(workspace.working.iterdir())


def test_a_posting_the_server_says_no_to_is_not_asked_about(api, config, processor, workspace,
                                                            capsys):
    api.is_jd = False
    intake, confirmer = build(api, config, processor)

    assert intake.take(JD_TEXT, "clipboard") is True
    assert api.calls == ["detect"]
    assert confirmer.seen == []
    assert not any(workspace.working.iterdir())
    assert processor.failures == 0, "a rejection is not a failure"
    assert "[clipboard] ✗ not a job description" in capsys.readouterr().out


def test_a_failed_analysis_counts_and_says_which_request(api, config, processor, workspace,
                                                         capsys):
    api.fail_on = "analyze"
    intake, confirmer = build(api, config, processor)

    assert intake.take(JD_TEXT, "clipboard") is True
    assert processor.failures == 1, "the exit code says so"
    assert api.calls == ["detect", "analyze"]
    assert confirmer.seen == []
    assert not any(workspace.working.iterdir())
    out = capsys.readouterr().out
    assert "analyze failed" in out and "request id: failed-request" in out


@pytest.mark.parametrize("where", ["cv", "discarded"])
def test_a_posting_seen_before_is_not_asked_about(api, config, processor, workspace, capsys,
                                                  where):
    earlier = make_job(getattr(workspace, where) / day())
    intake, confirmer = build(api, config, processor)

    assert intake.take(JD_TEXT, "clipboard") is True
    assert confirmer.seen == []
    assert not any(workspace.working.iterdir())
    assert str(earlier) in capsys.readouterr().out


def test_a_posting_whose_cv_is_being_written_is_not_asked_about_twice(api, config, processor,
                                                                      capsys):
    intake, confirmer = build(api, config, processor, Decision(submit=True))
    intake.take(JD_TEXT, "clipboard")
    intake.take(JD_TEXT, "clipboard")

    assert len(confirmer.seen) == 1
    assert "already in progress" in capsys.readouterr().out


# --- the question -------------------------------------------------------------------
@pytest.mark.parametrize("should_apply", ["YES", "CHECK", "NO"])
def test_you_are_asked_whatever_should_apply_says(api, config, processor, should_apply):
    api.analysis = analysis(should_apply=should_apply)
    intake, confirmer = build(api, config, processor, Decision(submit=False))
    intake.take(JD_TEXT, "clipboard")
    assert [a.should_apply for a in confirmer.seen] == [should_apply]


def test_yes_queues_its_cv_and_keeps_your_answer(api, config, processor, workspace):
    intake, _ = build(api, config, processor, Decision(submit=True))

    assert intake.take(JD_TEXT, "clipboard") is True
    [folder] = workspace.working_jobs()
    assert approval(folder) == "APPROVED"
    assert (folder / "jd.txt").read_text() == JD_TEXT
    assert processor.generations.get_nowait() == folder
    assert api.calls == ["detect", "analyze"], "the CV is the CV writer's to write"
    log = (folder / "log.log").read_text()
    assert "analyzing the posting" in log and "queued for its CV" in log


def test_a_pasted_url_is_stored_in_the_analysis(api, config, processor, workspace):
    intake, _ = build(api, config, processor,
                      Decision(submit=True, url="https://jobs.example/42"))
    intake.take(JD_TEXT, "clipboard")
    [folder] = workspace.working_jobs()
    assert read_analysis(folder).posting_url == "https://jobs.example/42"


def test_saying_no_files_it_under_discarded(api, config, processor, workspace):
    intake, _ = build(api, config, processor, Decision(submit=False))

    assert intake.take(JD_TEXT, "clipboard") is True
    [folder] = workspace.discarded.glob("*/Acme_Corp_Head_of_IT")
    assert (folder / "analysis.json").is_file(), "the analysis is kept, it was paid for"
    assert approval(folder) == "DISCARDED"
    assert processor.generations.empty()
    assert api.calls == ["detect", "analyze"], "nothing expensive ran"


def test_quitting_leaves_it_waiting_for_the_next_start(api, config, processor, workspace):
    intake, _ = build(api, config, processor, Decision(submit=False, quit=True))

    assert intake.take(JD_TEXT, "clipboard") is False
    [folder] = workspace.working_jobs()
    assert approval(folder) == "PENDING"
    assert processor.generations.empty()
    assert not any(workspace.discarded.iterdir()), "quitting is not a no"


def test_the_question_holds_back_the_background_lines(api, config, processor, console, capsys):
    """A line from the CV thread must not scroll the analysis off the screen."""

    class Interrupted:
        def confirm(self, analysis):
            console.say("a line from the CV thread")
            print("the question")
            return Decision(submit=False)

    Intake(Calls(api), config, processor, Interrupted(), console).take(JD_TEXT, "clipboard")

    out = capsys.readouterr().out
    assert out.index("the question") < out.index("a line from the CV thread")


# --- an analysis you already have ------------------------------------------------------
def test_a_given_analysis_skips_detection_and_analysis(api, config, processor, workspace):
    intake, confirmer = build(api, config, processor, Decision(submit=True))

    intake.take(JD_TEXT, "posting.txt", analysis=analysis(match_percentage=64))

    assert api.calls == []
    assert len(confirmer.seen) == 1, "it is still asked about before spending"
    [folder] = workspace.working_jobs()
    assert read_analysis(folder).match_percentage == 64, "the analysis given, not a new one"


# --- a leftover ------------------------------------------------------------------------
def test_a_leftover_is_asked_about_like_a_new_posting(api, config, processor, workspace):
    folder = make_job(workspace.working, approval="PENDING")
    intake, confirmer = build(api, config, processor, Decision(submit=True))

    assert intake.review(folder) is True
    assert len(confirmer.seen) == 1
    assert processor.generations.get_nowait() == folder
    assert api.calls == []


def test_a_leftover_that_cannot_be_read_is_filed_with_why(api, config, processor, workspace):
    folder = make_job(workspace.working, approval="PENDING")
    (folder / "analysis.json").write_text("{}")
    intake, confirmer = build(api, config, processor)

    assert intake.review(folder) is True
    assert confirmer.seen == []
    [failed] = workspace.error.glob("*/*_Acme_Corp_Head_of_IT")
    assert "cannot be shown any more" in (failed / "log.log").read_text()


def test_a_leftover_gone_meanwhile_is_skipped(api, config, processor, workspace):
    intake, confirmer = build(api, config, processor)
    assert intake.review(workspace.working / "gone") is True
    assert confirmer.seen == []
