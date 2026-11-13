"""The response envelope every endpoint returns, success or failure.

Deliberately small: what you asked for, or why you cannot have it. What a
request logged comes back as things to store (see
:class:`cv_contracts.events.StoreItem`), inside ``data``.

Every response carries its ``request_id``, and so does the ``X-Request-Id``
header.
"""

from __future__ import annotations

from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    """Uniform response body. ``data`` is present iff ``ok`` is true."""

    request_id: str = Field(description="Echoed in X-Request-Id")
    ok: bool = Field(description="True when 'data' holds a result")
    data: Optional[T] = Field(None, description="The endpoint's payload")
    error: Optional[str] = Field(None, description="Why it failed. Set iff ok is false")


__all__ = ["Envelope"]
