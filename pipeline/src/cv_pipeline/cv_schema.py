"""What the CV model is asked to write — a schema that is also a prompt.

Every ``Field(description=...)`` below reaches the model — in the request's
strict JSON schema, or in the format instructions of a ``json_mode`` prompt —
so editing one of them changes what the model produces. Treat these classes as
prompts, not just as types.

They live here rather than in ``resumix_contracts`` because no client reads
them: ``POST /v1/cv`` hands its caller the JSON the model wrote, and the
caller stores it and posts it back to be rendered without looking inside. The
shape is this server's business — which is also why it is open: a reply with
a field beyond the ones declared here is kept, not dropped, so a request that
brings its own prompt and its own template can put something new on the page
without the schema in the middle having to learn about it first.

Accepting an unexpected field and inviting one are different things, though:
the schema the model is *shown* is closed (:data:`CLOSED`), because a strict
``json_schema`` endpoint refuses ``additionalProperties: true`` outright.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

#: Open on the way in, closed in the schema the model is shown.
CLOSED = ConfigDict(extra="allow", json_schema_extra={"additionalProperties": False})


class WorkExperienceItem(BaseModel):
    model_config = CLOSED

    id: Optional[int] = Field(
        None,
        description="Numeric id copied as-is from the matching experience in the candidate's MASTER PROFILE.",
    )
    title: str = Field(description="Job title in this specific job experience")
    company: str = Field(description="Company name")
    location: str = Field(description="Location: City, Country")
    dates: str = Field(
        description="Employment date range, e.g., February 2021 -- Present"
    )
    project_name: Optional[str] = Field(
        None,
        description="Project Description eg. 'Milano Winter Olympics -- Organizing Committee'",
    )
    role_summary: str = Field(
        min_length= 20,
        max_length= 300,
        description="Overview of the role (scope, mission, team or context), "
        "tailored from the matching MASTER PROFILE experience's role_summary. ")
    duties: List[str] = Field(
        min_length=1,
        max_length=15,
        description="Tailored achievements/responsibilities/duties matching the master profile."
    )


class CertificationItem(BaseModel):
    model_config = CLOSED
    date: str = Field(description="When the certification was earned, e.g., 'July 2023'")
    name: str = Field(description="Certification name")


class TailoredCVData(BaseModel):
    """The model's half of a CV: what ``POST /v1/cv`` returns.

    Open to extra fields on the way in *and* on the way out — they survive
    ``model_dump()`` and reach the template — while the fields declared here
    stay required and stay bounded, because the page limit and the LaTeX
    template both depend on them.
    """

    model_config = CLOSED

    job_title: str = Field(description="Target job title extracted from job description")
    summary: str = Field(
        description="Professional summary tailored specifically to the job role"
    )
    skills: List[str] = Field(
        min_length = 6, max_length=20,
        description="Key technical and soft skills prioritized for this job, keep them grounded in candidate's profile."
    )
    experiences: List[WorkExperienceItem] = Field(
        min_length=3, max_length=6,
        description="Tailored work experience list"
    )
    certifications: List[CertificationItem] = Field(
        default_factory=list, max_length=5,
        description="The most relevant certifications from the candidate's MASTER PROFILE for this JD, most relevant/recent first."
    )


__all__ = [
    "WorkExperienceItem",
    "CertificationItem",
    "TailoredCVData",
]
