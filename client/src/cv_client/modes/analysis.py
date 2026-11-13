"""``cv analysis`` — one posting in, its analysis out. Nothing else.

No detection, no question, no CV: the posting goes straight to the analysis,
by the ``--server_ver`` server or else the default. The result is printed as
JSON, or written as a ``<Company>_<Title>`` folder holding ``jd.txt`` and
``analysis.json`` — the shape ``watch`` takes from its inbox, which is where
``--out`` points by default. Everything else it says goes to stderr, so
stdout is only ever the result.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ..api import CVError
from ..config import Config
from ..jdanalysis import JDAnalysis
from ..joblog import JobLog
from ..stages.jobfolder import write_analysis
from ..ui import fail
from ..workspace import JD_FILENAME, staged_job, unstage
from . import connect

#: What ``--output-format`` accepts.
OUTPUT_FORMATS = ("json", "folder")


def run(config: Config, jd_file: Path, out: Path, *, output_format: str = "json") -> int:
    jd_file = Path(jd_file).expanduser()
    if not jd_file.is_file():
        fail(f"no such file: {jd_file}")

    [server] = config.targets
    calls = connect([server], verbose=config.verbose)[server.name]
    text = jd_file.read_bytes().decode("utf-8", errors="replace")
    job = {JD_FILENAME: text.encode("utf-8")}
    log = JobLog()
    try:
        reply = calls.make(lambda: calls.api.analyze(server.pack(), job, server.settings))
    except CVError as exc:
        calls.failed(log, exc)
        _report(log)
        return 1
    calls.keep_log(log, reply.items)
    if config.verbose:
        _report(log)

    if output_format == "json":
        print(json.dumps(reply.analysis, indent=2, ensure_ascii=False), flush=True)
        return 0
    analysis = JDAnalysis.model_validate(reply.analysis)
    staged = staged_job(Path(out).expanduser(), analysis.company_name, analysis.job_title)
    (staged / JD_FILENAME).write_text(text, encoding="utf-8")
    write_analysis(staged, analysis)
    print(f"✅ {unstage(staged)}", flush=True)
    return 0


def _report(log: JobLog) -> None:
    """What happened, on stderr."""
    if log.lines:
        print("\n".join(log.lines), file=sys.stderr, flush=True)
