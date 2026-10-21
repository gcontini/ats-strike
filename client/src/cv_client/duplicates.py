"""Have you seen this posting before?

Every finished or discarded job keeps its ``analysis.json``. Before anything
is spent on a new posting, those are read back — as plain dictionaries, never
validated: they were written by older clients against older schemas, and a
file that no longer parses must not stop a new job. Best effort, not a gate.

Two postings are the same job when their URLs match, or when both the company
and the title match. A LinkedIn URL matches by its job id, whichever page it
was copied from; any other URL must match as written. A side missing its URL is compared by company and title
only; a side missing either of those is compared by URL only.

A posting saved with a ``JOB_POSTING: <url>`` line can be checked by that URL
alone, before anything is spent on it.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import parse_qs, urlsplit, urlunsplit

from .workspace import ANALYSIS_FILENAME


def normalize(value: Any) -> str:
    """Case-, accent- and spacing-blind text; ``""`` for anything absent."""
    if not isinstance(value, str):
        return ""
    ascii_only = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(ascii_only.lower().split())


_LINKEDIN_VIEW = re.compile(r"/jobs/view/(?:[^/]*-)?(\d+)/?$")


def posting_key(url: Any) -> str:
    """``linkedin:<id>`` for a LinkedIn job, its query dropped otherwise;
    any other URL normalized like text."""
    text = normalize(url)
    parts = urlsplit(text)
    host = parts.hostname or ""
    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        return text
    job_id = parse_qs(parts.query).get("currentjobid", [""])[0]
    if not job_id.isdigit():
        view = _LINKEDIN_VIEW.search(parts.path)
        job_id = view.group(1) if view else ""
    if job_id:
        return f"linkedin:{job_id}"
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


_JOB_POSTING_LINE = re.compile(r"^\s*JOB_POSTING:\s*(\S+)", re.MULTILINE)


def posting_url_in(text: str) -> Optional[str]:
    """The URL on the posting's ``JOB_POSTING:`` line; ``None`` without one."""
    found = _JOB_POSTING_LINE.search(text)
    return found.group(1) if found else None


def same_job(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    url_a, url_b = posting_key(a.get("posting_url")), posting_key(b.get("posting_url"))
    if url_a and url_a == url_b:
        return True
    company_a, company_b = normalize(a.get("company_name")), normalize(b.get("company_name"))
    title_a, title_b = normalize(a.get("job_title")), normalize(b.get("job_title"))
    return bool(company_a and title_a) and (company_a, title_a) == (company_b, title_b)


def find_previous(current: Mapping[str, Any], folders: Iterable[Path]) -> Optional[Path]:
    """The first folder whose ``analysis.json`` describes the same job."""
    for folder in folders:
        try:
            stored = json.loads((folder / ANALYSIS_FILENAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(stored, dict) and same_job(current, stored):
            return folder
    return None


__all__ = ["normalize", "posting_key", "posting_url_in", "same_job", "find_previous"]
