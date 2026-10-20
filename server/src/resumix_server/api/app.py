"""The FastAPI application.

Assembled in one place: logging, the request context, the shared state, the
routers and the single exception handler. Everything expensive happens once in
the lifespan — three model clients and the default resource bundle — because a
request must not pay for startup.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Optional
from uuid import uuid4

from fastapi import Depends, FastAPI
from openai import APIError
from pydantic import ValidationError
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from resumix_contracts import CVFailure, TokenUsage

from ..jobstore import SAFE_ID
from ..observability import LOGGER_ROOT, Run, configure_logging, use_run
from ..pipeline.errors import PipelineError
from .deps import AppState, require_token
from .errors import ApiError, handle_exception
from .routers import cv, health, jd, letter
from .settings import Settings

logger = logging.getLogger(f"{LOGGER_ROOT}.api")

API_PREFIX = "/v1"


class RequestContextMiddleware:
    """Give every request an id and a :class:`Run` to log into.

    Plain ASGI rather than ``BaseHTTPMiddleware`` on purpose: this sets a
    contextvar that the endpoint — and the worker thread it hands the pipeline
    to — must see, and that only holds reliably when no task boundary sits in
    between.

    An inbound ``X-Request-Id`` is honoured only if it looks like one. For a
    CV job the client derives it from the posting, and it names the session's
    directory under the work root, so anything else is a way to write where
    the server was not asked to.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or [])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1").strip()
        request_id = incoming if SAFE_ID.match(incoming) else uuid4().hex[:12]

        async def send_with_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"].append((b"x-request-id", request_id.encode("latin-1")))
            await send(message)

        with use_run(Run(request_id)):
            await self.app(scope, receive, send_with_id)


def create_app(
    settings: Optional[Settings] = None, state: Optional[AppState] = None
) -> FastAPI:
    """Build the application.

    ``state`` is the injection point: pass one built with fake models and
    the whole API can be exercised with no provider key and no network.
    Left out, it is built from the environment at startup.
    """
    settings = settings or (state.settings if state else Settings.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging()
        app.state.resumix = state or AppState.build(settings)
        _fail_orphans(app.state.resumix)
        logger.info(
            "  \U0001f680 resumix-server ready (jobs<=%d, auth %s, pdflatex %s)",
            settings.max_concurrent_jobs,
            "on" if settings.api_token else "off",
            "yes" if app.state.resumix.pdflatex else "NO",
        )
        yield

    from importlib.metadata import version

    try:
        server_version = version("resumix-server")
    except Exception:  # running from a source tree that was never installed
        server_version = "unknown"

    app = FastAPI(
        title="resumix",
        summary="Turn a job description into tailored CV data and a compiled LaTeX PDF.",
        version=server_version,
        lifespan=lifespan,
    )
    app.add_middleware(RequestContextMiddleware)
    # Expected failures are registered by type so Starlette treats them as
    # answers, not crashes; only a genuine bug reaches the catch-all (which
    # answers and then re-raises, so it is still logged as one).
    for failure in (ApiError, PipelineError, ValidationError, APIError):
        app.add_exception_handler(failure, handle_exception)
    app.add_exception_handler(Exception, handle_exception)

    app.include_router(health.router, tags=["health"])
    guarded = [Depends(require_token)]
    app.include_router(cv.router, prefix=API_PREFIX, tags=["cv"], dependencies=guarded)
    app.include_router(jd.router, prefix=API_PREFIX, tags=["jd"], dependencies=guarded)
    app.include_router(letter.router, prefix=API_PREFIX, tags=["letter"], dependencies=guarded)
    return app


def _fail_orphans(state: AppState) -> None:
    """Close jobs a previous process left running.

    A fresh process owns no worker threads, so a session that has not ended
    is provably orphaned — by a restart, a crash, a SIGKILL or an instance
    released mid-job, none of which a shutdown hook would have caught. It
    ends with an error, so a client following it stops waiting. Run one
    instance per work root: the semaphore and the workers are per-process, so
    a second one would declare a live job dead. What the job had spent is not
    known any more; the total says nothing.
    """
    for job in state.jobs.all():
        if not job.running:
            continue
        job.append("error", CVFailure(
            http_status=503, total=TokenUsage(),
            error="unavailable: the server restarted while this job was running",
        ).model_dump(mode="json"))
        logger.warning("  ⚠ job %s was left running by a previous process", job.request_id)


app = create_app()

__all__ = ["create_app", "app", "API_PREFIX"]
