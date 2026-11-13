"""Reading a job description: detection, then structured analysis.

Two jobs, each on its own model:

- :meth:`JDValidator.detect` — is this text a job posting at all? Only the
  model (``detect``) call: the free structural checks are the caller's, run
  before it, so a clipboard full of code costs nothing.
- :meth:`JDValidator.analyze` — on the ``analysis`` model, score the posting against the candidate
  profile and extract the facts a CV and a cover letter need.

The profile and the preferences arrive with the call, never from disk: the
same validator instance serves every request.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from .bundle import CandidateInputs
from .defaults import read_text_default
from .models import ModelSelector
from .observability import LOGGER_ROOT, stage
from .schemas import JDAnalysis, JDDetection

logger = logging.getLogger(f"{LOGGER_ROOT}.jd")

#: The contract with the model for :meth:`JDValidator.analyze`, shipped as
#: ``resources/sys_prompt_analysis.txt`` alongside the CV/letter prompts.
JD_SYSTEM_PROMPT = read_text_default("sys_prompt_analysis.txt")

#: The detection prompt, shipped as ``resources/sys_prompt_jd_detect.txt``.
DETECT_PROMPT = read_text_default("sys_prompt_jd_detect.txt")


class JDValidator:
    """Analyzes job descriptions with one model.

    Parameters
    ----------
    llm:
        The ``detect`` model answers :meth:`detect`, the ``analysis`` model
        :meth:`analyze` — reading a posting is extraction, not writing, so
        neither needs the large model.
    """

    def __init__(self, llm: ModelSelector) -> None:
        self.llm = llm

    # --- public API ---------------------------------------------------------
    def detect(self, text: str) -> JDDetection:
        """Is ``text`` a job description? The model's yes or no.

        The caller gets a yes or a no; why is in this request's log.
        """
        # The posting is a message of its own, after the instructions: a page
        # of 20000 characters must not bury them, nor pass for them.
        with stage("jd.detect"):
            verdict = self.llm.call_llm(
                "detect", bool, DETECT_PROMPT, [{"role": "user", "content": text}]
            )
        logger.info("  🔎 job description check: %s", "YES" if verdict else "NO")
        return JDDetection(is_job_description=verdict)

    def analyze(
        self, job_description: str, candidate: CandidateInputs,
        system_prompt: Optional[str] = None,
    ) -> JDAnalysis:
        """Compare a job description against the profile and extract key facts.

        ``system_prompt`` replaces the shipped one for this call; ``None``
        keeps it.
        """
        content = (
            "CANDIDATE_PROFILE:\n"
            f"{json.dumps(dict(candidate.profile), indent=2)}\n\n"
            "--------------------------------------------\n"
            "PERSONAL_PREFERENCES:\n"
            f"{candidate.preferences}\n\n"
            "--------------------------------------------\n"
            "JOB_DESCRIPTION:\n"
            f"{job_description}\n"
        )
        with stage("jd.analysis"):
            return self.llm.call_llm(
                "analysis", JDAnalysis, system_prompt or JD_SYSTEM_PROMPT,
                [{"role": "user", "content": content}],
            )


__all__ = ["JDValidator"]
