"""Where a CV session lives: a directory of events, one file each.

One directory per session under ``ATS_WORK_DIR``, named by its id::

    <root>/<request_id>/events/
        000001.json   {"type": "status", "data": {...}}
        000002.json   {"type": "log", "data": {...}}
        ...
        000042.json   {"type": "result", "data": {...}}   the last one

Each event is its own file, written once and never changed. That is what lets
the root be a bucket mounted as a folder (OSS, Cloud Storage): an object there
cannot be appended to, and a rename is a copy. A reader lists the directory
and reads what is new; nothing it has read can change under it. The one file
that may not read whole is the one being written, and it is always the
newest, so a reader stops there and finds it complete on its next look.

One worker writes a session and everyone else only reads, so there are no
locks between processes. A session lasts until the client acknowledges it,
or until its last event is older than the TTL; a running one is never pruned.
"""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path
from threading import Lock
from typing import Any, List, Optional, Tuple

from cv_contracts import TERMINAL_EVENTS

#: A request id becomes a path component, so it may only look like one.
SAFE_ID = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")

EVENTS = "events"
_EVENT_FILE = re.compile(r"\A(\d{6})\.json\Z")


class JobDir:
    """One session's directory."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.request_id = path.name
        self._events = path / EVENTS
        #: The writer's next sequence number, found on its first append.
        self._next: Optional[int] = None
        self._lock = Lock()

    # --- writing --------------------------------------------------------------
    def append(self, kind: str, data: Any) -> None:
        """Add one event. Never at the cost of the job: a full disk must not
        turn a finished CV into a crash."""
        with self._lock:
            if self._next is None:
                numbered = self._numbered()
                self._next = numbered[-1][0] + 1 if numbered else 1
            target = self._events / f"{self._next:06d}.json"
            try:
                self._events.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps({"type": kind, "data": data}), encoding="utf-8")
            except OSError:
                # A half-written event would stop every reader at it; the
                # next append takes the number over.
                target.unlink(missing_ok=True)
                return
            self._next += 1

    def delete(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)

    # --- reading --------------------------------------------------------------
    def events(self, after: int = 0) -> List[Tuple[int, str, Any]]:
        """Every event after number ``after``, in order: ``(number, type, data)``."""
        found = []
        for number, path in self._numbered():
            if number <= after:
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                break  # still being written: it will be there next time
            found.append((number, record["type"], record["data"]))
        return found

    @property
    def started(self) -> bool:
        """Whether the session has its first event."""
        return bool(self._numbered())

    @property
    def running(self) -> bool:
        """Whether the job has yet to write its last event."""
        numbered = self._numbered()
        if not numbered:
            return True
        try:
            last = json.loads(numbered[-1][1].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True
        return last["type"] not in TERMINAL_EVENTS

    def updated(self) -> float:
        """When the newest event was written, as a timestamp."""
        numbered = self._numbered()
        try:
            return numbered[-1][1].stat().st_mtime if numbered else 0.0
        except OSError:
            return 0.0

    def _numbered(self) -> List[Tuple[int, Path]]:
        try:
            names = [p.name for p in self._events.iterdir()]
        except OSError:
            return []
        return sorted(
            (int(match.group(1)), self._events / name)
            for name in names
            if (match := _EVENT_FILE.match(name))
        )


class JobStore:
    """The sessions under one root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def create(self, request_id: str) -> JobDir:
        """A session's directory, ready for its first event."""
        path = self.root / request_id
        path.mkdir(parents=True, exist_ok=True)
        return JobDir(path)

    def open(self, request_id: str) -> Optional[JobDir]:
        """A session with at least one event, or ``None`` — unknown,
        acknowledged, pruned, or not an id at all."""
        if not SAFE_ID.match(request_id):
            return None
        job = JobDir(self.root / request_id)
        return job if job.started else None

    def all(self) -> List[JobDir]:
        """Every session under the root.

        The root may be the system temp directory, so a directory only counts
        when it is named like a request id *and* holds an events directory —
        and one we cannot even look inside (a shared ``/tmp`` holds other
        processes' directories) is provably not one of ours.
        """
        found = []
        for path in self.root.glob("*"):
            if not SAFE_ID.match(path.name):
                continue
            try:
                if (path / EVENTS).is_dir():
                    found.append(JobDir(path))
            except OSError:
                continue
        return found

    def prune(self, older_than: float) -> Tuple[int, int]:
        """Delete every session whose last event is ``older_than`` seconds old
        or more — every session at all for 0 — never one still running.
        Returns how many were deleted and how many were kept because they were
        running."""
        deleted = running = 0
        now = time.time()
        for job in self.all():
            if older_than > 0 and now - job.updated() < older_than:
                continue
            if job.running:
                running += 1
                continue
            job.delete()
            deleted += 1
        return deleted, running


__all__ = ["JobStore", "JobDir", "SAFE_ID", "EVENTS"]
