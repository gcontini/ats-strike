"""Fixtures for the client tests.

The client is tested against :class:`FakeApi` — an in-memory
:class:`~cv_client.api.CVApi`, one per server — so every test runs with no
server, no network and no model. One test file goes the other way and drives
two real servers in-process; see ``tests/test_integration.py``.

A server here is a real folder under ``tmp_path`` holding the example
candidate's files and a ``server.toml``, because the pack is read from disk.
"""

from __future__ import annotations

import io
import json
import queue
import shutil
from pathlib import Path

import pytest
from cv_contracts import (
    Analysed,
    Cleanup,
    CVSession,
    Detection,
    Done,
    Envelope,
    Letter,
    Rendered,
    Status,
    StoreItem,
)

from cv_client.api import CVError
from cv_client.config import Config
from cv_client.jdanalysis import JDAnalysis
from cv_client.servers import Server
from cv_client.stages.calls import Calls
from cv_client.ui import Decision
from cv_client.workspace import Workspace

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CANDIDATE = REPO_ROOT / "examples" / "candidate"
#: What a server folder holds in these tests: the example candidate's files.
CANDIDATE_FILES = ("candidate_profile.json", "candidate_data.json",
                   "candidate_preferences.md", "candidate_signature.png")
#: The example candidate's name, which every PDF is named after.
NAME = json.loads((EXAMPLE_CANDIDATE / "candidate_data.json").read_text())["name"]

#: Long enough to pass the free structural check.
JD_TEXT = "We are hiring a Head of IT. Responsibilities and requirements. " * 30

PDF = b"%PDF-fake"
TEX = r"\documentclass{article}"
#: The intermediate result the fake session hands over.
KEY_POINTS = [{"id": 1, "requirement": "IT management", "keywords": ["IT"], "importance": 90}]
#: A line the server logged, and the line adding up its model calls.
LOG_LINE = ("2026-01-15T09:00:00 INFO    [cv.jd_evaluation] ⏱ [cv] gpt-fake 1.2s | "
            "input=10, thinking=0, output=20")
TOTAL_LINE = ("2026-01-15T09:00:05 INFO    [run] Σ 1 model call(s), 1.2s | "
              "input=10, thinking=0, output=20")


def analysis(**overrides) -> JDAnalysis:
    data = dict(
        match_percentage=82, match_rationale="Strong overlap.", job_title="Head of IT",
        work_location="Milan, Italy", work_mode="hybrid", expected_salary=None,
        max_salary=-1, hard_skills=["AWS"],
        soft_skills=["Communication"], company_name="Acme Corp",
        posting_url=None, gaps="No SAP.", pers_preferences="Hybrid is fine.",
        pers_preference_score=1.5, should_apply="CHECK",
        should_apply_reason="Good match, salary not stated.",
    )
    data.update(overrides)
    return JDAnalysis(**data)


def document() -> dict:
    """What a finished job stores as ``cv.json``: one flat object the client
    keeps without reading a field of it."""
    return {
        **json.loads((EXAMPLE_CANDIDATE / "candidate_data.json").read_text()),
        "company_name": "Acme Corp", "job_title": "Head of IT", "summary": "A summary.",
        "skills": [f"skill {i}" for i in range(6)],
        "experiences": [
            {"title": "Engineer", "company": "Initech", "location": "Rome, Italy",
             "dates": "2020 -- 2024", "project_name": None,
             "duties": ["Did a thing."]}
            for _ in range(3)
        ],
    }


def envelope(data, *, request_id: str = "test-request") -> Envelope:
    return Envelope(request_id=request_id, ok=True, data=data)


def log_item(text: str) -> StoreItem:
    return StoreItem(location="CV", type="log", text=text)


def step(name: str, content) -> StoreItem:
    return StoreItem.from_content("CV", "step", name, content)


def cv_session() -> list:
    """What a CV job streams when it writes, as ``(type, payload)``: the first
    status, a step, a log line, the step's result, another step, the
    document, the tex, the PDF, the total, then done."""
    return [
        ("status", Status(status="started")),
        ("status", Status(status="jd_evaluation")),
        ("store", log_item(LOG_LINE)),
        ("store", step("steps/key_points.json", json.dumps(KEY_POINTS, indent=2))),
        ("status", Status(status="profile_scoring", detail="1 key points, tokens used=30")),
        ("store", step("cv.json", json.dumps(document(), indent=2, ensure_ascii=False))),
        ("store", step("cv.tex", TEX)),
        ("store", StoreItem.from_content("CV", "cv", "cv.pdf", PDF)),
        ("store", log_item(TOTAL_LINE)),
        ("done", Done(detail="done, 1 model call, 1 page(s)")),
    ]


def render_session() -> list:
    """What a CV job streams when the job already holds its ``cv.json``."""
    return [
        ("status", Status(status="started")),
        ("store", step("cv.tex", TEX)),
        ("store", StoreItem.from_content("CV", "cv", "cv.pdf", PDF)),
        ("store", log_item("2026-01-15T09:00:01 INFO    [run] Σ 0 model call(s), 0.0s | "
                           "input=0, thinking=0, output=0")),
        ("done", Done(detail="rendered, 1 page(s)")),
    ]


class FakeApi:
    """One server: records what it was asked and replies with whatever it was given."""

    def __init__(self, **replies):
        self.is_jd = replies.get("is_jd", True)
        self.analysis = replies.get("analysis", analysis())
        #: What a CV job streams when it writes, as ``(type, payload)``, in order.
        self.session = replies.get("session") or cv_session()
        #: ... and when the job already holds its CV.
        self.render_session = render_session()
        #: Whether the posting already had a session on the server.
        self.reattached = replies.get("reattached", False)
        #: The stream breaks once after this many events: a dropped connection.
        self.break_after = replies.get("break_after")
        #: The status each acknowledge fails with, in order; None succeeds.
        self.acknowledge_errors = list(replies.get("acknowledge_errors", []))
        self.cleanup = replies.get("cleanup", Cleanup(deleted=2, running=0))
        self.letter_text = replies.get("letter_text", "Dear hiring manager.")
        self.protocol = replies.get("protocol", 2)
        self.fail_on = replies.get("fail_on")
        self.calls: list[str] = []
        #: Where each stream was opened from: the Last-Event-ID it sent.
        self.seen_after: list[int] = []
        #: The last request's three parts.
        self.seen_pack: dict = {}
        self.seen_job: dict = {}
        self.seen_settings: dict = {}

    def _record(self, name, pack=None, job=None, settings=None):
        self.calls.append(name)
        if pack is not None:
            self.seen_pack, self.seen_job, self.seen_settings = (
                dict(pack), dict(job), dict(settings))
        if self.fail_on == name:
            raise CVError(f"model_output [{name}]: {name} failed", status=502,
                          request_id="failed-request")

    def status(self):
        from cv_contracts import ServerStatus

        self._record("status")
        return envelope(ServerStatus(status="ok", version="9.9.9", protocol=self.protocol,
                                     pdflatex=True, models={}, auth_required=False))

    def detect(self, job):
        self._record("detect")
        self.seen_job = dict(job)
        return envelope(Detection(is_job_description=self.is_jd))

    def analyze(self, pack, job, settings):
        self._record("analyze", pack, job, settings)
        return envelope(Analysed(analysis=self.analysis.model_dump(exclude_unset=True),
                                 items=[log_item(LOG_LINE)]))

    def create_cv(self, pack, job, settings, request_id):
        self._record("create_cv", pack, job, settings)
        self.seen_request_id = request_id
        return envelope(CVSession(reattached=self.reattached), request_id=request_id)

    def events(self, request_id, after=0):
        self._record("events")
        self.seen_after.append(after)
        rendering = "cv.json" in self.seen_job or "cv.tex" in self.seen_job
        session = self.render_session if rendering else self.session
        for number, (kind, payload) in enumerate(session, start=1):
            if number <= after:
                continue
            if self.break_after is not None and number > self.break_after:
                self.break_after = None
                raise CVError("lost the stream: connection reset", request_id=request_id)
            yield number, kind, payload

    def acknowledge(self, request_id):
        self._record("acknowledge")
        status = self.acknowledge_errors.pop(0) if self.acknowledge_errors else None
        if status is not None:
            raise CVError(f"acknowledge failed with {status}", status=status,
                          request_id=request_id)
        return Envelope(request_id=request_id, ok=True)

    def reset(self):
        self._record("reset")
        return envelope(self.cleanup)

    def render(self, pack, job, settings):
        self._record("render", pack, job, settings)
        return envelope(Rendered(items=[step("cv.tex", TEX),
                                        StoreItem.from_content("CV", "cv", "cv.pdf", PDF)]))

    def letter(self, pack, job, settings):
        self._record("letter", pack, job, settings)
        return envelope(Letter(text=self.letter_text, items=[log_item(TOTAL_LINE)]))


class ScriptedConfirmer:
    """Answers the confirm prompt from a list, without a terminal."""

    def __init__(self, *decisions: Decision):
        self.decisions = list(decisions) or [Decision(submit=True)]
        self.seen: list = []

    def confirm(self, analysis):
        self.seen.append(analysis)
        return self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]


class Typist:
    """A stdin the test types into: ``readline`` blocks, like a terminal's."""

    def __init__(self):
        self._lines: queue.Queue = queue.Queue()

    def readline(self):
        return self._lines.get()

    def type(self, line):
        self._lines.put(line + "\n")

    def close(self):
        self._lines.put("")


# --- servers --------------------------------------------------------------------
def make_server(parent: Path, name: str, *, default: bool = False, settings=None,
                files=CANDIDATE_FILES) -> Server:
    """A server folder holding the example candidate's files and a server.toml."""
    folder = parent / name
    folder.mkdir(parents=True)
    for filename in files:
        shutil.copy(EXAMPLE_CANDIDATE / filename, folder / filename)
    url = f"http://{name}.invalid"
    (folder / "server.toml").write_text(
        f'url = "{url}"\n', encoding="utf-8")
    return Server(name=name, folder=folder, url=url, default=default,
                  settings=settings or {})


def calls_for(config: Config, apis: dict) -> dict:
    """Server name -> its calls, each over its own fake."""
    return {server.name: Calls(server, apis[server.name]) for server in config.servers()}


@pytest.fixture
def servers_dir(tmp_path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def server(servers_dir) -> Server:
    """The default server, ``v11``."""
    return make_server(servers_dir, "v11", default=True)


@pytest.fixture
def config(server) -> Config:
    """One server, the default; no network needed."""
    return Config(targets=(server,), default=server)


@pytest.fixture
def workspace(tmp_path) -> Workspace:
    return Workspace(tmp_path / "out").ensure()


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def calls(server, api) -> Calls:
    return Calls(server, api)


@pytest.fixture(autouse=True)
def no_reconnect_delay(monkeypatch):
    """The fake answers instantly; waiting 4s before reconnecting proves nothing."""
    monkeypatch.setattr("cv_client.cvjob.RECONNECT_SECONDS", 0)


@pytest.fixture
def typist():
    typist = Typist()
    yield typist
    typist.close()


def typed(*lines: str):
    """A keyboard on which these lines, then the end of input, were typed."""
    from cv_client.stages.terminal import Keyboard

    keyboard = Keyboard(io.StringIO("".join(f"{line}\n" for line in lines)))
    keyboard._read()  # what the reader thread does, without the thread
    return keyboard


def answering(*lines: str):
    """:func:`typed`, as if typed once the question was on screen."""
    keyboard = typed(*lines)
    keyboard.discard_typed_ahead = lambda: None
    return keyboard


# --- the stages ---------------------------------------------------------------
def make_job(parent: Path, name: str = "Acme_Corp_Head_of_IT", *, should_apply="CHECK",
             approval=None, with_analysis=True, **files) -> Path:
    """A job folder as the watcher, an earlier run or you might leave it.

    ``files`` are extra files by path, ``/`` allowed, e.g. ``**{"v11/cv.json": ...}``.
    """
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / "jd.txt").write_text(JD_TEXT, encoding="utf-8")
    if with_analysis:
        (folder / "analysis.json").write_text(
            analysis(should_apply=should_apply).model_dump_json(), encoding="utf-8")
    if approval is not None:
        (folder / "approval_status.txt").write_text(approval, encoding="utf-8")
    for filename, content in files.items():
        (folder / filename).parent.mkdir(parents=True, exist_ok=True)
        (folder / filename).write_text(content, encoding="utf-8")
    return folder


@pytest.fixture
def console():
    from cv_client.stages.terminal import Console

    return Console()


@pytest.fixture
def writer(api, config):
    from cv_client.stages.generation import CvWriter

    return CvWriter(calls_for(config, {"v11": api}), config)


@pytest.fixture
def processor(workspace, writer, console):
    from cv_client.stages.working import WorkingProcessor
    from cv_client.tracking import build_tracker

    return WorkingProcessor(workspace, writer, build_tracker(workspace.root, True), console)
