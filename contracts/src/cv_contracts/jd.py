"""The job-description side of the wire format.

:class:`JDAnalysis` is what ``POST /v1/jd/analysis`` returns and what clients
store as ``analysis.json``. Like every model-facing schema, its field
descriptions are part of the prompt.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class JDAnalysis(BaseModel):
    """Structured summary of a job description vs. the master profile."""
    match_percentage: int = Field(description="0-100 how well the JD fits the profile")
    match_rationale: str = Field(
        description="One-line reason for the match score"
    )
    job_title: str = Field(description="Job title extracted from the JD")
    work_location: Optional[str] = Field(
        None, description="Work location stated in the JD, e.g. 'Milan, Italy'"
    )
    work_mode: str = Field(
        description="full_remote | hybrid | on_site | not_specified"
    )
    expected_salary: Optional[str] = Field(
        None, description="Expected salary if stated, e.g. 'EUR 60k-80k'; null if absent"
    )
    max_salary: int = Field(
        -1,
        description=(
            "Upper bound of the salary range if a range is stated (or the single "
            "number if it is just a number); -1 if not found"
        ),
    )
    salary_match: Optional[bool] = Field(
        None,
        description=(
            "True if max_salary meets the PERSONAL_PREFERENCES salary rule, False if it falls short; always true or false when "
            "max_salary is stated, null only when max_salary is -1 or salary "
            "rule fails to apply"
        ),
    )
    hard_skills: List[str] = Field(
        description="Up to 4 hard/technical skills required by the JD"
    )
    soft_skills: List[str] = Field(
        description="Up to 4 soft skills required by the JD"
    )
    company_name: str = Field(
        description="The company that posted the job (or the headhunter agency)"
    )
    posting_url: Optional[str] = Field(
        None, description="URL of the job posting, if present in the JD; else null"
    )
    gaps: str = Field(
        description="Describe the gaps between the candidate and the job description"
    )
    pers_preferences: str = Field(
        description="Textual analysis of the gaps between the PERSONAL_PREFERENCES and the job description"
    )
    pers_preference_score: float = Field(
        description=(
            "Numeric (real) score, e.g. 2.5 or 0.5, obtained by summing the "
            "validated PERSONAL_PREFERENCES points against the job description; "
            "0.0 if none apply"
        ),
    )
    should_apply: Literal["NO", "CHECK", "YES"] = Field(
        description=(
            "NO = skip the job, CHECK = flag it for a human check, YES = apply "
            "automatically. Decided by the should_apply rules in "
            "PERSONAL_PREFERENCES; if there are none, by salary_match"
        ),
    )
    should_apply_reason: str = Field(
        max_length=300,
        description="Why should_apply has that value, max 300 characters",
    )
    company_evaluation: Optional[str] = Field(
        None,
        description=(
            "Only when should_apply is CHECK: a short evaluation of the hiring "
            "company from online reviews (e.g. trustpilot.com, glassdoor.it) and "
            "the instruction in PERSONAL_PREFERENCES->company_evaluation if present; null otherwise"
        ),
    )


class JDDetection(BaseModel):
    """Answer to "is this text a job description?" — the cheap gate.

    Yes or no. The reasoning is in the server's log for that request, not in
    the answer: a caller only ever branches on the verdict.
    """

    is_job_description: bool = Field(description="Whether to treat this text as a posting")


__all__ = ["JDAnalysis", "JDDetection"]
