"""The ``data`` of each REST reply.

Version-neutral on purpose: an analysis is an opaque JSON object, and what a
reply wants stored travels as :class:`~cv_contracts.events.StoreItem` items,
so a new server version needs no new client.
"""

from __future__ import annotations

from typing import Any, Dict, List

from pydantic import BaseModel, Field

from .events import StoreItem


class ServerStatus(BaseModel):
    """What ``/healthz`` reports."""

    status: str = Field(description="'ok' when the server can do its job")
    version: str = Field(description="ats-strike-server version")
    protocol: int = Field(
        1, description="The wire protocol it speaks; a server that does not say is 1"
    )
    pdflatex: bool = Field(description="Whether a pdflatex binary is available")
    models: Dict[str, str] = Field(description="Model name configured per role")
    auth_required: bool = Field(description="Whether a bearer token is required")


class Detection(BaseModel):
    """What ``POST /v1/jd/detect`` answers."""

    is_job_description: bool = Field(description="Whether to treat this text as a posting")


class Analysed(BaseModel):
    """What ``POST /v1/jd/analysis`` answers."""

    analysis: Dict[str, Any] = Field(description="The analysis, as the server's pipeline wrote it")
    items: List[StoreItem] = Field(default_factory=list, description="Things to store")


class Rendered(BaseModel):
    """What ``POST /v1/cv/render`` answers."""

    items: List[StoreItem] = Field(default_factory=list, description="Things to store")


class Letter(BaseModel):
    """What ``POST /v1/letter`` answers."""

    text: str = Field(description="The letter, plain text")
    items: List[StoreItem] = Field(default_factory=list, description="Things to store")


__all__ = ["ServerStatus", "Detection", "Analysed", "Rendered", "Letter"]
