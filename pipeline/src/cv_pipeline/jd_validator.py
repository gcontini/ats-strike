"""Reading a job description: detection, then structured analysis.

Two jobs, each on its own model:

- :meth:`JDValidator.detect` — is this text a job posting at all? Only the
  model (``detect``) call: the free structural checks are the caller's, run
  before it, so a clipboard full of code costs nothing.
- :meth:`JDValidator.analyze` — on the ``analysis`` model, score the posting against the candidate
  profile and extract the facts a CV and a cover letter need.

What the analysis is shown arrives with the call, never from disk: the
same validator instance serves every request.
"""

from __future__ import annotations

import logging

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

    def analyze(self, inputs: str, system_prompt: str) -> JDAnalysis:
        """Compare a job description against the profile and extract key facts.

        ``inputs`` is the user turn: one block per file ``system_prompt``
        declares (see :func:`~cv_pipeline.pack.prompt_inputs`) — the shipped
        :data:`JD_SYSTEM_PROMPT` asks for the profile, the preferences and
        the posting.
        """
        with stage("jd.analysis"):
            return self.llm.call_llm(
                "analysis", JDAnalysis, system_prompt,
                [{"role": "user", "content": inputs}],
            )


__all__ = ["JDValidator"]
