"""Writing the cover letter: prose in, prose out.

Much simpler than the CV pipeline — no schema, no renderer, no highlighter,
no validation. Whether the model can look the employer up is the
``letter`` role's own configuration (its ``extra_body``), not this code's.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict, List, Mapping, Optional

from ..models import ModelSelector
from ..observability import LOGGER_ROOT, stage
from .errors import ModelOutputError
from .parsing import strip_fences

logger = logging.getLogger(f"{LOGGER_ROOT}.letter")

#: The letter must read as a letter: bounds catch a stub and a runaway alike.
MIN_WORDS = 180
MAX_WORDS = 450

#: Generate -> validate rounds; the validation error is fed back between them,
#: the same self-correction the CV pipeline uses.
MAX_ATTEMPTS = 2


class LetterGenerator:
    """Generates a plain-text cover letter for one job description.

    Parameters
    ----------
    llm:
        The selector; the letter is written by its ``letter`` role.
    system_prompt:
        The letter prompt for this run (default, or the request's override).
    """

    def __init__(
        self,
        llm: ModelSelector,
        *,
        system_prompt: str,
    ) -> None:
        self.llm = llm
        self.system_prompt = system_prompt

    def _build_user_message(
        self,
        job_description: str,
        profile: Mapping[str, Any],
        candidate_data: Mapping[str, Any],
        analysis: Mapping[str, Any],
    ) -> str:
        """Assemble the single user turn: today's date, the master profile,
        the candidate's contact details (the letter's header block, unlike
        the CV, is written by the model rather than a template — it needs
        the real name, email, phone and LinkedIn, not what the profile
        happens to contain), a trimmed JD-analysis subset (only the fields
        relevant to a letter — the full JDAnalysis carries scoring fields
        with no place in one), and the raw JD.
        """
        analysis_subset = {
            k: analysis.get(k)
            for k in (
                "company_name",
                "job_title",
                "work_location",
                "hard_skills",
                "soft_skills",
                "gaps",
                "posting_url",
            )
            if analysis.get(k) is not None
        }

        return (
            "Write my cover letter for this JOB DESCRIPTION. "
            "Output plain text only, starting at the header block.\n"
            "--------------------------------------------\n"
            f"TODAY'S DATE: {date.today().isoformat()}\n"
            "--------------------------------------------\n"
            "MASTER PROFILE:\n"
            f"{json.dumps(dict(profile), indent=2)}\n"
            "--------------------------------------------\n"
            "CANDIDATE DATA:\n"
            f"{json.dumps(dict(candidate_data), indent=2)}\n"
            "--------------------------------------------\n"
            "JD ANALYSIS:\n"
            f"{json.dumps(analysis_subset, indent=2)}\n"
            "--------------------------------------------\n"
            "JOB DESCRIPTION:\n"
            f"{job_description}\n"
        )

    @staticmethod
    def _validate_letter(text: str) -> None:
        """Light sanity checks on the generated letter. Raises ``ValueError``
        with an actionable message on the first violation found so it can be
        fed back to the LLM for a corrective retry.
        """
        word_count = len(text.split())
        if not (MIN_WORDS <= word_count <= MAX_WORDS):
            raise ValueError(
                f"Letter is {word_count} words; must be between {MIN_WORDS} "
                f"and {MAX_WORDS} words."
            )
        for open_tok, close_tok in (("[", "]"), ("<<", ">>")):
            if open_tok in text and close_tok in text:
                raise ValueError(
                    f"Letter contains an unfilled placeholder "
                    f"(found '{open_tok}...{close_tok}'). Remove it — rewrite "
                    "the sentence instead of leaving a gap to fill in."
                )
        try:
            text.encode("ascii")
        except UnicodeEncodeError as e:
            raise ValueError(
                f"Letter contains non-ASCII characters: {e}. Use standard "
                "ASCII only (straight quotes, '--' for a dash)."
            ) from e

    def generate(
        self,
        job_description: str,
        *,
        profile: Mapping[str, Any],
        candidate_data: Mapping[str, Any],
        analysis: Optional[Mapping[str, Any]] = None,
    ) -> str:
        """Write the letter and return its text.

        ``analysis`` is optional everywhere in resumix: the JD alone is
        enough to write a letter, it just makes for a less targeted one.
        """
        logger.info("--- cover letter ---")
        messages: List[Dict[str, Any]] = [{
            "role": "user",
            "content": self._build_user_message(
                job_description, profile, candidate_data, dict(analysis or {})
            ),
        }]

        for attempt in range(MAX_ATTEMPTS):
            with stage("letter.generate"):
                # Asked for plain text, a model still wraps it in a fence now and then.
                text = strip_fences(
                    self.llm.call_llm("letter", str, self.system_prompt, messages)
                )
            try:
                self._validate_letter(text)
                logger.info("  ✓ Letter validated (attempt %d)", attempt + 1)
                return text
            except ValueError as e:
                logger.error(
                    "  ✗ Validation failed (attempt %d): %s: %s",
                    attempt + 1, type(e).__name__, e,
                )
                messages.append({"role": "assistant", "content": text})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous response is not acceptable as-is.\n"
                            f"Issue: {type(e).__name__}: {e}\n\n"
                            "Fix it and output only the corrected letter text."
                        ),
                    }
                )

        raise ModelOutputError(
            f"Could not produce a valid cover letter after {MAX_ATTEMPTS} attempts.",
            stage="letter.generate",
        )


__all__ = ["LetterGenerator", "MIN_WORDS", "MAX_WORDS"]
