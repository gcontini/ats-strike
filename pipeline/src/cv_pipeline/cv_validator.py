"""Judging the CV: everything wrong with one attempt, in one list.

Two things can be wrong with a tailored CV — it can say something the master
profile does not support, and it can run over the page limit — and they used
to be found in two different places, one round apart. They are found together
here, and come back as one list of violations written in the words the model
is about to be given: the reviewer's own complaints first, what to cut
appended last.

The render is unconditional. A ``pdflatex`` pass costs about a second and a
content review costs the most expensive call in the pipeline, so there is
nothing to save by withholding the page count from a CV the reviewer has
already rejected — and measuring it anyway means a CV that is both overstated
and too long is told about both in one round instead of two.

The review, by contrast, runs until it passes once. After that later rounds
only shorten a CV the reviewer has already accepted, and re-confirming that
would spend that same expensive call to hear the same answer. A review that
gets no usable answer at all leaves the CV unreviewed — not passed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional

from .models import ModelSelector, Usage
from .observability import LOGGER_ROOT, stage
from .cv_renderer import CVRenderer
from .cv_schema import TailoredCVData
from .errors import ModelOutputError

logger = logging.getLogger(f"{LOGGER_ROOT}.cv")

#: The most complaints one review can hand back. The prompt asks for at most
#: this many, most important first; this is the backstop when a model writes
#: more, so a runaway review cannot bloat the next round's prompt.
MAX_VIOLATIONS = 10

#: Text lines past the limit from which trimming duties is not enough and
#: whole work experiences have to go.
SERIOUS_OVERFLOW_LINES = 15


def length_advice(pages: int, limit: int, overflow_lines: int, earlier: int) -> str:
    """What to cut from a CV that came out too long, worded for the model.

    Asked for in whole duties, never in characters: told to remove characters,
    the model shortens sentences, and a paragraph that loses sixty characters
    but not a line frees no room at all. ``overflow_lines`` counts text only,
    so it misses an image or a gap that moved over with it; ``earlier`` — how
    many rounds in a row were already too long — adds one duty per round, since
    each of them proves the cut before was too small.
    """
    escalation = (
        f"It has been too long {earlier + 1} times in a row: the cuts made so "
        "far were not enough. "
        if earlier else ""
    )
    if overflow_lines >= SERIOUS_OVERFLOW_LINES:
        return (
            f"CV in previous attempt is rejected: EXTREMELY long. It is {pages} "
            f"pages, {overflow_lines} lines past the mandatory {limit}-page limit. "
            + escalation
            + "A heavy reduction of the content is needed: condense the summary "
            "and remove one or more work experiences completely. Aim for 3 work "
            "experiences and 15 duties in TOTAL over the whole CV."
        )
    duties = max(1, (overflow_lines + 1) // 2) + earlier
    return (
        f"CV in previous attempt is rejected: too long. It is {pages} pages, "
        f"{overflow_lines} line(s) past the mandatory {limit}-page limit. "
        + escalation
        + f"DELETE {duties} whole duty(s): fewer duties, not shorter "
        "ones - shortening sentences frees no line.\n Copy the rest of the CV as is."
    )


@dataclass(frozen=True)
class ValidationResult:
    """What is wrong with one CV, and how long it came out.

    The CV passes with no ``violations`` and ``reviewed``. When the list is
    not empty, every entry is an instruction the model can act on, and the
    length complaint — when there is one — is the last of them. ``reviewed``
    is false only when the review got no usable answer: nothing is known to
    be wrong with the content, and nothing is known to be right.
    """

    violations: List[str]
    pages: int
    reviewed: bool = True


class CVValidator:
    """Says what is wrong with one CV: its content, then its length."""

    def __init__(
        self,
        *,
        llm: ModelSelector,
        renderer: CVRenderer,
        master_profile: Mapping[str, Any],
        review_prompt: str,
        usage: Usage,
    ) -> None:
        self.llm = llm
        self.renderer = renderer
        self._master_profile = dict(master_profile)
        self._review_prompt = review_prompt
        #: The caller's ledger: the review's tokens count toward the same
        #: total the progress lines report.
        self._usage = usage
        #: True once the content review has passed. Public because the caller
        #: names the step it is about to run from it.
        self.content_reviewed = False
        #: Rounds in a row that came out too long; the length advice grows
        #: with it.
        self._too_long = 0

    # --- public API ---------------------------------------------------------
    def validate(
        self, cv_data: TailoredCVData, document: Mapping[str, Any], *, attempt: int
    ) -> ValidationResult:
        """Review the content, render it, measure it, and report everything wrong.

        Both halves of the same CV are passed in because they have different
        audiences: the reviewer is shown ``cv_data`` and never the candidate's
        own details, while the renderer needs ``document`` — the merged one —
        or it measures a page count that is not the page count of the CV you
        will send.

        The render happens whatever the review said. It is cheap, and the model
        can only fix what it is told about in the round it is told.
        """
        violations: List[str] = []
        reviewed = True

        if not self.content_reviewed:
            logger.info("--- content review ---")
            with stage("cv.review"):
                found = self._review_cv_data(cv_data, attempt)
            reviewed = found is not None
            violations = found or []
            if not reviewed:
                logger.warning("  ⚠ The review got no usable answer — the CV is not reviewed")
            elif violations:
                logger.info(
                    "  ✗ Review rejected CV (%d violation(s))", len(violations)
                )
                for violation in violations:
                    logger.info("      - %s", violation)
            else:
                logger.info("  ✓ Review OK")
                self.content_reviewed = True

        logger.info("--- render %d ---", attempt + 1)
        with stage("render"):
            result = self.renderer.render_document(
                document, stem=f"attempt_{attempt + 1}"
            )
        if result.pages > self.renderer.page_limit:
            # Handed to the model verbatim, as the last thing it has to fix.
            advice = length_advice(
                result.pages, self.renderer.page_limit,
                result.overflow_lines, self._too_long,
            )
            self._too_long += 1
            logger.info("  Page check: %d pages -> %s", result.pages, advice)
            violations.append(advice)
        else:
            self._too_long = 0
            logger.info("  ✓ Length OK (%d pages)", result.pages)

        return ValidationResult(violations=violations, pages=result.pages, reviewed=reviewed)

    # --- the content review -------------------------------------------------
    @staticmethod
    def _violations_from(reply: str) -> List[str]:
        """The reviewer's complaints: one per line of its plain-text reply.

        "OK" is a pass. A line too short to say where and what is dropped,
        which also disposes of a stray code fence.
        """
        content = reply.strip()
        if content.rstrip(".").upper() == "OK":
            return []
        lines = (line.strip() for line in content.splitlines())
        return [line for line in lines if len(line) > 15][:MAX_VIOLATIONS]

    def _review_cv_data(self, cv_data: TailoredCVData, attempt: int) -> Optional[List[str]]:
        """Review generated CV content against the master profile.

        Uses the ``review`` role with the review system prompt; the master
        profile and the generated CV are the only inputs, and the reply is
        plain text, one violation per line, or "OK". Its sampling is its own
        role's business — ``models.toml`` ships it at ``temperature = 0``,
        because a reviewer that samples draws a different set of violations
        every round and the loop has no fixed point to settle on.
        Returns ``None`` when the review got no usable answer (empty or cut
        off, every attempt).
        """

        review_request = ("Review the GENERATED CV below against the candidate MASTER "
            "PROFILE. Attempt %d\n") % (attempt + 1)
        review_request += (
            "--------------------------------------------\n"
            "MASTER PROFILE:\n"
            f"{json.dumps(self._master_profile, indent=2)}\n"
            "--------------------------------------------\n"
            "GENERATED CV:\n"
            f"{cv_data.model_dump_json(indent=2)}\n"
            "--------------------------------------------\n"
            "Reply with the violations, one per line and nothing else - or "
            "OK if the CV is acceptable."
        )
        if attempt > 0:
            review_request += ("The GENERATED CV has already been reviewed "+str(attempt +1)+
                               " times, it should be ok by now. "
            "Flag only outstanding syntax and logic issues this turn, if there are any.")

        try:
            reply = self.llm.call_llm(
                "review", str, self._review_prompt,
                [{"role": "user", "content": review_request}], usage=self._usage,
            )
        except ModelOutputError:
            return None
        return self._violations_from(reply)


__all__ = ["CVValidator", "ValidationResult", "length_advice"]
