"""A posting you hand over yourself — the clipboard, or ``cv submit``.

Analysed, then asked about before anything is spent on it, whatever its
``should_apply`` says: you answer every one. Its folder waits in ``working/``
marked ``PENDING`` while you read, so a posting you quit on — or that a
Ctrl-C catches mid-question — is asked about again at the next start rather
than decided for you.

    URL or y   write its CV (the URL is kept in analysis.json)
    n or s     discard it
    q          quit: it stays PENDING in working/
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from cv_contracts import JDAnalysis

from ..api import CVError
from ..config import Config
from ..ui import Confirmer
from .analysis import analyse
from .calls import Calls
from .jobfolder import read_analysis
from .terminal import Console, TaggedLog

if TYPE_CHECKING:
    from .working import WorkingProcessor


class Intake:
    """One posting at a time, from its text to your answer."""

    def __init__(
        self,
        calls: Calls,
        config: Config,
        working: "WorkingProcessor",
        confirmer: Confirmer,
        console: Console,
    ) -> None:
        self.calls = calls
        self.config = config
        self.working = working
        self.confirmer = confirmer
        self.console = console

    def take(self, text: str, label: str, analysis: Optional[JDAnalysis] = None) -> bool:
        """A new posting: analysed (unless ``analysis`` is given), then asked
        about. ``False`` means you quit.

        Nothing has a folder before its analysis, so a posting that is not
        one, or whose analysis fails, is reported and left at that.
        """
        log = TaggedLog(self.console, label)
        if analysis is None:
            try:
                analysis = analyse(self.calls, self.config, text, log)
            except CVError as exc:
                self.calls.failed(log, exc)
                self.working.count_failure()
                return True
            if analysis is None:
                return True
        folder = self.working.hold(text, analysis, log)
        if folder is None:
            return True  # seen before: said where, nothing written
        return self.review(folder)

    def review(self, folder: Path) -> bool:
        """Ask about one folder in ``working/``. ``False`` means you quit, and
        the folder is left exactly as it was."""
        if not folder.is_dir():
            return True
        try:
            analysis = read_analysis(folder)
        except (OSError, ValueError) as exc:
            # Edited by hand while it waited: file it with why, keep going.
            log = TaggedLog(self.console, folder.name)
            log.step(f"✗ cannot be shown any more: {exc}")
            self.working.reject(folder, log)
            return True
        with self.console.holding():
            decision = self.confirmer.confirm(analysis)
        if decision.quit:
            return False
        if decision.submit:
            self.working.approve(folder, url=decision.url)
        else:
            self.working.discard(folder)
        return True


__all__ = ["Intake"]
