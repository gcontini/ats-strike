"""A job folder on disk, and the stage it belongs in.

Every folder the watcher handles — one it has just analysed, one dropped into
the inbox, one an earlier run left in ``working/`` — goes where its own files
say. That rule is :func:`route`, and it is pure: what the folder holds in,
where it goes out. :func:`classify` only reads the folder for it.

The one file this package adds to a job folder is ``approval_status.txt``: what
you decided about a posting that needed you — or ``PENDING``, for one that
``clipboard`` or ``submit`` is asking you about and nobody has answered yet.
Everything else — ``jd.txt``, ``analysis.json``, the CV files, ``log.log`` —
is what the rest of the client already writes.

A folder that already holds its CV is written like any other: each server is
sent what it stored there, and decides itself to render it rather than write
it again (see :class:`~cv_client.workspace.JobFiles`).
"""

from __future__ import annotations

from pathlib import Path
from typing import Collection, Literal, Optional, Tuple

from ..jdanalysis import JDAnalysis
from ..workspace import ANALYSIS_FILENAME, JD_FILENAME

#: What you decided about a posting that needed you, kept in its folder.
APPROVAL_FILENAME = "approval_status.txt"
APPROVED = "APPROVED"
DISCARDED = "DISCARDED"
#: Asked about and not answered yet: asked again, never decided by should_apply.
PENDING = "PENDING"

#: Where a folder goes next.
Route = Literal["error", "generate", "approve", "discard"]

#: A folder nobody has decided about yet goes where its analysis says.
_BY_SHOULD_APPLY: dict[str, Route] = {"YES": "generate", "CHECK": "approve", "NO": "discard"}


def route(
    names: Collection[str], analysis: Optional[JDAnalysis], approval: Optional[str]
) -> Tuple[Route, str]:
    """Where a job folder goes next, and why.

    ``names`` are the files it holds, ``analysis`` its validated
    ``analysis.json`` (``None`` if absent or invalid), ``approval`` the text of
    its ``approval_status.txt`` (``None`` if absent). The rules are checked in
    this order, and the first that matches wins.
    """
    if JD_FILENAME not in names:
        return "error", f"no {JD_FILENAME}"
    if analysis is None:
        return "error", f"no valid {ANALYSIS_FILENAME}"
    if approval is None:
        return _BY_SHOULD_APPLY[analysis.should_apply], f"should_apply is {analysis.should_apply}"
    decision = approval.strip().upper()
    if decision == APPROVED:
        return "generate", "approved"
    if decision == DISCARDED:
        return "discard", "discarded"
    if decision == PENDING:
        return "approve", "not answered yet"
    return "error", f"{APPROVAL_FILENAME} says {approval.strip()!r}"


def classify(folder: Path) -> Tuple[Route, str]:
    """Read a job folder and :func:`route` it."""
    names = {p.name for p in folder.iterdir() if p.is_file()}
    analysis = None
    if ANALYSIS_FILENAME in names:
        try:
            analysis = read_analysis(folder)
        except (OSError, ValueError) as exc:
            return "error", f"{ANALYSIS_FILENAME} is not valid: {exc}"
    return route(names, analysis, read_approval(folder))


# --- the files themselves --------------------------------------------------
def read_jd(folder: Path) -> str:
    """The posting, whatever bytes it arrived as."""
    return (folder / JD_FILENAME).read_bytes().decode("utf-8", errors="replace")


def read_analysis(folder: Path) -> JDAnalysis:
    return JDAnalysis.model_validate_json((folder / ANALYSIS_FILENAME).read_text(encoding="utf-8"))


def write_analysis(folder: Path, analysis: JDAnalysis) -> None:
    """What the server wrote, and what you changed since: no default the
    client filled in is added."""
    (folder / ANALYSIS_FILENAME).write_text(
        analysis.model_dump_json(indent=2, exclude_unset=True), encoding="utf-8")


def read_approval(folder: Path) -> Optional[str]:
    path = folder / APPROVAL_FILENAME
    return path.read_text(encoding="utf-8") if path.is_file() else None


def write_approval(folder: Path, decision: str) -> None:
    (folder / APPROVAL_FILENAME).write_text(f"{decision}\n", encoding="utf-8")


__all__ = [
    "APPROVAL_FILENAME", "APPROVED", "DISCARDED", "PENDING", "Route",
    "route", "classify",
    "read_jd", "read_analysis", "write_analysis", "read_approval", "write_approval",
]
