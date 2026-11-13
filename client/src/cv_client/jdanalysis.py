"""A posting's analysis, as the client reads it.

The server sends its analysis as an opaque JSON object: each server version
has its own schema, and the client must not need an upgrade when one gains a
field. So this model asks for the three fields the client decides with —
the job title and company name a job folder is called after, and
``should_apply`` — and takes every other one it knows as optional, with a
neutral default. Fields it does not know are kept (``extra="allow"``), and
:func:`~.stages.jobfolder.write_analysis` writes back only what was there, so
``analysis.json`` loses nothing on the way through.

Not a prompt: no model ever sees this class.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict


class JDAnalysis(BaseModel):
    """What the client reads of an analysis; everything else rides along."""

    model_config = ConfigDict(extra="allow")

    job_title: str
    company_name: str
    should_apply: Literal["NO", "CHECK", "YES"]

    match_percentage: Optional[int] = None
    match_rationale: str = ""
    work_location: Optional[str] = None
    work_mode: str = "not_specified"
    expected_salary: Optional[str] = None
    max_salary: int = -1
    salary_match: Optional[bool] = None
    hard_skills: List[str] = []
    soft_skills: List[str] = []
    posting_url: Optional[str] = None
    gaps: str = ""
    pers_preferences: str = ""
    pers_preference_score: Optional[float] = None
    should_apply_reason: str = ""


__all__ = ["JDAnalysis"]
