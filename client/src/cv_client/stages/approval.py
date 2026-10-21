"""Stage 2: you, deciding about each CHECK posting, one at a time.

This runs on the main thread, so Ctrl-C lands here and stops everything at
once. ``q`` is the gentle stop: it works at a question and while nothing is
waiting for you, and it lets the posting being analysed and the CV being
written finish first.

    y    write its CV (paste the posting URL instead to keep it too)
    d    discard it
    s/n  next: ask again after the others
    q    quit
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Optional, Protocol

from cv_contracts import JDAnalysis

from ..ui import print_analysis
from .jobfolder import read_analysis, read_jd
from .terminal import END_OF_INPUT, Console, Keyboard, TaggedLog

if TYPE_CHECKING:
    from .working import WorkingProcessor

#: A pasted URL longer than this is a paste gone wrong, not a link.
MAX_URL_CHARS = 300

PROMPT = "y = write the CV (or paste the posting URL), d = discard, s/n = next, q = quit"
HINT = "⚠ y, d, s/n or q — or paste the posting URL (at most 300 characters)."
IDLE_HINT = ("nothing is waiting for you. q = let the running work finish, then quit; "
             "Ctrl-C = stop now")


@dataclass(frozen=True)
class Answer:
    """What to do with one posting."""

    action: Literal["approve", "discard", "skip", "quit"]
    url: Optional[str] = None


def parse_answer(text: str) -> Optional[Answer]:
    """One line you typed, or ``None`` for anything that is not an answer.

    Nothing unrecognised is ever read as "approve": a stray keystroke must
    not become a CV job.
    """
    answer = text.strip()
    low = answer.lower()
    if low in ("y", "yes"):
        return Answer("approve")
    if low in ("d", "discard"):
        return Answer("discard")
    if low in ("s", "skip", "n", "next"):
        return Answer("skip")
    if low in ("q", "quit"):
        return Answer("quit")
    if answer.startswith("http") and len(answer) <= MAX_URL_CHARS:
        return Answer("approve", url=answer)
    return None


class Reviewer(Protocol):
    """Shows one posting and returns what you decided."""

    def review(self, jd_text: str, analysis: JDAnalysis) -> Answer: ...


class TerminalReviewer:
    """The posting and its analysis on screen, your answer from the keyboard."""

    def __init__(self, keyboard: Keyboard, *, poll_seconds: float = 0.5) -> None:
        self.keyboard = keyboard
        self.poll_seconds = poll_seconds

    def review(self, jd_text: str, analysis: JDAnalysis) -> Answer:
        self.keyboard.discard_typed_ahead()
        print("\n" + "=" * 56, flush=True)
        print(jd_text.strip(), flush=True)
        print_analysis(analysis)
        print(PROMPT, flush=True)
        while True:
            print("> ", end="", flush=True)
            line = self.keyboard.read(self.poll_seconds)
            if line == END_OF_INPUT:
                print(flush=True)
                return Answer("quit")
            answer = parse_answer(line)
            if answer is not None:
                return answer
            if line:
                print(HINT, flush=True)


class HumanApproval:
    """The approval queue, one posting at a time, until you quit."""

    def __init__(
        self,
        working: "WorkingProcessor",
        reviewer: Reviewer,
        keyboard: Keyboard,
        console: Console,
        *,
        ask: bool = True,
        poll_seconds: float = 0.25,
    ) -> None:
        self.working = working
        self.reviewer = reviewer
        self.keyboard = keyboard
        self.console = console
        #: False with --yes: nothing is ever asked, only q is listened for.
        self.ask = ask
        self.poll_seconds = poll_seconds

    def run(self, stop: threading.Event) -> None:
        """Returns when you quit, or when something else stopped the watcher."""
        while not stop.is_set():
            if self.ask:
                folder = self._next_posting()
                if folder is not None:
                    if not self.review_one(folder):
                        return
                    continue
            line = self.keyboard.get(self.poll_seconds)
            if line is None or line == "":
                continue
            if line == END_OF_INPUT:
                if self.ask:
                    return  # nobody left to answer
                continue  # --yes: an unattended run may have no keyboard at all
            if line.lower() in ("q", "quit"):
                return
            self.console.say(IDLE_HINT)

    def review_one(self, folder: Path) -> bool:
        """Ask about one posting. ``False`` means you quit.

        Quitting leaves the folder exactly as it was: it is asked about again
        at the next start.
        """
        if not folder.is_dir():
            return True
        try:
            jd_text, analysis = read_jd(folder), read_analysis(folder)
        except (OSError, ValueError) as exc:
            # Edited by hand while it waited: file it with why, keep asking.
            log = TaggedLog(self.console, folder.name)
            log.step(f"✗ cannot be shown any more: {exc}")
            self.working.reject(folder, log)
            return True
        with self.console.holding():
            answer = self.reviewer.review(jd_text, analysis)
        if answer.action == "quit":
            return False
        if answer.action == "skip":
            self.working.requeue(folder)
        elif answer.action == "discard":
            self.working.discard(folder)
        else:
            self.working.approve(folder, url=answer.url)
        return True

    def _next_posting(self) -> Optional[Path]:
        try:
            return self.working.approvals.get(timeout=self.poll_seconds)
        except queue.Empty:
            return None


__all__ = ["Answer", "parse_answer", "Reviewer", "TerminalReviewer", "HumanApproval",
           "PROMPT", "MAX_URL_CHARS"]
