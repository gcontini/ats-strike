"""Running a CV job after its request has already been answered.

``POST /v1/cv`` accepts and returns; the writing happens in a thread of its
own, which reports what it does by appending events to the job's session:
each step as it starts, each intermediate result as it is ready, every line
it logs, every model call. That is the whole mechanism: a client follows the
session, so nothing has to be held open for the minutes a CV takes, and a
client that goes away misses nothing.

A plain thread, not the request's: the job has to outlive the response, and
the shared threadpool is where uploads are read, so a job parked there would
block them. The bound on live jobs stays the one semaphore — claimed on the
event loop before the thread starts, released by the thread when it ends.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from threading import Thread
from typing import Any, Callable, Dict, Tuple

from resumix_contracts import CVArtifact, CVFailure, CVResult, CVStatus, RenderedCV

from ..jobstore import JobDir
from ..observability import Run, use_run
from .deps import AppState
from .errors import describe_failure, log_failure


def start(
    state: AppState,
    job: JobDir,
    run: Run,
    build: Callable[
        [Path, Callable[[str, str], None], Callable[[str, Any], None]],
        Tuple[Dict[str, Any], str, bytes, str],
    ],
) -> None:
    """Hand one accepted job to a thread. Returns as soon as it is running.

    ``build`` is what produces the CV: it is given a scratch directory on
    local disk, a progress sink and an artifact sink, and returns the merged
    document, the LaTeX and the PDF it was rendered into, and one line about
    the run.
    """
    # The first step is announced before the thread starts, so a client that
    # follows the session at once finds it. The generator's own first report
    # repeats it, and a status identical to the one before says nothing.
    last = CVStatus(status="jd_evaluation").model_dump(mode="json")
    job.append("status", last)

    def progress(status: str, detail: str) -> None:
        nonlocal last
        current = CVStatus(status=status, detail=detail).model_dump(mode="json")
        if current != last:
            job.append("status", current)
            last = current

    def artifact(name: str, data: Any) -> None:
        job.append("artifact", CVArtifact(name=name, data=data).model_dump(mode="json"))

    def work() -> None:
        # A fresh thread starts with an empty context, so the run it logs into
        # has to be re-established here or every pipeline line is discarded.
        with use_run(run):
            try:
                with tempfile.TemporaryDirectory(prefix="resumix-") as scratch:
                    document, tex, pdf, summary = build(Path(scratch), progress, artifact)
                ending = "result", CVResult(
                    cv=RenderedCV.from_bytes(tex=tex, pdf=pdf, document=document),
                    detail=summary, total=run.total,
                )
            except Exception as exc:  # noqa: BLE001 — stored, not swallowed
                log_failure(exc, "POST /v1/cv")
                status, message = describe_failure(exc)
                ending = "error", CVFailure(http_status=status, error=message, total=run.total)
            state.job_slots.release()
            # Last of all: once a session has ended, a client may acknowledge
            # it and have it deleted, so nothing may still be on its way in.
            job.append(ending[0], ending[1].model_dump(mode="json"))

    Thread(target=work, name=f"resumix-{job.request_id}", daemon=True).start()


__all__ = ["start"]
