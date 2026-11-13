"""Running a CV job after its request has already been answered.

``POST /v1/cv`` accepts and returns; the writing happens in a thread of its
own, which reports what it does by appending events to the job's session:
what runs now as a ``status``, and as a ``store`` each step's result as it is
ready, every line it logs, then the CV. That is the whole mechanism: a client
follows the session, so nothing has to be held open for the minutes a CV
takes, and a client that goes away misses nothing.

A plain thread, not the request's: the job has to outlive the response, and
the shared threadpool is where uploads are read, so a job parked there would
block them. The bound on live jobs stays the one semaphore — claimed on the
event loop before the thread starts, released by the thread when it ends.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from threading import Thread
from typing import Any, Callable

from cv_contracts import Done, Failure, Status
from cv_pipeline import Run, Sink, use_run

from ..jobstore import JobDir
from .deps import AppState
from .errors import describe_failure, log_failure
from .items import dump, log_item, output_item, total_item


class _SessionSink:
    """The pipeline's sink for one job: each report becomes an event."""

    def __init__(self, job: JobDir) -> None:
        self.job = job
        # The first event of every session, appended before the thread
        # starts, so a client that follows the session at once finds it.
        self.last = Status(status="started").model_dump(mode="json")
        job.append("status", self.last)

    def status(self, status: str, detail: str = "") -> None:
        # A status identical to the one before says nothing.
        current = Status(status=status, detail=detail).model_dump(mode="json")
        if current != self.last:
            self.job.append("status", current)
            self.last = current

    def store(self, output: Any) -> None:
        self.job.append("store", dump(output_item(output)))

    def record(self, kind: str, payload: Any) -> None:
        """The run's sink: a log line is stored, a model call is in the total."""
        if kind == "log":
            self.job.append("store", dump(log_item(payload)))


def start(state: AppState, job: JobDir, run: Run, work: Callable[[Path, Sink], str]) -> None:
    """Hand one accepted job to a thread. Returns as soon as it is running.

    ``work`` is what produces the CV: it is given a scratch directory on local
    disk and the session's sink, and returns one line about the run.
    """
    sink = _SessionSink(job)
    run.sink = sink.record

    def worker() -> None:
        # A fresh thread starts with an empty context, so the run it logs into
        # has to be re-established here or every pipeline line is discarded.
        with use_run(run):
            try:
                with tempfile.TemporaryDirectory(prefix="ats-strike-") as scratch:
                    summary = work(Path(scratch), sink)
                ending = "done", Done(detail=summary)
            except Exception as exc:  # noqa: BLE001 — stored, not swallowed
                log_failure(exc, "POST /v1/cv")
                status, message = describe_failure(exc)
                ending = "error", Failure(http_status=status, error=message)
            job.append("store", dump(total_item(run.total)))
            state.job_slots.release()
            # Last of all: once a session has ended, a client may acknowledge
            # it and have it deleted, so nothing may still be on its way in.
            job.append(ending[0], ending[1].model_dump(mode="json"))

    Thread(target=worker, name=f"ats-strike-{job.request_id}", daemon=True).start()


__all__ = ["start"]
