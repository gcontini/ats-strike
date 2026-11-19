"""Dev runner of S3 and S4: a profile's pool and its review, until the store travels as cache items (S10).

    uv run python scripts/v2_pool.py <server folder> --out <dir> [--experiences 1 3] [--profile]
    uv run python scripts/v2_pool.py <server folder> --out <dir> md
    uv run python scripts/v2_pool.py <server folder> --out <dir> review

Reads ``candidate_profile.json`` from the server folder, ``<out>/items.json`` and ``<out>/tree.json``,
and ``<out>/pool.json`` as the previous pool when there is one: the blocks of experiences whose
items and stories are unchanged are copied, hand edits included, and so are the skill phrases and
frames.
``--experiences`` writes those experience ids again even when unchanged; ``--profile`` writes the
skill phrases and frames again. Writes ``<out>/pool.json`` and ``<out>/pool.md``. ``md`` makes no
model call: it checks a hand-edited ``pool.json`` and rebuilds ``pool.md``. ``review`` reviews
``pool.json`` against its items on the ``validation`` role, edits it in place, rebuilds ``pool.md``
and writes ``<out>/review.md``: every change, before and after, and every refused replacement.

The ``compile`` and ``validation`` roles are v2's ``models.toml``; their providers' keys and URLs come from ``.env``. Only
the pipelines' own log lines are shown, never the HTTP client's, which name the endpoint.
For the real profile, ``--out`` must be a git-ignored folder (``out/v2-store``).
Deleted in S10, with the other ``v2_*.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

from common.models import build_selector
from pipeline_v2 import (
    ROLES,
    Items,
    Pool,
    PoolReviewer,
    PoolWriter,
    Tree,
    pool_checks,
    pool_dump,
    pool_markdown,
    resources_dir,
    review_markdown,
    validate_pool,
)
from pipeline_v2.pool_store import ladder_and_group_bullets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path, help="the server folder holding candidate_profile.json")
    parser.add_argument("--out", type=Path, required=True, help="holds items.json and tree.json; pool.json goes here")
    parser.add_argument(
        "--experiences",
        type=int,
        nargs="+",
        default=[],
        metavar="ID",
        help="experience ids to write again even when their items are unchanged",
    )
    parser.add_argument("--profile", action="store_true", help="write the skill phrases and frames again")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["md", "review"],
        help="md: check pool.json, rebuild pool.md; review: review pool.json, write review.md",
    )
    args = parser.parse_args()

    def load(name: str) -> str:
        return (args.out / name).read_text(encoding="utf-8")

    profile = json.loads((args.folder / "candidate_profile.json").read_text(encoding="utf-8"))
    items = Items.model_validate_json(load("items.json"))
    tree = Tree.model_validate_json(load("tree.json"))
    pool_path, page_path = args.out / "pool.json", args.out / "pool.md"
    previous = Pool.model_validate_json(load("pool.json")) if pool_path.exists() else None
    today = date.today()

    if args.command and previous is None:
        sys.exit(f"{pool_path} does not exist")
    if args.command != "md":
        for name in ("common", "pipeline_v2"):
            log = logging.getLogger(name)
            log.setLevel(logging.INFO)
            log.addHandler(logging.StreamHandler(sys.stdout))
        llm = build_selector(resources_dir() / "models.toml", ROLES)

    if args.command == "md":
        pool = previous
    elif args.command == "review":
        reviewer = PoolReviewer(
            llm=llm, prompt=(resources_dir() / "sys_prompt_pool_review.txt").read_text(encoding="utf-8")
        )
        started = time.monotonic()
        pool, calls = reviewer.review(previous, items, profile)
        u = reviewer.usage
        print(
            f"\n{u.calls} call(s), {time.monotonic() - started:.0f}s wall, {u.seconds:.0f}s in calls, "
            f"tokens {u.input_tokens} in, {u.thinking_tokens} thinking, {u.output_tokens} out"
        )
        pool_path.write_text(pool_dump(pool), encoding="utf-8")
        (args.out / "review.md").write_text(review_markdown(calls), encoding="utf-8")
    else:

        def prompt(name: str) -> str:
            return (resources_dir() / f"sys_prompt_pool_{name}.txt").read_text(encoding="utf-8")

        writer = PoolWriter(
            llm=llm,
            experience_prompt=prompt("experience"),
            ladder_prompt=prompt("ladder"),
            profile_prompt=prompt("profile"),
            rules=prompt("rules"),
        )
        started = time.monotonic()
        pool = writer.write(
            items, tree, profile, today=today, previous=previous, force=args.experiences, profile_level=args.profile
        )
        u = writer.usage
        print(
            f"\n{u.calls} call(s), {time.monotonic() - started:.0f}s wall, {u.seconds:.0f}s in calls, "
            f"tokens {u.input_tokens} in, {u.thinking_tokens} thinking, {u.output_tokens} out"
        )
        pool_path.write_text(pool_dump(pool), encoding="utf-8")
    page_path.write_text(pool_markdown(pool, items, tree, profile, today), encoding="utf-8")

    bullets = [b for block in pool.experiences for b in ladder_and_group_bullets(block)]
    rungs = Counter((r.rung, r.wording) for block in pool.experiences for s in block.stories for r in s.ladder)
    groups = [g for block in pool.experiences for s in block.stories for g in s.groups]
    print(
        f"\n{len(bullets)} bullets: rung 1 {rungs[1, 'a']}+{rungs[1, 'b']}, rung 2 {rungs[2, 'a']}+{rungs[2, 'b']}, "
        f"rung 3 {rungs[3, 'a']}+{rungs[3, 'b']} (a+b); {sum(len(g.bullets) for g in groups)} in {len(groups)} "
        f"groups; {len(pool.experiences)} role summaries, {len(pool.skills)} skill phrases, "
        f"{len(pool.frames)} frames; {sum(len(b.dropped) for b in pool.experiences)} dropped"
    )
    found = pool_checks(pool, items, tree, profile, today)
    print("checks: " + ", ".join(f"{k} {len(v)}" for k, v in found.items()))
    problems = validate_pool(pool, items, tree)
    print(f"{len(problems)} problem(s)")
    for p in problems:
        print(f"  - {p}")
    wrote = {"md": f"{page_path}", "review": f"{pool_path}, {page_path} and {args.out / 'review.md'}"}
    print("wrote " + wrote.get(args.command, f"{pool_path} and {page_path}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
