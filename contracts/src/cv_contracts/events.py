"""A CV job as the client sees it: a session of events.

``POST /v1/cv`` starts a job, or finds the one already started for the same
posting, and ``GET /v1/cv/{id}/events`` streams what it does as server-sent
events. Each event carries an ``id`` (its position in the session, from 1), a
type, and one of the payloads below as JSON:

============ ============== ==============================================
type         payload        when
============ ============== ==============================================
``status``   Status         shown to a person, never stored
``store``    StoreItem      a thing to store: a step, a log line, the CV
``done``     Done           the job is done — the last event
``error``    Failure        the job failed — the last event
============ ============== ==============================================

A client skips an event type it does not know. The server keeps the events
until the client acknowledges the session with ``DELETE /v1/cv/{id}``, so a
client that lost its connection, or comes back later, replays them from the
start.
"""

from __future__ import annotations

import base64
from typing import Dict, Optional, Type, Union

from pydantic import BaseModel, Field


class StoreItem(BaseModel):
    """A thing to store: the ``store`` event, and each item of a REST reply.

    ``location`` and ``type`` are plain strings: a client skips a value it
    does not know, so a server can add one without breaking an older client.
    """

    location: str = Field(description="CV (the job folder) | CACHE (<server folder>/cache)")
    type: str = Field(description="log (append a line) | step (write a file) | cv (the final PDF)")
    name: str = Field("", description="Relative path, '/'-separated; empty for a log")
    text: Optional[str] = Field(None, description="The content, when it is UTF-8 text")
    base64: Optional[str] = Field(None, description="The content, when it is binary")

    @classmethod
    def from_content(
        cls, location: str, type: str, name: str, content: Union[str, bytes]
    ) -> "StoreItem":
        """Text as ``text``, bytes as ``base64``."""
        if isinstance(content, str):
            return cls(location=location, type=type, name=name, text=content)
        return cls(location=location, type=type, name=name,
                   base64=base64.b64encode(content).decode("ascii"))

    def content(self) -> bytes:
        """The bytes to write, whichever way they travelled."""
        if self.base64 is not None:
            return base64.b64decode(self.base64)
        return (self.text or "").encode("utf-8")


class Status(BaseModel):
    """Where a job is: the ``status`` event. Shown, never stored."""

    status: str = Field(description="What runs now, in the server's own words")
    detail: str = Field("", description="One line about what just finished; may span lines")


class Done(BaseModel):
    """The job is done: the ``done`` event, always the last."""

    detail: str = Field(description="One line about the whole run, for a person")


class Failure(BaseModel):
    """Why the job failed: the ``error`` event, always the last."""

    http_status: int = Field(description="The status the failure maps to: 400, 422, 502, 504, ...")
    error: str = Field(description="What went wrong, its kind and its stage")


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
    "status": Status,
    "store": StoreItem,
    "done": Done,
    "error": Failure,
}

#: The types that end a session's stream: nothing comes after them.
TERMINAL_EVENTS = ("done", "error")


__all__ = [
    "StoreItem",
    "Status",
    "Done",
    "Failure",
    "CVSession",
    "Cleanup",
    "EVENT_PAYLOADS",
    "TERMINAL_EVENTS",
]
