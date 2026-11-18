"""Dev runner of S1: a profile's base items and stories, until the store travels as cache items (S4).

    uv run python scripts/v2_items.py <server folder> --out <dir> [--experiences 1 3]
    uv run python scripts/v2_items.py <server folder> --out <dir> md

Reads ``candidate_profile.json`` from the server folder and ``<out>/items.json``, when there is
one, as the previous store: unchanged experiences are copied from it, hand edits included.
``--experiences`` compiles those experience ids again even when unchanged. Writes
``<out>/items.json`` and ``<out>/items.md``. ``md`` makes no model call: it checks a hand-edited
``items.json`` and rebuilds ``items.md``.

The ``compile`` role is v2's ``models.toml``; its provider's key and URL come from ``.env``. Only
the pipelines' own log lines are shown, never the HTTP client's, which name the endpoint.
For the real profile, ``--out`` must be a git-ignored folder (``out/v2-store``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path

from pipeline_v1.models import build_selector
from pipeline_v2 import ROLES, ItemCompiler, Items, items_markdown, resources_dir, validate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path, help="the server folder holding candidate_profile.json")
    parser.add_argument("--out", type=Path, required=True, help="where items.json and items.md go")
    parser.add_argument("--experiences", type=int, nargs="+", default=[], metavar="ID",
                        help="experience ids to compile again even when unchanged")
    parser.add_argument("command", nargs="?", choices=["md"], help="md: check items.json, rebuild items.md")
    args = parser.parse_args()

    profile = json.loads((args.folder / "candidate_profile.json").read_text(encoding="utf-8"))
    store_path, page_path = args.out / "items.json", args.out / "items.md"
    previous = Items.model_validate_json(store_path.read_text(encoding="utf-8")) if store_path.exists() else None

    if args.command == "md":
        if previous is None:
            sys.exit(f"{store_path} does not exist")
        store = previous
    else:
        for name in ("pipeline_v1", "pipeline_v2"):
            log = logging.getLogger(name)
            log.setLevel(logging.INFO)
            log.addHandler(logging.StreamHandler(sys.stdout))
        llm = build_selector(resources_dir(), ROLES)
        prompt = (resources_dir() / "sys_prompt_items.txt").read_text(encoding="utf-8")
        compiler = ItemCompiler(llm=llm, system_prompt=prompt)
        started = time.monotonic()
        store = compiler.compile(profile, today=date.today(), previous=previous, force=args.experiences)
        u = compiler.usage
        print(f"\n{u.calls} call(s), {time.monotonic() - started:.0f}s wall, {u.seconds:.0f}s in calls, "
              f"tokens {u.input_tokens} in, {u.thinking_tokens} thinking, {u.output_tokens} out")

    args.out.mkdir(parents=True, exist_ok=True)
    if args.command != "md":
        store_path.write_text(store.model_dump_json(indent=1) + "\n", encoding="utf-8")
    page_path.write_text(items_markdown(profile, store), encoding="utf-8")

    print()
    for x in store.experiences:
        n = sum(1 for i in store.items if i.experience == x.id)
        print(f"e{x.id}: {n} items, {len(x.context)} experience context, {len(x.stories)} stories, "
              f"{len(x.dropped)} dropped, {x.coverage:.0%} covered, {x.months} months")
    problems = validate(profile, store)
    print(f"\n{len(store.items)} items, {sum(len(x.stories) for x in store.experiences)} stories, "
          f"{sum(len(x.dropped) for x in store.experiences)} dropped; {len(problems)} problem(s)")
    for p in problems:
        print(f"  - {p}")
    print(f"wrote {page_path}" if args.command == "md" else f"wrote {store_path} and {page_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
