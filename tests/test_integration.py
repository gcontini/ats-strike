"""Client and server, together, in one process.

The client's HTTP calls go through an ASGI transport straight into the real
FastAPI app — real routing, real multipart parsing, real envelopes — with only
the model endpoint faked. It is the one test that would catch the two halves
disagreeing about the wire format.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "server" / "tests"))

from resumix_client.api import HttpApi, ResumixError          # noqa: E402
from resumix_client.config import Config                        # noqa: E402
from resumix_client.stages.calls import Calls                   # noqa: E402
from resumix_client.stages.generation import CvWriter, GenerationWorker  # noqa: E402
from resumix_client.stages.intake import Intake                 # noqa: E402
from resumix_client.stages.terminal import Console              # noqa: E402
from resumix_client.stages.working import WorkingProcessor      # noqa: E402
from resumix_client.tracking import build_tracker               # noqa: E402
from resumix_client.ui import Decision                          # noqa: E402
from resumix_client.workspace import Workspace                  # noqa: E402
from resumix_server.api.app import create_app                   # noqa: E402
from resumix_server.api.deps import AppState                    # noqa: E402
from resumix_server.api.settings import Settings                # noqa: E402
from resumix_server.bundle import default_bundle                # noqa: E402

from server_helpers import FakeLLM, sample_cv_data                # noqa: E402

needs_latex = pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
EXAMPLE = REPO_ROOT / "examples" / "candidate"
JD_TEXT = "We are hiring a Head of IT in Milan. Responsibilities and requirements. " * 30

ANALYSIS = {
    "match_percentage": 77, "match_rationale": "Good overlap.", "job_title": "Head of IT",
    "work_location": "Milan, Italy", "work_mode": "hybrid", "expected_salary": None,
    "max_salary": -1, "hard_skills": ["AWS"],
    "soft_skills": ["Communication"], "company_name": "Acme Corp",
    "posting_url": None, "gaps": "No SAP.", "pers_preferences": "Hybrid is fine.",
    "pers_preference_score": 1.5,
    "should_apply": "CHECK", "should_apply_reason": "Good match, salary not stated.",
}


class Scripted:
    """One decision, no terminal."""

    def __init__(self, decision=Decision(submit=True)):
        self.decision = decision

    def confirm(self, analysis):
        return self.decision


@pytest.fixture
def models():
    llm = FakeLLM()
    # YES to the detection call; the analysis and the CV model follow.
    llm["detect"].replies = ["YES"]
    llm["analysis"].replies = [json.dumps(ANALYSIS)]
    llm["cv"].replies = [sample_cv_data().model_dump_json()]
    # "OK": the reviewer finds nothing to fix.
    llm["review"].replies = ["OK"]
    llm["highlight"].replies = [sample_cv_data(summary="**Bold** summary.").model_dump_json()]
    return llm


@pytest.fixture
def api(models, tmp_path):
    from threading import BoundedSemaphore

    settings = Settings(work_root=tmp_path / "work", request_budget_seconds=120.0)
    settings.work_root.mkdir(parents=True, exist_ok=True)
    state = AppState(
        settings=settings, llm=models, bundle=default_bundle(),
        job_slots=BoundedSemaphore(settings.max_concurrent_jobs),
        pdflatex=shutil.which("pdflatex") is not None,
    )
    from fastapi.testclient import TestClient

    app = create_app(state=state)
    # TestClient runs the real app in-process and is an httpx.Client, so the
    # client's own request code is what makes the calls.
    with TestClient(app) as http:
        yield HttpApi("http://server.invalid", client=http)


class Submit:
    """``resumix submit`` against the in-process server: one posting, analysed,
    approved, and its CV written on this thread."""

    def __init__(self, api, root: Path) -> None:
        self.workspace = Workspace(root).ensure()
        self.config = Config(
            server_url="http://server.invalid",
            files={name: EXAMPLE / name for name in (
                "candidate_profile.json", "candidate_data.json",
                "candidate_preferences.md")},
            images={"candidate_signature.png": EXAMPLE / "candidate_signature.png"},
        )
        console = Console()
        calls = Calls(api)
        writer = CvWriter(calls, self.config)
        self.working = WorkingProcessor(self.workspace, writer,
                                        build_tracker(self.workspace.root, True), console)
        self.intake = Intake(calls, self.config, self.working, Scripted(), console)
        self.generation = GenerationWorker(self.working, writer, console)

    def __call__(self, text: str) -> Path:
        """The posting's folder once it is filed: under cv/ or error/."""
        self.intake.take(text, "posting.txt")
        self.generation.drain()
        [filed] = [*self.workspace.cv.glob("*/*"), *self.workspace.error.glob("*/*")]
        return filed


@pytest.fixture
def run(api, tmp_path):
    return Submit(api, tmp_path / "out")


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """The fakes answer instantly: neither side needs to wait between looks."""
    monkeypatch.setattr("resumix_client.cvjob.RECONNECT_SECONDS", 0)
    monkeypatch.setattr("resumix_server.api.routers.cv.FOLLOW_SECONDS", 0.01)


@needs_latex
def test_a_posting_becomes_a_real_pdf(run, models, tmp_path):
    folder = run(JD_TEXT)

    assert folder.parent.parent == run.workspace.cv, (folder / "log.log").read_text()
    assert folder.name == "Acme_Corp_Head_of_IT"
    pdf = folder / "cv_jordan_rivera_head_of_it.pdf"
    assert pdf.read_bytes().startswith(b"%PDF")
    assert r"\documentclass" in (folder / "cv_jordan_rivera_head_of_it.tex").read_text()

    log = (folder / "log.log").read_text()
    assert "writing the CV" in log
    # Every step the job went through: the session keeps them all.
    assert "   generate" in log and "   review" in log and "   highlight" in log
    # Filed, so acknowledged: the server keeps nothing of it.
    assert not any((tmp_path / "work").iterdir())


@needs_latex
def test_the_cv_s_own_server_log_and_its_cost_come_with_it(run):
    """What the server did for the CV streams in with it; the analysis' lines
    stay in the server's own log."""
    log = (run(JD_TEXT) / "log.log").read_text()

    assert "[cv.generate]" in log and "[render]" in log
    assert "jd.analysis" not in log
    assert "⏱ cv fake-cv" in log, "each model call, as it is made"
    assert "Σ " in log and "model call(s)" in log, "and the total"


@needs_latex
def test_a_posting_the_server_already_wrote_is_replayed_not_paid_for_twice(run, api, models):
    """The client died after the server had finished: the next run of the same
    posting gets the CV that is already there."""
    from resumix_client.cvjob import session_id

    request_id = session_id(JD_TEXT)
    api.create_cv(JD_TEXT, request_id=request_id,
                  profile=(EXAMPLE / "candidate_profile.json").read_bytes(),
                  candidate_data=(EXAMPLE / "candidate_data.json").read_bytes())
    *_, (_, last, _) = api.events(request_id)       # followed to the end, never acknowledged
    assert last == "result"
    written = len(models["cv"].calls)

    folder = run(JD_TEXT)

    assert folder.parent.parent == run.workspace.cv
    assert "replaying it" in (folder / "log.log").read_text()
    assert len(models["cv"].calls) == written


@needs_latex
def test_the_rendered_pdf_can_be_re_rendered_from_its_document(run, api):
    document = run(JD_TEXT) / "cv_jordan_rivera_head_of_it.json"

    stored = json.loads(document.read_text())
    assert stored["name"] == "Jordan Rivera", "your own data, merged in server-side"
    assert stored["job_title"] == "Staff Platform Engineer", "and the model's"

    envelope = api.render(document=stored)
    assert envelope.data.pdf_bytes().startswith(b"%PDF")


def test_a_server_error_reaches_the_client_as_a_typed_failure(run, api, models, capsys):
    models["analysis"].replies = ["not an analysis"]

    run.intake.take(JD_TEXT, "posting.txt")

    assert run.working.failures == 1
    assert not any(run.workspace.working.iterdir()), "nothing is filed before the analysis"
    out = capsys.readouterr().out
    assert "model_output [jd.analysis]" in out
    assert "request id: " in out, "what the server's own log has it under"


def test_the_client_reports_an_unreachable_server_clearly():
    api = HttpApi("http://127.0.0.1:1")  # nothing listens here
    with pytest.raises(ResumixError, match="cannot reach the resumix server"):
        api.detect("x" * 1500)
