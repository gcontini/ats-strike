"""What every request needs: the shared state, the token check, a job slot.

The state is built once at startup and never mutated: the pipelines and one
semaphore. Sharing a pipeline across requests is safe — its chat models are
built once and its HTTP clients are thread-safe.
"""

from __future__ import annotations

import hmac
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Callable, Dict, List, Optional, Tuple

from cv_contracts import StoreItem
from fastapi import Header, Request
from pipeline_v1 import Pipeline, current_run
from pipeline_v2 import Pipeline as PipelineV2
from starlette.concurrency import run_in_threadpool

from ..jobstore import JobStore
from .errors import TooManyJobs, Unauthorized
from .items import log_item, output_item, total_item
from .settings import Settings

logger = logging.getLogger("cv_server.api")

#: The pipelines served, by the first segment of their paths (``/v1/cv``, ``/v2/cv``).
PIPELINES = {"v1": Pipeline, "v2": PipelineV2}


@dataclass
class AppState:
    """Built once in the lifespan, read-only afterwards. The job store, the
    job slots and the settings are shared by every pipeline."""

    settings: Settings
    pipelines: Dict[str, Any]
    job_slots: BoundedSemaphore

    @property
    def jobs(self) -> JobStore:
        """The CV sessions. A value over a path, not shared state."""
        return JobStore(self.settings.work_root)

    @classmethod
    def build(cls, settings: Settings) -> "AppState":
        pipelines = {name: kind.build() for name, kind in PIPELINES.items()}
        if not pipelines["v1"].info()["pdflatex"]:
            logger.warning("  ⚠ pdflatex is not on PATH — every /cv and /cv/render will fail")
        return cls(
            settings=settings,
            pipelines=pipelines,
            job_slots=BoundedSemaphore(settings.max_concurrent_jobs),
        )


def get_state(request: Request) -> AppState:
    """The app state, for a route to depend on."""
    return request.app.state.cv


def get_pipeline(request: Request) -> Any:
    """The pipeline the path names by its first segment (``/v1/cv`` → ``v1``)."""
    return get_state(request).pipelines[request.url.path.split("/")[1]]


def get_text_pipeline(request: Request) -> Pipeline:
    """The pipeline that reads job descriptions and writes letters: v1's under every prefix, since
    v2 has no job analysis and no letter. One client server entry then works for every command."""
    return get_state(request).pipelines["v1"]


def require_token(request: Request, authorization: Optional[str] = Header(None)) -> None:
    """Bearer check. No token configured means the server is open on purpose."""
    expected = get_state(request).settings.api_token
    if not expected:
        return
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(presented.strip(), expected):
        raise Unauthorized("a valid bearer token is required")


def claim_slot(state: AppState) -> None:
    """Take one of the job slots, or refuse.

    A full server answers 429 immediately rather than queueing a caller for
    minutes. Released by whoever claimed it — for a CV job, by the worker
    thread, which is why this is not a context manager.
    """
    if not state.job_slots.acquire(blocking=False):
        raise TooManyJobs(f"all {state.settings.max_concurrent_jobs} job slots are busy; retry shortly")


class Collected:
    """What a short request produced, as things to store, in order: each
    output the pipeline hands over, each line the request logs."""

    def __init__(self) -> None:
        self.items: List[StoreItem] = []

    def status(self, status: str, detail: str = "") -> None:
        """A reply has no status to show."""

    def store(self, output: Any) -> None:
        self.items.append(output_item(output))

    def record(self, kind: str, payload: Any) -> None:
        """The run's sink: a log line is kept, a model call is in the total."""
        if kind == "log":
            self.items.append(log_item(payload))


async def execute(state: AppState, fn: Callable[..., Any], *, work_dir: bool = False) -> Tuple[Any, List[StoreItem]]:
    """Run one blocking pipeline call off the event loop: its value, and what
    it produced to store.

    The CV endpoint does not come through here — it answers before its job is
    done (see :mod:`cv_server.api.jobs`). What is left are the short
    ones, which still want the concurrency limit and, for a render, a scratch
    directory on local disk. ``fn`` is handed the collecting sink — after the
    scratch directory, when there is one. The request's log lines are
    collected too, and, when it made a model call, the line that adds them up.
    """
    claim_slot(state)
    run = current_run()
    collected = Collected()
    run.sink = collected.record
    try:
        if not work_dir:
            value = await run_in_threadpool(fn, collected)
        else:
            with tempfile.TemporaryDirectory(prefix="ats-strike-") as work:
                value = await run_in_threadpool(fn, Path(work), collected)
    finally:
        run.sink = None
        state.job_slots.release()
    if run.total.calls:
        collected.items.append(total_item(run.total))
    return value, collected.items


def envelope_for(request_id: str, data: Any) -> Dict[str, Any]:
    """Wrap a successful payload under the id it belongs to."""
    return {"request_id": request_id, "ok": True, "data": data}


def envelope_of(data: Any) -> Dict[str, Any]:
    """Wrap a successful payload."""
    return envelope_for(current_run().request_id, data)


__all__ = [
    "PIPELINES",
    "AppState",
    "Collected",
    "get_state",
    "get_pipeline",
    "get_text_pipeline",
    "require_token",
    "claim_slot",
    "execute",
    "envelope_for",
    "envelope_of",
]
