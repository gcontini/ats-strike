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
"""

from __future__ import annotations

from pathlib import Path
from typing import Collection, Literal, Optional, Tuple

from cv_contracts import JDAnalysis

from ..workspace import ANALYSIS_FILENAME, JD_FILENAME

#: What you decided about a posting that needed you, kept in its folder.
APPROVAL_FILENAME = "approval_status.txt"
APPROVED = "APPROVED"
DISCARDED = "DISCARDED"
#: Asked about and not answered yet: asked again, never decided by should_apply.
PENDING = "PENDING"

#: Where a folder goes next. ``render`` means "rendered now, then cv/".
Route = Literal["error", "render", "generate", "approve", "discard"]

#: A folder nobody has decided about yet goes where its analysis says.
_BY_SHOULD_APPLY: dict[str, Route] = {"YES": "generate", "CHECK": "approve", "NO": "discard"}


def is_cv_source(name: str) -> bool:
    """A file the CV can be re-rendered from: ``.tex``, or a ``.json`` that is
    not the analysis (the CV document)."""
    lower = name.lower()
    return lower.endswith(".tex") or (lower.endswith(".json") and name != ANALYSIS_FILENAME)


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
    if any(is_cv_source(name) for name in names):
        return "render", "it already holds its CV"
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


def render_source(folder: Path) -> Optional[Path]:
    """What to re-render from: the CV document if there is one, else a ``.tex``."""
    sources = sorted(p for p in folder.iterdir() if p.is_file() and is_cv_source(p.name))
    documents = [p for p in sources if p.suffix.lower() == ".json"]
    return (documents or sources or [None])[0]


# --- the files themselves --------------------------------------------------
def read_jd(folder: Path) -> str:
    """The posting, whatever bytes it arrived as."""
    return (folder / JD_FILENAME).read_bytes().decode("utf-8", errors="replace")


def read_analysis(folder: Path) -> JDAnalysis:
    return JDAnalysis.model_validate_json((folder / ANALYSIS_FILENAME).read_text(encoding="utf-8"))


def write_analysis(folder: Path, analysis: JDAnalysis) -> None:
    (folder / ANALYSIS_FILENAME).write_text(analysis.model_dump_json(indent=2), encoding="utf-8")


def read_approval(folder: Path) -> Optional[str]:
    path = folder / APPROVAL_FILENAME
    return path.read_text(encoding="utf-8") if path.is_file() else None


def write_approval(folder: Path, decision: str) -> None:
    (folder / APPROVAL_FILENAME).write_text(f"{decision}\n", encoding="utf-8")


__all__ = [
    "APPROVAL_FILENAME", "APPROVED", "DISCARDED", "PENDING", "Route",
    "route", "classify", "render_source", "is_cv_source",
    "read_jd", "read_analysis", "write_analysis", "read_approval", "write_approval",
]
