"""What a request's files and settings mean to this pipeline. Pure functions.

A request is three things: the **pack** (every file of a client's server
folder, by relative path), the **job** (the posting as this server sees it:
``job_description.txt``, ``analysis.json``, and what an earlier run stored) and the
**settings** (a JSON object, forwarded untouched by the client). Nothing else
in the pipeline knows file names; nothing here calls a model or writes a file.

A system prompt names the files its model is shown: its ``Inputs:`` block
lists one ``NAME: description`` per line, and each name is the file
``name.*``, top-level in the pack or the job or directly under the pack's
``cache/`` (see :func:`prompt_inputs`).

Text is UTF-8 with a BOM stripped. A file a call needs and does not find is a
:class:`MissingPartError`; one that is not UTF-8, not JSON or not a JSON
object is a :class:`BadPartError`. A file neither this module nor a prompt's
``Inputs:`` names is ignored.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import PurePosixPath
from typing import Any, Dict, List, Mapping, Optional

from .bundle import CandidateInputs, ResourceBundle
from .defaults import TEMPLATE_FILE
from .errors import BadPartError, MissingPartError

Files = Mapping[str, bytes]

#: Strip a UTF-8 BOM: Windows editors add one and JSON parsers choke on it.
_BOM = "﻿"

PROFILE_FILE = "candidate_profile.json"
DATA_FILE = "candidate_data.json"
ANALYSIS_PROMPT_FILE = "sys_prompt_analysis.txt"

#: The bundle fields a pack file replaces: ``<field>.txt`` for each prompt.
PROMPT_FIELDS = tuple(f.name for f in fields(ResourceBundle) if f.name.startswith("sys_prompt_"))
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")

#: The sampling settings a CV (role ``cv``) and a letter (role ``letter``) take.
SAMPLING_SETTINGS = ("temperature", "presence_penalty")

JD_FILE = "job_description.txt"
DOCUMENT_FILE = "cv.json"
TEX_FILE = "cv.tex"


# --- reading one file ---------------------------------------------------------
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


# --- the pack -----------------------------------------------------------------
def bundle_overrides(pack: Files) -> Dict[str, Any]:
    """The pack's replacements for the shipped bundle, as
    :meth:`ResourceBundle.with_overrides` takes them: ``None`` keeps a default."""
    overrides: Dict[str, Any] = {
        name: text_file(pack, f"{name}.txt") for name in PROMPT_FIELDS
    }
    overrides["template_source"] = text_file(pack, TEMPLATE_FILE)
    overrides["images"] = {
        name: blob for name, blob in pack.items()
        if "/" not in name and PurePosixPath(name).suffix in IMAGE_SUFFIXES
    }
    return overrides


def analysis_prompt(pack: Files) -> Optional[str]:
    """The analysis prompt override, if the pack has one."""
    return text_file(pack, ANALYSIS_PROMPT_FILE)


def candidate(pack: Files, *, data: bool = False) -> CandidateInputs:
    """The personal inputs: the profile always, the data when the call needs it."""
    return CandidateInputs(
        profile=json_file(pack, PROFILE_FILE, required=True),
        data=json_file(pack, DATA_FILE, required=True) if data else {},
    )


# --- the prompt's inputs ------------------------------------------------------
INPUTS_HEADER = "Inputs:"
#: Where an input's file may sit: top-level, or directly under ``cache/``,
#: where a client keeps what a server asked it to cache.
INPUT_FOLDERS = (PurePosixPath("."), PurePosixPath("cache"))
SEPARATOR = "--------------------------------------------"


def declared_inputs(system_prompt: str) -> List[str]:
    """The names the prompt's ``Inputs:`` block declares, in order: the text
    before the first ``:`` of each line, up to the first blank one."""
    lines = system_prompt.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == INPUTS_HEADER) + 1
    except StopIteration:
        raise BadPartError(f"the system prompt has no '{INPUTS_HEADER}' section") from None
    names: List[str] = []
    for line in lines[start:]:
        if not line.strip():
            break
        names.append(line.split(":", 1)[0].strip())
    return names


def input_text(files: Files, name: str) -> str:
    """The text of the one file ``name.*`` (lowercase, any extension),
    top-level or directly under ``cache/``."""
    stem = name.lower()
    matches = sorted(
        path for path in files
        if PurePosixPath(path).parent in INPUT_FOLDERS and PurePosixPath(path).stem == stem
    )
    if not matches:
        raise MissingPartError(f"no file '{stem}.*' for the prompt input {name}")
    if len(matches) > 1:
        raise BadPartError(f"the prompt input {name} matches several files: {', '.join(matches)}")
    return text_file(files, matches[0], required=True)


def prompt_inputs(system_prompt: str, pack: Files, job: Files) -> str:
    """The variable part of the user prompt: one block per input the prompt
    declares, in its order, each holding its file's text as sent."""
    files = {**pack, **job}
    return "".join(
        f"{SEPARATOR}\n{name}:\n{input_text(files, name)}\n"
        for name in declared_inputs(system_prompt)
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


def sampling(settings: Mapping[str, Any]) -> Dict[str, Optional[float]]:
    """``temperature`` and ``presence_penalty``, as
    :meth:`ModelSelector.with_overrides` takes them: ``None`` keeps the
    configured value."""
    taken: Dict[str, Optional[float]] = {}
    for name in SAMPLING_SETTINGS:
        value = settings.get(name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise BadPartError(f"setting '{name}' must be a number, got {value!r}")
        taken[name] = None if value is None else float(value)
    return taken


# --- the job ------------------------------------------------------------------
def jd_text(job: Files) -> str:
    """The posting, decoded; an undecodable byte becomes U+FFFD."""
    blob = job.get(JD_FILE)
    if blob is None:
        raise MissingPartError(f"file '{JD_FILE}' is required")
    return blob.decode("utf-8", errors="replace")


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
    "SAMPLING_SETTINGS",
    "text_file",
    "json_file",
    "bundle_overrides",
    "analysis_prompt",
    "candidate",
    "declared_inputs",
    "input_text",
    "prompt_inputs",
    "page_limit",
    "sampling",
    "jd_text",
    "job_action",
    "job_document",
    "job_tex",
]
