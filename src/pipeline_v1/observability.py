"""What the server reports about a run: its log lines and its token spend.

Responses say what happened, not how. A CV job streams the commentary as
events in its session — every log line, and every model call with its
duration and token counts — and ends with the run's total. Every other
request has no session: its lines go to stdout, under its request id, and
nowhere else.

One piece does the collecting: a :class:`logging.Handler` that copies every
record emitted during a request into that request's :class:`Run`, which hands
it on if the run has a session to write to. Pipeline code keeps calling
``logger.info(...)`` and never imports this module.

The active run is found through a :class:`~contextvars.ContextVar`. A CV job
runs in a thread of its own and re-establishes it with :func:`use_run`, so a
line emitted deep inside the CV pipeline still lands in the right job's
session — and so does one from the nodes LangGraph runs side by side, which it
starts in a copy of the job's context. They report at the same time, so a run
counts under a lock. Outside a request — a test, a REPL — the default is
:data:`NULL_RUN`, which discards.
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Callable, Iterator, Optional, Set

#: Root logger name; everything in the pipeline logs under it.
LOGGER_ROOT = "cv_pipeline"

#: Bounds, so one pathological run cannot produce an unbounded session.
MAX_LOG_ENTRIES = 2000
MAX_MESSAGE_CHARS = 4000


@dataclass
class LogEntry:
    """One line of a run's log: what ``sink`` is handed as a ``log``."""

    ts: datetime
    level: str
    stage: str
    message: str


@dataclass
class ModelCall:
    """One model call: what ``sink`` is handed as a ``usage``."""

    role: str
    model: str
    seconds: float
    input_tokens: int
    thinking_tokens: int
    output_tokens: int


@dataclass
class TokenUsage:
    """What a run's model calls cost, added up."""

    calls: int = 0
    seconds: float = 0.0
    input_tokens: int = 0
    thinking_tokens: int = 0
    output_tokens: int = 0


class Run:
    """The log lines and model calls of one request.

    ``sink`` is where they go as events: set for a CV job, to its session's
    append; unset for any other request. ``total`` adds up every model call
    either way. ``collect=False`` discards everything, which is what
    :data:`NULL_RUN` — the default outside a request — does.
    """

    def __init__(self, request_id: str, *, collect: bool = True) -> None:
        self.request_id = request_id
        self.sink: Optional[Callable[[str, Any], None]] = None
        self.total = TokenUsage()
        self._collect = collect
        self._lines = 0
        self._lock = Lock()

    # --- collection (called by the handler and by ModelSelector) -----------
    def add_log(self, level: str, stage: str, message: str) -> None:
        if not self._collect or self.sink is None:
            return
        with self._lock:
            self._lines += 1
            lines = self._lines
        if lines > MAX_LOG_ENTRIES:
            if lines == MAX_LOG_ENTRIES + 1:
                self._emit("log", self._entry(
                    "WARNING", "run", f"further log lines dropped (cap {MAX_LOG_ENTRIES})"
                ))
            return
        self._emit("log", self._entry(level, stage, message[:MAX_MESSAGE_CHARS]))

    def add_call(self, call: ModelCall) -> None:
        """Count one model call into the total, and report it."""
        if not self._collect:
            return
        with self._lock:
            self.total.calls += 1
            self.total.seconds += call.seconds
            self.total.input_tokens += call.input_tokens
            self.total.thinking_tokens += call.thinking_tokens
            self.total.output_tokens += call.output_tokens
        self._emit("usage", call)

    def _emit(self, kind: str, payload: Any) -> None:
        if self.sink is not None:
            self.sink(kind, payload)

    @staticmethod
    def _entry(level: str, stage: str, message: str) -> LogEntry:
        return LogEntry(ts=datetime.now(timezone.utc), level=level, stage=stage, message=message)


NULL_RUN = Run("-", collect=False)

_run: ContextVar[Run] = ContextVar("cv_run", default=NULL_RUN)
_stage: ContextVar[str] = ContextVar("cv_stage", default="-")


def current_run() -> Run:
    return _run.get()


def current_stage() -> str:
    return _stage.get()


@contextmanager
def use_run(run: Run) -> Iterator[Run]:
    """Make ``run`` the active one for this context (and any thread it hands
    work to through anyio)."""
    token = _run.set(run)
    try:
        yield run
    finally:
        _run.reset(token)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Label everything logged or called inside as belonging to ``name``."""
    token = _stage.set(name)
    try:
        yield
    finally:
        _stage.reset(token)


class RunCollectingHandler(logging.Handler):
    """Copies each record into the active :class:`Run`."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            current_run().add_log(record.levelname, current_stage(), record.getMessage())
        except Exception:  # logging must never break the request
            self.handleError(record)


class _RequestIdFilter(logging.Filter):
    """Adds ``%(request_id)s`` to every record, for the stdout format."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = current_run().request_id
        return True


_configured: Set[str] = set()


def configure_logging(*roots: str, level: Optional[str] = None) -> None:
    """Install stdout logging + the run collector on each logger root given
    (default: this package's). Idempotent per root.

    Called by the API's lifespan and by the ``ats-strike`` entry point;
    importing the package installs nothing.
    """
    for root in roots or (LOGGER_ROOT,):
        if root in _configured:
            continue
        _configured.add(root)

        logger = logging.getLogger(root)
        logger.setLevel(level or os.getenv("ATS_LOG_LEVEL", "INFO").upper())
        logger.propagate = False

        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s")
        )
        stream.addFilter(_RequestIdFilter())
        logger.addHandler(stream)
        logger.addHandler(RunCollectingHandler())


__all__ = [
    "LOGGER_ROOT",
    "LogEntry",
    "ModelCall",
    "TokenUsage",
    "Run",
    "configure_logging",
    "current_run",
    "stage",
    "use_run",
]
