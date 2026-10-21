"""``PromptConfirmer``: only y/n/s/q or a URL are ever accepted."""

from __future__ import annotations

import pytest

from cv_client.ui import AutoConfirmer, PromptConfirmer

from conftest import analysis, answering, typed


def _confirm(*answers: str):
    """Type ``answers`` once the question is on screen; return the decision."""
    return PromptConfirmer(answering(*answers), poll_seconds=0.01).confirm(analysis())


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES", "  y  "])
def test_yes_submits_without_a_url(answer):
    decision = _confirm(answer)
    assert decision.submit and decision.url is None and not decision.quit


@pytest.mark.parametrize("answer", ["n", "s", "skip", "N", "S", "  s  "])
def test_no_or_skip_discards(answer):
    decision = _confirm(answer)
    assert not decision.submit and not decision.quit


@pytest.mark.parametrize("answer", ["q", "quit", "Q", "  q  "])
def test_quit_stops_the_run(answer):
    decision = _confirm(answer)
    assert not decision.submit and decision.quit


def test_a_url_submits_with_it():
    decision = _confirm("https://jobs.example/42")
    assert decision.submit and decision.url == "https://jobs.example/42"


def test_a_url_too_long_to_be_one_is_asked_again(capsys):
    decision = _confirm("https://jobs.example/" + "x" * 300, "s")
    assert not decision.submit
    assert "URL is too long" in capsys.readouterr().out


def test_blank_input_is_ignored_and_asks_again():
    decision = _confirm("   ", "\t", "s")
    assert not decision.submit and not decision.quit


@pytest.mark.parametrize("garbage", ["ss", "maybe", "yy", "sure", "nope", "42"])
def test_anything_else_is_rejected_and_asks_again_instead_of_submitting(garbage):
    """A mistyped skip (or anything not y/n/s/q/URL) must never fall through
    to an implicit submit — that is exactly what let a stray keystroke turn
    into a real CV job."""
    decision = _confirm(garbage, "s")
    assert not decision.submit and not decision.quit


def test_the_end_of_input_quits():
    decision = _confirm()
    assert not decision.submit and decision.quit


def test_what_was_typed_before_the_question_is_thrown_away():
    """A second y meant for the last posting must not submit this one."""
    decision = PromptConfirmer(typed("y"), poll_seconds=0.01).confirm(analysis())
    assert decision.quit, "the y is gone; only the end of input is left"


def test_yes_submits_with_the_url_the_analysis_found(capsys):
    decision = AutoConfirmer().confirm(analysis(posting_url="https://jobs.example/42"))
    assert decision.submit and decision.url == "https://jobs.example/42"
    assert "submitting automatically (--yes)" in capsys.readouterr().out
