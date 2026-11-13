"""Writing the cover letter."""

from __future__ import annotations

from typing import List, Optional

from cv_contracts import Envelope, Letter
from fastapi import APIRouter, Request, UploadFile

from ..deps import envelope_of, execute, get_state
from ..multipart import JOB, PACK, SETTINGS, read_request

router = APIRouter()


@router.post("/letter", response_model=Envelope[Letter], response_model_exclude_none=True)
async def create_letter(
    request: Request,
    pack: List[UploadFile] = PACK,
    job: List[UploadFile] = JOB,
    settings: Optional[str] = SETTINGS,
) -> dict:
    """Write a cover letter for one job description."""
    state = get_state(request)
    parts = await read_request(pack, job, settings, max_bytes=state.settings.max_part_bytes)

    def letter(sink) -> str:
        return state.pipeline.letter(parts.pack, parts.job, parts.settings)

    text, items = await execute(state, letter)
    return envelope_of(Letter(text=text, items=items))
