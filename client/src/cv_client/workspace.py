"""The output folder and every move inside it.

One directory holds the whole workflow::

    <out>/
        working/      in flight, and nothing else
        error/        rejected or failed, by day
        discarded/    you said no, by day
        CV/           delivered, by day
        applications.xlsx

A job is assembled under ``working/`` and moved into place in one step when it
is finished, so a folder under ``CV/`` is never half-written. This module owns
every path and every move; nothing else in the client builds a path by hand.

Inside a job folder each server has a subfolder of its own, named after it,
holding what it stored for the job; the PDFs sit at the top::

    CV/26-11-13/Acme_Head_of_IT/
        jd.txt  analysis.json  approval_status.txt  log.log
        cv_jordan_rivera_head_of_it.pdf        the default server's
        cv_jordan_rivera_head_of_it_v05.pdf    any other server's
        v11/  cv.json  cv.tex  steps/*.json  candidate_signature.png
        v05/  cv.json  cv.tex

:class:`JobFiles` is the only code that knows that layout.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Dict, List, Optional, Tuple

from cv_contracts import StoreItem

from .joblog import JobLog
from .servers import Server

#: Daily folders under CV/, discarded/ and error/.
DAY_FORMAT = "%y-%m-%d"

JD_FILENAME = "jd.txt"
ANALYSIS_FILENAME = "analysis.json"
LOG_FILENAME = "log.log"
LETTER_FILENAME = "cover_letter.txt"
#: Inside a server folder: where the server's ``CACHE`` items land.
CACHE_DIRNAME = "cache"
#: The images a server folder sends, copied beside its ``cv.tex``.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg")


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

    def pdf(self, server: str = "") -> str:
        """The CV's PDF; ``server`` is the suffix of a server that is not the default."""
        return f"{self.stem}_{server}.pdf" if server else f"{self.stem}.pdf"


def _safe(name: str) -> bool:
    """A relative path that stays inside the folder it is joined to, on any system."""
    return bool(name) and "\\" not in name and not PureWindowsPath(name).drive and not (
        PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts
    )


class JobFiles:
    """One server's files in one job folder: what it is sent, and where what
    it sends back is kept.

    ========================= ==================================================
    location / type           goes to
    ========================= ==================================================
    ``CV`` / ``log``          the job's log, as ``  <server> <text>``
    ``CV`` / ``step``         ``<job>/<server>/<name>``
    ``CV`` / ``cv``           ``<job>/cv_<candidate>_<title>[_<server>].pdf``
    ``CACHE`` / ``step``      ``<server folder>/cache/<name>``
    ``CACHE`` / ``log``       appended to ``<server folder>/cache/log.log``
    anything else             skipped, with a line in the job's log
    ========================= ==================================================

    The suffix is left off for the default server. A name that would leave
    its folder is skipped the same way.
    """

    def __init__(self, job_folder: Path, server: Server) -> None:
        self.job = Path(job_folder)
        self.server = server
        #: What this server stored for the job.
        self.own = self.job / server.name

    def outgoing(self) -> Dict[str, bytes]:
        """The job as this server sees it: the posting, its analysis, and
        everything it stored here before."""
        files = {JD_FILENAME: (self.job / JD_FILENAME).read_bytes()}
        analysis = self.job / ANALYSIS_FILENAME
        if analysis.is_file():
            files[ANALYSIS_FILENAME] = analysis.read_bytes()
        if self.own.is_dir():
            for path in sorted(self.own.rglob("*")):
                if path.is_file():
                    files[path.relative_to(self.own).as_posix()] = path.read_bytes()
        return files

    def pdf(self) -> Path:
        """Where this server's PDF goes: named after you and the job title."""
        data_file = self.server.folder / "candidate_data.json"
        data = json.loads(data_file.read_text(encoding="utf-8")) if data_file.is_file() else {}
        analysis = json.loads((self.job / ANALYSIS_FILENAME).read_text(encoding="utf-8"))
        artifacts = Artifacts(str(data.get("name") or "candidate"),
                              str(analysis.get("job_title") or ""))
        return self.job / artifacts.pdf("" if self.server.default else self.server.name)

    def store(self, item: StoreItem, log: JobLog) -> Optional[Path]:
        """Keep one thing the server sent; the file written, if any."""
        where = (item.location, item.type)
        if where == ("CV", "log"):
            log.server_line(self.server.name, item.content().decode("utf-8", errors="replace"))
            return None
        if where == ("CV", "cv"):
            return self._pdf(item, log)
        if where == ("CACHE", "log"):
            cache = self.server.folder / CACHE_DIRNAME
            cache.mkdir(exist_ok=True)
            with (cache / LOG_FILENAME).open("ab") as fh:
                fh.write(item.content() + b"\n")
            return cache / LOG_FILENAME
        bases = {("CV", "step"): self.own,
                 ("CACHE", "step"): self.server.folder / CACHE_DIRNAME}
        if where not in bases or not _safe(item.name):
            log.step(f"⚠ {self.server.name}: skipped a {item.location}/{item.type} item "
                     f"named {item.name!r}")
            return None
        target = bases[where] / item.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(item.content())
        return target

    def _pdf(self, item: StoreItem, log: JobLog) -> Path:
        """The CV, then the pack's images beside its ``cv.tex``, so it
        compiles again by hand."""
        target = self.pdf()
        target.write_bytes(item.content())
        self.own.mkdir(exist_ok=True)
        for image in sorted(self.server.folder.iterdir()):
            if image.is_file() and image.suffix in IMAGE_SUFFIXES:
                shutil.copy2(image, self.own / image.name)
        log.step(f"{self.server.name}: CV Path: {target.name}")
        return target


class Workspace:
    """Owns the output tree and every move within it."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.working = self.root / "working"
        self.error = self.root / "error"
        self.discarded = self.root / "discarded"
        self.cv = self.root / "CV"

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
        """Finished: ``CV/<day>/<Company>_<Title>``."""
        return self._move_into(job, self.cv / day())

    def discard(self, job: Path) -> Path:
        """You said no: ``discarded/<day>/<Company>_<Title>``."""
        return self._move_into(job, self.discarded / day())

    def to_error(self, entry: Path) -> Path:
        """Failed or not a job description: ``error/<day>/<name>``.

        The day folder is the day it failed, like ``CV/`` and ``discarded/``.
        An entry of the same name that day is the same posting failing again,
        and is replaced rather than numbered.
        """
        target = self.error / day() / entry.name
        target.parent.mkdir(parents=True, exist_ok=True)
        _clear(target)
        shutil.move(str(entry), str(target))
        return target

    # --- history ------------------------------------------------------------
    def jobs_in(self, parent: Path) -> List[Path]:
        """Every analysed job under ``CV/`` or ``discarded/`` (``<day>/<job>``)."""
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
    "Workspace", "Artifacts", "JobFiles", "sanitize_name", "job_name", "staged_job",
    "unstage", "day",
    "JD_FILENAME", "ANALYSIS_FILENAME", "LOG_FILENAME", "LETTER_FILENAME", "CACHE_DIRNAME",
]
