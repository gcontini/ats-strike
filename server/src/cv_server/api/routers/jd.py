"""Reading job descriptions: the cheap gate and the full analysis."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from cv_contracts import Envelope, JDAnalysis, JDDetection

from ...bundle import CandidateInputs
from ...pipeline.jd_validator import JDValidator
from ..deps import envelope_of, execute, get_state
from ..multipart import extra_bodies, json_part, text_part

router = APIRouter()


@router.post("/jd/detect", response_model=Envelope[JDDetection], response_model_exclude_none=True)
async def detect_jd(
    request: Request,
    jd: Optional[UploadFile] = File(None, description="The text to check, as a file"),
    jd_text: Optional[str] = Form(None, description="The text to check, as a field"),
) -> dict:
    """Is this text a job posting? Static checks first, one model call second."""
    state = get_state(request)
    text = await text_part(
        jd, jd_text, name="jd", max_bytes=state.settings.max_part_bytes, required=True
    )
    validator = JDValidator(
        state.llm,
        min_chars=state.settings.jd_min_chars,
        max_chars=state.settings.jd_max_chars,
    )

    def job() -> JDDetection:
        return validator.detect(text)

    return envelope_of(await execute(state, job))


@router.post("/jd/analysis", response_model=Envelope[JDAnalysis], response_model_exclude_none=True)
async def analyze_jd(
    request: Request,
    jd: Optional[UploadFile] = File(None, description="The job description, as a file"),
    jd_text: Optional[str] = Form(None, description="The job description, as a field"),
    candidate_profile: Optional[UploadFile] = File(
        None, description="candidate_profile.json — the match score is against this"
    ),
    pers_preferences: Optional[UploadFile] = File(
        None, description="candidate_preferences.md — scored into pers_preference_score"
    ),
    pers_preferences_text: Optional[str] = Form(None, description="…or the same as a field"),
    sys_prompt_analysis: Optional[UploadFile] = File(
        None, description="Override the analysis prompt"
    ),
    analysis_extra_body: Optional[str] = Form(
        None, description="JSON object: the analysis model's settings, instead of models.toml's"
    ),
) -> dict:
    """Score a posting against the profile and extract its facts."""
    state = get_state(request)
    limit = state.settings.max_part_bytes

    text = await text_part(jd, jd_text, name="jd", max_bytes=limit, required=True)
    profile = await json_part(
        candidate_profile, name="candidate_profile", max_bytes=limit, required=True
    )
    preferences = await text_part(
        pers_preferences, pers_preferences_text, name="pers_preferences",
        max_bytes=limit, required=True,
    )
    candidate = CandidateInputs(profile=profile, preferences=preferences)
    prompt = await text_part(sys_prompt_analysis, name="sys_prompt_analysis", max_bytes=limit)
    llm = state.llm.with_extra_body(
        await extra_bodies({"analysis": analysis_extra_body}, max_bytes=limit)
    )

    validator = JDValidator(llm)

    def job() -> JDAnalysis:
        return validator.analyze(text, candidate, prompt)

    return envelope_of(await execute(state, job))
