"""``resumix analysis`` — one posting in, its analysis out. Nothing else.

No detection, no question, no CV: the posting goes straight to the analysis.
The result is printed as JSON, or written as a ``<Company>_<Title>`` folder
holding ``jd.txt`` and ``analysis.json`` — the shape ``watch`` takes from its
inbox, which is where ``--out`` points by default. Everything else it says
goes to stderr, so stdout is only ever the result.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..api import HttpApi, ResumixError, read_text
from ..config import Config
from ..joblog import JobLog
from ..stages.calls import Calls
from ..stages.jobfolder import write_analysis
from ..ui import fail
from ..workspace import JD_FILENAME, staged_job, unstage

#: What ``--output-format`` accepts.
OUTPUT_FORMATS = ("json", "folder")


def run(config: Config, jd_file: Path, out: Path, *, output_format: str = "json") -> int:
    jd_file = Path(jd_file).expanduser()
    if not jd_file.is_file():
        fail(f"no such file: {jd_file}")
    profile = config.require("candidate_profile.json").read_bytes()
    preferences = config.require("candidate_preferences.md").read_text(encoding="utf-8")
    prompt = read_text(config.path("sys_prompt_analysis.txt"))

    text = jd_file.read_bytes().decode("utf-8", errors="replace")
    calls = Calls(HttpApi(config.server_url, token=config.token, verbose=config.verbose))
    log = JobLog()
    try:
        analysis = calls.make(lambda: calls.api.analyze(
            text, profile=profile, preferences=preferences, prompt=prompt))
    except ResumixError as exc:
        calls.failed(log, exc)
        _report(log)
        return 1
    _report(log)

    if output_format == "json":
        print(analysis.model_dump_json(indent=2), flush=True)
        return 0
    staged = staged_job(Path(out).expanduser(), analysis.company_name, analysis.job_title)
    (staged / JD_FILENAME).write_text(text, encoding="utf-8")
    write_analysis(staged, analysis)
    print(f"✅ {unstage(staged)}", flush=True)
    return 0


def _report(log: JobLog) -> None:
    """What went wrong, on stderr."""
    if log.lines:
        print("\n".join(log.lines), file=sys.stderr, flush=True)
