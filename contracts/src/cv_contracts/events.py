"""A CV job as the client sees it: a session of events.

``POST /v1/cv`` starts a job, or finds the one already started for the same
posting, and ``GET /v1/cv/{id}/events`` streams what it does as server-sent
events. Each event carries an ``id`` (its position in the session, from 1), a
type, and one of the payloads below as JSON:

============ ============== ==============================================
type         payload        when
============ ============== ==============================================
``status``   CVStatus       each step: jd_evaluation, profile_scoring, ...
``artifact`` CVArtifact     each intermediate result, as a step finishes
``log``      LogEntry       every line the server logged for the job
``usage``    ModelCall      after every model call, failed ones included
``result``   CVResult       the job is done — the last event
``error``    CVFailure      the job failed — the last event
============ ============== ==============================================

The server keeps the events until the client acknowledges the session with
``DELETE /v1/cv/{id}``, so a client that lost its connection, or comes back
later, replays them from the start.
"""

from __future__ import annotations

from typing import Any, Dict, Type

from pydantic import BaseModel, Field

from .envelope import LogEntry
from .payloads import CVStatus, RenderedCV


class TokenUsage(BaseModel):
    """What a run's model calls cost, added up."""

    calls: int = Field(0, description="Model calls made, failed attempts included")
    seconds: float = Field(0.0, description="Time spent waiting on them")
    input_tokens: int = Field(0, description="Prompt tokens")
    thinking_tokens: int = Field(0, description="Reasoning tokens")
    output_tokens: int = Field(0, description="Reply tokens, thinking excluded")


class ModelCall(BaseModel):
    """One model call: the ``usage`` event."""

    role: str = Field(description="Which model role made it: jd_evaluation, cv, validation, ...")
    model: str = Field(description="The model name the provider was asked for")
    seconds: float = Field(description="How long the call took")
    input_tokens: int = Field(description="Prompt tokens")
    thinking_tokens: int = Field(description="Reasoning tokens")
    output_tokens: int = Field(description="Reply tokens, thinking excluded")


class CVArtifact(BaseModel):
    """One intermediate result of a CV job: the ``artifact`` event.

    What each step hands the next one — the job description's key points, the
    scored profile, the chosen format, the CV before and after validation, the
    highlighted CV — as the JSON it is. Only the final PDF is ever sent: it is
    in the result.
    """

    name: str = Field(description="What it is, e.g. key_points or checked_body: a file name")
    data: Any = Field(description="The result itself, as JSON")


class CVResult(BaseModel):
    """The finished CV: the ``result`` event."""

    cv: RenderedCV = Field(description="The merged document, its LaTeX and its PDF")
    detail: str = Field(description="One line about the whole run, for a person")
    total: TokenUsage = Field(description="Every model call of the run, added up")


class CVFailure(BaseModel):
    """Why the job failed: the ``error`` event."""

    http_status: int = Field(description="The status the failure maps to: 422, 502, 504, ...")
    error: str = Field(description="What went wrong, its kind and its stage")
    total: TokenUsage = Field(description="Every model call of the run, added up")


class CVSession(BaseModel):
    """What ``POST /v1/cv`` answers, besides the session's id."""

    reattached: bool = Field(
        description="True when this posting already had a session: nothing new was "
                    "started, and its events replay from the start"
    )


class Cleanup(BaseModel):
    """What ``DELETE /v1/cv?all`` did."""

    deleted: int = Field(description="Sessions deleted")
    running: int = Field(description="Sessions kept because their job is still running")


#: Each event type, and the payload its ``data`` holds.
EVENT_PAYLOADS: Dict[str, Type[BaseModel]] = {
    "status": CVStatus,
    "artifact": CVArtifact,
    "log": LogEntry,
    "usage": ModelCall,
    "result": CVResult,
    "error": CVFailure,
}

#: The types that end a session's stream: nothing comes after them.
TERMINAL_EVENTS = ("result", "error")


__all__ = [
    "TokenUsage",
    "ModelCall",
    "CVArtifact",
    "CVResult",
    "CVFailure",
    "CVSession",
    "Cleanup",
    "EVENT_PAYLOADS",
    "TERMINAL_EVENTS",
]
