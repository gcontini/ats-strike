"""``resumix`` — the command line.

Parses, builds the :class:`Config` (flags beat the environment beats
``resumix.toml`` beats what is in the current folder) and hands over to a
mode. It contains no logic of its own beyond that.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .config import COVER_LETTER_MODES, ConfigError, load_config
from .modes import analysis as analysis_mode
from .modes import clipboard as clipboard_mode
from .modes import render as render_mode
from .modes import reset as reset_mode
from .modes import submit as submit_mode
from .modes import submit_raw as submit_raw_mode
from .modes import watch as watch_mode
from .ui import fail, warn

EPILOGUE = """\
files:
  Put candidate_profile.json, candidate_data.json and candidate_preferences.md
  in the current folder and they are picked up by name. A prompt or
  resume.tex.jinja found there overrides the server's default; anything absent
  falls back to it. Every .png, .jpg and .jpeg in the folder is sent too, under
  its own file name — the name your template includes it under.

  --out defaults to the current folder, and watch's --in defaults to
  ./incoming (created if missing). analysis --out defaults to
  <data-dir>/incoming, where watch picks its folders up.

examples:
  resumix clipboard --out ~/applications
  resumix watch --in ~/Downloads/jds --out ~/applications --cover-letter yes
  resumix submit posting.txt --out ~/applications --yes
  resumix submit posting.txt analysis.json --out ~/applications
  resumix submit-raw posting.txt -o cv.pdf -o cv.json
  resumix analysis --in posting.txt --output-format folder
  resumix render ~/applications/cv/26-01-15/Acme_Head_of_IT/cv_jordan_rivera_head_of_it.json
  resumix reset

a CV is a session on the server, kept until its files are saved: running the
same posting again replays it instead of paying for a new one. --new writes
it again; reset drops every session the server keeps.
"""


def global_options() -> argparse.ArgumentParser:
    """Flags that work on either side of the mode name.

    ``SUPPRESS`` so an unset flag writes nothing: the subparser's copy must
    not overwrite a value given before the mode name.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", type=Path, default=argparse.SUPPRESS,
                        help="Path to resumix.toml.")
    common.add_argument("--data-dir", type=Path, default=argparse.SUPPRESS,
                        help="Folder to look in for your files (before the current folder).")
    common.add_argument("--server", default=argparse.SUPPRESS,
                        help="Server URL (default: http://localhost:8080).")
    common.add_argument("--token", default=argparse.SUPPRESS,
                        help="Bearer token, if the server requires one.")
    common.add_argument("--pages", type=int, default=argparse.SUPPRESS,
                        help="Page limit the CV must fit (default: 2).")
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS,
                        help="Print what the server reports about each step it finishes.")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = global_options()
    parser = argparse.ArgumentParser(
        prog="resumix",
        description="Turn job descriptions into tailored CVs, against a resumix server.",
        epilog=EPILOGUE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
    )

    modes = parser.add_subparsers(dest="mode", required=True, metavar="MODE")

    def add_new_flag(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--new", action="store_true",
                         help="Write the CV again even if the server still has a run "
                              "for the same posting (by default it is replayed).")

    def add_job_flags(sub: argparse.ArgumentParser, *,
                      yes_help: str = "Submit every valid posting without asking.") -> None:
        sub.add_argument("--out", type=Path,
                         help="Output folder (working/, error/, discarded/, cv/). "
                              "Default: the current folder.")
        sub.add_argument("--cover-letter", choices=COVER_LETTER_MODES,
                         help="Also write a cover letter (default: no).")
        sub.add_argument("--yes", action="store_true", help=yes_help)
        sub.add_argument("--no-xlsx", action="store_true",
                         help="Do not record delivered CVs in applications.xlsx.")
        add_new_flag(sub)

    clip = modes.add_parser("clipboard", help="Watch the clipboard for postings.",
                              parents=[common])
    add_job_flags(clip)

    watch = modes.add_parser("watch", help="Watch a folder for postings.",
                              parents=[common])
    watch.add_argument("--in", dest="inbox", type=Path,
                       help="Folder to watch for job description files. "
                            "Default: ./incoming (created if missing).")
    add_job_flags(watch, yes_help="Never ask: YES postings get a CV, NO postings are "
                                  "discarded, CHECK postings wait in working/ for a run "
                                  "without --yes.")

    submit = modes.add_parser("submit", help="Process one job description file.",
                              parents=[common])
    submit.add_argument("jd_file", type=Path, help="The job description to process.")
    submit.add_argument("analysis", nargs="?", type=Path,
                        help="Its analysis.json, if you have one: skip detection "
                             "and analysis and go straight to the CV.")
    add_job_flags(submit)

    raw = modes.add_parser(
        "submit-raw",
        help="Write one CV into the current folder. Nothing else.",
        parents=[common],
    )
    raw.add_argument("jd_file", type=Path, help="The job description to process.")
    raw.add_argument("-o", "--output", type=Path, action="append", default=[],
                     metavar="FILE",
                     help="Write one output here: .pdf, .json or .tex. Repeatable. "
                          "Without it, a PDF named after the job description.")
    add_new_flag(raw)

    analysis = modes.add_parser("analysis", help="Analyse one job description. Nothing else.",
                                parents=[common])
    analysis.add_argument("--in", dest="jd_file", type=Path, required=True,
                          help="The job description to analyse.")
    analysis.add_argument("--output-format", choices=analysis_mode.OUTPUT_FORMATS,
                          default="json",
                          help="json: print the analysis on stdout. folder: write "
                               "<Company>_<Title>/ with jd.txt and analysis.json into "
                               "--out, replacing one of the same name. Default: json.")
    analysis.add_argument("--out", type=Path,
                          help="Where --output-format folder writes. "
                               "Default: <data-dir>/incoming (created if missing).")

    render = modes.add_parser("render", help="Compile a cv_*.json or cv_*.tex into a PDF.",
                              parents=[common])
    render.add_argument("source", type=Path, help="The .json document or .tex source.")
    render.add_argument("--output", type=Path,
                        help="Where to write the PDF (default: beside the input).")

    modes.add_parser("reset", help="Delete every CV session the server still keeps.",
                     parents=[common])

    # No parents=[common]: it reads no configuration and takes no options.
    modes.add_parser("version", help="Print the version and exit.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    # Before load_config: which build you are holding is the one question a
    # broken resumix.toml must not stop you answering.
    if args.mode == "version":
        print(f"version {__version__}")
        return 0

    flag = lambda name: getattr(args, name, None)  # noqa: E731 — SUPPRESS defaults
    try:
        config = load_config(
            config_file=flag("config"),
            data_dir=flag("data_dir"),
            overrides={
                "server_url": flag("server"),
                "token": flag("token"),
                "pages": flag("pages"),
                "cover_letter": getattr(args, "cover_letter", None),
                "verbose": flag("verbose"),
            },
        )
    except (ConfigError, FileNotFoundError) as exc:
        fail(str(exc))

    if config.verbose:
        # stderr: analysis prints its JSON on stdout, and nothing else may.
        print(f"version {__version__}", file=sys.stderr)

    try:
        if args.mode == "reset":
            return reset_mode.run(config)
        if args.mode == "render":
            return render_mode.run(config, args.source, output=args.output)
        if args.mode == "submit-raw":
            return submit_raw_mode.run(config, args.jd_file, args.output, new=args.new)
        if args.mode == "analysis":
            out = args.out or (flag("data_dir") or Path.cwd()) / "incoming"
            return analysis_mode.run(config, args.jd_file, out,
                                     output_format=args.output_format)

        common = {"assume_yes": args.yes, "track": not args.no_xlsx, "new": args.new}
        out = args.out or Path.cwd()
        if args.mode == "clipboard":
            return clipboard_mode.run(config, out, **common)
        if args.mode == "watch":
            return watch_mode.run(config, args.inbox, out, **common)
        return submit_mode.run(config, args.jd_file, out, analysis=args.analysis, **common)
    except ConfigError as exc:
        fail(str(exc))
    except KeyboardInterrupt:
        # watch/clipboard already print their own message and return before
        # this is ever reached; this is the net for submit/submit-raw, which
        # have no loop of their own to catch it in.
        warn("stopped")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
