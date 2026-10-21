"""Everything the person sees and answers.

Kept behind :class:`Confirmer` so the decision to spend tokens has one
implementation for a terminal and one for ``--yes``, and so ``clipboard`` and
``submit`` can be tested without one.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional, Protocol

from cv_contracts import JDAnalysis

from .stages.terminal import END_OF_INPUT, Keyboard


@dataclass(frozen=True)
class Decision:
    """What to do with an analyzed posting."""

    submit: bool
    url: Optional[str] = None
    quit: bool = False


class Confirmer(Protocol):
    """Asked once per posting, before anything is spent on it."""

    def confirm(self, analysis: JDAnalysis) -> Decision: ...


def print_analysis(analysis: JDAnalysis) -> None:
    """The analysis table — the thing you actually read before deciding."""
    print("\n" + "=" * 56)
    print("📊 JD ANALYSIS")
    print("=" * 56)
    print(f"Job title         : {analysis.job_title}")
    print(f"Match %           : {analysis.match_percentage}%  "
          f"({analysis.match_rationale or 'no rationale'})")
    print(f"Work location     : {analysis.work_location or 'not specified'}")
    print(f"Work mode         : {analysis.work_mode}")
    print(f"Expected salary   : {analysis.expected_salary or 'not specified'}")
    print(f"Max salary        : "
          f"{analysis.max_salary if analysis.max_salary != -1 else 'not found'}")
    print(f"Salary match      : "
          f"{'not specified' if analysis.salary_match is None else analysis.salary_match}")
    print(f"Hard skills       : {', '.join(analysis.hard_skills) or 'n/a'}")
    print(f"Soft skills       : {', '.join(analysis.soft_skills) or 'n/a'}")
    print(f"Company           : {analysis.company_name}")
    if analysis.company_evaluation:
        print(f"Company eval      : {analysis.company_evaluation}")
    print(f"Posting URL       : {analysis.posting_url or 'not detected'}")
    print(f"Gaps              : {analysis.gaps}")
    print(f"Personal pref     : {analysis.pers_preference_score} — {analysis.pers_preferences}")
    print(f"Should apply      : {analysis.should_apply} — {analysis.should_apply_reason}")
    print("=" * 56)


class PromptConfirmer:
    """Asks on the terminal, and takes the posting URL while it is there.

    Answers come from the :class:`~.stages.terminal.Keyboard`, not
    ``input()``: ``clipboard`` also listens for ``q`` while nothing is being
    asked, and stdin can have only one reader.
    """

    def __init__(self, keyboard: Keyboard, *, poll_seconds: float = 0.5) -> None:
        self.keyboard = keyboard
        self.poll_seconds = poll_seconds

    def confirm(self, analysis: JDAnalysis) -> Decision:
        # A second y meant for the last posting must not submit this one.
        self.keyboard.discard_typed_ahead()
        print_analysis(analysis)
        print("Paste the posting URL to submit, y = submit without link, "
              "n/s = skip, q = quit", flush=True)
        while True:
            print("> ", end="", flush=True)
            answer = self.keyboard.read(self.poll_seconds)
            if answer == END_OF_INPUT:
                print(flush=True)
                return Decision(submit=False, quit=True)
            if not answer:
                continue
            low = answer.lower()
            if low in ("q", "quit"):
                return Decision(submit=False, quit=True)
            if low in ("n", "s", "skip"):
                return Decision(submit=False)
            if low in ("y", "yes"):
                return Decision(submit=True)
            if answer.startswith("http"):
                if len(answer) > 300:
                    print("⚠ URL is too long (>300 chars). Please paste a shorter URL.", flush=True)
                    continue
                return Decision(submit=True, url=answer)
            # Anything else is not one of y/n/s/q or a URL — ask again rather
            # than guessing: silently treating stray input as "submit" is
            # what let a mistyped skip turn into a submission.
            print("⚠ y = submit, n/s = skip, q = quit, or paste the posting URL.", flush=True)


class AutoConfirmer:
    """Says yes to every new posting — ``--yes``, for an unattended run."""

    def confirm(self, analysis: JDAnalysis) -> Decision:
        print_analysis(analysis)
        print("→ submitting automatically (--yes)", flush=True)
        return Decision(submit=True, url=analysis.posting_url)


def ask_recovery(files: int, folders: int) -> str:
    """``working/`` is not empty at startup: clean it or resume it."""
    print(f"\n⚠ working/ still holds {files} file(s) and {folders} job folder(s) "
          "from a previous run.", flush=True)
    print("  r = resume them, c = clean them out, q = quit", flush=True)
    while True:
        try:
            answer = (input("> ").strip() or "").lower()
        except EOFError:
            return "quit"
        if answer in ("r", "resume"):
            return "resume"
        if answer in ("c", "clean"):
            return "clean"
        if answer in ("q", "quit"):
            return "quit"


def fail(message: str, code: int = 1) -> None:
    """Report a fatal problem the way a CLI should: stderr, non-zero exit."""
    print(f"✗ {message}", file=sys.stderr, flush=True)
    raise SystemExit(code)


def warn(message: str) -> None:
    """A non-fatal heads-up: stderr, execution continues."""
    print(f"⚠ {message}", file=sys.stderr, flush=True)


__all__ = ["Decision", "Confirmer", "PromptConfirmer", "AutoConfirmer",
           "print_analysis", "ask_recovery", "fail", "warn"]
