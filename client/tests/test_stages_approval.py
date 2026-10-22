"""Stage 2: your answers, and how q and the end of input stop the loop."""

from __future__ import annotations

import threading
import time

import pytest

from cv_client.stages.approval import (
    Answer,
    HumanApproval,
    TerminalReviewer,
    parse_answer,
)
from cv_client.stages.jobfolder import read_analysis
from cv_client.stages.terminal import END_OF_INPUT, TaggedLog

from conftest import JD_TEXT, analysis, typed


class Scripted:
    """Answers from a list, and remembers what it was shown."""

    def __init__(self, *answers: Answer):
        self.answers = list(answers)
        self.shown: list = []

    def review(self, jd_text, analysis):
        self.shown.append(analysis.company_name)
        return self.answers.pop(0)


def check_posting(processor, company="Acme Corp"):
    folder = processor.submit(JD_TEXT, analysis(should_apply="CHECK", company_name=company),
                              TaggedLog(processor.console, "posting.txt"))
    return folder


def loop(processor, console, reviewer, keyboard=None, *, ask=True):
    return HumanApproval(processor, reviewer, keyboard or typed(), console, ask=ask,
                         poll_seconds=0.01)


# --- what counts as an answer ----------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("y", Answer("approve")), (" YES ", Answer("approve")),
        ("d", Answer("discard")), ("discard", Answer("discard")),
        ("s", Answer("skip")), ("n", Answer("skip")), ("next", Answer("skip")),
        ("q", Answer("quit")), ("QUIT", Answer("quit")),
        ("https://jobs.example/42", Answer("approve", url="https://jobs.example/42")),
        ("https://jobs.example/" + "x" * 300, None),
        ("", None), ("yy", None), ("maybe", None), ("42", None),
    ],
)
def test_parse_answer(text, expected):
    assert parse_answer(text) == expected


# --- one posting ---------------------------------------------------------------------
def test_y_approves_and_queues_it(processor, console):
    folder = check_posting(processor)
    approval = loop(processor, console, Scripted(Answer("approve")))

    assert approval.review_one(processor.approvals.get_nowait()) is True
    assert (folder / "approval_status.txt").read_text().strip() == "APPROVED"
    assert processor.generations.get_nowait() == folder


def test_a_pasted_url_approves_it_and_is_kept(processor, console):
    folder = check_posting(processor)
    loop(processor, console, Scripted(Answer("approve", url="https://jobs.example/42"))
         ).review_one(processor.approvals.get_nowait())
    assert read_analysis(folder).posting_url == "https://jobs.example/42"


def test_d_discards_it(processor, console, workspace):
    folder = check_posting(processor)
    loop(processor, console, Scripted(Answer("discard"))
         ).review_one(processor.approvals.get_nowait())

    assert not folder.exists()
    [kept] = workspace.discarded.glob("*/Acme_Corp_Head_of_IT")
    assert (kept / "approval_status.txt").read_text().strip() == "DISCARDED"


def test_q_leaves_the_posting_as_it_was(processor, console):
    folder = check_posting(processor)
    approval = loop(processor, console, Scripted(Answer("quit")))

    assert approval.review_one(processor.approvals.get_nowait()) is False
    assert not (folder / "approval_status.txt").exists()
    assert processor.generations.empty()


def test_a_posting_broken_while_it_waited_is_filed_not_fatal(processor, console, workspace):
    folder = check_posting(processor)
    (folder / "analysis.json").write_text("{}")
    reviewer = Scripted()

    assert loop(processor, console, reviewer).review_one(
        processor.approvals.get_nowait()) is True
    assert reviewer.shown == []
    [filed] = workspace.error.glob("*/Acme_Corp_Head_of_IT")
    assert "cannot be shown any more" in (filed / "log.log").read_text()


def test_s_asks_again_after_the_others(processor, console):
    check_posting(processor, "Alpha")
    check_posting(processor, "Beta")
    reviewer = Scripted(Answer("skip"), Answer("approve"), Answer("approve"))
    approval = loop(processor, console, reviewer)

    for _ in range(3):
        approval.review_one(processor.approvals.get_nowait())

    assert reviewer.shown == ["Alpha", "Beta", "Alpha"]
    assert processor.approvals.empty()


def test_background_lines_wait_until_you_have_answered(processor, console, capsys):
    folder = check_posting(processor)
    capsys.readouterr()

    class Interrupted(Scripted):
        def review(self, jd_text, analysis):
            console.say("[other] a line from another thread")
            print("the question", flush=True)
            return super().review(jd_text, analysis)

    loop(processor, console, Interrupted(Answer("skip"))).review_one(folder)
    out = capsys.readouterr().out
    assert out.index("the question") < out.index("a line from another thread")


# --- the loop --------------------------------------------------------------------------
def test_q_while_nothing_is_waiting_stops_the_loop(processor, console, capsys):
    approval = loop(processor, console, Scripted(), typed("hello", "q"))
    approval.run(threading.Event())
    assert "nothing is waiting for you" in capsys.readouterr().out


def test_the_end_of_input_quits_when_there_is_someone_to_ask(processor, console):
    loop(processor, console, Scripted(), typed()).run(threading.Event())


def test_with_yes_the_end_of_input_is_ignored(processor, console):
    """An unattended run may have no keyboard at all: it runs until stopped."""
    stop = threading.Event()
    timer = threading.Timer(0.2, stop.set)
    timer.start()
    started = time.monotonic()
    loop(processor, console, Scripted(), typed(), ask=False).run(stop)
    assert time.monotonic() - started >= 0.2


def test_the_loop_asks_about_each_posting_in_turn(processor, console):
    check_posting(processor, "Alpha")
    check_posting(processor, "Beta")
    reviewer = Scripted(Answer("approve"), Answer("quit"))
    loop(processor, console, reviewer).run(threading.Event())
    assert reviewer.shown == ["Alpha", "Beta"]


# --- the terminal ----------------------------------------------------------------------
def test_the_terminal_asks_again_until_it_gets_an_answer(capsys):
    keyboard = typed("maybe", "", "d")
    keyboard.discard_typed_ahead = lambda: None  # as if typed after the question
    answer = TerminalReviewer(keyboard, poll_seconds=0.01).review(JD_TEXT, analysis())
    assert answer == Answer("discard")
    out = capsys.readouterr().out
    assert "Head of IT" in out and "JD ANALYSIS" in out
    assert out.count("⚠ y, d, s/n or q") == 1, "a blank line is not complained about"


def test_what_was_typed_before_the_question_is_thrown_away():
    """A second y meant for the last posting must not approve this one."""
    keyboard = typed("y")
    assert TerminalReviewer(keyboard, poll_seconds=0.01).review(JD_TEXT, analysis()) \
        == Answer("quit"), "the y is gone; only the end of input is left"


def test_the_keyboard_reports_the_end_of_input_once():
    keyboard = typed("y")
    assert keyboard.get(0.01) == "y"
    assert keyboard.get(0.01) == END_OF_INPUT
    assert keyboard.get(0.01) is None
