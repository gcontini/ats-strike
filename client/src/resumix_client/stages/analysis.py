"""Is it a posting, and what does it say: the two calls every new posting costs.

The free structural check comes first, so copying a password or a line of code
never reaches the network; deciding whether plausible text really is a posting
is the server's job. The inbox, the clipboard and ``submit`` all start here.
"""

from __future__ import annotations

from typing import Optional

from resumix_contracts import JDAnalysis, static_jd_guess

from ..api import read_text
from ..config import Config
from ..joblog import JobLog
from .calls import Calls


def analyse(calls: Calls, config: Config, text: str, log: JobLog) -> Optional[JDAnalysis]:
    """The posting's analysis; ``None``, with why in ``log``, for one that is not.

    A failed call raises :class:`~resumix_client.api.ResumixError` for the
    caller to file.
    """
    if not static_jd_guess(text):
        log.step(f"✗ not a job description ({len(text)} chars, nothing sent)")
        return None
    detection = calls.make(lambda: calls.api.detect(text))
    if not detection.is_job_description:
        log.step("✗ not a job description")
        return None
    log.step(f"✓ job description ({len(text)} chars)")
    log.step("📊 analyzing the posting...")
    return calls.make(lambda: calls.api.analyze(
        text,
        profile=config.require("candidate_profile.json").read_bytes(),
        preferences=config.require("candidate_preferences.md").read_text(encoding="utf-8"),
        prompt=read_text(config.path("sys_prompt_analysis.txt")),
        extra_body=config.extra_body,
    ))


__all__ = ["analyse"]
