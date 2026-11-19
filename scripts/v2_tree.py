"""Dev runner of S2: a profile's competence tree, until the store travels as cache items (S10).

    uv run python scripts/v2_tree.py <server folder> --out <dir> [--reshape] [--experiences 1 3]
    uv run python scripts/v2_tree.py <server folder> --out <dir> md

Reads ``candidate_profile.json`` and ``candidate_data.json`` from the server folder (the skills
list, the certifications, the languages line), ``<out>/items.json`` and ``<out>/market.json``, and
``<out>/tree.json`` as the previous tree when there is one: its shape is kept, and the links of
experiences whose items are unchanged are copied, hand edits included. ``--reshape`` builds a new
tree, first moving the old file to ``tree.prev.json``. ``--experiences`` attaches those experience
ids again even when unchanged. Writes ``<out>/tree.json`` and ``<out>/tree.md``. ``md`` makes no
model call: it checks a hand-edited ``tree.json`` and rebuilds ``tree.md``.

The ``compile`` role is v2's ``models.toml``; its provider's key and URL come from ``.env``. Only
the pipelines' own log lines are shown, never the HTTP client's, which name the endpoint.
For the real profile, ``--out`` must be a git-ignored folder (``out/v2-store``).
Deleted in S10, with ``v2_items.py`` and ``v2_market.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from common.models import build_selector
from pipeline_v2 import (
    ROLES,
    Items,
    Market,
    Tree,
    TreeBuilder,
    leaf_status,
    resources_dir,
    tree_dump,
    tree_markdown,
    validate_tree,
)
from pipeline_v2.tree_store import checks, level


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path, help="the server folder holding candidate_profile.json")
    parser.add_argument("--out", type=Path, required=True, help="holds items.json and market.json; tree.json goes here")
    parser.add_argument("--reshape", action="store_true", help="build a new tree (new ids, every experience attached)")
    parser.add_argument(
        "--experiences",
        type=int,
        nargs="+",
        default=[],
        metavar="ID",
        help="experience ids to attach again even when their items are unchanged",
    )
    parser.add_argument("command", nargs="?", choices=["md"], help="md: check tree.json, rebuild tree.md")
    args = parser.parse_args()

    def load(name: str) -> str:
        return (args.out / name).read_text(encoding="utf-8")

    items = Items.model_validate_json(load("items.json"))
    market = Market.model_validate_json(load("market.json"))
    tree_path, page_path = args.out / "tree.json", args.out / "tree.md"
    previous = Tree.model_validate_json(load("tree.json")) if tree_path.exists() else None

    if args.command == "md":
        if previous is None:
            sys.exit(f"{tree_path} does not exist")
        tree = previous
    else:
        profile = json.loads((args.folder / "candidate_profile.json").read_text(encoding="utf-8"))
        data = json.loads((args.folder / "candidate_data.json").read_text(encoding="utf-8"))
        for name in ("common", "pipeline_v2"):
            log = logging.getLogger(name)
            log.setLevel(logging.INFO)
            log.addHandler(logging.StreamHandler(sys.stdout))
        llm = build_selector(resources_dir() / "models.toml", ROLES)
        builder = TreeBuilder(
            llm=llm,
            shape_prompt=(resources_dir() / "sys_prompt_tree_shape.txt").read_text(encoding="utf-8"),
            attach_prompt=(resources_dir() / "sys_prompt_tree_attach.txt").read_text(encoding="utf-8"),
        )
        started = time.monotonic()
        tree = builder.build(
            items,
            market,
            skills=profile.get("skills") or [],
            certifications=[c["name"] for c in profile.get("certifications") or []],
            languages=data.get("languages") or "",
            previous=previous,
            reshape=args.reshape,
            force=args.experiences,
        )
        u = builder.usage
        print(
            f"\n{u.calls} call(s), {time.monotonic() - started:.0f}s wall, {u.seconds:.0f}s in calls, "
            f"tokens {u.input_tokens} in, {u.thinking_tokens} thinking, {u.output_tokens} out"
        )
        if args.reshape and previous is not None:
            tree_path.replace(args.out / "tree.prev.json")
        tree_path.write_text(tree_dump(tree), encoding="utf-8")
    page_path.write_text(tree_markdown(tree, items, market), encoding="utf-8")

    status = leaf_status(tree, items)
    leaves = [n.id for n in tree.nodes if level(n.id) == 3]
    count = {s: sum(status[i] == s for i in leaves) for s in ("proven", "weak", "ghost")}
    print(
        f"\n{len(tree.nodes)} nodes: {sum(level(n.id) == 1 for n in tree.nodes)} disciplines, "
        f"{sum(level(n.id) == 2 for n in tree.nodes)} competences, {len(leaves)} leaves "
        f"({count['proven']} proven, {count['weak']} weak, {count['ghost']} ghost); "
        f"{sum(bool(n.outside) for n in tree.nodes)} outside"
    )
    print("checks: " + ", ".join(f"{k} {len(v)}" for k, v in checks(tree, items, market).items()))
    problems = validate_tree(tree, items)
    print(f"{len(problems)} problem(s)")
    for p in problems:
        print(f"  - {p}")
    print(f"wrote {page_path}" if args.command == "md" else f"wrote {tree_path} and {page_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
