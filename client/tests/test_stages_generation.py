"""Stage 3: a CV written, filed and recorded — or failed and kept."""

from __future__ import annotations

import json
from dataclasses import replace

import openpyxl
import pytest
from cv_contracts import Failure, Status

from cv_client.stages.generation import CvWriter, GenerationWorker
from cv_client.stages.terminal import Console, TaggedLog
from cv_client.stages.working import WorkingProcessor
from cv_client.tracking import build_tracker
from cv_client.workspace import Artifacts, day

from conftest import (
    JD_TEXT, KEY_POINTS, LOG_LINE, NAME, PDF, TEX, FakeApi, analysis, calls_for, document,
    make_server,
)

#: What the example candidate's CV for the Head of IT posting is called.
STEM = Artifacts(NAME, "Head of IT").stem


def yes_posting(processor):
    folder = processor.submit(JD_TEXT, analysis(should_apply="YES"),
                              TaggedLog(processor.console, "posting.txt"))
    assert processor.generations["v11"].get_nowait() == folder
    return folder


def run_one(processor, folder, lane="v11"):
    [server] = [s for s in processor.writer.config.lanes() if s.name == lane]
    GenerationWorker(processor, processor.writer, processor.console, server).process(folder)


def build(config, apis, workspace, console, *, track=True, new=False):
    writer = CvWriter(calls_for(config, apis), config, new=new)
    return WorkingProcessor(workspace, writer, build_tracker(workspace.root, track), console)


def delivered(workspace):
    return workspace.cv / day() / "Acme_Corp_Head_of_IT"


def row(workspace):
    sheet = openpyxl.load_workbook(workspace.root / "applications.xlsx").active
    headers = [c.value for c in sheet[1]]
    return dict(zip(headers, [c.value for c in sheet[2]]))


def test_a_cv_is_delivered_with_everything_needed_to_render_it_again(
        processor, workspace, api):
    folder = yes_posting(processor)
    run_one(processor, folder)

    done = delivered(workspace)
    assert sorted(p.name for p in done.iterdir()) == [
        "analysis.json", f"{STEM}.pdf", "jd.txt", "log.log", "v11"]
    assert sorted(p.relative_to(done / "v11").as_posix() for p in (done / "v11").rglob("*")
                  if p.is_file()) == [
        "candidate_signature.png", "cv.json", "cv.tex", "steps/key_points.json"]
    assert (done / f"{STEM}.pdf").read_bytes() == PDF
    assert (done / "v11" / "cv.tex").read_text() == TEX
    assert not folder.exists()
    assert api.calls == ["create_cv", "events", "acknowledge"]
    assert "v11: writing the CV" in (done / "log.log").read_text()


def test_a_generated_cv_gets_a_spreadsheet_row_pointing_at_its_pdf(processor, workspace):
    run_one(processor, yes_posting(processor))

    recorded = row(workspace)
    assert (recorded["company_name"], recorded["job_title"]) == ("Acme Corp", "Head of IT")
    assert recorded["cv_path"] == str(delivered(workspace) / f"{STEM}.pdf")


@pytest.mark.parametrize(
    "mode, expected, letter",
    [
        ("no", ["create_cv", "events", "acknowledge"], False),
        ("yes", ["create_cv", "events", "acknowledge", "letter"], True),
        ("letter_only", ["letter"], True),
    ],
)
def test_cover_letter_modes_decide_what_runs(api, config, workspace, console, mode, expected,
                                             letter):
    processor = build(replace(config, cover_letter=mode), {"v11": api}, workspace, console)
    run_one(processor, yes_posting(processor))

    done = delivered(workspace)
    assert api.calls == expected
    assert (done / "cover_letter.txt").is_file() is letter
    assert (done / f"{STEM}.pdf").is_file() is (mode != "letter_only")
    cv_path = row(workspace)["cv_path"]
    if mode == "letter_only":
        assert cv_path is None
    else:
        assert cv_path.endswith(f"{STEM}.pdf")


def test_the_letter_is_sent_the_job_with_its_analysis_and_its_log_kept(
        api, config, workspace, console):
    processor = build(replace(config, cover_letter="letter_only"), {"v11": api}, workspace,
                      console)
    run_one(processor, yes_posting(processor))

    assert sorted(api.seen_job) == ["analysis.json", "jd.txt"]
    assert (delivered(workspace) / "cover_letter.txt").read_text() == "Dear hiring manager."
    assert "  v11 2026-01-15T09:00:05 INFO    [run] Σ" in (
        delivered(workspace) / "log.log").read_text()


def test_only_the_default_writes_the_letter(config, servers_dir, workspace, console, server):
    v05 = make_server(servers_dir, "v05")
    apis = {"v11": FakeApi(), "v05": FakeApi()}
    config = replace(config, targets=(v05,), cover_letter="yes")
    processor = build(config, apis, workspace, console)
    folder = processor.submit(JD_TEXT, analysis(should_apply="YES"),
                              TaggedLog(console, "posting.txt"))
    for lane in ("v05", "v11"):
        processor.generations[lane].get_nowait()
        run_one(processor, folder, lane)

    assert apis["v05"].calls == ["create_cv", "events", "acknowledge"]
    assert apis["v11"].calls == ["letter"], "not a target: the letter only"
    done = delivered(workspace)
    assert [p.name for p in done.glob("*.pdf")] == [f"{STEM}_v05.pdf"]
    assert (done / "cover_letter.txt").is_file()


def test_the_server_s_settings_and_folder_are_what_is_sent(api, config, workspace, console,
                                                           server):
    settings = {"pages": 1, "extra_body": {"cv": {"temperature": 0.7}}}
    (server.folder / "sys_prompt_cv_body.txt").write_text("MY PROMPT")
    config = replace(config, targets=(replace(server, settings=settings),))
    processor = build(config, {"v11": api}, workspace, console)
    run_one(processor, yes_posting(processor))

    assert api.seen_settings == settings
    assert api.seen_pack["sys_prompt_cv_body.txt"] == b"MY PROMPT"
    assert "candidate_signature.png" in api.seen_pack
    assert json.loads(api.seen_pack["candidate_data.json"])["name"] == NAME


def test_each_steps_result_is_kept_under_the_server_s_steps(processor, workspace):
    run_one(processor, yes_posting(processor))

    steps = delivered(workspace) / "v11" / "steps"
    assert [p.name for p in steps.iterdir()] == ["key_points.json"]
    assert json.loads((steps / "key_points.json").read_text()) == KEY_POINTS


def test_the_stored_document_is_kept_exactly_as_it_arrived(processor, workspace):
    """One flat object, not read here at all: a server that learns to write
    one more field must not mean a client to upgrade first."""
    run_one(processor, yes_posting(processor))
    stored = json.loads((delivered(workspace) / "v11" / "cv.json").read_text())
    assert stored == document()


def test_a_failed_job_is_kept_under_error_with_what_the_server_said(processor, workspace, api):
    api.session = api.session[:3] + [
        ("store", api.session[8][1]),
        ("error", Failure(http_status=502,
                          error="model_output [cv.cv]: cv: no usable reply in 3 attempts")),
    ]
    folder = processor.submit(JD_TEXT, analysis(should_apply="CHECK"),
                              TaggedLog(processor.console, "posting.txt"))
    processor.approvals.get_nowait()
    processor.approve(folder)

    run_one(processor, processor.generations["v11"].get_nowait())

    [failed] = workspace.error.glob("*/Acme_Corp_Head_of_IT")
    assert failed.parent.name == day()
    assert sorted(p.name for p in failed.iterdir()) == [
        "analysis.json", "approval_status.txt", "jd.txt", "log.log"]
    log = (failed / "log.log").read_text()
    assert "model_output [cv.cv]: cv: no usable reply in 3 attempts" in log
    assert f"  v11 {LOG_LINE}" in log, "the server's own lines"
    assert "Σ 1 model call(s)" in log, "what the failed attempts cost"
    assert api.calls[-1] == "acknowledge", "dealt with: the server may drop it"
    assert processor.failures == 1
    assert not (workspace.root / "applications.xlsx").exists()


def test_a_job_the_server_refused_is_kept_under_error(processor, workspace, api):
    api.fail_on = "create_cv"
    run_one(processor, yes_posting(processor))

    [failed] = workspace.error.glob("*/Acme_Corp_Head_of_IT")
    log = (failed / "log.log").read_text()
    assert "create_cv failed" in log
    assert "request id: failed-request" in log
    assert processor.failures == 1


def test_a_broken_spreadsheet_costs_a_warning_and_not_the_cv(workspace, writer, console):
    class Broken:
        def record(self, job_dir, analysis, cv=None):
            raise RuntimeError("applications.xlsx has no date column")

    processor = WorkingProcessor(workspace, writer, Broken(), console)
    run_one(processor, yes_posting(processor))

    log = (delivered(workspace) / "log.log").read_text()
    assert "could not record this job in applications.xlsx" in log
    assert "has no date column" in log
    assert processor.failures == 0


def test_every_line_the_server_logged_is_in_the_job_log(processor, workspace):
    run_one(processor, yes_posting(processor))
    log = (delivered(workspace) / "log.log").read_text()
    assert f"  v11 {LOG_LINE}" in log
    assert "  v11 2026-01-15T09:00:05 INFO    [run] Σ 1 model call(s), 1.2s" in log


def test_a_folder_gone_from_the_queue_is_skipped(processor, api):
    folder = yes_posting(processor)
    processor.discard(folder)
    run_one(processor, folder)
    assert api.calls == []


def test_each_status_is_reported_and_log_lines_are_not_shown(processor, workspace, api,
                                                              capsys):
    """The wait is minutes long; a silent one is indistinguishable from a
    hung one. The log lines are one per model call: kept, not shown."""
    run_one(processor, yes_posting(processor))

    out = capsys.readouterr().out
    assert "   jd_evaluation" in out and "   profile_scoring" in out
    assert "tokens used=30" not in out, "that is what --verbose is for"
    assert LOG_LINE not in out


def test_verbose_shows_the_details_and_the_server_s_lines(api, config, workspace, capsys):
    api.session = [
        ("status", Status(status="profile_scoring", detail="12 key points, tokens used=99")),
        *api.session[2:],
    ]
    processor = build(replace(config, verbose=True), {"v11": api}, workspace,
                      Console(verbose=True))
    run_one(processor, yes_posting(processor))

    out = capsys.readouterr().out
    assert "tokens used=99" in out and "done, 1 model call, 1 page(s)" in out
    assert f"v11 {LOG_LINE}" in out


def test_tracking_can_be_turned_off(api, config, workspace, console):
    processor = build(config, {"v11": api}, workspace, console, track=False)
    run_one(processor, yes_posting(processor))
    assert not (workspace.root / "applications.xlsx").exists()


def test_a_run_the_server_already_has_is_replayed_and_filed(api, processor, workspace):
    """The same posting again — after a crash, a lost connection — costs
    nothing: the server answers with the session it already has."""
    api.reattached = True
    run_one(processor, yes_posting(processor))

    assert len(list(delivered(workspace).glob("cv_*.pdf"))) == 1
    assert "replaying it (--new starts over)" in (delivered(workspace) / "log.log").read_text()


def test_new_drops_the_run_the_server_has_and_writes_the_cv_again(
        api, config, workspace, console):
    api.acknowledge_errors = [404]                  # nothing there: fine
    processor = build(config, {"v11": api}, workspace, console, track=False, new=True)
    run_one(processor, yes_posting(processor))

    assert api.calls == ["acknowledge", "create_cv", "events", "acknowledge"]
    assert len(list(delivered(workspace).glob("cv_*.pdf"))) == 1


def test_drain_writes_everything_queued_then_returns(processor, workspace):
    """submit's way: the CV on the calling thread, and back when it is filed."""
    processor.submit(JD_TEXT, analysis(should_apply="YES", company_name="Alpha"),
                     TaggedLog(processor.console, "a.txt"))
    processor.submit(JD_TEXT, analysis(should_apply="YES", company_name="Beta"),
                     TaggedLog(processor.console, "b.txt"))

    [server] = processor.writer.config.lanes()
    GenerationWorker(processor, processor.writer, processor.console, server).drain()

    assert sorted(p.name for p in workspace.cv.glob("*/*")) == [
        "Alpha_Head_of_IT", "Beta_Head_of_IT"]
    assert processor.generations["v11"].empty()
    assert not any(workspace.working.iterdir())
