"""One server call, and what to keep of it when it fails.

Both the inbox and the CV writer call the server, and "a failure goes into
the job's log with the request it belongs to" is one decision in one place.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from cv_contracts import Envelope

from ..api import CVApi, CVError
from ..joblog import JobLog

T = TypeVar("T")


class Calls:
    """The API, plus what its failures leave in a job's log."""

    def __init__(self, api: CVApi) -> None:
        self.api = api

    def make(self, call: Callable[[], Envelope[T]]) -> T:
        """Make one call; its payload."""
        return call().data

    def failed(self, log: JobLog, exc: CVError) -> None:
        """A call failed: say so, and under which request."""
        log.step(f"✗ {exc}")
        if exc.request_id:
            log.step(f"   request id: {exc.request_id}")


__all__ = ["Calls"]
