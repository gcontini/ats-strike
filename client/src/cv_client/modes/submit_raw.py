"""``cv submit-raw`` — a job description in, files in this folder out.

Nothing but the CV: no detection, no analysis, no question before spending,
no cover letter, no spreadsheet and no folder structure. What you get is what
you asked for with ``-o``, written where you are standing, by the
``--server_ver`` server or else the default.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Set, Tuple

from cv_contracts import StoreItem

from ..api import CVError
from ..config import Config
from ..cvjob import JD_FILE, write_cv
from ..ui import fail
from . import connect

#: What ``-o`` understands, and the item each takes: the suffix is the whole
#: instruction. The CV is the ``cv`` item whatever its name.
WANTED = {".pdf": ("cv", ""), ".json": ("step", "cv.json"), ".tex": ("step", "cv.tex")}


def run(config: Config, jd_file: Path, outputs: List[Path], *, new: bool = False) -> int:
    jd_file = Path(jd_file).expanduser()
    if not jd_file.is_file():
        fail(f"no such file: {jd_file}")
    for target in outputs:
        if target.suffix not in WANTED:
            fail(f"-o takes a {', '.join(WANTED)} file, not {target.name}")
    # A name you chose is a name you meant, so it is overwritten without
    # asking. The default name is not, so it counts up instead.
    targets = outputs or [_free_pdf(jd_file.stem)]
    by_item: Dict[Tuple[str, str], List[Path]] = {}
    for target in targets:
        by_item.setdefault(WANTED[target.suffix], []).append(target)
    written: Set[Path] = set()

    def store(item: StoreItem) -> None:
        """Written as it arrives, so it is on disk before the session is let go."""
        if (item.location, item.type) == ("CV", "log"):
            if config.verbose:
                print(f"  {server.name} {item.text}", flush=True)
            return
        if item.location != "CV":
            return
        for target in by_item.get((item.type, "" if item.type == "cv" else item.name), []):
            target.write_bytes(item.content())
            written.add(target)
            print(f"✅ {target}", flush=True)

    [server] = config.targets
    calls = connect([server], verbose=config.verbose)[server.name]
    try:
        write_cv(calls.api, server, {JD_FILE: jd_file.read_bytes()}, say=_say, store=store,
                 new=new, verbose=config.verbose)
    except CVError as exc:
        where = f" (request {exc.request_id})" if exc.request_id else ""
        fail(f"{exc}{where}")
    missing = [target.name for target in targets if target not in written]
    if missing:
        fail(f"{server.name} sent nothing for {', '.join(missing)}")
    return 0


def _free_pdf(stem: str) -> Path:
    """``posting.pdf``, then ``posting_1.pdf``, ``posting_2.pdf``..."""
    target = Path(f"{stem}.pdf")
    n = 0
    while target.exists():
        n += 1
        target = Path(f"{stem}_{n}.pdf")
    return target


def _say(message: str) -> None:
    print(message, flush=True)
