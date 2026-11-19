"""Dev runner of S6: the selection for one job from the store and its allocation, until a CV is served (S10).

    uv run python scripts/v2_select.py <store dir> <allocation.json> --profile <candidate_profile.json> --out <dir>

Reads ``items.json``, ``tree.json`` and ``pool.json`` from the store dir, the allocation, and the
profile's ``job_title`` list for the headline and its dates (older months weigh less in the
narrative). No model call. Writes ``<out>/plan.json`` and
``<out>/plan.md``: the chosen slots and their texts, the lines used of the budget, and the scores.
Prints the greedy's and the refill pass's moves.
For a real job, ``--out`` must be a git-ignored folder (``out/...``).
Deleted in S10, with the other ``v2_*.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date
from pathlib import Path

from pipeline_v2 import Allocation, Items, Pool, Tree
from pipeline_v2.select import context, plan_markdown, recency, select


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("store", type=Path, help="holds items.json, tree.json and pool.json")
    parser.add_argument("allocation", type=Path, help="the job's allocation.json (S5)")
    parser.add_argument(
        "--profile", type=Path, required=True, help="candidate_profile.json, for its job titles and dates"
    )
    parser.add_argument("--out", type=Path, required=True, help="plan.json and plan.md go here")
    args = parser.parse_args()

    def load(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    items = Items.model_validate_json(load(args.store / "items.json"))
    tree = Tree.model_validate_json(load(args.store / "tree.json"))
    pool = Pool.model_validate_json(load(args.store / "pool.json"))
    allocation = Allocation.model_validate_json(load(args.allocation))
    profile = json.loads(load(args.profile))

    started = time.monotonic()
    ctx = context(items, tree, pool, allocation, profile["job_title"], recency(profile["experiences"], date.today()))
    plan = select(ctx, print)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "plan.json").write_text(plan.model_dump_json(indent=1) + "\n", encoding="utf-8")
    (args.out / "plan.md").write_text(plan_markdown(plan, pool), encoding="utf-8")
    sc = plan.scores
    forms = " ".join(f"e{x.experience}:{x.form[0]}{len(x.slots) or ''}" for x in plan.experiences)
    print(
        f"{time.monotonic() - started:.1f}s  {forms}  lines {plan.lines}/{plan.budget_lines}  "
        f"ATS {sc.ats:.3f} (literal {sc.literal:.3f}, tree {sc.tree:.3f})  narrative {sc.narrative:.3f}  "
        f"objective {sc.objective:.4f} (refill {sc.objective - plan.greedy_objective:+.4f})"
    )
    print(f"wrote {args.out / 'plan.json'} and {args.out / 'plan.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
