"""The facade (Serving): the store completed from the pack's ``cache/``, then a CV.

No key, no network: the fake HTTP client inside a real ``ModelSelector`` answers every role, with
the fake replies of the step tests (the compile by :func:`v2_helpers.cut`, the tree by
``test_tree``'s, the pool by ``test_pool``'s). The example candidate's pack, the shipped market.
"""

from __future__ import annotations

import json
import shutil

import pytest
from pipeline_helpers import EXAMPLE_CANDIDATE, FakeLLM, section, user_text
from test_pool import answer as pool_answer
from test_tree import answer as tree_answer
from v2_helpers import cut

from pipeline_v2 import ROLES, Pipeline, resources_dir

needs_latex = pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")

JD = b"Cloud Engineer. We need Kubernetes and Agile, and Terraform would help."


class Sink:
    def __init__(self) -> None:
        self.statuses: list = []
        self.outputs: list = []

    def status(self, status: str, detail: str = "") -> None:
        self.statuses.append(status)

    def store(self, output) -> None:
        self.outputs.append(output)

    def names(self, location: str) -> list:
        return [o.name for o in self.outputs if o.location == location]

    def cache(self) -> dict:
        """What the client keeps in the server folder's ``cache/``, as the pack sends it back."""
        return {f"cache/{o.name}": o.content.encode() for o in self.outputs if o.location == "CACHE"}


def pack() -> dict:
    return {p.name: p.read_bytes() for p in EXAMPLE_CANDIDATE.iterdir()}


def fake() -> FakeLLM:
    """Every role answered: the store's calls by the step tests' fakes, the job's with no edits."""
    llm = FakeLLM(roles=ROLES)

    def compile_reply(body: dict) -> str:
        text = user_text(body)
        if text.startswith("EXPERIENCE:\n"):
            return cut(body)
        if "MARKET TERMS" in text or text.startswith("TREE:\n"):
            return tree_answer(body)
        return pool_answer(body)

    requirements = [
        {"id": 1, "requirement": "Kubernetes", "keywords": ["Kubernetes"], "importance": 100, "node": "1.1.1"},
        {"id": 2, "requirement": "Agile", "keywords": ["Agile"], "importance": 80, "node": "2.1.1"},
    ]
    llm["compile"].replies = [compile_reply]
    llm["review"].replies = ["OK"]
    llm["allocate"].replies = [
        json.dumps({"title": "Cloud Engineer", "requirements": [{**r, "relation": "same"} for r in requirements]})
    ]
    llm["varnish"].replies = [
        json.dumps({"experiences": {}, "edits": [], "titles": {}}),
        json.dumps({"job_title": "Cloud Engineer", "sentence": ""}),
        json.dumps({"replacements": []}),
    ]
    llm["highlight"].replies = [lambda body: json.dumps(section(body, "CV"))]
    return llm


def store_calls(llm: FakeLLM) -> int:
    return len(llm["compile"].calls) + len(llm["review"].calls)


@needs_latex
def test_a_full_cache_makes_no_store_call_and_writes_a_cv(tmp_path):
    built = Sink()
    Pipeline(llm=fake()).cv(pack(), {"job_description.txt": JD}, {}, tmp_path / "a", built)
    assert built.names("CACHE") == [
        "items.json",
        "items.md",
        "tree.json",
        "tree.md",
        "pool.json",
        "pool.md",
        "review.md",
    ]
    (tmp_path / "b").mkdir()
    llm, sink = fake(), Sink()
    summary = Pipeline(llm=llm).cv({**pack(), **built.cache()}, {"job_description.txt": JD}, {}, tmp_path / "b", sink)
    assert store_calls(llm) == 0
    system = next(m["content"] for m in llm["highlight"].calls[0]["messages"] if m["role"] == "system")
    assert (resources_dir() / "sys_prompt_highlight.txt").read_text(encoding="utf-8").strip() in system
    assert sink.names("CACHE") == []
    assert sink.statuses == ["Job reading", "Content choice", "Polish", "Highlight", "Page layout"]
    steps = [f"steps/{n}.{x}" for n in ("allocation", "plan") for x in ("json", "md")]
    assert sink.names("CV") == [*steps, "steps/varnish.json", "steps/varnish.md", "cv.json", "cv.tex", "cv.pdf"]
    pdf = next(o.content for o in sink.outputs if o.name == "cv.pdf")
    assert pdf.startswith(b"%PDF") and summary.startswith("done, ") and summary.endswith("2 page(s)")
    document = json.loads(next(o.content for o in sink.outputs if o.name == "cv.json"))
    assert document["name"] == json.loads((EXAMPLE_CANDIDATE / "candidate_data.json").read_text())["name"]


@needs_latex
def test_render_compiles_the_jobs_cv_json_with_the_v2_template(tmp_path):
    built = Sink()
    Pipeline(llm=fake()).cv(pack(), {"job_description.txt": JD}, {}, tmp_path / "a", built)
    document = next(o.content for o in built.outputs if o.name == "cv.json").encode()
    (tmp_path / "b").mkdir()
    sink = Sink()
    Pipeline(llm=fake()).render(pack(), {"cv.json": document, "cv.tex": b"ignored"}, tmp_path / "b", sink)
    assert [o.name for o in sink.outputs] == ["cv.tex", "cv.pdf"]


@needs_latex
def test_a_pack_with_no_signature_image_compiles_with_the_blank_one(tmp_path):
    bare = {name: blob for name, blob in pack().items() if not name.endswith(".png")}
    sink = Sink()
    Pipeline(llm=fake()).cv(bare, {"job_description.txt": JD}, {}, tmp_path, sink)
    assert next(o.content for o in sink.outputs if o.name == "cv.pdf").startswith(b"%PDF")
