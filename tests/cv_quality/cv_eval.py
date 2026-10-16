"""CV quality evaluation: the exact half of the /cv-eval skill.

    uv run python tests/cv_quality/cv_eval.py generate quick|thorough [--temperature T]
    uv run python tests/cv_quality/cv_eval.py score tests/cv_quality/runs/<run>

``generate`` starts this checkout's own server, uncommitted changes included,
and writes one CV per job description in jds/ through the resumix client, the
same way ``resumix submit-raw`` does, into a new folder under runs/. The cv-judge agent (.claude/agents/cv-judge.md) then reads each CV and
writes <stem>.judge.json next to it. ``score`` turns those judgements into
three numbers per CV and appends their averages to results.csv.

Not a pytest test: it needs a model, pdflatex and minutes per CV. The pure
functions it scores with are tested in test_cv_eval.py.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import httpx
from resumix_client.api import HttpApi, ResumixError
from resumix_client.config import load_config
from resumix_client.cvjob import write_cv

HERE = Path(__file__).resolve().parent
JDS = HERE / "jds"
RUNS = HERE / "runs"
RESULTS = HERE / "results.csv"
REPO = HERE.parents[1]
DATA_DIR = REPO / "resumix-data" / "gab"

#: Files 01-10 in jds/ are the quick set.
QUICK = 10
#: CV jobs in flight at once. The server has 10 job slots.
PARALLEL = 4
#: Seconds to wait for one CV: above the server's own 1200 s budget, so a job
#: that runs out of time fails with the server's cause, not with ours.
POLL_TIMEOUT = 1500
#: Seconds the local server may take to answer /healthz.
SERVER_START = 120
#: Weight of an ai_tells.md pattern, by the first letter of its id.
WEIGHTS = {"S": 1.0, "M": 0.5, "W": 0.25}
#: Weight of a requirement in the appeal score.
IMPORTANCE = {"must": 2, "nice": 1}
HEADER = ["version", "date", "set", "temperature", "judge", "appeal", "ai", "readability"]


def plain(text: str) -> str:
    """The highlight pass's **bold** and *italic* markers removed."""
    return text.replace("*", "")


def units(cv: dict) -> list[tuple[str, str]]:
    """(label, text) of every summary sentence, role summary and duty, in order."""
    found = [("summary", s) for s in re.split(r"(?<=[.!?])\s+", plain(cv["summary"]).strip()) if s]
    for e in cv["experiences"]:
        where = f"{plain(e['title'])} @ {plain(e['company'])}"
        found.append((f"{where}: role summary", plain(e["role_summary"])))
        found += [(f"{where}: duty", plain(d)) for d in e["duties"]]
    return found


def units_text(cv: dict) -> str:
    """What the judge reads: one numbered unit per line."""
    return "".join(f"U{i} [{label}] {text}\n" for i, (label, text) in enumerate(units(cv), 1))


def strings(value) -> list[str]:
    """Every string anywhere in a JSON value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


def keyword_hit(keywords: list[str], text: str) -> bool:
    """Any keyword in the text as a whole word, ignoring case."""
    return any(re.search(rf"(?<!\w){re.escape(k)}(?!\w)", text, re.IGNORECASE) for k in keywords)


def appeal(requirements: list[dict], experience: list[dict], cv: dict) -> float:
    """Half keyword coverage, half experience match, each weighted by importance."""
    text = plain(" ".join(strings(cv)))
    weight = {r["id"]: IMPORTANCE[r["importance"]] for r in requirements}
    match = {e["id"]: e["match"] for e in experience}
    total = sum(weight.values())
    hits = sum(weight[r["id"]] for r in requirements if keyword_hit(r["keywords"], text))
    shown = sum(w * match.get(i, 0) for i, w in weight.items())
    return 50 * (hits / total + shown / (2 * total))


def verified(found: list[dict], unit_list: list[tuple[str, str]]) -> list[dict]:
    """The findings whose quote is in one of the units they cite."""
    norm = lambda s: " ".join(plain(s).lower().split())  # noqa: E731
    kept = []
    for f in found:
        cited = [u for u in f["units"] if 1 <= u <= len(unit_list)]
        if any(norm(f["quote"]) in norm(unit_list[u - 1][1]) for u in cited):
            kept.append({**f, "units": cited})
    return kept


def ai_score(tells: list[dict], unit_list: list[tuple[str, str]]) -> float:
    """Share of units carrying AI tells: a strong tell fills a unit, weak ones need company."""
    load = [0.0] * len(unit_list)
    for t in verified(tells, unit_list):
        for u in t["units"]:
            load[u - 1] += WEIGHTS[t["pattern"][0]]
    return 100 * sum(min(1.0, x) for x in load) / len(unit_list)


def readability(flags: list[dict], unit_list: list[tuple[str, str]]) -> float:
    """Share of units that read without stumbling."""
    hard = {u for f in verified(flags, unit_list) for u in f["units"]}
    return 100 * (1 - len(hard) / len(unit_list))


def append_result(path: Path, row: list) -> None:
    """One row per run, after a header the first time."""
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        if new:
            out.writerow(HEADER)
        out.writerow(row)


@contextmanager
def local_server(log: Path):
    """This checkout's resumix-api on a free port, once it answers; yields its URL."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    # Empty values keep .env from setting them: no auth on a port only this
    # machine can reach, and the prompts of the checkout, not a mounted copy.
    env = {**os.environ, "HOST": "127.0.0.1", "PORT": str(port),
           "RESUMIX_API_TOKEN": "", "RESUMIX_RESOURCES": ""}
    with log.open("w", encoding="utf-8") as out:
        server = subprocess.Popen([sys.executable, "-m", "resumix_server.server"], cwd=REPO, env=env,
                                  stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + SERVER_START
            while True:
                if server.poll() is not None or time.monotonic() > deadline:
                    raise SystemExit(f"the local server did not start; see {log}")
                try:
                    httpx.get(url + "/healthz", timeout=5)
                    break
                except httpx.TransportError:
                    time.sleep(0.5)
            yield url
        finally:
            server.kill()
            server.wait()


def generate(set_name: str, temperature: float | None) -> Path:
    """One CV per job description of the set, written into a new run folder."""
    config = load_config(config_file=DATA_DIR / "resumix.toml", data_dir=DATA_DIR,
                         overrides={"temperature": temperature})
    jds = sorted(JDS.glob("*.txt"))[: QUICK if set_name == "quick" else None]
    now = datetime.now()
    run = RUNS / f"{now:%Y-%m-%d_%H%M}-{set_name}"
    run.mkdir(parents=True)
    with local_server(run / "server.log") as url:
        config = replace(config, server_url=url, token=None, timeout=POLL_TIMEOUT, verbose=False)
        health = httpx.get(url + "/healthz", timeout=60).json()["data"]
        (run / "run.json").write_text(json.dumps({
            "version": health["version"], "models": health["models"], "server": url,
            "date": f"{now:%Y-%m-%d}", "set": set_name, "temperature": config.temperature,
            "jds": [jd.stem for jd in jds],
        }, indent=2), encoding="utf-8")
        print(f"resumix {health['version']} at {url}, {len(jds)} CVs into {run}", flush=True)
        api = HttpApi(url)

        def one(jd: Path) -> None:
            say = lambda message: print(f"{jd.stem[:2]} {message.strip()}", flush=True)  # noqa: E731
            try:
                request_id, rendered = write_cv(api, config, jd.read_text(encoding="utf-8"), say=say)
            except ResumixError as exc:
                (run / f"{jd.stem}.error.txt").write_text(
                    f"{exc} (request {exc.request_id})\n", encoding="utf-8")
                say(f"FAILED: {exc}")
                return
            (run / f"{jd.stem}.cv.json").write_text(
                json.dumps(rendered.document, indent=2, ensure_ascii=False), encoding="utf-8")
            (run / f"{jd.stem}.pdf").write_bytes(rendered.pdf_bytes())
            (run / f"{jd.stem}.units.txt").write_text(units_text(rendered.document), encoding="utf-8")
            say(f"done (request {request_id})")

        with ThreadPoolExecutor(PARALLEL) as pool:
            list(pool.map(one, jds))
    print(run, flush=True)
    return run


def score(run: Path) -> None:
    """The three numbers per judged CV, their averages, and one more row in results.csv."""
    meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
    rows, judges = [], set()
    for stem in meta["jds"]:
        cv_file, judge_file = run / f"{stem}.cv.json", run / f"{stem}.judge.json"
        if not cv_file.exists() or not judge_file.exists():
            print(f"{stem}: skipped, no {'CV' if not cv_file.exists() else 'judgement'}")
            continue
        cv = json.loads(cv_file.read_text(encoding="utf-8"))
        judge = json.loads(judge_file.read_text(encoding="utf-8"))
        requirements = json.loads((JDS / f"{stem}.requirements.json").read_text(encoding="utf-8"))
        unit_list = units(cv)
        judges.add(judge["model"])
        rows.append((stem, appeal(requirements, judge["experience"], cv),
                     ai_score(judge["ai_tells"], unit_list),
                     readability(judge["hard_to_read"], unit_list)))

    print(f"{'CV':52} {'appeal':>7} {'ai':>7} {'read':>7}")
    for stem, *values in rows:
        print(f"{stem[:52]:52} " + " ".join(f"{v:7.1f}" for v in values))
    averages = [round(sum(r[i] for r in rows) / len(rows), 1) for i in (1, 2, 3)]
    print(f"{f'average, {len(rows)} of {len(meta['jds'])} CVs':52} "
          + " ".join(f"{v:7.1f}" for v in averages))
    print(f"judged by {', '.join(sorted(judges))}")
    temperature = "default" if meta["temperature"] is None else meta["temperature"]
    append_result(RESULTS, [meta["version"], meta["date"], meta["set"], temperature,
                            "+".join(sorted(judges)), *averages])
    print(f"appended to {RESULTS}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    steps = parser.add_subparsers(dest="step", required=True)
    gen = steps.add_parser("generate", help="Write the CVs of a set into a new run folder.")
    gen.add_argument("set", choices=("quick", "thorough"))
    gen.add_argument("--temperature", type=float,
                     help="CV model temperature. Default: resumix.toml, else the server's.")
    sc = steps.add_parser("score", help="Score a judged run and append its averages.")
    sc.add_argument("run", type=Path)
    args = parser.parse_args()
    if args.step == "generate":
        generate(args.set, args.temperature)
    else:
        score(args.run)


if __name__ == "__main__":
    main()
