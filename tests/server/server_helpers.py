"""Plain helpers for the server tests (fixtures live in conftest.py).

Nothing here knows a pipeline: the server is tested against a stand-in, so
these files are the same on every branch that carries this server.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Union

from cv_pipeline import PipelineError, current_run

#: A posting the free check lets through.
POSTING = "Job posting. " * 120

logger = logging.getLogger("cv_pipeline.stub")


@dataclass(frozen=True)
class Out:
    """What a pipeline hands its sink: the four fields the server reads."""

    location: str
    type: str
    name: str
    content: Union[str, bytes]


class Failing(PipelineError):
    """A pipeline failure of any kind the test names."""

    def __init__(self, kind: str, message: str = "it went wrong", **kwargs: Any) -> None:
        super().__init__(message, **kwargs)
        self.kind = kind


def a_model_call() -> None:
    """What a model call leaves behind: a line, and its cost in the run."""
    logger.info("  ⏱ [stub] stub-model 0.5s | input=11, thinking=2, output=20")
    current_run().add_call(SimpleNamespace(
        role="stub", model="stub-model", seconds=0.5,
        input_tokens=11, thinking_tokens=2, output_tokens=20,
    ))


class StubPipeline:
    """Stands in for :class:`cv_pipeline.Pipeline`: records each call and
    answers with canned values. No model, no LaTeX, no files.

    ``failure``, when set, is raised by the next long call after its model
    call; ``gate`` holds a CV job until it is opened, so a test can see it
    running.
    """

    def __init__(self) -> None:
        self.calls: List[tuple] = []
        self.verdict = True
        self.analysis: Dict[str, Any] = {"job_title": "Head of IT", "company_name": "Acme"}
        self.text = "Dear hiring manager"
        self.outputs = [
            Out("CV", "step", "steps/key_points.json", '[{"id": 1}]'),
            Out("CV", "step", "cv.json", '{"name": "Jordan"}'),
            Out("CV", "cv", "cv.pdf", b"%PDF-1.7 \x00\xff"),
        ]
        self.failure: Optional[Exception] = None
        self.gate: Optional[threading.Event] = None
        self.pdflatex = True

    def info(self) -> Dict[str, Any]:
        return {"models": {"cv": "stub-cv", "detect": "stub-detect"}, "pdflatex": self.pdflatex}

    def detect(self, jd: str) -> bool:
        self.calls.append(("detect", jd))
        self._work()
        return self.verdict

    def analyze(self, pack, job, settings) -> Dict[str, Any]:
        self.calls.append(("analyze", dict(pack), dict(job), dict(settings)))
        self._work()
        return self.analysis

    def letter(self, pack, job, settings) -> str:
        self.calls.append(("letter", dict(pack), dict(job), dict(settings)))
        self._work()
        return self.text

    def cv(self, pack, job, settings, work_dir, sink) -> str:
        self.calls.append(("cv", dict(pack), dict(job), dict(settings), work_dir.is_dir()))
        if self.gate is not None:
            assert self.gate.wait(timeout=10), "the gate was never opened"
        sink.status("writing")
        sink.status("writing")
        self._work()
        sink.status("rendering", "written")
        for output in self.outputs:
            sink.store(output)
        return "done, 1 model call"

    def render(self, pack, job, work_dir, sink) -> None:
        self.calls.append(("render", dict(pack), dict(job), work_dir.is_dir()))
        logger.info("rendered")
        if self.failure is not None:
            raise self.failure
        for output in self.outputs[-2:]:
            sink.store(output)

    def _work(self) -> None:
        a_model_call()
        if self.failure is not None:
            raise self.failure


# --- a request ----------------------------------------------------------------
def files(pack: Optional[dict] = None, job: Optional[dict] = None) -> list:
    """The pack and the job as httpx multipart files."""
    return (
        [("pack", (name, content)) for name, content in (pack or {}).items()]
        + [("job", (name, content)) for name, content in (job or {}).items()]
    )


def a_job(text: str = POSTING, **more) -> list:
    """A pack of one profile and a job of one posting."""
    return files(pack={"candidate_profile.json": b"{}"}, job={"jd.txt": text, **more})


# --- a CV session -------------------------------------------------------------
def start_cv(client, *, request_id: Optional[str] = None, parts: Optional[list] = None,
             settings: Optional[dict] = None) -> str:
    """POST /v1/cv and return the session id."""
    headers = {"X-Request-Id": request_id} if request_id else {}
    data = {"settings": json.dumps(settings)} if settings is not None else {}
    response = client.post("/v1/cv", files=parts or a_job(), data=data, headers=headers)
    assert response.status_code == 202, response.text
    return response.json()["request_id"]


def read_events(client, request_id: str, *, after: int = 0) -> list[tuple[int, str, dict]]:
    """Follow a session's stream to its end: ``[(id, type, data), ...]``.

    The stream ends with the job's last event, so this waits for the job; the
    stub answers at once, and a closed gate gives up after ten seconds.
    """
    return [event for event in raw_events(client, request_id, after=after)
            if event[1] != "ping"]


def raw_events(client, request_id: str, *, after: int = 0) -> list[tuple[int, str, dict]]:
    """The same, with each keep-alive comment as a ``(0, "ping", {})``."""
    headers = {"Last-Event-ID": str(after)} if after else {}
    events, fields = [], {}
    with client.stream("GET", f"/v1/cv/{request_id}/events", headers=headers) as response:
        assert response.status_code == 200, response.read()
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith(":"):
                events.append((0, "ping", {}))
            elif line:
                name, _, value = line.partition(": ")
                fields[name] = value
            elif fields:
                events.append((int(fields["id"]), fields["event"], json.loads(fields["data"])))
                fields = {}
    return events


def run_cv(client, **kwargs) -> list[tuple[int, str, dict]]:
    """Start a CV job and follow it to its end."""
    return read_events(client, start_cv(client, **kwargs))


def of_type(events, kind: str) -> list[dict]:
    """The payloads of one type of event, in order."""
    return [data for _, event, data in events if event == kind]


def logs(items: list[dict]) -> list[str]:
    """The text of each log item, in order."""
    return [item["text"] for item in items if item["type"] == "log"]
