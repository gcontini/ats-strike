"""Stage 3: writing each CV, one at a time per server.

:class:`CvWriter` finishes a job folder in place for one server: the PDF at
the top, and in the server's own subfolder everything the server stored as it
streamed in — its CV document, its LaTeX, each step's result, the images the
LaTeX includes — so it renders again later with no model call. The default
server also writes the cover letter.

Each server is a lane with a queue of its own, and :class:`GenerationWorker`
is the loop around one lane: take the next folder, write it, and tell the
:class:`~.working.WorkingProcessor`, which delivers the folder once every lane
has finished it. ``watch`` and ``clipboard`` run one per lane, each on a
thread of its own; ``submit`` drains them one after the other and waits.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING, List, Mapping, Optional, Sequence

from ..api import CVError
from ..config import Config
from ..cvjob import write_cv
from ..joblog import JobLog
from ..servers import Server
from ..workspace import LETTER_FILENAME, JobFiles
from .calls import Calls
from .terminal import Console, TaggedLog

if TYPE_CHECKING:
    from .working import WorkingProcessor


class CvWriter:
    """Writes a folder's CV, server by server, beside the others."""

    def __init__(self, calls: Mapping[str, Calls], config: Config, *, new: bool = False) -> None:
        #: Server name -> its calls.
        self.calls = calls
        self.config = config
        #: --new: write each CV again rather than replay the run the server
        #: already has for that posting.
        self.new = new

    @property
    def lanes(self) -> List[str]:
        """The servers that take each folder queued for its CV."""
        return [server.name for server in self.config.lanes()]

    def generate(self, folder: Path, server: Server, log: JobLog) -> None:
        """This server's part of a folder: its CV when it is a target, the
        letter when it is the default and one is asked for.

        A failed call is recorded in ``log`` — with every line the server
        logged for it — and raised again for the caller to count.
        """
        calls = self.calls[server.name]
        files = JobFiles(folder, server)
        mode = self.config.cover_letter
        try:
            if server in self.config.targets and mode != "letter_only":
                log.step(f"✍ {server.name}: writing the CV (this takes minutes)...")
                write_cv(calls.api, server, files.outgoing(), say=log.step,
                         store=lambda item: files.store(item, log),
                         new=self.new, verbose=self.config.verbose)
            if server.default and mode in ("yes", "letter_only"):
                self._letter(folder, calls, files, log)
        except CVError as exc:
            calls.failed(log, exc)
            raise

    def pdf(self, folder: Path, written: Sequence[str]) -> Optional[Path]:
        """The PDF a spreadsheet row points at: the default server's when
        there is one, else the first written; ``None`` for a letter only."""
        if self.config.cover_letter == "letter_only":
            return None
        servers = {server.name: server for server in self.config.servers()}
        default = [self.config.default.name] if self.config.default else []
        for name in default + list(written):
            if name in servers and JobFiles(folder, servers[name]).pdf().is_file():
                return JobFiles(folder, servers[name]).pdf()
        return None

    @staticmethod
    def _letter(folder: Path, calls: Calls, files: JobFiles, log: JobLog) -> None:
        server = calls.server
        log.step(f"✉ {server.name}: writing the cover letter...")
        letter = calls.make(lambda: calls.api.letter(server.pack(), files.outgoing(),
                                                     server.settings))
        for item in letter.items:
            files.store(item, log)
        (folder / LETTER_FILENAME).write_text(letter.text, encoding="utf-8")
        log.step(f"Letter Path: {LETTER_FILENAME}")


class GenerationWorker:
    """One server's CV queue, one folder at a time: on a thread of its own,
    or drained right here by ``submit``."""

    def __init__(
        self, working: "WorkingProcessor", writer: CvWriter, console: Console, server: Server,
        *, poll_seconds: float = 0.5,
    ) -> None:
        self.working = working
        self.writer = writer
        self.console = console
        self.server = server
        self.poll_seconds = poll_seconds
        #: The folder being written right now, for "finishing ..." on q.
        self.current: Optional[str] = None

    @property
    def queue(self) -> "queue.Queue[Path]":
        return self.working.generations[self.server.name]

    def run(self, stop: threading.Event) -> None:
        """Until stopped. A folder already started is always finished first."""
        while not stop.is_set():
            try:
                folder = self.queue.get(timeout=self.poll_seconds)
            except queue.Empty:
                continue
            self.process(folder)

    def drain(self) -> None:
        """Every folder queued now, on the calling thread."""
        while True:
            try:
                folder = self.queue.get_nowait()
            except queue.Empty:
                return
            self.process(folder)

    def busy(self) -> Optional[str]:
        """What is being written right now, for "finishing ..." on q."""
        current = self.current
        return f"the {self.server.name} CV of {current}" if current else None

    def process(self, folder: Path) -> None:
        """This server's part of one folder, then tell the working processor."""
        if not folder.is_dir():
            return
        self.current = folder.name
        log = TaggedLog(self.console, folder.name)
        try:
            self.writer.generate(folder, self.server, log)
        except CVError:
            ok = False  # in the log already; counted by the working processor
        else:
            ok = True
        finally:
            self.current = None
        self.working.finished(folder, self.server.name, log, ok)


__all__ = ["CvWriter", "GenerationWorker"]
