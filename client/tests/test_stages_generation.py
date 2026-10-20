"""Stage 3: a CV written, filed and recorded — or failed and kept."""

from __future__ import annotations

import json
from dataclasses import replace

import openpyxl
import pytest
from resumix_contracts import CVFailure, CVStatus, TokenUsage

from resumix_client.stages.calls import Calls
from resumix_client.stages.generation import CvWriter, GenerationWorker
from resumix_client.stages.terminal import TaggedLog
from resumix_client.stages.working import WorkingProcessor
from resumix_client.tracking import build_tracker
from resumix_client.workspace import Artifacts, day

from conftest import EXAMPLE_CANDIDATE, JD_TEXT, KEY_POINTS, analysis

#: The example candidate, and what their CV for the Head of IT posting is called.
NAME = json.loads((EXAMPLE_CANDIDATE / "candidate_data.json").read_text())["name"]
STEM = Artifacts(NAME, "Head of IT").stem


def yes_posting(processor):
    folder = processor.submit(JD_TEXT, analysis(should_apply="YES"),
                              TaggedLog(processor.console, "posting.txt"))
    assert processor.generations.get_nowait() == folder
    return folder


def run_one(processor, writer, folder):
    GenerationWorker(processor, writer, processor.console).process(folder)


def row(workspace):
    sheet = openpyxl.load_workbook(workspace.root / "applications.xlsx").active
    headers = [c.value for c in sheet[1]]
    return dict(zip(headers, [c.value for c in sheet[2]]))


def test_a_cv_is_delivered_with_everything_needed_to_render_it_again(
        processor, writer, workspace, api):
    folder = yes_posting(processor)
    run_one(processor, writer, folder)

    delivered = workspace.cv / day() / "Acme_Corp_Head_of_IT"
    assert sorted(p.name for p in delivered.iterdir()) == [
        "analysis.json", "candidate_signature.png", f"{STEM}.json", f"{STEM}.pdf", f"{STEM}.tex",
        "jd.txt", "log.log", "steps",
    ]
    assert not folder.exists()
    assert api.calls == ["create_cv", "events", "acknowledge"]
    assert "writing the CV" in (delivered / "log.log").read_text()


def test_a_generated_cv_gets_a_spreadsheet_row_pointing_at_its_pdf(processor, writer, workspace):
    run_one(processor, writer, yes_posting(processor))

    recorded = row(workspace)
    assert (recorded["company_name"], recorded["job_title"]) == ("Acme Corp", "Head of IT")
    assert recorded["cv_path"] == str(workspace.cv / day() / "Acme_Corp_Head_of_IT"
                                      / f"{STEM}.pdf")


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
    writer = CvWriter(Calls(api), replace(config, cover_letter=mode))
    processor = WorkingProcessor(workspace, writer, build_tracker(workspace.root, True), console)
    run_one(processor, writer, yes_posting(processor))

    delivered = workspace.cv / day() / "Acme_Corp_Head_of_IT"
    assert api.calls == expected
    assert (delivered / "cover_letter.txt").is_file() is letter
    assert (delivered / f"{STEM}.pdf").is_file() is (mode != "letter_only")
    cv_path = row(workspace)["cv_path"]
    if mode == "letter_only":
        assert cv_path is None
    else:
        assert cv_path.endswith(f"{STEM}.pdf")


def test_your_extra_bodies_go_to_the_cv_and_the_letter(api, config, workspace, console):
    bodies = {"cv": {"temperature": 0.7}, "letter": {"top_p": 0.9}}
    writer = CvWriter(Calls(api), replace(config, cover_letter="yes", extra_body=bodies))
    processor = WorkingProcessor(workspace, writer, build_tracker(workspace.root, True), console)
    run_one(processor, writer, yes_posting(processor))

    assert api.seen_extra_body == bodies
    assert api.seen_letter_extra_body == bodies


def test_each_steps_result_is_kept_under_steps(processor, writer, workspace):
    """What the server handed over on the way, beside the CV — in a folder, so
    no file of it can be taken for the CV."""
    run_one(processor, writer, yes_posting(processor))

    steps = workspace.cv / day() / "Acme_Corp_Head_of_IT" / "steps"
    assert [p.name for p in steps.iterdir()] == ["key_points.json"]
    assert json.loads((steps / "key_points.json").read_text()) == KEY_POINTS


def test_a_failed_job_is_kept_under_error_with_what_the_server_said(
        processor, writer, workspace, api):
    api.session = api.session[:3] + [("error", CVFailure(
        http_status=502, error="model_output [cv.cv]: cv: no usable reply in 3 attempts",
        total=TokenUsage(calls=4, input_tokens=40, output_tokens=80),
    ))]
    folder = processor.submit(JD_TEXT, analysis(should_apply="CHECK"),
                              TaggedLog(processor.console, "posting.txt"))
    processor.approvals.get_nowait()
    processor.approve(folder)

    run_one(processor, writer, processor.generations.get_nowait())

    [failed] = workspace.error.glob("*/*_Acme_Corp_Head_of_IT")
    assert failed.parent.name == day()
    assert sorted(p.name for p in failed.iterdir()) == [
        "analysis.json", "approval_status.txt", "jd.txt", "log.log"]
    log = (failed / "log.log").read_text()
    assert "model_output [cv.cv]: cv: no usable reply in 3 attempts" in log
    assert "[cv.jd_evaluation] ⏱ [cv] gpt-fake 1.2s" in log, "the server's own lines"
    assert "Σ 4 model call(s)" in log, "what the failed attempts cost"
    assert api.calls[-1] == "acknowledge", "dealt with: the server may drop it"
    assert processor.failures == 1
    assert not (workspace.root / "applications.xlsx").exists()


def test_a_job_the_server_refused_is_kept_under_error(processor, writer, workspace, api):
    api.fail_on = "create_cv"
    run_one(processor, writer, yes_posting(processor))

    [failed] = workspace.error.glob("*/*_Acme_Corp_Head_of_IT")
    log = (failed / "log.log").read_text()
    assert "create_cv failed" in log
    assert "request id: failed-request" in log
    assert processor.failures == 1


def test_a_broken_spreadsheet_costs_a_warning_and_not_the_cv(workspace, writer, console):
    class Broken:
        def record(self, job_dir, analysis, cv=None):
            raise RuntimeError("applications.xlsx has no date column")

    processor = WorkingProcessor(workspace, writer, Broken(), console)
    run_one(processor, writer, yes_posting(processor))

    delivered = workspace.cv / day() / "Acme_Corp_Head_of_IT"
    log = (delivered / "log.log").read_text()
    assert "could not record this job in applications.xlsx" in log
    assert "has no date column" in log
    assert processor.failures == 0


def test_every_line_the_server_logged_is_in_the_job_log(processor, writer, workspace):
    run_one(processor, writer, yes_posting(processor))

    log = (workspace.cv / day() / "Acme_Corp_Head_of_IT" / "log.log").read_text()
    assert "INFO    [cv.jd_evaluation] ⏱ [cv] gpt-fake 1.2s | input=10" in log


def test_a_folder_gone_from_the_queue_is_skipped(processor, writer, workspace, api):
    folder = yes_posting(processor)
    processor.discard(folder)
    run_one(processor, writer, folder)
    assert api.calls == []


def test_the_stored_document_is_the_reply_kept_exactly_as_it_arrived(
        processor, writer, workspace, api):
    """One flat object, not read here at all: a server that learns to write
    one more field must not mean a client to upgrade first."""
    run_one(processor, writer, yes_posting(processor))

    stored = json.loads((workspace.cv / day() / "Acme_Corp_Head_of_IT"
                         / f"{STEM}.json").read_text())
    assert stored == api.rendered.document


def test_your_candidate_data_is_sent_so_the_page_count_is_real(processor, writer, api):
    run_one(processor, writer, yes_posting(processor))
    assert json.loads(api.seen_candidate_data)["name"] == NAME


def test_your_own_prompts_schemas_and_template_are_sent(api, config, workspace, console, tmp_path):
    prompt = tmp_path / "sys_prompt_cv_body.txt"
    prompt.write_text("MY PROMPT")
    schema = tmp_path / "cv_duties_schema.json"
    schema.write_text('{"MY": "SCHEMA"}')
    template = tmp_path / "resume.tex.jinja"
    template.write_text("MY TEMPLATE")
    files = {**config.files, "sys_prompt_cv_body.txt": prompt, "cv_duties_schema.json": schema,
             "resume.tex.jinja": template}
    writer = CvWriter(Calls(api), replace(config, files=files))
    processor = WorkingProcessor(workspace, writer, build_tracker(workspace.root, False), console)
    run_one(processor, writer, yes_posting(processor))

    assert api.seen_prompts == {"sys_prompt_cv_body": "MY PROMPT",
                                "cv_duties_schema": '{"MY": "SCHEMA"}'}
    assert api.seen_template == "MY TEMPLATE"
    assert "candidate_signature.png" in api.seen_images


def test_each_new_status_is_reported_as_it_happens(processor, writer, workspace, api):
    """The wait is minutes long; a silent one is indistinguishable from a
    hung one."""
    api.session = [
        ("status", CVStatus(status="jd_evaluation", detail="")),
        ("status", CVStatus(status="profile_scoring", detail="12 key points, tokens used=99")),
        api.session[-1],
    ]
    run_one(processor, writer, yes_posting(processor))

    log = (workspace.cv / day() / "Acme_Corp_Head_of_IT" / "log.log").read_text()
    assert "   jd_evaluation" in log and "   profile_scoring" in log
    assert "tokens used=99" not in log, "that is what --verbose is for"


def test_verbose_adds_what_the_server_said_about_each_step(api, config, workspace, console):
    api.session = [
        ("status", CVStatus(status="profile_scoring", detail="12 key points, tokens used=99")),
        api.session[-1],
    ]
    writer = CvWriter(Calls(api), replace(config, verbose=True))
    processor = WorkingProcessor(workspace, writer, build_tracker(workspace.root, False), console)
    run_one(processor, writer, yes_posting(processor))

    log = (workspace.cv / day() / "Acme_Corp_Head_of_IT" / "log.log").read_text()
    assert "tokens used=99" in log
    assert "done, 1 model call, 1 page(s)" in log


def test_what_each_model_call_cost_and_the_total_are_reported(processor, writer, workspace):
    run_one(processor, writer, yes_posting(processor))

    log = (workspace.cv / day() / "Acme_Corp_Head_of_IT" / "log.log").read_text()
    assert "⏱ cv gpt-fake 1.2s | input=10, thinking=0, output=20" in log
    assert "Σ 1 model call(s), 1.2s | input=10, thinking=0, output=20" in log


def test_tracking_can_be_turned_off(api, config, workspace, console):
    writer = CvWriter(Calls(api), config)
    processor = WorkingProcessor(workspace, writer, build_tracker(workspace.root, False), console)
    run_one(processor, writer, yes_posting(processor))
    assert not (workspace.root / "applications.xlsx").exists()


def test_a_run_the_server_already_has_is_replayed_and_filed(api, processor, writer, workspace):
    """The same posting again — after a crash, a lost connection — costs
    nothing: the server answers with the session it already has."""
    api.reattached = True
    run_one(processor, writer, yes_posting(processor))

    delivered = workspace.cv / day() / "Acme_Corp_Head_of_IT"
    assert len(list(delivered.glob("cv_*.pdf"))) == 1
    assert "replaying it (--new starts over)" in (delivered / "log.log").read_text()


def test_new_drops_the_run_the_server_has_and_writes_the_cv_again(
        api, config, workspace, console):
    api.acknowledge_errors = [404]                  # nothing there: fine
    writer = CvWriter(Calls(api), config, new=True)
    processor = WorkingProcessor(workspace, writer, build_tracker(workspace.root, False), console)
    run_one(processor, writer, yes_posting(processor))

    assert api.calls == ["acknowledge", "create_cv", "events", "acknowledge"]
    assert len(list((workspace.cv / day() / "Acme_Corp_Head_of_IT").glob("cv_*.pdf"))) == 1


def test_drain_writes_everything_queued_then_returns(processor, writer, workspace, api):
    """submit's way: the CV on the calling thread, and back when it is filed."""
    processor.submit(JD_TEXT, analysis(should_apply="YES", company_name="Alpha"),
                     TaggedLog(processor.console, "a.txt"))
    processor.submit(JD_TEXT, analysis(should_apply="YES", company_name="Beta"),
                     TaggedLog(processor.console, "b.txt"))

    GenerationWorker(processor, writer, processor.console).drain()

    assert sorted(p.name for p in workspace.cv.glob("*/*")) == [
        "Alpha_Head_of_IT", "Beta_Head_of_IT"]
    assert processor.generations.empty()
    assert not any(workspace.working.iterdir())
