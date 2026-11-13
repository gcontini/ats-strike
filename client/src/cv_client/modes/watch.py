"""``cv watch`` — watch a folder; each posting goes through four stages.

The stages live in :mod:`cv_client.stages`. This checks the folders, builds
the stages — the inbox on the default server, one CV writer per server — and
runs them.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from ..config import Config
from ..stages.approval import HumanApproval, TerminalReviewer
from ..stages.generation import CvWriter, GenerationWorker
from ..stages.inbox import InputProcessor
from ..stages.terminal import Console, Keyboard
from ..stages.watcher import Watcher
from ..stages.working import WorkingProcessor
from ..tracking import build_tracker
from ..ui import fail, warn
from ..workspace import Workspace
from . import connect


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

    workspace = Workspace(out).ensure()
    if workspace.root == inbox:
        fail("the input folder and the output folder must be different")

    ask = not assume_yes
    console = Console(verbose=config.verbose)
    calls = connect(config.servers(), verbose=config.verbose)
    writer = CvWriter(calls, config, new=new)
    working = WorkingProcessor(workspace, writer, build_tracker(workspace.root, track), console,
                               ask=ask)
    keyboard = Keyboard(sys.stdin)
    approval = HumanApproval(working, TerminalReviewer(keyboard), keyboard, console, ask=ask)
    background = {"inbox": InputProcessor(inbox, calls[config.default.name], working, console)}
    for server in config.lanes():
        background[f"cv-{server.name}"] = GenerationWorker(working, writer, console, server)
    watcher = Watcher(working, approval.run, background, keyboard, console)

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
