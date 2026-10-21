"""``cv render`` — a document or a .tex in, a PDF beside it out.

The only mode that needs neither your profile nor a job description: it is
the one to reach for after hand-editing a ``cv_*.json`` or a ``cv_*.tex``.

The ``.json`` is one flat object — whatever the model wrote with your own
candidate data over the top — which is exactly what a CV job's result held
and what the job folder holds.

A failed render is nearly always a LaTeX error, and the server's answer
carries the end of the TeX log, which says where.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..api import HttpApi, CVError, read_text
from ..config import Config
from ..ui import fail


def run(config: Config, source: Path, *, output: Path | None = None) -> int:
    source = Path(source).expanduser()
    if not source.is_file():
        fail(f"no such file: {source}")

    api = HttpApi(config.server_url, token=config.token, verbose=config.verbose)
    template = read_text(config.path("resume.tex.jinja"))
    images = config.image_parts()

    try:
        if source.suffix == ".tex":
            envelope = api.render(tex=source.read_text(encoding="utf-8"), images=images)
        elif source.suffix == ".json":
            # Not validated here: the server is the one that has to be able to
            # render it, and it says so with a stage and a cause.
            document = json.loads(source.read_text(encoding="utf-8"))
            envelope = api.render(document=document, template=template, images=images)
        else:
            fail(f"render takes a .tex or a .json file, not {source.suffix or 'a directory'}")
    except CVError as exc:
        fail(f"{exc}")
    except ValueError as exc:
        fail(f"{source.name} is not valid JSON: {exc}")

    rendered = envelope.data
    target = Path(output) if output else source.with_suffix(".pdf")
    target.write_bytes(rendered.pdf_bytes())
    print(f"✅ {target}", flush=True)
    return 0
