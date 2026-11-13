"""Is it a posting, and what does it say: the two calls every new posting costs.

The free structural check comes first, so copying a password or a line of code
never reaches the network; deciding whether plausible text really is a posting
is the server's job — the default server's, which analyses every posting
whatever server writes its CV. The inbox, the clipboard and ``submit`` all
start here.
"""

from __future__ import annotations

from typing import Optional

from cv_contracts import static_jd_guess

from ..jdanalysis import JDAnalysis
from ..joblog import JobLog
from ..workspace import JD_FILENAME
from .calls import Calls


def analyse(calls: Calls, text: str, log: JobLog) -> Optional[JDAnalysis]:
    """The posting's analysis; ``None``, with why in ``log``, for one that is not.

    The reply's log lines go into ``log``. A failed call raises
    :class:`~cv_client.api.CVError` for the caller to file.
    """
    if not static_jd_guess(text):
        log.step(f"✗ not a job description ({len(text)} chars, nothing sent)")
        return None
    job = {JD_FILENAME: text.encode("utf-8")}
    detection = calls.make(lambda: calls.api.detect(job))
    if not detection.is_job_description:
        log.step("✗ not a job description")
        return None
    log.step(f"✓ job description ({len(text)} chars)")
    log.step("📊 analyzing the posting...")
    server = calls.server
    reply = calls.make(lambda: calls.api.analyze(server.pack(), job, server.settings))
    calls.keep_log(log, reply.items)
    return JDAnalysis.model_validate(reply.analysis)


__all__ = ["analyse"]
