"""Fixtures for the client tests.

The client is tested against :class:`FakeApi` — an in-memory
:class:`~cv_client.api.CVApi` — so every test runs with no
server, no network and no model. One test file goes the other way and drives
the real server in-process; see ``tests/test_integration.py``.
"""

from __future__ import annotations

import io
import json
import queue
from datetime import datetime
from pathlib import Path

import pytest
from cv_contracts import (
    Cleanup,
    CoverLetter,
    CVArtifact,
    CVResult,
    CVSession,
    CVStatus,
    Envelope,
    JDAnalysis,
    JDDetection,
    LogEntry,
    ModelCall,
    RenderedCV,
    TokenUsage,
)

from cv_client.api import CVError
from cv_client.config import Config
from cv_client.ui import Decision
from cv_client.workspace import Workspace

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE_CANDIDATE = REPO_ROOT / "examples" / "candidate"

#: Long enough to pass the free structural check.
JD_TEXT = "We are hiring a Head of IT. Responsibilities and requirements. " * 30


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
    """What a finished job hands back: one flat object the client stores
    without reading a field of it."""
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


#: The intermediate result the fake session hands over.
KEY_POINTS = [{"id": 1, "requirement": "IT management", "keywords": ["IT"], "importance": 90}]


def cv_session(rendered: RenderedCV) -> list:
    """What a CV job streams, as ``(type, payload)``: a step, a log line, a
    model call, the step's result, another step, then the CV with the run's
    total."""
    return [
        ("status", CVStatus(status="jd_evaluation")),
        ("log", LogEntry(ts=datetime(2026, 1, 15, 9, 0), level="INFO", stage="cv.jd_evaluation",
                         message="⏱ [cv] gpt-fake 1.2s | input=10, thinking=0, output=20")),
        ("usage", ModelCall(role="cv", model="gpt-fake", seconds=1.2, input_tokens=10,
                            thinking_tokens=0, output_tokens=20)),
        ("artifact", CVArtifact(name="key_points", data=KEY_POINTS)),
        ("status", CVStatus(status="profile_scoring", detail="1 key points, tokens used=30")),
        ("result", CVResult(cv=rendered, detail="done, 1 model call, 1 page(s)",
                            total=TokenUsage(calls=1, seconds=1.2, input_tokens=10,
                                             output_tokens=20))),
    ]


class FakeApi:
    """Records what it was asked and replies with whatever it was given."""

    def __init__(self, **replies):
        self.is_jd = replies.get("is_jd", True)
        self.analysis = replies.get("analysis", analysis())
        self.rendered = replies.get(
            "rendered",
            RenderedCV.from_bytes(tex=r"\documentclass{article}", pdf=b"%PDF-fake",
                                  document=document()),
        )
        #: What a CV job streams, as ``(type, payload)``, in order.
        self.session = replies.get("session") or cv_session(self.rendered)
        #: Whether the posting already had a session on the server.
        self.reattached = replies.get("reattached", False)
        #: The stream breaks once after this many events: a dropped connection.
        self.break_after = replies.get("break_after")
        #: The status each acknowledge fails with, in order; None succeeds.
        self.acknowledge_errors = list(replies.get("acknowledge_errors", []))
        self.cleanup = replies.get("cleanup", Cleanup(deleted=2, running=0))
        self.cover_letter = replies.get(
            "cover_letter", CoverLetter(text="Dear hiring manager.", words=3)
        )
        self.fail_on = replies.get("fail_on")
        self.calls: list[str] = []
        #: Where each stream was opened from: the Last-Event-ID it sent.
        self.seen_after: list[int] = []

    def _record(self, name):
        self.calls.append(name)
        if self.fail_on == name:
            raise CVError(f"model_output [{name}]: {name} failed", status=502,
                                 request_id="failed-request")

    def detect(self, text):
        self._record("detect")
        return envelope(JDDetection(is_job_description=self.is_jd))

    def analyze(self, text, *, profile, preferences, prompt=None, extra_body=None):
        self._record("analyze")
        self.seen_profile = profile
        self.seen_preferences = preferences
        self.seen_analysis_prompt = prompt
        self.seen_analysis_extra_body = extra_body
        return envelope(self.analysis)

    def create_cv(self, text, *, request_id, profile, candidate_data, prompts=None,
                  template=None, images=None, extra_body=None, pages=None):
        self._record("create_cv")
        self.seen_request_id = request_id
        self.seen_prompts = dict(prompts or {})
        self.seen_template = template
        self.seen_images = dict(images or {})
        self.seen_extra_body = extra_body
        self.seen_candidate_data = candidate_data
        self.seen_pages = pages
        return envelope(CVSession(reattached=self.reattached), request_id=request_id)

    def events(self, request_id, *, after=0):
        self._record("events")
        self.seen_after.append(after)
        for number, (kind, payload) in enumerate(self.session, start=1):
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

    def render(self, *, document=None, tex=None, template=None, images=None):
        self._record("render")
        self.seen_document = document
        self.seen_images = dict(images or {})
        return envelope(self.rendered)

    def letter(self, text, *, profile, candidate_data, analysis=None, prompt=None,
               extra_body=None):
        self._record("letter")
        self.seen_candidate_data = candidate_data
        self.seen_letter_extra_body = extra_body
        return envelope(self.cover_letter)


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


@pytest.fixture
def config() -> Config:
    """Points at the shipped fictional candidate; no server needed."""
    return Config(
        server_url="http://test.invalid",
        files={name: EXAMPLE_CANDIDATE / name for name in (
            "candidate_profile.json", "candidate_data.json",
            "candidate_preferences.md")},
        images={"candidate_signature.png": EXAMPLE_CANDIDATE / "candidate_signature.png"},
    )


@pytest.fixture
def workspace(tmp_path) -> Workspace:
    return Workspace(tmp_path / "out").ensure()


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


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

    ``files`` are extra files by name, e.g. ``**{"cv_jordan_rivera_head_of_it.json": ...}``.
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
        (folder / filename).write_text(content, encoding="utf-8")
    return folder


@pytest.fixture
def console():
    from cv_client.stages.terminal import Console

    return Console()


@pytest.fixture
def writer(api, config):
    from cv_client.stages.calls import Calls
    from cv_client.stages.generation import CvWriter

    return CvWriter(Calls(api), config)


@pytest.fixture
def processor(workspace, writer, console):
    from cv_client.stages.working import WorkingProcessor
    from cv_client.tracking import build_tracker

    return WorkingProcessor(workspace, writer, build_tracker(workspace.root, True), console)
