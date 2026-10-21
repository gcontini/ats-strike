"""``cv watch`` — watch a folder; each posting goes through four stages.

The stages live in :mod:`cv_client.stages`. This checks the folders and
your files, builds the stages and runs them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from ..api import HttpApi
from ..config import REQUIRED_FILES, Config
from ..stages.approval import HumanApproval, TerminalReviewer
from ..stages.calls import Calls
from ..stages.generation import CvWriter, GenerationWorker
from ..stages.inbox import InputProcessor
from ..stages.terminal import Console, Keyboard
from ..stages.watcher import Watcher
from ..stages.working import WorkingProcessor
from ..tracking import build_tracker
from ..ui import fail, warn
from ..workspace import Workspace


def run(config: Config, inbox: Optional[Path], out: Path, *, assume_yes: bool = False,
        track: bool = True, new: bool = False) -> int:
    if inbox is None:
        inbox = Path.cwd() / "incoming"
        warn(f"no --in given, watching {inbox}")
        inbox.mkdir(exist_ok=True)
    else:
        inbox = Path(inbox).expanduser().resolve()
        if not inbox.is_dir():
            fail(f"input folder does not exist: {inbox}")
    if not os.access(inbox, os.W_OK):
        # Each posting is deleted once handed over; one that cannot be would
        # be analysed, and paid for, again on every pass.
        fail(f"cannot delete files from {inbox}: the watcher removes each posting it hands over")
    for name in REQUIRED_FILES:
        config.require(name)

    workspace = Workspace(out).ensure()
    if workspace.root == inbox:
        fail("the input folder and the output folder must be different")

    ask = not assume_yes
    console = Console()
    calls = Calls(HttpApi(config.server_url, token=config.token, verbose=config.verbose))
    writer = CvWriter(calls, config, new=new)
    working = WorkingProcessor(workspace, writer, build_tracker(workspace.root, track), console,
                               ask=ask)
    keyboard = Keyboard(sys.stdin)
    approval = HumanApproval(working, TerminalReviewer(keyboard), keyboard, console, ask=ask)
    watcher = Watcher(
        working,
        approval.run,
        {"inbox": InputProcessor(inbox, calls, config, working, console),
         "cv": GenerationWorker(working, writer, console)},
        keyboard,
        console,
    )

    print(f"👀 watching {inbox}. Output: {workspace.root}", flush=True)
    print("   Drop postings (.txt) or analysed job folders in. "
          "q = let the running work finish, then quit. Ctrl-C = stop now.", flush=True)
    if assume_yes:
        print("   --yes: nothing is asked. CHECK postings wait in working/ for a run "
              "without --yes.", flush=True)
    try:
        return watcher.run()
    except KeyboardInterrupt:
        print("\n⏹ stopped now. Anything in working/ is picked up at the next start, "
              "anything left in the input folder is read again.", flush=True)
        return 0
