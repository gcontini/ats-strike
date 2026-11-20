"""What a request's files and settings mean to this pipeline. Pure functions.

A request is three things: the **pack** (every file of a client's server
folder, by relative path), the **job** (the posting as this server sees it:
``job_description.txt``, ``analysis.json``, and what an earlier run stored) and the
**settings** (a JSON object, forwarded untouched by the client). Nothing else
in the pipeline knows file names; nothing here calls a model or writes a file.

Files are read by :mod:`common.files`. A file a call needs and does not find is a
:class:`MissingPartError`; one that is not UTF-8, not JSON or not a JSON
object is a :class:`BadPartError`. A file this module does not name is
ignored — ``cache/`` included.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import PurePosixPath
from typing import Any, Dict, Iterable, Mapping, Optional

from common.errors import BadPartError, MissingPartError
from common.files import Files, json_file, text_file

from .bundle import CandidateInputs, ResourceBundle
from .defaults import TEMPLATE_FILE

PROFILE_FILE = "candidate_profile.json"
DATA_FILE = "candidate_data.json"
PREFERENCES_FILE = "candidate_preferences.md"
ANALYSIS_PROMPT_FILE = "sys_prompt_analysis.txt"

#: The bundle fields a pack file replaces: ``<field>.txt`` for each prompt,
#: ``<field>.json`` for each schema.
PROMPT_FIELDS = tuple(f.name for f in fields(ResourceBundle) if f.name.startswith("sys_prompt_"))
SCHEMA_FIELDS = ("cv_body_schema", "cv_duties_schema")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")

#: The model roles each call takes an ``extra_body`` for.
ANALYSIS_ROLES = ("analysis",)
LETTER_ROLES = ("letter",)
CV_ROLES = (
    "jd_evaluation",
    "profile_scoring",
    "format_selection",
    "cv",
    "duties",
    "validation",
    "highlight",
)

JD_FILE = "job_description.txt"
JOB_ANALYSIS_FILE = "analysis.json"
DOCUMENT_FILE = "cv.json"
TEX_FILE = "cv.tex"


# --- the pack -----------------------------------------------------------------
def bundle_overrides(pack: Files) -> Dict[str, Any]:
    """The pack's replacements for the shipped bundle, as
    :meth:`ResourceBundle.with_overrides` takes them: ``None`` keeps a default."""
    overrides: Dict[str, Any] = {name: text_file(pack, f"{name}.txt") for name in PROMPT_FIELDS}
    overrides.update({name: text_file(pack, f"{name}.json") for name in SCHEMA_FIELDS})
    overrides["template_source"] = text_file(pack, TEMPLATE_FILE)
    overrides["images"] = {
        name: blob for name, blob in pack.items() if "/" not in name and PurePosixPath(name).suffix in IMAGE_SUFFIXES
    }
    return overrides


def analysis_prompt(pack: Files) -> Optional[str]:
    """The analysis prompt override, if the pack has one."""
    return text_file(pack, ANALYSIS_PROMPT_FILE)


def candidate(pack: Files, *, data: bool = False, preferences: bool = False) -> CandidateInputs:
    """The personal inputs: the profile always, the data and the preferences
    when the call needs them."""
    return CandidateInputs(
        profile=json_file(pack, PROFILE_FILE, required=True),
        preferences=text_file(pack, PREFERENCES_FILE, required=True) if preferences else "",
        data=json_file(pack, DATA_FILE, required=True) if data else {},
    )


# --- the settings -------------------------------------------------------------
def page_limit(settings: Mapping[str, Any]) -> Optional[int]:
    """``pages``: an integer of at least 1, or ``None`` for the default."""
    pages = settings.get("pages")
    if pages is None:
        return None
    if not isinstance(pages, int) or pages < 1:
        raise BadPartError(f"setting 'pages' must be an integer of at least 1, got {pages!r}")
    return pages


def extra_bodies(settings: Mapping[str, Any], roles: Iterable[str]) -> Dict[str, Dict[str, Any]]:
    """``extra_body``, keeping only ``roles``: each a JSON object that replaces
    the role's request settings (see :meth:`ModelSelector.with_extra_body`)."""
    bodies = settings.get("extra_body")
    if bodies is None:
        return {}
    if not isinstance(bodies, dict):
        raise BadPartError("setting 'extra_body' must be an object of role -> object")
    taken: Dict[str, Dict[str, Any]] = {}
    for role in roles:
        if role not in bodies:
            continue
        if not isinstance(bodies[role], dict):
            raise BadPartError(f"setting 'extra_body.{role}' must be an object")
        taken[role] = bodies[role]
    return taken


# --- the job ------------------------------------------------------------------
def jd_text(job: Files) -> str:
    """The posting, decoded; an undecodable byte becomes U+FFFD."""
    blob = job.get(JD_FILE)
    if blob is None:
        raise MissingPartError(f"file '{JD_FILE}' is required")
    return blob.decode("utf-8", errors="replace")


def job_analysis(job: Files) -> Optional[Dict[str, Any]]:
    """The posting's analysis, when the job carries one."""
    return json_file(job, JOB_ANALYSIS_FILE)


def job_action(job: Files) -> str:
    """What a CV request does with this job: the document wins over the tex,
    and either wins over writing."""
    if DOCUMENT_FILE in job:
        return "render_document"
    if TEX_FILE in job:
        return "render_tex"
    return "generate"


def job_document(job: Files) -> Dict[str, Any]:
    return json_file(job, DOCUMENT_FILE, required=True)


def job_tex(job: Files) -> str:
    return text_file(job, TEX_FILE, required=True)


__all__ = [
    "Files",
    "PROMPT_FIELDS",
    "SCHEMA_FIELDS",
    "ANALYSIS_ROLES",
    "LETTER_ROLES",
    "CV_ROLES",
    "text_file",
    "json_file",
    "bundle_overrides",
    "analysis_prompt",
    "candidate",
    "page_limit",
    "extra_bodies",
    "jd_text",
    "job_analysis",
    "job_action",
    "job_document",
    "job_tex",
]
