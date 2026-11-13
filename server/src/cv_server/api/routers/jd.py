"""Reading job descriptions: the cheap gate and the full analysis."""

from __future__ import annotations

import logging
from typing import List, Optional

from cv_contracts import Analysed, Detection, Envelope, static_jd_guess
from fastapi import APIRouter, Request, UploadFile

from ..deps import envelope_of, execute, get_state
from ..errors import MissingPart
from ..multipart import JOB, PACK, SETTINGS, read_request

logger = logging.getLogger("cv_server.jd")

router = APIRouter()


@router.post("/jd/detect", response_model=Envelope[Detection], response_model_exclude_none=True)
async def detect_jd(
    request: Request,
    pack: List[UploadFile] = PACK,
    job: List[UploadFile] = JOB,
    settings: Optional[str] = SETTINGS,
) -> dict:
    """Is the job's ``jd.txt`` a job posting? Static checks first, one model call second.

    The caller gets a yes or a no; why is in this request's log, which also
    records whether the model was asked at all (it is not, when the structural
    checks already say no — that is the point of having them).
    """
    state = get_state(request)
    parts = await read_request(pack, job, settings, max_bytes=state.settings.max_part_bytes)
    if "jd.txt" not in parts.job:
        raise MissingPart("job file 'jd.txt' is required")
    text = parts.job["jd.txt"].decode("utf-8", errors="replace")

    if not static_jd_guess(
        text, min_chars=state.settings.jd_min_chars, max_chars=state.settings.jd_max_chars
    ):
        logger.info("  ✗ not a job description (%d chars, no model call)", len(text))
        return envelope_of(Detection(is_job_description=False))

    verdict, _ = await execute(state, lambda sink: state.pipeline.detect(text))
    return envelope_of(Detection(is_job_description=verdict))


@router.post("/jd/analysis", response_model=Envelope[Analysed], response_model_exclude_none=True)
async def analyze_jd(
    request: Request,
    pack: List[UploadFile] = PACK,
    job: List[UploadFile] = JOB,
    settings: Optional[str] = SETTINGS,
) -> dict:
    """Score a posting against the profile and extract its facts."""
    state = get_state(request)
    parts = await read_request(pack, job, settings, max_bytes=state.settings.max_part_bytes)

    def analyze(sink) -> dict:
        return state.pipeline.analyze(parts.pack, parts.job, parts.settings)

    analysis, items = await execute(state, analyze)
    return envelope_of(Analysed(analysis=analysis, items=items))
