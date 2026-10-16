"""The scoring half of cv_eval.py: no server, no model, no network."""

from __future__ import annotations

import csv

import cv_eval

CV = {
    "job_title": "Cloud Engineer",
    "summary": "Cloud engineer with **10+ years**. I build *Kubernetes* platforms.",
    "skills": ["Terraform", "GCP"],
    "experiences": [{
        "title": "Engineer", "company": "Acme", "location": "Milan, Italy", "dates": "2020 -- Present",
        "role_summary": "Platform team of four.",
        "duties": ["To cut costs, I moved CI to spot nodes.", "Spearheaded the cloud journey."],
    }],
    "certifications": [{"date": "2023", "name": "CKA"}],
}


def test_units_split_the_summary_and_drop_the_highlight_markers():
    assert cv_eval.units(CV) == [
        ("summary", "Cloud engineer with 10+ years."),
        ("summary", "I build Kubernetes platforms."),
        ("Engineer @ Acme: role summary", "Platform team of four."),
        ("Engineer @ Acme: duty", "To cut costs, I moved CI to spot nodes."),
        ("Engineer @ Acme: duty", "Spearheaded the cloud journey."),
    ]
    assert cv_eval.units_text(CV).splitlines()[1] == "U2 [summary] I build Kubernetes platforms."


def test_keywords_match_whole_words_only():
    assert cv_eval.keyword_hit(["C++"], "Wrote C++ services")
    assert cv_eval.keyword_hit(["kubernetes"], "Kubernetes, Helm")
    assert not cv_eval.keyword_hit(["Go"], "Google Cloud")


def test_appeal_is_half_keywords_half_experience_weighted_by_importance():
    requirements = [
        {"id": 1, "requirement": "Kubernetes", "keywords": ["Kubernetes"], "importance": "must"},
        {"id": 2, "requirement": "AWS", "keywords": ["AWS"], "importance": "nice"},
    ]
    # Keywords: Kubernetes found (2 of 3). Experience: Kubernetes shown, AWS absent (4 of 6).
    experience = [{"id": 1, "match": 2}, {"id": 2, "match": 0}]
    assert round(cv_eval.appeal(requirements, experience, CV), 2) == round(50 * (2 / 3 + 4 / 6), 2)


def test_ai_score_weights_tells_caps_each_unit_and_drops_unfound_quotes():
    unit_list = cv_eval.units(CV)
    tells = [
        {"pattern": "S5", "units": [5], "quote": "Spearheaded"},          # fills U5
        {"pattern": "M2", "units": [5], "quote": "journey"},              # U5 is already full
        {"pattern": "W1", "units": [4], "quote": "To cut costs"},         # a quarter of U4
        {"pattern": "S1", "units": [3], "quote": "not in this unit"},     # dropped
        {"pattern": "S1", "units": [9], "quote": "Spearheaded"},          # no such unit
    ]
    assert cv_eval.ai_score(tells, unit_list) == 100 * (1 + 0.25) / 5


def test_readability_counts_each_flagged_unit_once():
    unit_list = cv_eval.units(CV)
    flags = [
        {"units": [4], "reason": "too many clauses", "quote": "To cut costs"},
        {"units": [4], "reason": "wrong word", "quote": "spot nodes"},
        {"units": [2], "reason": "grammar error", "quote": "not there"},
    ]
    assert cv_eval.readability(flags, unit_list) == 100 * (1 - 1 / 5)


def test_results_get_a_header_once_and_a_row_per_run(tmp_path):
    path = tmp_path / "results.csv"
    cv_eval.append_result(path, ["0.5.0", "2026-10-15", "quick", "default", "claude-opus-5-5", 70.0, 40.0, 90.0])
    cv_eval.append_result(path, ["0.5.0", "2026-10-16", "quick", 0.4, "claude-sonnet-5-5", 72.0, 35.0, 91.0])
    rows = list(csv.reader(path.open()))
    assert rows[0] == cv_eval.HEADER
    assert [r[1] for r in rows[1:]] == ["2026-10-15", "2026-10-16"]
