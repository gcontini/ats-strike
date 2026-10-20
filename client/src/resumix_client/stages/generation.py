"""Stage 3: writing each CV, one at a time.

:class:`CvWriter` finishes a job folder in place: the CV document, its LaTeX,
its PDF and the images the LaTeX includes — everything needed to render it
again later with no model call. :class:`GenerationWorker` is the loop around
it: take the next folder, write it, and hand it back to the
:class:`~.working.WorkingProcessor` to deliver, or to file under ``error/``.
``watch`` and ``clipboard`` run it on a thread of its own; ``submit`` drains
it on the main thread and waits.
"""

from __future__ import annotations

import json
import queue
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from resumix_contracts import RenderedCV

from ..api import ResumixError, read_text
from ..config import LETTER_PROMPT, Config
from ..cvjob import write_cv
from ..joblog import JobLog
from ..workspace import LETTER_FILENAME, Artifacts
from .calls import Calls
from .jobfolder import read_analysis, read_jd, render_source
from .terminal import Console, TaggedLog

if TYPE_CHECKING:
    from .working import WorkingProcessor


class CvWriter:
    """Writes a folder's CV — or re-renders the one already in it — beside it."""

    def __init__(self, calls: Calls, config: Config, *, new: bool = False) -> None:
        self.calls = calls
        self.config = config
        #: --new: write each CV again rather than replay the run the server
        #: already has for that posting.
        self.new = new

    def generate(self, folder: Path, log: JobLog) -> None:
        """The CV (and the letter, if asked for): minutes of the large model.

        A failed call is recorded in ``log`` — with every line the server
        logged for the CV — and raised again for the caller to file the folder.
        """
        jd_text = read_jd(folder)
        try:
            if self.config.cover_letter != "letter_only":
                log.step("✍ writing the CV (this takes minutes)...")
                write_cv(
                    self.calls.api, self.config, jd_text, say=log.step,
                    keep=lambda rendered: self._save(folder, rendered, log, document=True),
                    log_line=log.server_line, new=self.new,
                )
            if self.config.cover_letter in ("yes", "letter_only"):
                self._letter(folder, jd_text, log)
        except ResumixError as exc:
            self.calls.failed(log, exc)
            raise

    def render(self, folder: Path, log: JobLog) -> None:
        """The CV already in ``folder``, compiled again: no model call.

        From the document when there is one, with the template it would be
        written with today; else from the ``.tex``, exactly as it stands.
        """
        source = render_source(folder)
        try:
            if source is None:
                raise ResumixError(f"there is no CV to render in {folder.name}")
            log.step(f"♻ re-rendering {source.name} (no model call)")
            is_document = source.suffix.lower() == ".json"
            try:
                text = source.read_text(encoding="utf-8")
                document = json.loads(text) if is_document else None
            except OSError as exc:
                raise ResumixError(f"{source.name} cannot be read: {exc}") from exc
            except ValueError as exc:  # bad JSON, or bytes that are not UTF-8
                kind = "JSON" if is_document else "UTF-8 text"
                raise ResumixError(f"{source.name} is not valid {kind}: {exc}") from exc
            if is_document:
                rendered = self.calls.make(lambda: self.calls.api.render(
                    document=document,
                    template=read_text(self.config.path("resume.tex.jinja")),
                    images=self.config.image_parts(),
                ))
                self._save(folder, rendered, log, document=False)
            else:
                rendered = self.calls.make(lambda: self.calls.api.render(
                    tex=text, images=self.config.image_parts(),
                ))
                self._save(folder, rendered, log, document=False, tex=False)
        except ResumixError as exc:
            self.calls.failed(log, exc)
            raise

    def pdf_name(self, folder: Path) -> Optional[str]:
        """What the CV in a delivered folder is called; ``None`` for a letter only."""
        return None if self.config.cover_letter == "letter_only" else self._artifacts(folder).pdf

    # --- helpers ------------------------------------------------------------
    def _save(
        self, folder: Path, rendered: RenderedCV, log: JobLog, *,
        document: bool, tex: bool = True,
    ) -> None:
        """The files, and the images the ``.tex`` includes, so the folder
        renders again on its own."""
        artifacts = self._artifacts(folder)
        if document:
            (folder / artifacts.document).write_text(
                json.dumps(rendered.document, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        if tex:
            (folder / artifacts.tex).write_text(rendered.tex, encoding="utf-8")
        (folder / artifacts.pdf).write_bytes(rendered.pdf_bytes())
        for name, path in self.config.images.items():
            shutil.copy2(path, folder / name)
        log.step(f"CV Path: {artifacts.pdf}")

    def _letter(self, folder: Path, jd_text: str, log: JobLog) -> None:
        log.step("✉ writing the cover letter...")
        letter = self.calls.make(lambda: self.calls.api.letter(
            jd_text,
            profile=self.config.require("candidate_profile.json").read_bytes(),
            candidate_data=self.config.require("candidate_data.json").read_bytes(),
            analysis=read_analysis(folder).model_dump_json(),
            prompt=read_text(self.config.path(LETTER_PROMPT)),
            temperature=self.config.temperature,
            presence_penalty=self.config.presence_penalty,
        ))
        (folder / LETTER_FILENAME).write_text(letter.text, encoding="utf-8")
        log.step(f"Letter Path: {LETTER_FILENAME}")

    def _artifacts(self, folder: Path) -> Artifacts:
        """File names come from your own candidate data and the folder's job title."""
        data = json.loads(self.config.require("candidate_data.json").read_text(encoding="utf-8"))
        return Artifacts(str(data.get("name") or "candidate"), read_analysis(folder).job_title)


class GenerationWorker:
    """The CV queue, one folder at a time: on a thread of its own, or drained
    right here by ``submit``."""

    def __init__(
        self, working: "WorkingProcessor", writer: CvWriter, console: Console,
        *, poll_seconds: float = 0.5,
    ) -> None:
        self.working = working
        self.writer = writer
        self.console = console
        self.poll_seconds = poll_seconds
        #: The folder being written right now, for "finishing ..." on q.
        self.current: Optional[str] = None

    def run(self, stop: threading.Event) -> None:
        """Until stopped. A folder already started is always finished first."""
        while not stop.is_set():
            try:
                folder = self.working.generations.get(timeout=self.poll_seconds)
            except queue.Empty:
                continue
            self.process(folder)

    def drain(self) -> None:
        """Every folder queued now, on the calling thread."""
        while True:
            try:
                folder = self.working.generations.get_nowait()
            except queue.Empty:
                return
            self.process(folder)

    def busy(self) -> Optional[str]:
        """What is being written right now, for "finishing ..." on q."""
        current = self.current
        return f"the generation of {current}" if current else None

    def process(self, folder: Path) -> None:
        """Write one folder's CV, then deliver it or file it under error/."""
        if not folder.is_dir():
            return
        self.current = folder.name
        log = TaggedLog(self.console, folder.name)
        try:
            self.writer.generate(folder, log)
        except ResumixError:
            self.working.fail(folder, log)
        else:
            self.working.deliver(folder, log, record_row=True)
        finally:
            self.current = None


__all__ = ["CvWriter", "GenerationWorker"]
