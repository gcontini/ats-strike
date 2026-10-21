"""Stage 1: whatever lands in ``--in``, checked, analysed and handed over.

Nothing is claimed up front any more. A posting stays where you dropped it
until its analysis has been handed to the
:class:`~.working.WorkingProcessor`, and only then is it deleted — so a crash
in between costs one more analysis at the next start, never the posting.

- a ``.txt`` file is a posting: dropped at once if its ``JOB_POSTING:`` URL
  is one already applied to, discarded or in progress; otherwise the free
  check, the server's check, the analysis, then it is handed over;
- any other file goes to ``error/`` without a call;
- a folder must hold ``jd.txt`` and a valid ``analysis.json`` (an earlier
  job, one from ``error/`` to retry): it is handed over whole, or goes to
  ``error/`` with why.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

from ..api import CVError
from ..config import Config
from ..duplicates import posting_url_in
from .analysis import analyse
from .calls import Calls
from .jobfolder import classify
from .terminal import Console, TaggedLog

if TYPE_CHECKING:
    from .working import WorkingProcessor

POLL_SECONDS = 2.0
#: A file whose size stopped changing for this long is finished being written.
SETTLE_SECONDS = 1.0


class InputProcessor:
    """Reads the inbox, one entry at a time, until stopped."""

    def __init__(
        self,
        inbox: Path,
        calls: Calls,
        config: Config,
        working: "WorkingProcessor",
        console: Console,
        *,
        poll_seconds: float = POLL_SECONDS,
        settle_seconds: float = SETTLE_SECONDS,
    ) -> None:
        self.inbox = Path(inbox)
        self.calls = calls
        self.config = config
        self.working = working
        self.console = console
        self.poll_seconds = poll_seconds
        self.settle_seconds = settle_seconds
        #: The entry being handled right now, for "finishing ..." on q.
        self.current: Optional[str] = None

    def run(self, stop: threading.Event) -> None:
        """Until stopped. An entry already started is always finished first."""
        while not stop.is_set():
            self.scan(stop)
            stop.wait(self.poll_seconds)

    def busy(self) -> Optional[str]:
        """What is being analysed right now, for "finishing ..." on q."""
        current = self.current
        return f"the analysis of {current}" if current else None

    def scan(self, stop: threading.Event) -> None:
        """Everything in the inbox once, in name order."""
        entries = sorted(self.inbox.iterdir()) if self.inbox.is_dir() else []
        for entry in entries:
            if stop.is_set():
                return
            if entry.name.startswith(".") or not self._settled(entry, stop):
                continue
            if stop.is_set():
                return
            self.handle(entry)

    def handle(self, entry: Path) -> None:
        """One entry, from the inbox to the working processor or to error/."""
        self.current = entry.name
        log = TaggedLog(self.console, entry.name)
        try:
            if entry.is_dir():
                self._folder(entry, log)
            elif entry.suffix.lower() != ".txt":
                log.step("✗ not a .txt file")
                self.working.reject(entry, log)
            else:
                self._posting(entry, log)
        except CVError as exc:
            self.calls.failed(log, exc)
            self.working.reject(entry, log, failed=True)
        finally:
            self.current = None

    def _posting(self, entry: Path, log: TaggedLog) -> None:
        text = entry.read_bytes().decode("utf-8", errors="replace")
        url = posting_url_in(text)
        if url and self.working.seen_url(url, log):
            log.step("🗑 dropped from the inbox, nothing sent")
            entry.unlink()
            return
        analysis = analyse(self.calls, self.config, text, log)
        if analysis is None:
            self.working.reject(entry, log)
            return
        self.working.submit(text, analysis, log)
        # Handed over (or already known): the inbox copy is done with.
        entry.unlink()

    def _folder(self, entry: Path, log: TaggedLog) -> None:
        where, why = classify(entry)
        if where == "error":
            log.step(f"✗ {why}")
            self.working.reject(entry, log)
            return
        self.working.submit_folder(entry, log)
        shutil.rmtree(entry)

    def _settled(self, path: Path, stop: threading.Event) -> bool:
        """Still being copied in? Let it finish."""
        try:
            first = _footprint(path)
            if self.settle_seconds:
                stop.wait(self.settle_seconds)
            return first is not None and _footprint(path) == first
        except OSError:
            return False


def _footprint(path: Path) -> Optional[Tuple[int, int]]:
    """``(file count, total bytes)`` — a folder's own size says nothing about its contents."""
    if path.is_file():
        return 1, path.stat().st_size
    if path.is_dir():
        files = [p for p in path.rglob("*") if p.is_file()]
        return len(files), sum(p.stat().st_size for p in files)
    return None


__all__ = ["InputProcessor", "POLL_SECONDS", "SETTLE_SECONDS"]
