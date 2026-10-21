"""WorkingProcessor: where each folder goes, and what it writes on the way."""

from __future__ import annotations

import json

import pytest

from cv_client.stages.jobfolder import read_analysis
from cv_client.stages.terminal import TaggedLog
from cv_client.stages.working import WorkingProcessor
from cv_client.tracking import build_tracker
from cv_client.workspace import Artifacts, day

from conftest import EXAMPLE_CANDIDATE, JD_TEXT, analysis, document, make_job

#: What the example candidate's CV for the Head of IT posting is called.
STEM = Artifacts(json.loads((EXAMPLE_CANDIDATE / "candidate_data.json").read_text())["name"],
                 "Head of IT").stem


def submit(processor, **fields):
    log = TaggedLog(processor.console, "posting.txt")
    return processor.submit(JD_TEXT, analysis(**fields), log)


def names(folder):
    return sorted(p.name for p in folder.iterdir())


def queued(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


# --- a fresh posting ----------------------------------------------------------
def test_no_goes_straight_to_discarded(processor, workspace):
    submit(processor, should_apply="NO")

    [folder] = workspace.discarded.glob("*/Acme_Corp_Head_of_IT")
    assert folder.parent.name == day()
    assert names(folder) == ["analysis.json", "jd.txt", "log.log"]
    assert not any(workspace.working.iterdir())
    assert processor.generations.empty() and processor.approvals.empty()


def test_yes_is_queued_for_its_cv_and_marks_nothing(processor, workspace):
    folder = submit(processor, should_apply="YES")

    assert folder.parent == workspace.working
    assert names(folder) == ["analysis.json", "jd.txt", "log.log"]
    assert queued(processor.generations) == [folder]
    assert processor.approvals.empty()


def test_check_waits_for_you(processor):
    folder = submit(processor, should_apply="CHECK")
    assert queued(processor.approvals) == [folder]
    assert processor.generations.empty()


def test_with_yes_check_waits_in_working_and_is_not_queued(workspace, writer, console):
    processor = WorkingProcessor(workspace, writer, build_tracker(workspace.root, False),
                                 console, ask=False)
    folder = submit(processor, should_apply="CHECK")

    assert folder.is_dir()
    assert processor.approvals.empty() and processor.generations.empty()
    assert "waits in working/" in (folder / "log.log").read_text()


def test_the_log_keeps_what_the_inbox_did(processor, console):
    log = TaggedLog(console, "posting.txt")
    log.step("✓ job description (1800 chars)")
    folder = processor.submit(JD_TEXT, analysis(should_apply="YES"), log)

    text = (folder / "log.log").read_text()
    assert "job description (1800 chars)" in text
    assert "should_apply is YES" in text


# --- seen before ----------------------------------------------------------------
def previous(parent, **fields):
    folder = parent / "26-09-01" / "Acme_Corp_Head_of_IT"
    folder.mkdir(parents=True)
    data = {"company_name": "Acme Corp", "job_title": "Head of IT", **fields}
    (folder / "analysis.json").write_text(json.dumps(data), encoding="utf-8")
    return folder


@pytest.mark.parametrize("where, label", [("cv", "already applied"),
                                          ("discarded", "already discarded")])
def test_a_posting_seen_before_writes_nothing(processor, workspace, capsys, where, label):
    earlier = previous(getattr(workspace, where))

    assert submit(processor, should_apply="YES") is None
    assert not any(workspace.working.iterdir())
    assert processor.generations.empty()
    out = capsys.readouterr().out
    assert label in out and str(earlier) in out


def test_a_posting_still_in_flight_is_not_filed_twice(processor, workspace, capsys):
    first = submit(processor, should_apply="YES")
    assert submit(processor, should_apply="YES") is None
    assert workspace.working_jobs() == [first]
    assert "already in progress" in capsys.readouterr().out


def test_the_same_url_is_the_same_posting(processor, workspace):
    previous(workspace.cv, company_name="Other", job_title="Else",
             posting_url="https://jobs.example/42")
    assert submit(processor, should_apply="YES", posting_url="https://JOBS.example/42") is None


def test_a_name_clash_never_touches_the_first_folder(processor):
    """Different postings can sanitise to one folder name (titles are cut at
    40 characters); the second gets a suffix, the first is left alone."""
    title = "Head of " + "Infrastructure " * 4
    first = submit(processor, should_apply="YES", job_title=title + "Milan")
    second = submit(processor, should_apply="YES", job_title=title + "Rome")

    assert second.name == first.name + "_2"
    assert read_analysis(first).job_title.endswith("Milan")
    assert read_analysis(second).job_title.endswith("Rome")


# --- your answers ---------------------------------------------------------------
def test_approving_queues_it_and_keeps_your_answer(processor):
    folder = submit(processor, should_apply="CHECK")
    queued(processor.approvals)

    processor.approve(folder, url="https://jobs.example/42")

    assert (folder / "approval_status.txt").read_text().strip() == "APPROVED"
    assert read_analysis(folder).posting_url == "https://jobs.example/42"
    assert queued(processor.generations) == [folder]


def test_discarding_files_it_with_your_answer(processor, workspace):
    folder = submit(processor, should_apply="CHECK")
    target = processor.discard(folder)

    assert target.parent.parent == workspace.discarded
    assert (target / "approval_status.txt").read_text().strip() == "DISCARDED"
    assert not folder.exists()


def test_a_skipped_posting_comes_back_after_the_others(processor):
    a = submit(processor, should_apply="CHECK", company_name="Alpha")
    b = submit(processor, should_apply="CHECK", company_name="Beta")

    first = processor.approvals.get_nowait()
    processor.requeue(first)

    assert first == a
    assert queued(processor.approvals) == [b, a]


def test_after_close_nothing_is_queued_but_everything_is_kept(processor, workspace):
    processor.close()
    yes = submit(processor, should_apply="YES", company_name="Alpha")
    check = submit(processor, should_apply="CHECK", company_name="Beta")

    assert processor.generations.empty() and processor.approvals.empty()
    assert workspace.working_jobs() == [yes, check]


# --- a posting you are asked about (clipboard, submit) ------------------------------
def hold(processor, **fields):
    return processor.hold(JD_TEXT, analysis(**fields), TaggedLog(processor.console, "clipboard"))


@pytest.mark.parametrize("should_apply", ["YES", "CHECK", "NO"])
def test_a_held_posting_waits_for_you_whatever_its_analysis_says(processor, workspace,
                                                                 should_apply):
    folder = hold(processor, should_apply=should_apply)

    assert folder.parent == workspace.working
    assert names(folder) == ["analysis.json", "approval_status.txt", "jd.txt", "log.log"]
    assert (folder / "approval_status.txt").read_text().strip() == "PENDING"
    assert "waiting for your answer" in (folder / "log.log").read_text()
    assert processor.generations.empty() and processor.approvals.empty(), \
        "the caller asks at once; nothing is queued"


def test_a_held_posting_seen_before_writes_nothing(processor, workspace):
    previous(workspace.discarded)
    assert hold(processor, should_apply="YES") is None
    assert not any(workspace.working.iterdir())


def test_a_posting_left_unanswered_is_asked_about_again(processor, workspace):
    """Quit at the question, or stopped mid-question: never decided for you."""
    left = hold(processor, should_apply="YES")

    next_run = WorkingProcessor(workspace, processor.writer, processor.tracker,
                                processor.console)
    next_run.recover()

    assert queued(next_run.approvals) == [left]
    assert next_run.generations.empty(), "a YES is not written without your answer"


def test_approving_a_held_posting_replaces_pending(processor):
    folder = hold(processor)
    processor.approve(folder)
    assert (folder / "approval_status.txt").read_text().strip() == "APPROVED"
    assert queued(processor.generations) == [folder]


# --- what an earlier run left --------------------------------------------------
def test_recovery_follows_the_table(processor, workspace, api):
    working = workspace.working
    make_job(working, "A_no_analysis", with_analysis=False)
    yes = make_job(working, "B_yes", should_apply="YES")
    check = make_job(working, "C_check", should_apply="CHECK")
    make_job(working, "D_no", should_apply="NO")
    approved = make_job(working, "E_approved", approval="APPROVED")
    make_job(working, "F_discarded", approval="DISCARDED")
    make_job(working, "G_odd", approval="MAYBE")
    (working / "26-09-28-16-13-16_jd.txt").write_text(JD_TEXT)

    processor.recover()

    assert queued(processor.generations) == [yes, approved]
    assert queued(processor.approvals) == [check]
    assert sorted(p.name for p in workspace.discarded.glob("*/*")) == ["D_no", "F_discarded"]
    errors = [p.name for p in workspace.error.glob("*/*")]
    assert sorted(n[len("26-09-28-16-13-16_"):] for n in errors) == [
        "A_no_analysis", "G_odd", "jd.txt", "jd.txt.log"], errors
    assert "a loose file" in (workspace.error / day() / "26-09-28-16-13-16_jd.txt.log").read_text()
    assert api.calls == [], "nothing but a render ever costs a call at startup"
    assert processor.failures == 0, "rejections are not failures"


def test_a_leftover_holding_its_cv_is_rendered_and_delivered_without_a_row(
        processor, workspace, api):
    make_job(workspace.working, should_apply="CHECK",
             **{f"{STEM}.json": json.dumps(document())})

    processor.recover()

    assert api.calls == ["render"]
    assert api.seen_document == document()
    delivered = workspace.cv / day() / "Acme_Corp_Head_of_IT"
    assert (delivered / f"{STEM}.pdf").read_bytes() == b"%PDF-fake"
    assert (delivered / "candidate_signature.png").is_file()
    assert not (workspace.root / "applications.xlsx").exists(), "a render is not an application"


def test_a_leftover_with_only_its_tex_is_compiled_as_it_stands(processor, workspace, api):
    make_job(workspace.working, **{f"{STEM}.tex": r"\documentclass{article}"})
    processor.recover()
    assert api.calls == ["render"]
    assert api.seen_document is None, "the .tex was sent, not a document"


def test_a_failed_render_goes_to_error_with_its_cause(processor, workspace, api):
    api.fail_on = "render"
    make_job(workspace.working, **{f"{STEM}.json": json.dumps(document())})

    processor.recover()

    [failed] = workspace.error.glob("*/*_Acme_Corp_Head_of_IT")
    log = (failed / "log.log").read_text()
    assert "model_output" in log
    assert "request id: failed-request" in log
    assert processor.failures == 1


def test_a_file_failing_again_replaces_itself_and_its_log(processor, workspace):
    """The same posting dropped back and failing again: one file, one log, no ``_2``."""
    for attempt in ("first", "second"):
        entry = workspace.working / "26-09-28-16-13-16_jd.txt"
        entry.write_text(JD_TEXT)
        log = TaggedLog(processor.console, entry.name)
        log.step(f"✗ {attempt} attempt")
        processor.fail(entry, log)

    assert sorted(p.name for p in workspace.error.glob("*/*")) == [
        "26-09-28-16-13-16_jd.txt", "26-09-28-16-13-16_jd.txt.log"]
    text = (workspace.error / day() / "26-09-28-16-13-16_jd.txt.log").read_text()
    assert "second attempt" in text and "first attempt" not in text


def test_a_document_that_is_not_json_fails_without_a_call(processor, workspace, api):
    make_job(workspace.working, **{f"{STEM}.json": "{not json"})
    processor.recover()
    assert api.calls == []
    [failed] = workspace.error.glob("*/*_Acme_Corp_Head_of_IT")
    assert "not valid JSON" in (failed / "log.log").read_text()


def test_a_tex_that_is_not_text_fails_without_a_call(processor, workspace, api):
    job = make_job(workspace.working)
    (job / f"{STEM}.tex").write_bytes(b"\xff\xfe\x00latex")
    processor.recover()
    assert api.calls == []
    [failed] = workspace.error.glob("*/*_Acme_Corp_Head_of_IT")
    assert "not valid UTF-8 text" in (failed / "log.log").read_text()


# --- a folder dropped into the inbox --------------------------------------------
def test_a_dropped_folder_is_copied_whole_and_routed(processor, workspace, tmp_path):
    source = make_job(tmp_path / "in", "26-09-28-21-35-05_Acme", approval="APPROVED",
                      **{"log.log": "an earlier run\n"})

    folder = processor.submit_folder(source, TaggedLog(processor.console, source.name))

    assert folder.name == "Acme_Corp_Head_of_IT"
    assert queued(processor.generations) == [folder]
    assert (folder / "log.log").read_text().startswith("an earlier run\n")
    assert (folder / "approval_status.txt").is_file()
    assert source.is_dir(), "removing it from the inbox is the inbox's job"


def test_a_dropped_folder_holding_its_cv_is_rendered(processor, workspace, api, tmp_path):
    source = make_job(tmp_path / "in", **{f"{STEM}.json": json.dumps(document())})
    processor.submit_folder(source, TaggedLog(processor.console, source.name))
    assert api.calls == ["render"]
    assert (workspace.cv / day() / "Acme_Corp_Head_of_IT" / f"{STEM}.pdf").is_file()


def test_a_dropped_folder_seen_before_writes_nothing(processor, workspace, tmp_path):
    previous(workspace.cv)
    source = make_job(tmp_path / "in", should_apply="YES")
    assert processor.submit_folder(source, TaggedLog(processor.console, "x")) is None
    assert not any(workspace.working.iterdir())
