"""Dev runner of S2: the market list the competence tree is named by, with no model call.

    uv run python scripts/v2_market.py <data folder> --out <dir> --hold-out <file> --exclude-jds <dir>
        [--items <items.json> --profile <candidate_profile.json>]

Reads ``job_description.txt`` and ``analysis.json`` of every run under the data folder
(``discarded/*/*``, ``CV/*/*``, ``incoming/*``) and ``CV/*/*/steps/key_points.json``. Dedups the job
descriptions by their first 1,500 characters, drops the copies of the CV test set's job
descriptions (``--exclude-jds``, 8-word shingle Jaccard >= 0.5) and of the held-out runs
(``--hold-out``: one run path per line, relative to the data folder), and writes
``<out>/market.json``.

- Requirements and keywords: the key points of the v1 runs that are not held out (build runs).
- Corpus terms: the ``hard_skills`` of every corpus analysis with ``match_percentage`` >= 40,
  compounds split; plus the items' tech (``--items``) and the skills list (``--profile``), each
  skill whole and its parts that stand alone (below).
- Kept: terms asked by 2+ build runs, or in the profile, or in 3+ of those analyses and standing
  alone somewhere (a whole hard skill of an analysis, or a build run's keyword): a part split off
  "Business and IT alignment" is not a term. The shape call's input stays under about 30k tokens:
  the rarest corpus terms are cut first.

Everything it writes is derived from the data folder: ``--out`` must be a git-ignored folder.
Deleted in S10, with ``v2_items.py`` and ``v2_tree.py``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from pipeline_v2 import Items, Market, resources_dir
from pipeline_v2.terms import has_term, jaccard, norm, shingles, split_compound
from pipeline_v2.tree import shape_input
from pipeline_v2.tree_store import MarketRequirement, MarketTerm

#: Requirements v1 added to every job; they are not the job's.
META = {"success stories", "concrete examples"}
COPY = 0.5  # shingle Jaccard of a copy
MATCH = 40  # analyses at or above this match_percentage give corpus terms
MIN_JOBS = 2  # build runs
MIN_ANALYSES = 3  # corpus analyses at or above MATCH
TOKENS = 30_000  # the shape call's input, at most about
CHARS_PER_TOKEN = 3.3  # S2's example shape input: 87,578 characters, 26,426 tokens


def squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(read(path))
    except (OSError, ValueError):
        return None


def corpus(data: Path) -> Tuple[List[Tuple[Path, str]], int]:
    """Unique job descriptions (the first run that holds each), and the count of duplicates."""
    seen: Dict[str, Tuple[Path, str]] = {}
    duplicates = 0
    for pattern in ("discarded/*/*", "CV/*/*", "incoming/*"):
        for run in sorted(data.glob(pattern)):
            jd = run / "job_description.txt"
            if not jd.is_file():
                continue
            text = read(jd)
            key = hashlib.md5(squash(text.lower())[:1500].encode()).hexdigest()
            if key in seen:
                duplicates += 1
            else:
                seen[key] = (run, text)
    return list(seen.values()), duplicates


def is_copy(text: str, of: List[frozenset]) -> bool:
    mine = shingles(text)
    return any(jaccard(mine, other) >= COPY for other in of)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("data", type=Path, help="the data folder (CV/, discarded/, incoming/)")
    parser.add_argument("--out", type=Path, required=True, help="where market.json goes")
    parser.add_argument("--hold-out", type=Path, required=True, help="held-out run paths, one per line")
    parser.add_argument("--exclude-jds", type=Path, required=True, help="the CV test set's .txt job descriptions")
    parser.add_argument("--items", type=Path, help="items.json: the items' tech joins the terms")
    parser.add_argument("--profile", type=Path, help="candidate_profile.json: the skills list joins the terms")
    args = parser.parse_args()

    held_runs = [args.data / line.strip() for line in read(args.hold_out).splitlines() if line.strip()]
    held = [shingles(read(run / "job_description.txt")) for run in held_runs]
    tests = [shingles(read(p)) for p in sorted(args.exclude_jds.glob("*.txt"))]

    unique, duplicates = corpus(args.data)
    jds: List[Tuple[Path, str]] = []
    excluded = {"test": 0, "held_out": 0, "duplicates": duplicates}
    for run, text in unique:
        if is_copy(text, tests):
            excluded["test"] += 1
        elif is_copy(text, held):
            excluded["held_out"] += 1
        else:
            jds.append((run, text))

    # --- the build runs' requirements and keywords
    held_set = {run.resolve() for run in held_runs}
    builds, skipped_test = [], 0
    for steps in sorted(args.data.glob("CV/*/*/steps/key_points.json")):
        run = steps.parents[1]
        if run.resolve() in held_set:
            continue
        if is_copy(read(run / "job_description.txt"), tests):
            skipped_test += 1
            continue
        builds.append(run)
    spelling: Dict[str, Counter] = defaultdict(Counter)
    keyword_runs: Dict[str, set] = defaultdict(set)
    requirements: List[MarketRequirement] = []
    for run in builds:
        for kp in read_json(run / "steps" / "key_points.json") or []:
            if kp["requirement"].strip().lower() in META:
                continue
            requirements.append(
                MarketRequirement(name=kp["requirement"], importance=kp["importance"], keywords=kp["keywords"])
            )
            for word in kp["keywords"]:
                keyword_runs[norm(word)].add(run)
                spelling[norm(word)][word.strip()] += 1

    # --- the corpus analyses' hard skills
    analysed = 0
    asked_all: Counter = Counter()
    asked_near: Counter = Counter()
    whole: set = set()  # hard skills as the analyses write them, unsplit
    for run, _ in jds:
        analysis = read_json(run / "analysis.json")
        if not analysis:
            continue
        analysed += 1
        mine = set()
        for phrase in analysis.get("hard_skills") or []:
            whole.add(norm(phrase))
            for part in split_compound(phrase):
                mine.add(norm(part))
                spelling[norm(part)][part] += 1
        asked_all.update(mine)
        if (analysis.get("match_percentage") or 0) >= MATCH:
            asked_near.update(mine)

    # --- the profile's terms
    items = Items.model_validate_json(read(args.items)) if args.items else None
    profile = read_json(args.profile) if args.profile else {}
    skills = list(profile.get("skills") or [])
    certifications = [c["name"] for c in profile.get("certifications") or []]
    data = read_json(args.profile.parent / "candidate_data.json") if args.profile else None
    languages = (data or {}).get("languages", "")
    own = set()
    parts = [p for s in skills for p in split_compound(s) if norm(p) in whole or norm(p) in keyword_runs]
    for t in [t for i in (items.items if items else []) for t in i.tech] + skills + parts:
        own.add(norm(t))
        spelling[norm(t)][t.strip()] += 1

    def alone(k: str) -> bool:
        return k in whole or k in keyword_runs

    kept = {
        k
        for k in spelling
        if k and (len(keyword_runs.get(k, ())) >= MIN_JOBS or k in own or (asked_near[k] >= MIN_ANALYSES and alone(k)))
    }
    fragments = sum(
        1
        for k in spelling
        if asked_near[k] >= MIN_ANALYSES and not alone(k) and len(keyword_runs.get(k, ())) < MIN_JOBS and k not in own
    )
    texts = [(squash(text), squash(text).lower()) for _, text in jds]
    terms = []
    for k in kept:
        word = squash(spelling[k].most_common(1)[0][0])
        hits = sum(1 for text, low in texts if k in low and has_term(word, text))
        terms.append(
            MarketTerm(
                term=word,
                jobs=len(keyword_runs.get(k, ())),
                asked=round(asked_all[k] / analysed, 4) if analysed else 0.0,
                share=round(hits / len(texts), 4) if texts else 0.0,
            )
        )
    terms.sort(key=lambda t: (-t.share, -t.jobs, -t.asked, t.term.lower()))

    # --- the cut: the rarest corpus-only terms go first
    def market(with_terms: List[MarketTerm]) -> Market:
        return Market(
            build_jobs=len(builds), corpus_jobs=len(jds), excluded=excluded, terms=with_terms, requirements=requirements
        )

    prompt = (resources_dir() / "sys_prompt_tree_shape.txt").read_text(encoding="utf-8")
    empty = Items(profile_sha256="", experiences=[], items=[])

    def tokens(m: Market) -> int:
        text = shape_input(items or empty, m, skills=skills, certifications=certifications, languages=languages)
        return int((len(prompt) + len(text)) / CHARS_PER_TOKEN)

    protected = {norm(t.term) for t in terms if t.jobs >= MIN_JOBS or norm(t.term) in own}
    cuttable = sorted(
        (t for t in terms if norm(t.term) not in protected), key=lambda t: (asked_near[norm(t.term)], t.asked, t.share)
    )
    cut: List[MarketTerm] = []
    while tokens(market(terms)) > TOKENS and cuttable:
        drop = cuttable[: max(1, len(cuttable) // 20)]
        cuttable = cuttable[len(drop) :]
        cut += drop
        gone = {id(t) for t in drop}
        terms = [t for t in terms if id(t) not in gone]
    result = market(terms)

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "market.json").write_text(result.model_dump_json(indent=1) + "\n", encoding="utf-8")

    print(
        f"corpus {len(jds)} unique JDs ({analysed} with an analysis); excluded: {excluded['test']} test copies, "
        f"{excluded['held_out']} held-out copies, {excluded['duplicates']} duplicates"
    )
    print(
        f"build runs {len(builds)} ({len(held_runs)} held out, {skipped_test} test copies skipped), "
        f"{len(requirements)} requirements, {len(keyword_runs)} distinct keywords"
    )
    print(
        f"terms: {len(spelling)} seen, {len(kept)} kept by the rules "
        f"({sum(len(keyword_runs.get(k, ())) >= MIN_JOBS for k in kept)} asked by 2+ build runs, "
        f"{sum(asked_near[k] >= MIN_ANALYSES for k in kept)} in 3+ analyses, {len(own & kept)} from the profile; "
        f"{fragments} split parts in 3+ analyses that never stand alone left out), "
        f"{len(cut)} cut for the token budget, {len(terms)} in the list"
    )
    if cut:
        print("  cut, rarest first: " + ", ".join(t.term for t in cut[:15]) + (" ..." if len(cut) > 15 else ""))
    print(f"shape input about {tokens(result):,} tokens (budget {TOKENS:,})")
    print("top 25:")
    for t in terms[:25]:
        print(f"  {t.share:6.1%} of JDs | asked {t.asked:5.1%} | {t.jobs:2d} jobs | {t.term}")
    print(f"wrote {args.out / 'market.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
