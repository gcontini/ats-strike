"""The stages at once.

Recovery first, synchronously: whatever an earlier run left in ``working/``
is sent on before anything new is read. Then the background stages run on
threads of their own — the inbox and the CV writer for ``watch``, the CV
writer alone for ``clipboard`` — and the part that asks you runs here, on the
main thread, so Ctrl-C lands where it can stop everything.

``q`` stops gently: nothing new is queued, each background stage finishes
the posting in hand, and all of them are waited for. Ctrl-C is not handled
here at all. It propagates, the threads (daemons) go with the process, and
the next start picks up whatever they were doing.
"""

from __future__ import annotations

import threading
import traceback
from typing import Callable, Dict, List, Optional, Protocol

from .terminal import Console, Keyboard
from .working import WorkingProcessor


class Stage(Protocol):
    """A stage that runs on a thread of its own until stopped."""

    def run(self, stop: threading.Event) -> None: ...

    def busy(self) -> Optional[str]:
        """What it is in the middle of, for "finishing ..."; ``None`` when idle."""
        ...


class Watcher:
    """Starts the stages, and stops them when you quit."""

    def __init__(
        self,
        working: WorkingProcessor,
        foreground: Callable[[threading.Event], None],
        background: Dict[str, Stage],
        keyboard: Keyboard,
        console: Console,
        *,
        join_seconds: float = 0.5,
    ) -> None:
        self.working = working
        #: The main thread's loop; it returns when you quit.
        self.foreground = foreground
        #: Thread name -> stage.
        self.background = background
        self.keyboard = keyboard
        self.console = console
        self.join_seconds = join_seconds
        self._crashed = False

    def run(self) -> int:
        """Until you quit. 1 if a job failed or a stage crashed, else 0."""
        self.working.recover()
        stop = threading.Event()
        self.keyboard.start()
        threads = [self._start(name, stage, stop) for name, stage in self.background.items()]
        self.foreground(stop)

        self.working.close()
        stop.set()
        self._wait(threads)
        left = [p for p in self.working.workspace.working.iterdir() if not p.name.startswith(".")]
        if left:
            self.console.say(f"⏹ stopped. {len(left)} posting(s) wait in working/ "
                             "for the next start.")
        else:
            self.console.say("⏹ stopped.")
        return 1 if self.working.failures or self._crashed else 0

    def _start(self, name: str, stage: Stage, stop: threading.Event) -> threading.Thread:
        """A stage on a daemon thread. A crash stops every stage, loudly."""

        def guarded() -> None:
            try:
                stage.run(stop)
            except Exception:
                self._crashed = True
                self.console.say(f"✗ the {name} thread stopped:\n"
                                 f"{traceback.format_exc().rstrip()}")
                stop.set()

        thread = threading.Thread(target=guarded, name=name, daemon=True)
        thread.start()
        return thread

    def _wait(self, threads: List[threading.Thread]) -> None:
        """Join in short steps: a lock wait cannot be interrupted on Windows,
        and Ctrl-C has to stay a way out while the last CV is finished."""
        busy = [what for what in (stage.busy() for stage in self.background.values()) if what]
        if busy:
            self.console.say(f"⏳ finishing {' and '.join(busy)}. Ctrl-C to stop now.")
        while any(thread.is_alive() for thread in threads):
            for thread in threads:
                thread.join(timeout=self.join_seconds)


__all__ = ["Watcher", "Stage"]
