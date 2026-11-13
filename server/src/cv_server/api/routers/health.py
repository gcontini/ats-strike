"""Liveness and configuration readout — the one route with no auth."""

from __future__ import annotations

from cv_contracts import PROTOCOL, Envelope, ServerStatus
from fastapi import APIRouter, Request

from ..deps import envelope_of, get_state

router = APIRouter()


@router.get("/healthz", response_model=Envelope[ServerStatus], response_model_exclude_none=True)
async def healthz(request: Request) -> dict:
    """What this server is, which protocol it speaks, and whether it can do its job.

    Unauthenticated on purpose: a platform health check has no token, and
    nothing here is a secret — model names and whether LaTeX is installed.
    """
    state = get_state(request)
    info = state.pipeline.info()
    from importlib.metadata import version

    try:
        server_version = version("ats-strike-server")
    except Exception:  # running from a source tree that was never installed
        server_version = "unknown"

    return envelope_of(
        ServerStatus(
            status="ok" if info["pdflatex"] else "degraded",
            version=server_version,
            protocol=PROTOCOL,
            pdflatex=info["pdflatex"],
            models=info["models"],
            auth_required=bool(state.settings.api_token),
        )
    )
