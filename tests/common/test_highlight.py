"""What the highlighter keeps of a reply. The calls themselves are tested through v1's CV run."""

from __future__ import annotations

from common.highlight import keep_words


def test_keep_words_takes_markers_and_nothing_else():
    old = {"summary": "A Kubernetes engineer.", "duties": ["Ran Go.|1-80"]}
    marked = {"summary": "A **Kubernetes** engineer.", "duties": ["Ran *Go*.|1-80"]}
    assert keep_words(marked, old) == marked
    assert keep_words({"summary": "A K8s engineer.", "duties": ["Ran *Go*.|1-80"]}, old)["summary"] == (
        "A Kubernetes engineer."
    )
    assert keep_words({"summary": "A engineer.", "duties": ["Ran Go.|1-80|2-10"]}, old)["duties"] == ["Ran Go.|1-80"]
    assert keep_words({"summary": "x"}, old)["duties"] == old["duties"]
