"""One server, its calls, and what to keep of them.

The inbox, the clipboard and the CV writers all call servers, and "a failure
goes into the job's log with the request it belongs to" is one decision in
one place. So is "a reply's log lines go into the job's log, under the
server's name", for the replies that have no job folder yet to store into.
"""

from __future__ import annotations

from typing import Callable, Iterable, TypeVar

from cv_contracts import Envelope, StoreItem

from ..api import CVApi, CVError
from ..joblog import JobLog
from ..servers import Server

T = TypeVar("T")


class Calls:
    """A server, the API that reaches it, and what its replies leave in a job's log."""

    def __init__(self, server: Server, api: CVApi) -> None:
        self.server = server
        self.api = api

    def make(self, call: Callable[[], Envelope[T]]) -> T:
        """Make one call; its payload."""
        return call().data

    def keep_log(self, log: JobLog, items: Iterable[StoreItem]) -> None:
        """A reply's log lines, into ``log``. Anything else it sent has nowhere
        to go yet: skipped, with a line saying so."""
        for item in items:
            if (item.location, item.type) == ("CV", "log"):
                log.server_line(self.server.name,
                                item.content().decode("utf-8", errors="replace"))
            else:
                log.step(f"⚠ {self.server.name}: skipped a {item.location}/{item.type} item "
                         f"named {item.name!r}")

    def failed(self, log: JobLog, exc: CVError) -> None:
        """A call failed: say so, and under which request."""
        log.step(f"✗ {self.server.name}: {exc}")
        if exc.request_id:
            log.step(f"   request id: {exc.request_id}")


__all__ = ["Calls"]
