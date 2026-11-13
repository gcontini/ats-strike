"""Reading a request: the same three parts on every route, checked before any work.

=========== ================ ============================================
Part        Kind             Holds
=========== ================ ============================================
``pack``    file, repeatable every file of the client's server folder
``job``     file, repeatable the posting as this server sees it
``settings`` form field      a JSON object, forwarded untouched
=========== ================ ============================================

A file's part name is its path, relative, with ``/``. What the files mean is
the pipeline's business; this module only makes sure they are what a request
may carry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from fastapi import File, Form, UploadFile

from .errors import BadPart, PartTooLarge

#: How many files one request may carry, pack and job together. A server
#: folder plus a job folder is a few dozen; this is far above that.
MAX_FILES = 200

#: The three parts, as every route declares them.
PACK = File([], description="Every file of the server folder; its file name is its path")
JOB = File([], description="The posting as this server sees it: jd.txt, analysis.json, ...")
SETTINGS = Form(None, description="JSON object: the server folder's [settings], untouched")


@dataclass(frozen=True)
class PipelineRequest:
    """A request's three parts, read."""

    pack: Dict[str, bytes] = field(default_factory=dict)
    job: Dict[str, bytes] = field(default_factory=dict)
    settings: Dict[str, Any] = field(default_factory=dict)


def _safe_name(name: str) -> bool:
    """Relative, ``/``-separated, inside the folder it names a file of."""
    return bool(name) and not name.startswith("/") and "\\" not in name and (
        ".." not in name.split("/")
    )


async def _files(uploads: List[UploadFile], *, part: str, max_bytes: int) -> Dict[str, bytes]:
    files: Dict[str, bytes] = {}
    for upload in uploads:
        name = upload.filename or ""
        if not _safe_name(name):
            raise BadPart(f"a '{part}' file has an unusable name: {name!r}")
        size = getattr(upload, "size", None)
        if size is not None and size > max_bytes:
            raise PartTooLarge(f"{part} file '{name}' is {size} bytes (limit {max_bytes})")
        blob = await upload.read()
        if len(blob) > max_bytes:
            raise PartTooLarge(f"{part} file '{name}' is {len(blob)} bytes (limit {max_bytes})")
        files[name] = blob  # a repeated name: the last one wins
    return files


async def read_request(
    pack: Optional[List[UploadFile]],
    job: Optional[List[UploadFile]],
    settings: Optional[str],
    *,
    max_bytes: int,
) -> PipelineRequest:
    """The pack, the job and the settings, or the reason they cannot be had."""
    pack, job = pack or [], job or []
    # Counted before anything is read: refusing a thousand parts after reading
    # them is not a limit.
    if len(pack) + len(job) > MAX_FILES:
        raise BadPart(f"too many files: {len(pack) + len(job)} (limit {MAX_FILES})")
    parsed: Dict[str, Any] = {}
    if settings is not None:
        try:
            parsed = json.loads(settings)
        except json.JSONDecodeError as exc:
            raise BadPart(f"part 'settings' is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise BadPart(f"part 'settings' must be a JSON object, got {type(parsed).__name__}")
    return PipelineRequest(
        pack=await _files(pack, part="pack", max_bytes=max_bytes),
        job=await _files(job, part="job", max_bytes=max_bytes),
        settings=parsed,
    )


__all__ = ["PipelineRequest", "read_request", "MAX_FILES", "PACK", "JOB", "SETTINGS"]
