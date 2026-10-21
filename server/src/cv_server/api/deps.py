"""What every request needs: the shared state, the token check, a job slot.

The state is built once at startup and never mutated: the model selector, the
default resource bundle, and one semaphore. Sharing a
:class:`~cv_server.models.ModelSelector` across requests is safe — its
chat models are built once and its HTTP clients are thread-safe.
"""

from __future__ import annotations

import hmac
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore
from typing import Any, Callable, Dict, Optional

from fastapi import Header, Request
from starlette.concurrency import run_in_threadpool

from ..bundle import ResourceBundle, default_bundle
from ..jobstore import JobStore
from ..models import ModelSelector, build_selector
from ..observability import LOGGER_ROOT, current_run
from .errors import TooManyJobs, Unauthorized
from .settings import Settings

logger = logging.getLogger(f"{LOGGER_ROOT}.api")


@dataclass
class AppState:
    """Built once in the lifespan, read-only afterwards."""

    settings: Settings
    llm: ModelSelector
    bundle: ResourceBundle
    job_slots: BoundedSemaphore
    pdflatex: bool

    @property
    def jobs(self) -> JobStore:
        """The CV sessions. A value over a path, not shared state."""
        return JobStore(self.settings.work_root)

    @classmethod
    def build(cls, settings: Settings) -> "AppState":
        pdflatex = shutil.which("pdflatex") is not None
        if not pdflatex:
            logger.warning(
                "  ⚠ pdflatex is not on PATH — /v1/cv and /v1/cv/render will fail"
            )
        return cls(
            settings=settings,
            llm=build_selector(),
            bundle=default_bundle(),
            job_slots=BoundedSemaphore(settings.max_concurrent_jobs),
            pdflatex=pdflatex,
        )


def get_state(request: Request) -> AppState:
    """The app state, for a route to depend on."""
    return request.app.state.cv


def require_token(
    request: Request, authorization: Optional[str] = Header(None)
) -> None:
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
        raise TooManyJobs(
            f"all {state.settings.max_concurrent_jobs} job slots are busy; retry shortly"
        )


async def execute(
    state: AppState, fn: Callable[..., Any], *, work_dir: bool = False
) -> Any:
    """Run one blocking pipeline job off the event loop.

    The CV endpoint does not come through here — it answers before its job is
    done (see :mod:`cv_server.api.jobs`). What is left are the short
    ones, which still want the concurrency limit and, for a render, a scratch
    directory on local disk. They keep no session: their log is stdout.
    """
    claim_slot(state)
    try:
        if not work_dir:
            return await run_in_threadpool(fn)
        with tempfile.TemporaryDirectory(prefix="ats-strike-") as work:
            return await run_in_threadpool(fn, Path(work))
    finally:
        state.job_slots.release()


def envelope_for(request_id: str, data: Any) -> Dict[str, Any]:
    """Wrap a successful payload under the id it belongs to."""
    return {"request_id": request_id, "ok": True, "data": data}


def envelope_of(data: Any) -> Dict[str, Any]:
    """Wrap a successful payload. The commentary is fetched separately."""
    return envelope_for(current_run().request_id, data)


__all__ = [
    "AppState",
    "get_state",
    "require_token",
    "claim_slot",
    "execute",
    "envelope_for",
    "envelope_of",
]
