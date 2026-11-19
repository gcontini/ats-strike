"""Pure text helpers (S2): whole-word terms, compound phrases, copy detection."""

from __future__ import annotations

import pytest

from pipeline_v2.terms import has_term, jaccard, norm, shingles, split_compound


# --- has_term ---------------------------------------------------------------------------
def test_a_term_is_found_as_a_whole_word_in_any_case():
    assert has_term("Kubernetes", "We run kubernetes in production.")
    assert has_term("project management", "Strong Project Management skills")
    assert not has_term("Java", "JavaScript developers")
    assert not has_term("Go", "Gopher")


def test_a_short_term_does_not_count_in_lower_case():
    """"ai" is an Italian preposition, "go" an English verb."""
    assert not has_term("AI", "Contribuire ai progetti del cliente")
    assert has_term("AI", "Experience with AI platforms")
    assert has_term("ai", "Experience with AI platforms")
    assert not has_term("Go", "ready to go-live next week")
    assert has_term("Go", "Backend services in Go and Rust")


def test_a_hyphen_before_joins_the_term_to_the_word_before_and_one_after_does_not():
    assert not has_term("functional requirements", "non-functional requirements")
    assert not has_term("sales", "pre-sales support")
    assert has_term("AI", "AI-driven tools")
    assert has_term("Kubernetes", "Kubernetes-native deployments")


def test_symbols_in_a_term_are_matched_literally():
    assert has_term("C++", "Modern C++ and Python")
    assert has_term("CI/CD", "CI/CD pipelines")
    assert not has_term("C++", "C and Python")
    assert not has_term("", "anything")


# --- split_compound ------------------------------------------------------------------------
@pytest.mark.parametrize("phrase, parts", [
    ("docker and kubernetes", ["docker", "kubernetes"]),
    ("Docker & Kubernetes", ["Docker", "Kubernetes"]),
    ("SQL, JPA/Hibernate and JDBC", ["SQL", "JPA/Hibernate", "JDBC"]),
    ("IaC(Terraform)", ["IaC", "Terraform"]),
    ("Linux/Windows administration", ["Linux", "Windows administration"]),
    ("CI/CD", ["CI/CD"]),
    ("TCP/IP networking", ["TCP/IP networking"]),
    ("Kubernetes", ["Kubernetes"]),
])
def test_split_compound(phrase, parts):
    assert split_compound(phrase) == parts


def test_norm_lowers_squashes_and_strips():
    assert norm("  Project   Management. ") == "project management"


# --- shingles -------------------------------------------------------------------------------
JD = ("We are looking for a senior platform engineer to design and run our Kubernetes clusters, "
      "automate the infrastructure with Terraform and mentor a small team of engineers in Milan.")


def test_shingles_are_word_8_grams():
    assert len(shingles("one two three four five six seven eight nine")) == 2
    assert shingles("too short") == frozenset()


def test_a_repost_is_a_copy_and_another_job_is_not():
    repost = "Job posted again. " + JD.replace("Milan", "Milan or Turin")
    other = ("We are hiring a project manager to coordinate vendors, run the budget and report to the "
             "steering committee on a public-sector programme in Rome.")
    assert jaccard(shingles(JD), shingles(repost)) >= 0.5
    assert jaccard(shingles(JD), shingles(other)) < 0.5
    assert jaccard(frozenset(), frozenset()) == 0.0
