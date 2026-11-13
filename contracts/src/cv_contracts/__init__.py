"""The wire format shared by the ats-strike server and its clients.

Nothing here talks to a network, a filesystem or a model — it is the set of
shapes both sides agree on, so neither has to import the other. It knows no
pipeline: no prompt names, no model roles, no step names, no analysis schema.
A client sends a pack of files and stores what the server tells it to store.

- ``events`` — the session a CV job streams: :data:`EVENT_PAYLOADS` names
  each event type's payload; :class:`StoreItem` is a thing to store.
- ``payloads`` — the ``data`` of each REST reply, and :class:`ServerStatus`.
- ``envelope`` — :class:`Envelope`, the uniform response body.
- ``guess`` — :func:`static_jd_guess`, the free pre-check.

:data:`PROTOCOL` is the protocol number; a client refuses a server that
reports another.
"""

from .envelope import Envelope
from .events import (
    EVENT_PAYLOADS,
    TERMINAL_EVENTS,
    Cleanup,
    CVSession,
    Done,
    Failure,
    Status,
    StoreItem,
)
from .guess import MAX_JD_CHARS, MIN_JD_CHARS, static_jd_guess
from .payloads import Analysed, Detection, Letter, Rendered, ServerStatus

#: The wire protocol this package describes.
PROTOCOL = 2

__all__ = [
    "PROTOCOL",
    "Envelope",
    "StoreItem",
    "Status",
    "Done",
    "Failure",
    "CVSession",
    "Cleanup",
    "EVENT_PAYLOADS",
    "TERMINAL_EVENTS",
    "ServerStatus",
    "Detection",
    "Analysed",
    "Rendered",
    "Letter",
    "static_jd_guess",
    "MIN_JD_CHARS",
    "MAX_JD_CHARS",
]
