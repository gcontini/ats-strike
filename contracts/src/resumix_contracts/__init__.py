"""The wire format shared by the resumix server and its clients.

Nothing here talks to a network, a filesystem or a model — it is the set of
shapes both sides agree on, so neither has to import the other:

- ``payloads`` — :class:`CVStatus`, each step of a CV job, and
  :class:`RenderedCV`, the finished document, LaTeX and PDF in one.
- ``events`` — the session a CV job streams: :data:`EVENT_PAYLOADS` names
  each event type's payload, :class:`CVArtifact` is a step's intermediate
  result, :class:`CVResult` and :class:`CVFailure` end it.
- ``jd`` — :class:`JDAnalysis` (``analysis.json``) and :class:`JDDetection`.
- ``envelope`` — :class:`Envelope`, the uniform response body, and
  :class:`LogEntry`, one line of the server's log.
- ``guess`` — :func:`static_jd_guess`, the free pre-check both sides run.

The CV document itself has no schema here: it is whatever the model wrote
merged with whatever your ``candidate_data.json`` holds, and only your LaTeX
template has an opinion about it.
"""

from .envelope import Envelope, LogEntry
from .events import (
    EVENT_PAYLOADS,
    TERMINAL_EVENTS,
    Cleanup,
    CVArtifact,
    CVFailure,
    CVResult,
    CVSession,
    ModelCall,
    TokenUsage,
)
from .guess import MAX_JD_CHARS, MIN_JD_CHARS, static_jd_guess
from .jd import JDAnalysis, JDDetection
from .payloads import CoverLetter, CVStatus, RenderedCV, ServerStatus

__all__ = [
    "JDAnalysis",
    "JDDetection",
    "CVStatus",
    "RenderedCV",
    "CoverLetter",
    "ServerStatus",
    "Envelope",
    "LogEntry",
    "TokenUsage",
    "ModelCall",
    "CVArtifact",
    "CVResult",
    "CVFailure",
    "CVSession",
    "Cleanup",
    "EVENT_PAYLOADS",
    "TERMINAL_EVENTS",
    "static_jd_guess",
    "MIN_JD_CHARS",
    "MAX_JD_CHARS",
]
