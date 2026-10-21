"""``clipboard``'s main thread: every new posting asked about as it arrives.

Each posting is analysed and asked about here, one at a time; once you say
yes its CV goes to the CV writer's thread and this goes straight back to the
clipboard, so you read the next posting while the last one's CV is written.

Leftovers still waiting for an answer are asked about first. Between
postings it listens for ``q``, the gentle stop: the CV being written is
finished, the ones still queued wait in ``working/`` for the next start.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Protocol

from .intake import Intake
from .terminal import END_OF_INPUT, Console, Keyboard

if TYPE_CHECKING:
    from .working import WorkingProcessor

#: How often the clipboard is read. Typing q is answered at once regardless.
POLL_SECONDS = 2.0

IDLE_HINT = ("watching the clipboard. q = let the CV being written finish, then quit; "
             "Ctrl-C = stop now")


class JDSource(Protocol):
    """Where postings come from."""

    def poll(self) -> Optional[str]:
        """The next new text, or ``None`` if nothing new has arrived."""
        ...


class Listener:
    """Asks about each posting as it arrives, until you quit."""

    def __init__(
        self,
        source: JDSource,
        intake: Intake,
        working: "WorkingProcessor",
        keyboard: Keyboard,
        console: Console,
        *,
        assume_yes: bool = False,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self.source = source
        self.intake = intake
        self.working = working
        self.keyboard = keyboard
        self.console = console
        #: --yes: an unattended run may have no keyboard at all.
        self.assume_yes = assume_yes
        self.poll_seconds = poll_seconds

    def run(self, stop: threading.Event) -> None:
        """Returns when you quit, or when something else stopped the watcher."""
        while not stop.is_set():
            folder = self._leftover()
            if folder is not None:
                if not self.intake.review(folder):
                    return
                continue
            text = self.source.poll()
            if text is not None:
                if not self.intake.take(text, "clipboard"):
                    return
                continue
            line = self.keyboard.get(self.poll_seconds)
            if not line:
                continue
            if line == END_OF_INPUT:
                if self.assume_yes:
                    continue
                return  # nobody left to answer
            if line.lower() in ("q", "quit"):
                return
            self.console.say(IDLE_HINT)

    def _leftover(self) -> Optional[Path]:
        try:
            return self.working.approvals.get_nowait()
        except queue.Empty:
            return None


__all__ = ["JDSource", "Listener", "POLL_SECONDS"]
