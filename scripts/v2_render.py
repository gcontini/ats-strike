"""Dev runner of S7: one job's plan rendered to a PDF of at most 2 pages, until a CV is served (S10).

    uv run python scripts/v2_render.py <store dir> <plan.json> --allocation <allocation.json> \\
        --profile <candidate_profile.json> --out <dir>

Reads ``items.json``, ``tree.json`` and ``pool.json`` from the store dir, the plan (S6), its
allocation (S5: a fit step weighs what it loses), the profile, and ``candidate_data.json`` and
``candidate_signature.png`` next to the profile. No model call. Writes ``<out>/cv.pdf`` and
``<out>/document.json``; prints the pages, the fit steps and the lines left.
For a real job, ``--out`` must be a git-ignored folder (``out/...``).
Deleted in S10, with the other ``v2_*.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from common.cv_renderer import CVRenderer
from pipeline_v2 import Allocation, Items, Plan, Pool, Tree, resources_dir
from pipeline_v2.render import render, room_left
from pipeline_v2.select import PITCH_PT, context, recency


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("store", type=Path, help="holds items.json, tree.json and pool.json")
    parser.add_argument("plan", type=Path, help="the job's plan.json (S6)")
    parser.add_argument("--allocation", type=Path, required=True, help="the job's allocation.json (S5)")
    parser.add_argument(
        "--profile", type=Path, required=True, help="candidate_profile.json, with its data and signature"
    )
    parser.add_argument("--out", type=Path, required=True, help="cv.pdf and document.json go here")
    args = parser.parse_args()

    def load(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    items = Items.model_validate_json(load(args.store / "items.json"))
    tree = Tree.model_validate_json(load(args.store / "tree.json"))
    pool = Pool.model_validate_json(load(args.store / "pool.json"))
    allocation = Allocation.model_validate_json(load(args.allocation))
    plan = Plan.model_validate_json(load(args.plan))
    profile = json.loads(load(args.profile))
    data = json.loads(load(args.profile.parent / "candidate_data.json"))
    signature = (args.profile.parent / "candidate_signature.png").read_bytes()
    ctx = context(items, tree, pool, allocation, profile["job_title"], recency(profile["experiences"], date.today()))

    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.out) as work:
        renderer = CVRenderer(
            template_source=load(resources_dir() / "resume.tex.jinja"),
            assets={"candidate_signature.png": signature},
            work_dir=Path(work),
        )
        out = render(plan, ctx, profile, pool, data, renderer)
    (args.out / "cv.pdf").write_bytes(out.result.pdf)
    (args.out / "document.json").write_text(
        json.dumps(out.document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    for step in out.steps:
        print(f"fit: {step}")
    shown = [x.experience for x in out.plan.experiences]
    ends = ctx.experiences[0] in shown and ctx.experiences[-1] in shown
    forms = " ".join(f"e{x.experience}:{x.form[0]}{len(x.slots) or ''}" for x in out.plan.experiences)
    print(
        f"first render {out.first_pages} pages, fit steps {len(out.steps)}, final {out.result.pages} pages, "
        f"{room_left(out.result.pdf, data['location'], PITCH_PT)} lines left  {forms}  "
        f"summary {len(out.document['summary'])} ch  earliest and latest shown: {ends}"
    )
    print(f"wrote {args.out / 'cv.pdf'} and {args.out / 'document.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
