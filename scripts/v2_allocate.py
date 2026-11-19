"""Dev runner of S5: one job description's requirements placed on the competence tree, until a CV is served (S10).

    uv run python scripts/v2_allocate.py <store dir> <job folder> --out <dir>

Reads ``items.json`` and ``tree.json`` from the store dir and ``job_description.txt`` from the job
folder; one call on the ``allocate`` role. Writes ``<out>/allocation.json`` and
``<out>/allocation.md``: each requirement's node, relation and landing, and the top nodes by points.

The ``allocate`` role is v2's ``models.toml``; its provider's key and URL come from ``.env``. Only
the pipelines' own log lines are shown, never the HTTP client's, which name the endpoint.
For a real job, ``--out`` must be a git-ignored folder (``out/...``).
Deleted in S10, with the other ``v2_*.py``.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections import Counter
from pathlib import Path

from common.models import build_selector
from pipeline_v2 import ROLES, Allocator, Items, Tree, allocation_markdown, resources_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("store", type=Path, help="holds items.json and tree.json")
    parser.add_argument("job", type=Path, help="the job folder holding job_description.txt")
    parser.add_argument("--out", type=Path, required=True, help="allocation.json and allocation.md go here")
    args = parser.parse_args()

    items = Items.model_validate_json((args.store / "items.json").read_text(encoding="utf-8"))
    tree = Tree.model_validate_json((args.store / "tree.json").read_text(encoding="utf-8"))
    jd = (args.job / "job_description.txt").read_text(encoding="utf-8")
    for name in ("common", "pipeline_v2"):
        log = logging.getLogger(name)
        log.setLevel(logging.INFO)
        log.addHandler(logging.StreamHandler(sys.stdout))

    allocator = Allocator(
        llm=build_selector(resources_dir() / "models.toml", ROLES),
        prompt=(resources_dir() / "sys_prompt_allocate.txt").read_text(encoding="utf-8"),
    )
    started = time.monotonic()
    allocation = allocator.allocate(jd, tree, items)
    u = allocator.usage
    print(
        f"{u.calls} call(s), {time.monotonic() - started:.0f}s wall, {u.seconds:.0f}s in calls, "
        f"tokens {u.input_tokens} in, {u.thinking_tokens} thinking, {u.output_tokens} out"
    )
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "allocation.json").write_text(allocation.model_dump_json(indent=1) + "\n", encoding="utf-8")
    (args.out / "allocation.md").write_text(allocation_markdown(allocation, tree), encoding="utf-8")
    landing = Counter(r.landing for r in allocation.requirements)
    print(
        f"{len(allocation.requirements)} requirements: "
        + ", ".join(f"{k} {landing[k]}" for k in ("proven", "weak", "ghost", "nowhere"))
    )
    print(f"wrote {args.out / 'allocation.json'} and {args.out / 'allocation.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
