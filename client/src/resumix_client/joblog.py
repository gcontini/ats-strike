"""``log.log``: what happened to one job.

The client's own steps, and — for a CV — every line the server logged while
writing it, as it streamed in. The server's lines include one per model call
with its duration and token counts, so the bill is readable without a
provider dashboard.
"""

from __future__ import annotations

from datetime import datetime
from typing import List

from resumix_contracts import LogEntry


class JobLog:
    """Collects lines for one job, for its ``log.log``."""

    def __init__(self) -> None:
        self.lines: List[str] = []

    def step(self, message: str) -> None:
        """A client-side step."""
        self._write(f"{datetime.now().isoformat(timespec='seconds')} {message}")

    def server_line(self, entry: LogEntry) -> None:
        """One line the server logged, indented under the client's steps."""
        self._write(f"  {entry.ts.isoformat(timespec='seconds')} "
                    f"{entry.level:<7} [{entry.stage}] {entry.message}")

    def _write(self, line: str) -> None:
        self.lines.append(line)


__all__ = ["JobLog"]
