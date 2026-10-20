"""The response envelope every endpoint returns, success or failure.

Deliberately small: what you asked for, or why you cannot have it. The
running commentary — including one line per model call with its token spend —
is kept out of the response. A CV job streams it as ``log`` events (see
:mod:`resumix_contracts.events`); every other request writes it to the
server's own log, under its request id.

Every response carries its ``request_id``, and so does the ``X-Request-Id``
header.
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class LogEntry(BaseModel):
    """One line of the server's account of a request: the ``log`` event.

    Model calls appear here like anything else: the line for a completed call
    carries its duration and its token counts.
    """

    ts: datetime = Field(description="When the line was emitted")
    level: str = Field(description="INFO | WARNING | ERROR")
    stage: str = Field(description="Pipeline stage, e.g. 'cv.generate' or 'render'")
    message: str = Field(description="The message itself")


class Envelope(BaseModel, Generic[T]):
    """Uniform response body. ``data`` is present iff ``ok`` is true."""

    request_id: str = Field(description="Echoed in X-Request-Id")
    ok: bool = Field(description="True when 'data' holds a result")
    data: Optional[T] = Field(None, description="The endpoint's payload")
    error: Optional[str] = Field(None, description="Why it failed. Set iff ok is false")


__all__ = ["LogEntry", "Envelope"]
