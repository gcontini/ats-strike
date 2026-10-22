"""Stages 2 to 4: where a job folder goes next, and every move that puts it there.

:class:`WorkingProcessor` is the only thing that moves a job folder or writes
``approval_status.txt``. The duplicate check, the new folder and every move
happen under one lock, so a posting being filed never races one being
delivered, and the same posting dropped twice is never written twice.

The inbox hands it analysed postings and folders (:meth:`submit`,
:meth:`submit_folder`), ``clipboard`` and ``submit`` the postings they are
about to ask you about (:meth:`hold`), the approval loop your answers
(:meth:`approve`, :meth:`discard`, :meth:`requeue`), and the CV worker
finished and failed jobs (:meth:`deliver`, :meth:`fail`). Anything that has to
wait goes on one of its two queues; where it goes is decided by
:func:`~.jobfolder.route`, for new postings and leftovers alike.
"""

from __future__ import annotations

import queue
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Optional

from cv_contracts import JDAnalysis

from ..api import CVError
from ..duplicates import find_previous
from ..tracking import TEMPLATE_NAME, Tracker
from ..workspace import JD_FILENAME, LOG_FILENAME, Workspace
from .jobfolder import (
    APPROVED,
    DISCARDED,
    PENDING,
    Route,
    classify,
    read_analysis,
    write_analysis,
    write_approval,
)
from .terminal import Console, TaggedLog

if TYPE_CHECKING:
    from .generation import CvWriter


class WorkingProcessor:
    """Routes job folders between the stages and files them at the end."""

    def __init__(
        self,
        workspace: Workspace,
        writer: "CvWriter",
        tracker: Tracker,
        console: Console,
        *,
        ask: bool = True,
    ) -> None:
        self.workspace = workspace
        self.writer = writer
        self.tracker = tracker
        self.console = console
        #: False with watch --yes: postings that need you wait in working/.
        self.ask = ask
        #: Postings waiting for you (CHECK, or PENDING), in the order they arrived.
        self.approvals: "queue.Queue[Path]" = queue.Queue()
        #: Folders waiting for their CV.
        self.generations: "queue.Queue[Path]" = queue.Queue()
        #: Jobs that failed (rejections are not failures): the exit code.
        self.failures = 0
        self._lock = threading.Lock()
        self._closed = threading.Event()

    # --- from the inbox -----------------------------------------------------
    def seen_url(self, url: str, log: TaggedLog) -> bool:
        """A posting known by its URL alone, before it is analysed."""
        with self._lock:
            return self._seen_before({"posting_url": url}, log)

    def submit(self, jd_text: str, analysis: JDAnalysis, log: TaggedLog) -> Optional[Path]:
        """A posting the inbox has analysed. ``None`` for one seen before."""
        folder = self._new_job(jd_text, analysis, log)
        if folder is not None:
            self._send(folder, log)
        return folder

    def submit_folder(self, source: Path, log: TaggedLog) -> Optional[Path]:
        """An analysed folder dropped into the inbox, copied in whole.

        Everything in it comes along — a CV document to re-render, your
        ``approval_status.txt`` — and the same table as for a leftover decides
        where it goes. ``None`` for a posting seen before.
        """
        analysis = read_analysis(source)
        with self._lock:
            if self._seen_before(analysis.model_dump(), log):
                return None
            folder = self.workspace.new_job(analysis.company_name, analysis.job_title)
            shutil.copytree(source, folder, dirs_exist_ok=True)
        self._send(folder, log)
        return folder

    # --- from clipboard and submit ------------------------------------------
    def hold(self, jd_text: str, analysis: JDAnalysis, log: TaggedLog) -> Optional[Path]:
        """A posting you are about to be asked about: its folder, marked
        ``PENDING`` and sent nowhere until you answer. ``None`` for one seen
        before.

        Left behind unanswered — you quit, or the run was stopped — it is
        asked about again, never decided by its ``should_apply``.
        """
        folder = self._new_job(jd_text, analysis, log)
        if folder is not None:
            write_approval(folder, PENDING)
            log.tag = folder.name
            log.step("❓ waiting for your answer")
            log.append_to(folder / LOG_FILENAME)
        return folder

    # --- at startup ---------------------------------------------------------
    def recover(self) -> None:
        """Whatever an earlier run left in ``working/``, sent on by the table."""
        for entry in sorted(self.workspace.working.iterdir()):
            if entry.name.startswith("."):
                continue
            log = TaggedLog(self.console, entry.name)
            if entry.is_dir():
                log.step("♻ left in working/ by an earlier run")
                self._send(entry, log)
            else:
                log.step("✗ a loose file in working/, not a job folder")
                self.reject(entry, log)

    # --- from you -----------------------------------------------------------
    def approve(self, folder: Path, url: Optional[str] = None) -> None:
        """You said y (or pasted the posting's URL): it goes to the CV queue."""
        log = TaggedLog(self.console, folder.name)
        if url:
            analysis = read_analysis(folder)
            analysis.posting_url = url
            write_analysis(folder, analysis)
        write_approval(folder, APPROVED)
        log.step("👍 approved")
        self._queue_cv(folder, log)

    def discard(self, folder: Path) -> Path:
        """You said d: kept under discarded/, with your answer in it."""
        log = TaggedLog(self.console, folder.name)
        write_approval(folder, DISCARDED)
        log.step("🗑 discarded by you")
        return self._file(folder, log, self.workspace.discard, "discarded")

    def requeue(self, folder: Path) -> None:
        """You said s or n: asked again after the others."""
        self.approvals.put(folder)

    # --- from the CV worker -------------------------------------------------
    def deliver(self, folder: Path, log: TaggedLog, *, record_row: bool) -> Path:
        """Finished: ``CV/<day>/``. Only a generated CV gets a spreadsheet row."""
        delivered = self._file(folder, log, self.workspace.deliver, "delivered")
        if record_row:
            pdf = self.writer.pdf_name(delivered)
            try:
                self.tracker.record(delivered, read_analysis(delivered),
                                    delivered / pdf if pdf else None)
            except Exception as exc:
                # The CV is delivered and was paid for; a broken or locked
                # spreadsheet is bookkeeping, and must not undo that or stop
                # the thread that writes the next one.
                log.step(f"⚠ could not record this job in {TEMPLATE_NAME}: {exc}")
                log.append_to(delivered / LOG_FILENAME)
        return delivered

    def fail(self, folder: Path, log: TaggedLog) -> Path:
        """A call failed: ``error/``, every file kept so it can be dropped back."""
        return self.reject(folder, log, failed=True)

    # --- endings ------------------------------------------------------------
    def reject(self, entry: Path, log: TaggedLog, *, failed: bool = False) -> Path:
        """``error/<day>/<name>``, with why: inside a folder, beside a file."""
        if entry.is_dir():
            log.append_to(entry / LOG_FILENAME)
        with self._lock:
            target = self.workspace.to_error(entry)
        if failed:
            self.count_failure()
        if target.is_file():
            beside = target.with_name(target.name + ".log")
            beside.unlink(missing_ok=True)  # it explained the file just replaced
            log.append_to(beside)
        self.console.say(f"{'❌ failed' if failed else '⚠ rejected'}: {entry.name} -> {target}")
        return target

    def count_failure(self) -> None:
        """A job that failed, with or without a folder to file."""
        with self._lock:
            self.failures += 1

    def close(self) -> None:
        """Stop queuing: what arrives now waits in working/ for the next start."""
        self._closed.set()

    # --- the one switch -----------------------------------------------------
    def _send(self, folder: Path, log: TaggedLog) -> None:
        """Put a folder in working/ where :func:`~.jobfolder.route` says."""
        log.tag = folder.name
        where, why = classify(folder)
        self._route(folder, log, where, why)

    def _route(self, folder: Path, log: TaggedLog, where: Route, why: str) -> None:
        if where == "error":
            log.step(f"✗ {why}")
            self.reject(folder, log)
        elif where == "discard":
            log.step(f"🗑 {why}")
            self._file(folder, log, self.workspace.discard, "discarded")
        elif where == "render":
            self._render(folder, log)
        elif where == "approve":
            if not self.ask:
                log.step(f"⏸ {why}: waits in working/ for a run without --yes")
            elif self._closed.is_set():
                log.step(f"⏸ {why}: waits in working/ for the next start")
            else:
                log.step(f"❓ {why}: waiting for you")
            log.append_to(folder / LOG_FILENAME)
            if self.ask and not self._closed.is_set():
                self.approvals.put(folder)
        else:
            log.step(f"✍ {why}")
            self._queue_cv(folder, log)

    def _queue_cv(self, folder: Path, log: TaggedLog) -> None:
        # The log is written before the folder is queued: from then on the CV
        # worker appends to the same log.log.
        if self._closed.is_set():
            log.step("⏸ waits in working/ for the next start")
            log.append_to(folder / LOG_FILENAME)
            return
        log.step("queued for its CV")
        log.append_to(folder / LOG_FILENAME)
        self.generations.put(folder)

    def _render(self, folder: Path, log: TaggedLog) -> None:
        try:
            self.writer.render(folder, log)
        except CVError:
            self.fail(folder, log)
            return
        self.deliver(folder, log, record_row=False)

    def _file(self, folder: Path, log: TaggedLog, move: Callable[[Path], Path], what: str) -> Path:
        """Write the log into the folder, then move the folder, under the lock."""
        log.append_to(folder / LOG_FILENAME)
        with self._lock:
            target = move(folder)
        self.console.say(f"{'✅' if what == 'delivered' else '🗑'} {what}: {folder.name} -> {target}")
        return target

    def _new_job(self, jd_text: str, analysis: JDAnalysis, log: TaggedLog) -> Optional[Path]:
        """``working/<Company>_<Title>`` holding the posting and its analysis,
        or ``None`` for a posting seen before."""
        with self._lock:
            if self._seen_before(analysis.model_dump(), log):
                return None
            folder = self.workspace.new_job(analysis.company_name, analysis.job_title)
            (folder / JD_FILENAME).write_text(jd_text, encoding="utf-8")
            write_analysis(folder, analysis)
        return folder

    def _seen_before(self, current: Mapping[str, Any], log: TaggedLog) -> bool:
        """Applied, discarded or still in flight: say where, and write nothing."""
        for label, folders in (
            ("already applied", self.workspace.jobs_in(self.workspace.cv)),
            ("already discarded", self.workspace.jobs_in(self.workspace.discarded)),
            ("already in progress", self.workspace.working_jobs()),
        ):
            previous = find_previous(current, folders)
            if previous is not None:
                log.step(f"⚠ {label}: {previous}")
                return True
        return False


__all__ = ["WorkingProcessor"]
