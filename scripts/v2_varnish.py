"""Dev runner of S8 and S9: one job end to end: allocate, select, varnish, highlight and render, until a CV is
served (S10).

    uv run python scripts/v2_varnish.py <store dir> <job folder> --profile <candidate_profile.json> --out <dir>

Reads ``items.json``, ``tree.json`` and ``pool.json`` from the store dir, ``job_description.txt``
from the job folder, the profile, and ``candidate_data.json`` and ``candidate_signature.png`` next
to it. One call on ``allocate``, three on ``varnish``, one on ``highlight``. Writes ``<out>/document.json``,
``cv.pdf`` and ``varnish.md`` (the picks, the headline, the sentence, the terms applied and refused, the bold
and italic phrases), and the ``allocation.json`` and ``plan.json`` they came from. Prints the cost of each
step.

The roles are v2's ``models.toml``; the providers' keys and URLs come from ``.env``. Only the
pipelines' own log lines are shown, never the HTTP client's, which name the endpoint.
For a real job, ``--out`` must be a git-ignored folder (``out/...``).
Deleted in S10, with the other ``v2_*.py``.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

from common.cv_renderer import CVRenderer
from common.highlight import Highlighter, unmarked
from common.models import Usage, build_selector
from pipeline_v2 import ROLES, Allocator, Items, Pool, Tree, Varnisher, resources_dir, varnish_markdown
from pipeline_v2.document import document
from pipeline_v2.highlight import inputs, marks
from pipeline_v2.render import render, room_left
from pipeline_v2.select import PITCH_PT, context, literal_score, recency, select
from pipeline_v2.varnish import cv_text


def cost(name: str, u: Usage, wall: float) -> str:
    return (
        f"{name}: {u.calls} call(s), {wall:.0f}s wall, {u.seconds:.0f}s in calls, "
        f"tokens {u.input_tokens} in, {u.thinking_tokens} thinking, {u.output_tokens} out"
    )


def marked_phrases(doc: dict) -> list:
    """The bold and italic phrases a document prints, each with where: skills, summary, eN role / bullet / compact."""
    texts = [("skills", t) for t in doc["skills"]] + [("summary", doc["summary"])]
    for n, e in enumerate(doc["experiences"]):
        texts += [(f"e{n} role", e["role_summary"]), *((f"e{n} bullet", d) for d in e["duties"])]
        texts += [(f"e{n} compact", e["compact"])]
    bold = [f"{b} ({where})" for where, t in texts for b in re.findall(r"\*\*([^*]+)\*\*", t)]
    italics = [f"{i} ({where})" for where, t in texts for i in re.findall(r"(?<!\*)\*([^*]+)\*(?!\*)", t)]
    return [
        "",
        "## Highlight",
        "",
        f"Bold ({len(bold)}): {'; '.join(bold) or '(none)'}",
        "",
        f"Italics ({len(italics)}): {'; '.join(italics) or '(none)'}",
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("store", type=Path, help="holds items.json, tree.json and pool.json")
    parser.add_argument("job", type=Path, help="the job folder holding job_description.txt")
    parser.add_argument(
        "--profile", type=Path, required=True, help="candidate_profile.json, with its data and signature"
    )
    parser.add_argument("--out", type=Path, required=True, help="document.json, cv.pdf and varnish.md go here")
    args = parser.parse_args()

    def load(path: Path) -> str:
        return path.read_text(encoding="utf-8")

    items = Items.model_validate_json(load(args.store / "items.json"))
    tree = Tree.model_validate_json(load(args.store / "tree.json"))
    pool = Pool.model_validate_json(load(args.store / "pool.json"))
    jd = load(args.job / "job_description.txt")
    profile = json.loads(load(args.profile))
    data = json.loads(load(args.profile.parent / "candidate_data.json"))
    signature = (args.profile.parent / "candidate_signature.png").read_bytes()
    for name in ("common", "pipeline_v2"):
        log = logging.getLogger(name)
        log.setLevel(logging.INFO)
        log.addHandler(logging.StreamHandler(sys.stdout))

    llm = build_selector(resources_dir() / "models.toml", ROLES)
    prompt = {
        n: load(resources_dir() / f"sys_prompt_{n}.txt")
        for n in ("allocate", "varnish_pick", "varnish_headline", "varnish_terms")
    }
    allocator = Allocator(llm=llm, prompt=prompt["allocate"])
    varnisher = Varnisher(
        llm=llm,
        pick_prompt=prompt["varnish_pick"],
        headline_prompt=prompt["varnish_headline"],
        terms_prompt=prompt["varnish_terms"],
    )
    highlighter = Highlighter(llm)
    highlighted = Usage()
    args.out.mkdir(parents=True, exist_ok=True)

    started = time.monotonic()
    allocation = allocator.allocate(jd, tree, items)
    allocated = time.monotonic()
    (args.out / "allocation.json").write_text(allocation.model_dump_json(indent=1) + "\n", encoding="utf-8")
    ctx = context(items, tree, pool, allocation, profile["job_title"], recency(profile["experiences"], date.today()))
    plan = select(ctx)
    selected = time.monotonic()
    (args.out / "plan.json").write_text(plan.model_dump_json(indent=1) + "\n", encoding="utf-8")
    varnish = varnisher.varnish(plan, profile, pool, items, allocation, data)
    varnished = time.monotonic()
    title = profile["job_title"][0]
    sent = inputs(document(plan, profile, pool, title, varnish), plan, varnish.picks, ctx, pool, items)
    found = marks(sent[1], highlighter.highlight(*sent, usage=highlighted))
    highlit = time.monotonic()

    with tempfile.TemporaryDirectory(dir=args.out) as work:
        renderer = CVRenderer(
            template_source=load(resources_dir() / "resume.tex.jinja"),
            assets={"candidate_signature.png": signature},
            work_dir=Path(work),
        )
        out = render(plan, ctx, profile, pool, data, renderer, varnish, found)
    (args.out / "cv.pdf").write_bytes(out.result.pdf)
    (args.out / "document.json").write_text(
        json.dumps(out.document, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    def literal(doc: dict) -> float:
        return literal_score(unmarked(cv_text(doc)), allocation.requirements)

    bare = {**document(plan, profile, pool, profile["job_title"][0]), **data}
    lines = [
        cost("allocate", allocator.usage, allocated - started),
        f"select: {selected - allocated:.1f}s",
        cost("varnish", varnisher.usage, varnished - selected),
        cost("highlight", highlighted, highlit - varnished),
        f"literal score (S5's keywords): {literal(bare):.3f} before the varnish, {literal(out.document):.3f} after",
        f"first render {out.first_pages} pages, fit steps {len(out.steps)}, final {out.result.pages} pages, "
        f"{room_left(out.result.pdf, data['location'], PITCH_PT)} lines left, "
        f"summary {len(out.document['summary'])} ch",
    ]
    (args.out / "varnish.md").write_text(
        varnish_markdown(varnish, plan)
        + "\n".join(marked_phrases(out.document))
        + "\n\n## Run\n\n"
        + "\n".join(f"- {line}" for line in lines)
        + "\n",
        encoding="utf-8",
    )
    for step in out.steps:
        print(f"fit: {step}")
    print("\n".join(lines))
    print(f"wrote {args.out / 'document.json'}, {args.out / 'cv.pdf'} and {args.out / 'varnish.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
