"""Plain helpers shared by the pipeline 2.0 tests (fixtures live in conftest.py)."""

from __future__ import annotations

import json

from pipeline_helpers import section


def raw(n, duty, type_, source, story, **fields) -> dict:
    return {"n": n, "duty": duty, "type": type_, "text": f"text {n}", "source": source, "story": story,
            "agency": "" if type_ == "context" else "did", "tags": ["billing"], **fields}


def cut(body: dict) -> str:
    """The compile reply for the experience in the request: the role summary as experience context,
    each duty one action spanning all its text, two duties per story, every duty weighing 2."""
    exp = section(body, "EXPERIENCE")
    items = [raw(1, -1, "context", [exp["role_summary"]], 0)] if exp["role_summary"] else []
    for d, duty in enumerate(exp["duties"]):
        items.append({**raw(len(items) + 1, d, "action", [duty], 1 + d // 2), "text": duty})
    stories = sorted({i["story"] for i in items if i["story"]})
    return json.dumps({
        "items": items,
        "stories": [{"n": s, "title": f"Piece of work {s}",
                     "anchor": [next(i["n"] for i in items if i["story"] == s)]} for s in stories],
        "duties": [{"duty": d, "weight": 2, "why": "recurring"} for d in range(len(exp["duties"]))],
    })
