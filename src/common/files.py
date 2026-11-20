"""A request's files, read: text decoded, JSON parsed, each failure a kind.

A request carries its files in memory, by relative path. Text is UTF-8 with
a BOM stripped. A file a call needs and does not find is a
:class:`MissingPartError`; one that is not UTF-8, not JSON or not a JSON
object is a :class:`BadPartError`. Each pipeline's own module says which
files it reads; this one only reads them. Pure functions.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from .errors import BadPartError, MissingPartError

Files = Mapping[str, bytes]

#: Strip a UTF-8 BOM: Windows editors add one and JSON parsers choke on it.
_BOM = "﻿"


def text_file(files: Files, name: str, *, required: bool = False) -> Optional[str]:
    """A text file, decoded, its BOM stripped; ``None`` when absent and not required."""
    blob = files.get(name)
    if blob is None:
        if required:
            raise MissingPartError(f"file '{name}' is required")
        return None
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BadPartError(f"file '{name}' is not UTF-8 text: {exc}") from exc
    text = text.lstrip(_BOM)
    if required and not text.strip():
        raise BadPartError(f"file '{name}' is empty")
    return text


def json_file(files: Files, name: str, *, required: bool = False) -> Optional[Dict[str, Any]]:
    """A JSON object file, parsed; ``None`` when absent and not required."""
    text = text_file(files, name, required=required)
    if text is None:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BadPartError(f"file '{name}' is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BadPartError(f"file '{name}' must be a JSON object, got {type(data).__name__}")
    return data


__all__ = ["Files", "text_file", "json_file"]
