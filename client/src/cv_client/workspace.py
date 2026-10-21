"""The output folder and every move inside it.

One directory holds the whole workflow::

    <out>/
        working/      in flight, and nothing else
        error/        rejected or failed, by day, timestamp-prefixed
        discarded/    you said no, by day
        cv/           delivered, by day
        applications.xlsx

A job is assembled under ``working/`` and moved into place in one step when it
is finished, so a folder under ``cv/`` is never half-written. This module owns
every path and every move; nothing else in the client builds a path by hand.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

#: Prefix for an entry filed under error/ — sorts chronologically.
TS_FORMAT = "%y-%m-%d-%H-%M-%S"
#: Daily folders under cv/, discarded/ and error/.
DAY_FORMAT = "%y-%m-%d"

_TS_PREFIX = re.compile(r"^\d{2}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2}_")

JD_FILENAME = "jd.txt"
ANALYSIS_FILENAME = "analysis.json"
LOG_FILENAME = "log.log"
LETTER_FILENAME = "cover_letter.txt"
#: Inside a job folder: each step's intermediate result, one JSON file each.
#: A folder, so no file of it can be mistaken for the CV beside it.
STEPS_DIRNAME = "steps"


def timestamp(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime(TS_FORMAT)


def day(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime(DAY_FORMAT)


def sanitize_name(text: str, max_len: int = 40) -> str:
    """Turn a company or job title into a filesystem-safe folder fragment."""
    clean = re.sub(r"\W+", "_", (text or "").strip()).strip("_")
    return clean[:max_len].strip("_") or "unknown"


def job_name(company: str, title: str) -> str:
    """What a job folder is called: ``<Company>_<Title>``."""
    return f"{sanitize_name(company)}_{sanitize_name(title)}"


def staged_job(parent: Path, company: str, title: str) -> Path:
    """An empty ``<parent>/.<Company>_<Title>``, to be filled before it is shown.

    The dot keeps ``watch`` away from it while it is being written: hidden
    entries are skipped. :func:`unstage` gives it its real name. A leftover
    from a run that died half-way is emptied first.
    """
    staged = Path(parent) / f".{job_name(company, title)}"
    _clear(staged)
    staged.mkdir(parents=True)
    return staged


def unstage(staged: Path) -> Path:
    """``.<name>`` becomes ``<name>``, replacing whatever already has that name."""
    target = staged.with_name(staged.name[1:])
    _clear(target)
    staged.rename(target)
    return target


def _clear(path: Path) -> None:
    """Whatever is at ``path``, gone."""
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


@dataclass(frozen=True)
class Artifacts:
    """What a finished job folder is called: your name and the job title."""

    candidate: str
    job_title: str

    @property
    def stem(self) -> str:
        return f"cv_{sanitize_name(self.candidate)}_{sanitize_name(self.job_title)}".lower()

    @property
    def pdf(self) -> str:
        return f"{self.stem}.pdf"

    @property
    def tex(self) -> str:
        return f"{self.stem}.tex"

    @property
    def document(self) -> str:
        return f"{self.stem}.json"


class Workspace:
    """Owns the output tree and every move within it."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.working = self.root / "working"
        self.error = self.root / "error"
        self.discarded = self.root / "discarded"
        self.cv = self.root / "cv"

    def ensure(self) -> "Workspace":
        for directory in (self.working, self.error, self.discarded, self.cv):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    # --- intake -------------------------------------------------------------
    def new_job(self, company: str, title: str) -> Path:
        """A fresh ``working/<Company>_<Title>`` that never replaces another.

        A folder of the same name is left alone and this one gets a ``_2``
        suffix: that name can belong to a job whose CV is still being written.
        """
        job = self._free(self.working / job_name(company, title))
        job.mkdir(parents=True)
        return job

    # --- outcomes -----------------------------------------------------------
    def deliver(self, job: Path) -> Path:
        """Finished: ``cv/<day>/<Company>_<Title>``."""
        return self._move_into(job, self.cv / day())

    def discard(self, job: Path) -> Path:
        """You said no: ``discarded/<day>/<Company>_<Title>``."""
        return self._move_into(job, self.discarded / day())

    def to_error(self, entry: Path) -> Path:
        """Failed or not a job description: ``error/<day>/<timestamp>_<name>``.

        A file that already carries a timestamp keeps it — the time it arrived
        is more useful than the time it failed. The day folder is the day it
        failed, like ``cv/`` and ``discarded/``. An entry of the same name is
        the same posting failing again, and is replaced rather than numbered.
        """
        name = entry.name if _TS_PREFIX.match(entry.name) else f"{timestamp()}_{entry.name}"
        target = self.error / day() / name
        target.parent.mkdir(parents=True, exist_ok=True)
        _clear(target)
        shutil.move(str(entry), str(target))
        return target

    # --- history ------------------------------------------------------------
    def jobs_in(self, parent: Path) -> List[Path]:
        """Every analysed job under ``cv/`` or ``discarded/`` (``<day>/<job>``)."""
        return sorted(p.parent for p in parent.glob(f"*/*/{ANALYSIS_FILENAME}"))

    def working_jobs(self) -> List[Path]:
        """Every analysed job still in flight: ``working/<job>``."""
        return sorted(p.parent for p in self.working.glob(f"*/{ANALYSIS_FILENAME}"))

    # --- recovery -----------------------------------------------------------
    def pending(self) -> Tuple[List[Path], List[Path]]:
        """What is left in ``working/``: loose files and job folders.

        A folder was analysed but never finished, and resumes where its own
        files say; a loose file cannot be resumed (only older clients left
        them) and goes to ``error/``. Hidden entries are not a run's leftovers.
        """
        if not self.working.is_dir():
            return [], []
        entries = [e for e in sorted(self.working.iterdir()) if not e.name.startswith(".")]
        return ([e for e in entries if e.is_file()], [e for e in entries if e.is_dir()])

    def clean(self) -> int:
        """Empty ``working/``. Returns how many entries were removed."""
        files, folders = self.pending()
        for entry in files + folders:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        return len(files) + len(folders)

    # --- helpers ------------------------------------------------------------
    def _move_into(self, entry: Path, parent: Path) -> Path:
        parent.mkdir(parents=True, exist_ok=True)
        target = self._free(parent / entry.name)
        shutil.move(str(entry), str(target))
        return target

    @staticmethod
    def _free(target: Path) -> Path:
        """A path that does not exist yet: never overwrite a previous run."""
        if not target.exists():
            return target
        stem, suffix = target.stem, target.suffix
        for n in range(2, 1000):
            candidate = target.with_name(f"{stem}_{n}{suffix}")
            if not candidate.exists():
                return candidate
        raise FileExistsError(f"cannot find a free name near {target}")


__all__ = [
    "Workspace", "Artifacts", "sanitize_name", "job_name", "staged_job", "unstage",
    "timestamp", "day",
    "JD_FILENAME", "ANALYSIS_FILENAME", "LOG_FILENAME", "LETTER_FILENAME", "STEPS_DIRNAME",
]
