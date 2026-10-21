"""The terminal, shared by the stages' threads.

The background stages and the question you are answering all print, so every
line from the background goes through one :class:`Console`: one lock, so two
lines never interleave mid-line, and a hold, so nothing scrolls the posting
you are reading off the screen. Lines that arrive while you are being asked
are printed right after your answer.

Your keyboard is read on a thread of its own (:class:`Keyboard`) because ``q``
has to work when nothing is waiting for you — and then there is no
``input()`` running to type it into.
"""

from __future__ import annotations

import queue
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator, List, Optional

from ..joblog import JobLog

#: What :meth:`Keyboard.get` returns, once, when input ends (Ctrl-D, a closed
#: stdin). The EOT character: it cannot be typed as a line by accident.
END_OF_INPUT = "\x04"


class Console:
    """Every background line, printed whole and never over a question."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: Optional[List[str]] = None

    def say(self, line: str) -> None:
        with self._lock:
            if self._held is not None:
                self._held.append(line)
                return
            print(line, flush=True)

    @contextmanager
    def holding(self) -> Iterator[None]:
        """Keep other threads' lines back while you are being asked."""
        with self._lock:
            self._held = []
        try:
            yield
        finally:
            with self._lock:
                held, self._held = self._held or [], None
                for line in held:
                    print(line, flush=True)


class TaggedLog(JobLog):
    """A job's ``log.log``, echoed on the console under the job's name.

    Several stages write to one job's log over its life — the inbox, then the
    CV writer, maybe on a later run — so it is appended to, never rewritten.
    """

    def __init__(self, console: Console, tag: str) -> None:
        super().__init__()
        self.console = console
        self.tag = tag

    def step(self, message: str) -> None:
        super().step(message)
        self.console.say(f"[{self.tag}] {message}")

    def append_to(self, path: Path) -> Path:
        """Add what was collected since the last write, then start afresh."""
        if self.lines:
            with path.open("a", encoding="utf-8") as fh:
                fh.write("\n".join(self.lines) + "\n")
            self.lines.clear()
        return path


class Keyboard:
    """The lines you type, read on a daemon thread and queued."""

    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream
        self._lines: "queue.Queue[str]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._read, name="keyboard", daemon=True)
            self._thread.start()

    def get(self, timeout: float) -> Optional[str]:
        """The next line, stripped; ``None`` if none came within ``timeout``.

        The end of input comes back once as :data:`END_OF_INPUT`; after that
        every call just waits out its timeout.
        """
        try:
            return self._lines.get(timeout=timeout)
        except queue.Empty:
            return None

    def read(self, poll_seconds: float = 0.5) -> str:
        """The next line, however long it takes — waited for in short steps,
        so Ctrl-C lands on every platform."""
        while True:
            line = self.get(poll_seconds)
            if line is not None:
                return line

    def discard_typed_ahead(self) -> None:
        """Forget what was typed before the question was on screen: a second
        ``y`` meant for the last posting must not approve this one."""
        ended = False
        while True:
            try:
                ended = self._lines.get_nowait() == END_OF_INPUT or ended
            except queue.Empty:
                break
        if ended:
            self._lines.put(END_OF_INPUT)

    def _read(self) -> None:
        while True:
            try:
                line = self._stream.readline()
            except (OSError, ValueError):  # closed or unreadable: same as the end
                line = ""
            if not line:
                self._lines.put(END_OF_INPUT)
                return
            self._lines.put(line.strip())


__all__ = ["Console", "TaggedLog", "Keyboard", "END_OF_INPUT"]
