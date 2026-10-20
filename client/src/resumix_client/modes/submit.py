"""``resumix submit`` — one file, one CV, then exit.

The same steps as ``clipboard`` — analysed, asked about, written — but the CV
is written right here, and the command returns once it is filed. Whatever an
earlier run left in ``working/`` is not touched: a one-off run must not start
by asking about it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

from resumix_contracts import JDAnalysis

from ..api import HttpApi
from ..config import Config
from ..stages.calls import Calls
from ..stages.generation import CvWriter, GenerationWorker
from ..stages.intake import Intake
from ..stages.terminal import Console, Keyboard
from ..stages.working import WorkingProcessor
from ..tracking import build_tracker
from ..ui import AutoConfirmer, Confirmer, PromptConfirmer, fail
from ..workspace import Workspace


def run(config: Config, jd_file: Path, out: Path, *, analysis: Optional[Path] = None,
        assume_yes: bool = False, track: bool = True, new: bool = False) -> int:
    jd_file = Path(jd_file).expanduser()
    if not jd_file.is_file():
        fail(f"no such file: {jd_file}")
    given: Optional[JDAnalysis] = None
    if analysis is not None:
        analysis = Path(analysis).expanduser()
        if not analysis.is_file():
            fail(f"no such file: {analysis}")
        try:
            given = JDAnalysis.model_validate_json(analysis.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            fail(f"{analysis} is not a valid analysis: {exc}")

    workspace = Workspace(out).ensure()
    console = Console()
    calls = Calls(HttpApi(config.server_url, token=config.token, verbose=config.verbose))
    writer = CvWriter(calls, config, new=new)
    working = WorkingProcessor(workspace, writer, build_tracker(workspace.root, track), console)
    if assume_yes:
        confirmer: Confirmer = AutoConfirmer()
    else:
        keyboard = Keyboard(sys.stdin)
        keyboard.start()
        confirmer = PromptConfirmer(keyboard)

    text = jd_file.read_bytes().decode("utf-8", errors="replace")
    if not Intake(calls, config, working, confirmer, console).take(text, jd_file.name,
                                                                   analysis=given):
        print("⏹ stopped. The posting waits in working/, and is asked about again at "
              "the next start of clipboard or watch.", flush=True)
    GenerationWorker(working, writer, console).drain()
    return 1 if working.failures else 0
