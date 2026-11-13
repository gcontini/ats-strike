"""``log.log``: what happened to one job.

The client's own steps, and every line a server logged for the job — the
analysis, the CV as it streamed in, the letter — each under that server's
name. A server's lines include one per model call with its duration and
token counts, and a last one adding them up, so the bill is readable without
a provider dashboard.
"""

from __future__ import annotations

from datetime import datetime
from typing import List


class JobLog:
    """Collects lines for one job, for its ``log.log``."""

    def __init__(self) -> None:
        self.lines: List[str] = []

    def step(self, message: str) -> None:
        """A client-side step."""
        self._write(f"{datetime.now().isoformat(timespec='seconds')} {message}")

    def server_line(self, server_name: str, text: str) -> None:
        """One line a server logged, indented under the client's steps."""
        self._write(f"  {server_name} {text}")

    def _write(self, line: str) -> None:
        self.lines.append(line)


__all__ = ["JobLog"]
