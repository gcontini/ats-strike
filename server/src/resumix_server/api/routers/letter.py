"""Writing the cover letter."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from resumix_contracts import CoverLetter, Envelope

from ...pipeline.letter_generator import LetterGenerator
from ..deps import envelope_of, execute, get_state
from ..multipart import extra_bodies, json_part, text_part

router = APIRouter()


@router.post("/letter", response_model=Envelope[CoverLetter], response_model_exclude_none=True)
async def create_letter(
    request: Request,
    jd: Optional[UploadFile] = File(None, description="The job description, as a file"),
    jd_text: Optional[str] = Form(None, description="The job description, as a field"),
    candidate_profile: Optional[UploadFile] = File(
        None, description="candidate_profile.json — what the letter draws on"
    ),
    candidate_data: Optional[UploadFile] = File(
        None, description="candidate_data.json — the header block's contact details"
    ),
    analysis: Optional[UploadFile] = File(
        None, description="analysis.json — optional; makes the letter more targeted"
    ),
    sys_prompt_letter: Optional[UploadFile] = File(
        None, description="Override the letter prompt"
    ),
    letter_extra_body: Optional[str] = Form(
        None, description="JSON object: the letter model's settings, instead of models.toml's"
    ),
) -> dict:
    """Write a cover letter for one job description."""
    state = get_state(request)
    limit = state.settings.max_part_bytes

    text = await text_part(jd, jd_text, name="jd", max_bytes=limit, required=True)
    profile = await json_part(
        candidate_profile, name="candidate_profile", max_bytes=limit, required=True
    )
    data = await json_part(
        candidate_data, name="candidate_data", max_bytes=limit, required=True
    )
    jd_analysis = await json_part(analysis, name="analysis", max_bytes=limit)
    bundle = state.bundle.with_overrides(
        sys_prompt_letter=await text_part(
            sys_prompt_letter, name="sys_prompt_letter", max_bytes=limit
        )
    )

    llm = state.llm.with_extra_body(
        await extra_bodies({"letter": letter_extra_body}, max_bytes=limit)
    )

    def job() -> CoverLetter:
        letter = LetterGenerator(llm, system_prompt=bundle.sys_prompt_letter).generate(
            text, profile=profile, candidate_data=data, analysis=jd_analysis
        )
        return CoverLetter(text=letter, words=len(letter.split()))

    return envelope_of(await execute(state, job))
