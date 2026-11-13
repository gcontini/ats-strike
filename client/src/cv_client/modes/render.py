"""``cv render`` — a ``cv.json`` or a ``cv.tex`` in, a PDF beside it out.

The one to reach for after hand-editing the CV a server stored in a job
folder (``<job>/<server>/cv.json`` or ``cv.tex``). The file goes to the
``--server_ver`` server, or else the default, with that server folder's files,
so the template and images are the ones it would write with today. Only the
PDF is kept.

A failed render is nearly always a LaTeX error, and the server's answer
carries the end of the TeX log, which says where.
"""

from __future__ import annotations

from pathlib import Path

from ..api import CVError
from ..config import Config
from ..ui import fail
from . import connect

#: What the file is sent as, by its suffix: the server renders a document and
#: compiles a ``.tex`` as it stands.
JOB_NAMES = {".json": "cv.json", ".tex": "cv.tex"}


def run(config: Config, source: Path, *, output: Path | None = None) -> int:
    source = Path(source).expanduser()
    if not source.is_file():
        fail(f"no such file: {source}")
    name = JOB_NAMES.get(source.suffix)
    if name is None:
        fail(f"render takes a .tex or a .json file, not {source.suffix or 'a directory'}")

    [server] = config.targets
    calls = connect([server], verbose=config.verbose)[server.name]
    try:
        reply = calls.make(lambda: calls.api.render(server.pack(), {name: source.read_bytes()},
                                                    server.settings))
    except CVError as exc:
        fail(f"{exc}")

    if config.verbose:
        for item in reply.items:
            if (item.location, item.type) == ("CV", "log"):
                print(f"  {server.name} {item.text}", flush=True)
    pdf = next((item for item in reply.items if (item.location, item.type) == ("CV", "cv")),
               None)
    if pdf is None:
        fail(f"{server.name} sent no PDF")
    target = Path(output) if output else source.with_suffix(".pdf")
    target.write_bytes(pdf.content())
    print(f"✅ {target}", flush=True)
    return 0
