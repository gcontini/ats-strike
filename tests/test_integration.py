"""Client and servers, together, in one process.

The client's HTTP calls go through ASGI transports straight into two real
FastAPI apps — real routing, real multipart parsing, real envelopes, the real
pipeline — with only the model endpoint faked, under a real
``ModelSelector``. It is the one test that would catch the client, the
protocol and the pipeline disagreeing about the wire format.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from threading import BoundedSemaphore

import openpyxl
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "pipeline" / "tests"))

from cv_client.api import CVError, HttpApi                 # noqa: E402
from cv_client.config import Config                         # noqa: E402
from cv_client.cvjob import session_id, write_cv            # noqa: E402
from cv_client.servers import Server                        # noqa: E402
from cv_client.stages.calls import Calls                    # noqa: E402
from cv_client.stages.generation import CvWriter, GenerationWorker  # noqa: E402
from cv_client.stages.intake import Intake                  # noqa: E402
from cv_client.stages.terminal import Console, TaggedLog    # noqa: E402
from cv_client.stages.working import WorkingProcessor       # noqa: E402
from cv_client.tracking import build_tracker                # noqa: E402
from cv_client.ui import Decision                           # noqa: E402
from cv_client.workspace import Artifacts, Workspace        # noqa: E402
from cv_pipeline import Pipeline                            # noqa: E402
from cv_pipeline.bundle import default_bundle               # noqa: E402
from cv_server.api.app import create_app                    # noqa: E402
from cv_server.api.deps import AppState                     # noqa: E402
from cv_server.api.settings import Settings                 # noqa: E402

from pipeline_helpers import SAMPLE_BODY, FakeLLM, script_cv  # noqa: E402

needs_latex = pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
EXAMPLE = REPO_ROOT / "examples" / "candidate"
CANDIDATE_FILES = ("candidate_profile.json", "candidate_data.json", "candidate_preferences.md",
                   "candidate_signature.png")
NAME = json.loads((EXAMPLE / "candidate_data.json").read_text())["name"]
STEM = Artifacts(NAME, "Head of IT").stem
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

    def confirm(self, analysis):
        return Decision(submit=True)


def model_calls(llm: FakeLLM) -> int:
    return sum(len(role.calls) for role in llm.roles.values())


class Running:
    """One in-process server: its fake models, its sessions, its client."""

    def __init__(self, name: str, root: Path, *, default: bool) -> None:
        self.models = FakeLLM()
        # YES to the detection call; the analysis and the six steps of the CV follow.
        self.models["detect"].replies = ["YES"]
        self.models["analysis"].replies = [json.dumps(ANALYSIS)]
        script_cv(self.models)
        self.work = root / f"work-{name}"
        self.work.mkdir(parents=True)
        settings = Settings(work_root=self.work)
        state = AppState(settings=settings,
                         pipeline=Pipeline(llm=self.models, bundle=default_bundle(),
                                           budget_seconds=120.0),
                         job_slots=BoundedSemaphore(settings.max_concurrent_jobs))
        self.http = TestClient(create_app(state=state))
        folder = root / "data" / name
        folder.mkdir(parents=True)
        for filename in CANDIDATE_FILES:
            shutil.copy(EXAMPLE / filename, folder / filename)
        url = f"http://{name}.invalid"
        (folder / "server.toml").write_text(f'url = "{url}"\n')
        self.server = Server(name=name, folder=folder, url=url, default=default)
        # TestClient runs the real app in-process and is an httpx.Client, so the
        # client's own request code is what makes the calls.
        self.api = HttpApi(url, client=self.http)


@pytest.fixture
def servers(tmp_path):
    """v11, the default, and v05, both up."""
    running = {name: Running(name, tmp_path, default=name == "v11") for name in ("v11", "v05")}
    for server in running.values():
        server.http.__enter__()
    yield running
    for server in running.values():
        server.http.__exit__(None, None, None)


class Submit:
    """``cv submit --server_ver v11 --server_ver v05`` against the in-process
    servers: one posting, analysed, approved, and its CVs written on this
    thread, one server after the other."""

    def __init__(self, servers: dict, root: Path) -> None:
        self.workspace = Workspace(root).ensure()
        v11, v05 = servers["v11"], servers["v05"]
        self.config = Config(targets=(v11.server, v05.server), default=v11.server)
        self.console = Console()
        calls = {s.server.name: Calls(s.server, s.api) for s in servers.values()}
        self.writer = CvWriter(calls, self.config)
        self.working = WorkingProcessor(self.workspace, self.writer,
                                        build_tracker(self.workspace.root, True), self.console)
        self.intake = Intake(calls["v11"], self.working, Scripted(), self.console)

    def drain(self) -> Path:
        """The posting's folder once it is filed: under CV/ or error/."""
        for server in self.config.lanes():
            GenerationWorker(self.working, self.writer, self.console, server).drain()
        [filed] = [*self.workspace.cv.glob("*/*"), *self.workspace.error.glob("*/*")]
        return filed

    def __call__(self, text: str) -> Path:
        self.intake.take(text, "posting.txt")
        return self.drain()


@pytest.fixture
def run(servers, tmp_path):
    return Submit(servers, tmp_path / "out")


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """The fakes answer instantly: neither side needs to wait between looks."""
    monkeypatch.setattr("cv_client.cvjob.RECONNECT_SECONDS", 0)
    monkeypatch.setattr("cv_server.api.routers.cv.FOLLOW_SECONDS", 0.01)


@needs_latex
def test_one_posting_one_analysis_two_pdfs_in_one_folder(run, servers):
    folder = run(JD_TEXT)

    assert folder.parent.parent == run.workspace.cv, (folder / "log.log").read_text()
    assert folder.name == "Acme_Corp_Head_of_IT"
    assert len(servers["v11"].models["analysis"].calls) == 1
    assert not servers["v05"].models["analysis"].calls, "the default analyses, once"
    assert not servers["v05"].models["detect"].calls

    assert sorted(p.name for p in folder.glob("*.pdf")) == [f"{STEM}.pdf", f"{STEM}_v05.pdf"]
    for pdf in folder.glob("*.pdf"):
        assert pdf.read_bytes().startswith(b"%PDF")
    for name in ("v11", "v05"):
        own = folder / name
        assert r"\documentclass" in (own / "cv.tex").read_text()
        assert json.loads((own / "cv.json").read_text())["name"] == NAME
        assert len(list((own / "steps").glob("*.json"))) == 9, "each step's result, kept"
        assert (own / "candidate_signature.png").is_file()
    stored = json.loads((folder / "v05" / "cv.json").read_text())
    assert stored["job_title"] == SAMPLE_BODY["job_title"], "the model's, merged with yours"

    log = (folder / "log.log").read_text()
    for step in ("jd_evaluation", "profile_scoring", "format_selection", "cv", "validation",
                 "highlight"):
        assert f"   {step}\n" in log, step
    assert "  v11 " in log and "[jd.analysis]" in log, "the analysis' own lines"
    for name in ("v11", "v05"):
        assert f"  {name} " in log and "[cv.profile_scoring]" in log
        assert any(line.startswith(f"  {name} ") and "Σ " in line and "model call(s)" in line
                   for line in log.splitlines()), f"{name}'s total"
    # Filed, so acknowledged: neither server keeps anything of it.
    assert not any(servers["v11"].work.iterdir()) and not any(servers["v05"].work.iterdir())


@needs_latex
def test_a_folder_dropped_back_renders_with_no_model_call(run, servers, tmp_path):
    delivered = run(JD_TEXT)
    before = {name: model_calls(s.models) for name, s in servers.items()}
    dropped = tmp_path / "in" / delivered.name
    shutil.move(str(delivered), str(dropped))
    (dropped / f"{STEM}.pdf").unlink()          # so the default's must come back

    run.working.submit_folder(dropped, TaggedLog(run.console, dropped.name))
    again = run.drain()

    assert again.parent.parent == run.workspace.cv, (again / "log.log").read_text()
    assert {name: model_calls(s.models) for name, s in servers.items()} == before
    assert sorted(p.name for p in again.glob("*.pdf")) == [f"{STEM}.pdf", f"{STEM}_v05.pdf"]
    assert (again / f"{STEM}.pdf").read_bytes().startswith(b"%PDF")
    sheet = openpyxl.load_workbook(run.workspace.root / "applications.xlsx").active
    assert sheet.cell(row=3, column=1).value is None, "a re-run is not a new application"


@needs_latex
def test_a_posting_the_server_already_wrote_is_replayed_not_paid_for_twice(servers, tmp_path):
    """The client died after the server had finished: the next run of the same
    posting gets the CV that is already there."""
    v11 = servers["v11"]
    job = {"jd.txt": JD_TEXT.encode()}
    v11.api.create_cv(v11.server.pack(), job, {}, session_id(job["jd.txt"]))
    *_, (_, last, _) = v11.api.events(session_id(job["jd.txt"]))  # followed, never acknowledged
    assert last == "done"
    paid = model_calls(v11.models)

    said, stored = [], []
    write_cv(v11.api, v11.server, job, say=said.append, store=stored.append)

    assert any("replaying it" in line for line in said)
    assert model_calls(v11.models) == paid
    assert [i.name for i in stored if i.type == "cv"] == ["cv.pdf"]


def test_a_server_error_reaches_the_client_as_a_typed_failure(run, servers, capsys):
    servers["v11"].models["analysis"].replies = ["not an analysis"]

    run.intake.take(JD_TEXT, "posting.txt")

    assert run.working.failures == 1
    assert not any(run.workspace.working.iterdir()), "nothing is filed before the analysis"
    out = capsys.readouterr().out
    assert "model_output [jd.analysis]" in out
    assert "request id: " in out, "what the server's own log has it under"


def test_the_client_reports_an_unreachable_server_clearly():
    api = HttpApi("http://127.0.0.1:1")  # nothing listens here
    with pytest.raises(CVError, match="cannot reach the ats-strike server"):
        api.detect({"jd.txt": b"x" * 1500})
