"""Writing and rendering CVs.

``POST /v1/cv`` is the expensive one — minutes of model calls and several
LaTeX compiles — so it does not wait: it starts the job, answers with its id
and writes the CV in the background. ``GET /v1/cv/{id}/events`` streams the
job's session as it grows, from the first event or from any later one, and
``DELETE /v1/cv/{id}`` is the client saying it has everything.

The id is the client's to choose (``X-Request-Id``), and the client derives
it from the posting, so posting the same job description twice finds the
session already there instead of paying for a second CV.

It takes ``candidate_data`` as well as ``candidate_profile``. No model is
shown it — only the template is — but the loop renders the CV to count its
pages, and a page count taken without the contact details is not the page
count of the CV you will send.

``POST /v1/cv/render`` is the cheap, deterministic half: a document you have
edited, or a ``.tex`` you have edited, compiled again for nothing.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional, Tuple

import anyio
from fastapi import APIRouter, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from resumix_contracts import TERMINAL_EVENTS, Cleanup, CVSession, Envelope, RenderedCV
from starlette.concurrency import run_in_threadpool

from ...bundle import CandidateInputs
from ...jobstore import JobDir
from ...observability import current_run
from ...pipeline.cv_generator import CVGenerator
from ...pipeline.cv_renderer import CVRenderer
from .. import jobs
from ..deps import claim_slot, envelope_for, envelope_of, execute, get_state
from ..errors import ApiError, BadPart, JobNotReady, MissingPart, UnknownRequest
from ..multipart import images_part, json_part, text_part

router = APIRouter()

#: How often a stream looks for new events, and how long it may stay silent
#: before it says it is still there — a model call can take a minute.
FOLLOW_SECONDS = 1.0
PING_SECONDS = 15.0


@router.post("/cv", status_code=202, response_model=Envelope[CVSession],
             response_model_exclude_none=True)
async def create_cv(
    request: Request,
    jd: Optional[UploadFile] = File(None, description="The job description, as a file"),
    jd_text: Optional[str] = Form(None, description="The job description, as a field"),
    candidate_profile: Optional[UploadFile] = File(
        None, description="candidate_profile.json — what the model tailors from"
    ),
    candidate_data: Optional[UploadFile] = File(
        None, description="candidate_data.json — what the template prints"
    ),
    sys_prompt_cv: Optional[UploadFile] = File(None, description="Override the CV prompt"),
    sys_prompt_highlight: Optional[UploadFile] = File(
        None, description="Override the highlighting prompt"
    ),
    sys_prompt_review: Optional[UploadFile] = File(
        None, description="Override the review prompt"
    ),
    template: Optional[UploadFile] = File(
        None, description="Override resume.tex.jinja (also used for the page check)"
    ),
    images: List[UploadFile] = File(
        [], description="Images the template includes, each by its own file name"
    ),
    temperature: Optional[float] = Form(
        None, description="Sampling temperature override for the CV model only"
    ),
    presence_penalty: Optional[float] = Form(
        None, description="Presence penalty override for the CV model only"
    ),
    pages: Optional[int] = Form(None, description="Page limit the CV must fit (default: 2)"),
) -> dict:
    """Start one CV job, or find the one this id already has."""
    state = get_state(request)
    limit = state.settings.max_part_bytes
    if pages is not None and pages < 1:
        raise BadPart("pages must be at least 1")

    job_description = await text_part(jd, jd_text, name="jd", max_bytes=limit, required=True)
    profile = await json_part(
        candidate_profile, name="candidate_profile", max_bytes=limit, required=True
    )
    data = await json_part(
        candidate_data, name="candidate_data", max_bytes=limit, required=True
    )

    bundle = state.bundle.with_overrides(
        sys_prompt_cv=await text_part(sys_prompt_cv, name="sys_prompt_cv", max_bytes=limit),
        sys_prompt_highlight=await text_part(
            sys_prompt_highlight, name="sys_prompt_highlight", max_bytes=limit
        ),
        sys_prompt_review=await text_part(
            sys_prompt_review, name="sys_prompt_review", max_bytes=limit
        ),
        template_source=await text_part(template, name="template", max_bytes=limit),
        images=await images_part(images, max_bytes=limit),
    )
    candidate = CandidateInputs(profile=profile, data=data)

    llm = state.llm.with_overrides(
        "cv", temperature=temperature, presence_penalty=presence_penalty
    )

    def build(work: Path, progress) -> Tuple[Dict[str, Any], str, bytes, str]:
        return CVGenerator(
            llm=llm,
            bundle=bundle,
            candidate=candidate,
            work_dir=work,
            max_attempts=state.settings.max_attempts,
            deadline=time.monotonic() + state.settings.request_budget_seconds,
            latex_timeout=state.settings.latex_timeout,
            page_limit=pages,
            progress=progress,
        ).generate(job_description)

    run = current_run()
    store = state.jobs
    # Expired sessions go first, so an old one for this id cannot be found.
    await run_in_threadpool(store.prune, state.settings.session_ttl_days * 86400)
    if await run_in_threadpool(store.open, run.request_id) is not None:
        return envelope_of(CVSession(reattached=True))

    claim_slot(state)
    job = store.create(run.request_id)
    run.sink = job.append
    jobs.start(state, job, run, build)
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


@router.post("/cv/render", response_model=Envelope[RenderedCV],
             response_model_exclude_none=True)
async def render_cv(
    request: Request,
    document: Optional[UploadFile] = File(None, description="A CV document, as JSON"),
    tex: Optional[UploadFile] = File(None, description="Ready LaTeX source, compiled as-is"),
    template: Optional[UploadFile] = File(None, description="Override resume.tex.jinja"),
    images: List[UploadFile] = File(
        [], description="Images the template includes, each by its own file name"
    ),
) -> dict:
    """Compile a document (or a hand-edited ``.tex``) into a PDF."""
    state = get_state(request)
    limit = state.settings.max_part_bytes

    tex_source = await text_part(tex, name="tex", max_bytes=limit)
    doc_data = await json_part(document, name="document", max_bytes=limit)
    if tex_source is None and doc_data is None:
        raise MissingPart("send either 'document' (CV data as JSON) or 'tex' (LaTeX source)")
    if tex_source is not None and doc_data is not None:
        raise BadPart("send 'document' or 'tex', not both")

    bundle = state.bundle.with_overrides(
        template_source=await text_part(template, name="template", max_bytes=limit),
        images=await images_part(images, max_bytes=limit),
    )

    def job(work: Path) -> RenderedCV:
        renderer = CVRenderer(
            template_source=bundle.template_source,
            template_name=bundle.template_name,
            assets=bundle.assets,
            work_dir=work,
            latex_timeout=state.settings.latex_timeout,
        )
        result = (
            renderer.compile_tex(tex_source)
            if doc_data is None
            else renderer.render_document(doc_data)
        )
        # No document echoed back: whoever asked for this render already has it.
        return RenderedCV.from_bytes(tex=result.tex, pdf=result.pdf)

    return envelope_of(await execute(state, job, work_dir=True))
