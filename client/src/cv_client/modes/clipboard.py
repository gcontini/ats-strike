"""``cv clipboard`` — watch the clipboard; each CV is written while you read the next posting.

What an earlier run left in ``working/`` is dealt with first, as you choose:
resumed (approved postings go to the CV writer, unanswered ones are asked
about before anything new), cleaned out, or left alone by quitting. Then the
clipboard is read on the main thread (:mod:`cv_client.stages.listener`)
and the CVs are written on a thread of their own.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..api import HttpApi
from ..config import REQUIRED_FILES, Config
from ..sources.clipboard import NO_CLIPBOARD_HINT, ClipboardSource, clipboard_error
from ..stages.calls import Calls
from ..stages.generation import CvWriter, GenerationWorker
from ..stages.intake import Intake
from ..stages.listener import Listener
from ..stages.terminal import Console, Keyboard
from ..stages.watcher import Watcher
from ..stages.working import WorkingProcessor
from ..tracking import build_tracker
from ..ui import AutoConfirmer, Confirmer, PromptConfirmer, ask_recovery, fail
from ..workspace import Workspace


def run(config: Config, out: Path, *, assume_yes: bool = False, track: bool = True,
        new: bool = False) -> int:
    problem = clipboard_error()
    if problem:
        fail(f"{problem}\n{NO_CLIPBOARD_HINT}")
    for name in REQUIRED_FILES:
        config.require(name)

    workspace = Workspace(out).ensure()
    files, folders = workspace.pending()
    if files or folders:
        # Asked before the keyboard thread starts: input() is still free.
        choice = ask_recovery(len(files), len(folders))
        if choice == "quit":
            return 0
        if choice == "clean":
            print(f"🧹 removed {workspace.clean()} leftover entr(ies)", flush=True)
        # "resume" is the watcher's own first step: WorkingProcessor.recover.

    console = Console()
    keyboard = Keyboard(sys.stdin)
    calls = Calls(HttpApi(config.server_url, token=config.token, verbose=config.verbose))
    writer = CvWriter(calls, config, new=new)
    # ask stays True with --yes: here every posting goes to the confirmer, and
    # --yes is the confirmer that says yes.
    working = WorkingProcessor(workspace, writer, build_tracker(workspace.root, track), console)
    confirmer: Confirmer = AutoConfirmer() if assume_yes else PromptConfirmer(keyboard)
    listener = Listener(ClipboardSource(), Intake(calls, config, working, confirmer, console),
                        working, keyboard, console, assume_yes=assume_yes)
    watcher = Watcher(working, listener.run, {"cv": GenerationWorker(working, writer, console)},
                      keyboard, console)

    print(f"📋 watching the clipboard. Output: {workspace.root}", flush=True)
    print("   Copy a job posting to start. q = let the CV being written finish, then quit. "
          "Ctrl-C = stop now.", flush=True)
    try:
        return watcher.run()
    except KeyboardInterrupt:
        print("\n⏹ stopped now. Anything in working/ is offered again at the next start.",
              flush=True)
        return 0
