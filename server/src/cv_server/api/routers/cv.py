"""Writing and rendering CVs.

``POST /v1/cv`` is the expensive one — minutes of model calls, then a LaTeX
compile or a few — so it does not wait: it starts the job, answers with its id
and writes the CV in the background. ``GET /v1/cv/{id}/events`` streams the
job's session as it grows, from the first event or from any later one, and
``DELETE /v1/cv/{id}`` is the client saying it has everything.

The id is the client's to choose (``X-Request-Id``), and the client derives
it from the posting, so posting the same job description twice finds the
session already there instead of paying for a second CV.

What the job does with the pack, the job and the settings is the pipeline's
business: this module only reads them (:mod:`..multipart`) and hands them on.

``POST /v1/cv/render`` is the cheap, deterministic half: a document you have
edited, or a ``.tex`` you have edited, compiled again for nothing.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, List, Optional

import anyio
from cv_contracts import TERMINAL_EVENTS, Cleanup, CVSession, Envelope, Rendered
from cv_pipeline import current_run
from fastapi import APIRouter, Header, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ...jobstore import JobDir
from .. import jobs
from ..deps import claim_slot, envelope_for, envelope_of, execute, get_state
from ..errors import ApiError, JobNotReady, UnknownRequest
from ..multipart import JOB, PACK, SETTINGS, read_request

router = APIRouter()

#: How often a stream looks for new events, and how long it may stay silent
#: before it says it is still there — a model call can take a minute.
FOLLOW_SECONDS = 1.0
PING_SECONDS = 15.0


@router.post("/cv", status_code=202, response_model=Envelope[CVSession],
             response_model_exclude_none=True)
async def create_cv(
    request: Request,
    pack: List[UploadFile] = PACK,
    job: List[UploadFile] = JOB,
    settings: Optional[str] = SETTINGS,
) -> dict:
    """Start one CV job, or find the one this id already has."""
    state = get_state(request)
    parts = await read_request(pack, job, settings, max_bytes=state.settings.max_part_bytes)

    def work(scratch, sink) -> str:
        return state.pipeline.cv(parts.pack, parts.job, parts.settings, scratch, sink)

    run = current_run()
    store = state.jobs
    # Expired sessions go first, so an old one for this id cannot be found.
    await run_in_threadpool(store.prune, state.settings.session_ttl_days * 86400)
    if await run_in_threadpool(store.open, run.request_id) is not None:
        return envelope_of(CVSession(reattached=True))

    claim_slot(state)
    session = store.create(run.request_id)
    jobs.start(state, session, run, work)
    return envelope_of(CVSession(reattached=False))


@router.get("/cv/{request_id}/events", response_class=StreamingResponse)
async def cv_events(
    request: Request, request_id: str,
    last_event_id: int = Header(0, description="Send only the events after this one"),
) -> StreamingResponse:
    """The session as server-sent events: everything after ``Last-Event-ID``,
    then each new event as it is written, until the job's last one."""
    job = _session(request, request_id)
    return StreamingResponse(_follow(job, last_event_id), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@router.delete("/cv/{request_id}", response_model=Envelope[None],
               response_model_exclude_none=True)
async def acknowledge(request: Request, request_id: str) -> dict:
    """The client has everything: delete the session."""
    job = _session(request, request_id)
    if job.running:
        raise JobNotReady(
            f"job {request_id} is still running; acknowledge it once its last event is in"
        )
    await run_in_threadpool(job.delete)
    return envelope_for(request_id, None)


@router.delete("/cv", response_model=Envelope[Cleanup], response_model_exclude_none=True)
async def reset(
    request: Request,
    everything: Optional[str] = Query(None, alias="all", description="Required: ?all"),
) -> dict:
    """Delete every session whose job is not running."""
    if everything is None:
        raise ApiError("DELETE /v1/cv deletes every session: say so with ?all")
    deleted, running = await run_in_threadpool(get_state(request).jobs.prune, 0)
    return envelope_of(Cleanup(deleted=deleted, running=running))


def _session(request: Request, request_id: str) -> JobDir:
    job = get_state(request).jobs.open(request_id)
    if job is None:
        raise UnknownRequest(
            f"no CV session {request_id} — it was acknowledged, it is older than "
            "the server keeps sessions, or it never existed"
        )
    return job


async def _follow(job: JobDir, after: int) -> AsyncIterator[str]:
    """Every event after ``after``, then the new ones as they come, until the
    job's last. A comment line now and then keeps a quiet stream open."""
    # A session that has already ended has nothing more coming: what is after
    # ``after`` is all there is, even if that is nothing.
    ended = not await run_in_threadpool(lambda: job.running)
    quiet = 0.0
    while True:
        events = await run_in_threadpool(job.events, after)
        for number, kind, data in events:
            yield f"id: {number}\nevent: {kind}\ndata: {json.dumps(data)}\n\n"
            if kind in TERMINAL_EVENTS:
                return
            after = number
        if ended:
            return
        if events:
            quiet = 0.0
        elif quiet >= PING_SECONDS:
            yield ": ping\n\n"
            quiet = 0.0
        await anyio.sleep(FOLLOW_SECONDS)
        quiet += FOLLOW_SECONDS


@router.post("/cv/render", response_model=Envelope[Rendered],
             response_model_exclude_none=True)
async def render_cv(
    request: Request,
    pack: List[UploadFile] = PACK,
    job: List[UploadFile] = JOB,
    settings: Optional[str] = SETTINGS,
) -> dict:
    """Compile the job's ``cv.json`` (or a hand-edited ``cv.tex``) into a PDF."""
    state = get_state(request)
    parts = await read_request(pack, job, settings, max_bytes=state.settings.max_part_bytes)

    def render(work, sink) -> None:
        state.pipeline.render(parts.pack, parts.job, work, sink)

    _, items = await execute(state, render, work_dir=True)
    return envelope_of(Rendered(items=items))
