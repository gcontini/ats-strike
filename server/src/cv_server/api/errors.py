"""Turning failures into a status code and a sentence.

One table, one handler, one walk: :func:`describe_failure` is the single place
that answers "what does this exception produce on the wire", so a new failure
type is added once rather than once per question asked about it. A request
that fails gets the same body shape as one that succeeds: a cause, and the
request id its lines carry in the server's log.
"""

from __future__ import annotations

import logging
from typing import Tuple

from cv_contracts import Envelope
from cv_pipeline import PipelineError, current_run
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

logger = logging.getLogger("cv_server.api")


class ApiError(Exception):
    """A request the server refuses before any work starts."""

    status = 400
    kind = "bad_request"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class MissingPart(ApiError):
    kind = "missing_part"


class BadPart(ApiError):
    kind = "bad_part"


class PartTooLarge(ApiError):
    status = 413
    kind = "part_too_large"


class Unauthorized(ApiError):
    status = 401
    kind = "unauthorized"


class TooManyJobs(ApiError):
    status = 429
    kind = "too_many_jobs"


class UnknownRequest(ApiError):
    status = 404
    kind = "unknown_request"


class JobNotReady(ApiError):
    status = 409
    kind = "job_not_ready"


#: A pipeline failure's kind, and the status it answers with. Any other kind
#: is a 502 — the model never produced something usable, or its endpoint
#: failed, which is a gateway problem rather than ours.
_PIPELINE_STATUS = {
    "missing_part": 400,
    "bad_part": 400,
    "latex_compile": 422,
    "latex_timeout": 504,
    "budget_exceeded": 504,
    "provider_timeout": 504,
}


def _pipeline_message(exc: PipelineError) -> str:
    """``kind [stage]: message (detail)`` — the stage and the detail are what
    tell a reader which of several attempts failed, and on which file. A
    failed compile adds the end of the TeX log on the lines after it."""
    where = f" [{exc.stage}]" if exc.stage else ""
    detail = "".join(f" {key}={value}" for key, value in exc.detail.items())
    message = f"{exc.kind}{where}: {exc}{f' ({detail.strip()})' if detail else ''}"
    log_tail = getattr(exc, "log_tail", "")
    if log_tail:
        return f"{message}\n{log_tail}"
    return message


def describe_failure(exc: Exception) -> Tuple[int, str]:
    """The HTTP status and the one-line cause for a failure, decided once.

    Anything the model provider did wrong is a gateway problem, not ours: the
    server is working, the thing behind it is not. The pipeline has already
    turned a provider's error into a :class:`PipelineError` of its kind. Only
    a genuine bug here earns a 500.
    """
    if isinstance(exc, ApiError):
        return exc.status, f"{exc.kind}: {exc.message}"
    if isinstance(exc, PipelineError):
        return _PIPELINE_STATUS.get(exc.kind, 502), _pipeline_message(exc)
    if isinstance(exc, ValidationError):
        return 422, "validation_error: the uploaded document does not match the expected schema"
    # A bare TimeoutError is still the thing behind us being slow; anything
    # left over is a bug here.
    return (504 if isinstance(exc, TimeoutError) else 500), (
        f"internal_error: {type(exc).__name__}: {exc}"
    )


def failure_response(request_id: str, status: int, message: str) -> JSONResponse:
    """The failure envelope for one request id."""
    envelope = Envelope[str](request_id=request_id, ok=False, error=message)
    headers = {"X-Request-Id": request_id}
    if status == 429:
        headers["Retry-After"] = "30"
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(
        status_code=status,
        content=envelope.model_dump(mode="json", exclude_none=True),
        headers=headers,
    )


def log_failure(exc: Exception, where: str) -> None:
    """Log a failure the way the handler does: a traceback only for a real bug."""
    status = describe_failure(exc)[0]
    if status >= 500 and not isinstance(exc, (ApiError, PipelineError)):
        logger.exception("unhandled error on %s", where)
    else:
        logger.warning("%s -> %d: %s", where, status, exc)


async def handle_exception(request: Request, exc: Exception) -> JSONResponse:
    """Single exception handler for the whole app."""
    log_failure(exc, request.url.path)
    status, message = describe_failure(exc)
    return failure_response(current_run().request_id, status, message)


__all__ = [
    "ApiError",
    "MissingPart",
    "BadPart",
    "PartTooLarge",
    "Unauthorized",
    "TooManyJobs",
    "UnknownRequest",
    "JobNotReady",
    "describe_failure",
    "failure_response",
    "handle_exception",
    "log_failure",
]
