"""clipboard end to end: real threads, a fake server, a clipboard and a keyboard the test drives.

Every wait has a deadline, so a deadlock fails the test instead of hanging it.
"""

from __future__ import annotations

import queue
import threading
import time

from resumix_client.stages.calls import Calls
from resumix_client.stages.generation import CvWriter, GenerationWorker
from resumix_client.stages.intake import Intake
from resumix_client.stages.listener import IDLE_HINT, Listener
from resumix_client.stages.terminal import Console, Keyboard
from resumix_client.stages.watcher import Watcher
from resumix_client.stages.working import WorkingProcessor
from resumix_client.tracking import build_tracker
from resumix_client.ui import Decision

from conftest import JD_TEXT, FakeApi, ScriptedConfirmer, analysis, envelope, make_job, typed

DEADLINE = 10.0


class Clipboard:
    """A clipboard the test copies into: each copy is new text once."""

    def __init__(self):
        self._copied: queue.Queue = queue.Queue()

    def copy(self, text):
        self._copied.put(text)

    def poll(self):
        try:
            return self._copied.get_nowait()
        except queue.Empty:
            return None


class ByFirstLine(FakeApi):
    """Analyses each posting by its first line: the company. Always YES —
    which clipboard asks about anyway."""

    def analyze(self, text, *, profile, preferences, prompt=None):
        self._record("analyze")
        return envelope(analysis(company_name=text.split("\n", 1)[0], should_apply="YES"))


class Slow(ByFirstLine):
    """Holds the first CV in the middle of being written until released."""

    def __init__(self, **replies):
        super().__init__(**replies)
        self.writing = threading.Event()
        self.release = threading.Event()

    def events(self, request_id, *, after=0):
        self.writing.set()
        assert self.release.wait(DEADLINE), "never released"
        yield from super().events(request_id, after=after)


def posting(company):
    return f"{company}\n{JD_TEXT}"


def build(api, config, workspace, keyboard, confirmer, clipboard):
    console = Console()
    calls = Calls(api)
    writer = CvWriter(calls, config)
    working = WorkingProcessor(workspace, writer, build_tracker(workspace.root, True), console)
    listener = Listener(clipboard, Intake(calls, config, working, confirmer, console),
                        working, keyboard, console, poll_seconds=0.01)
    return Watcher(working, listener.run,
                   {"cv": GenerationWorker(working, writer, console, poll_seconds=0.01)},
                   keyboard, console, join_seconds=0.01)


def in_background(watcher):
    result: dict = {}
    thread = threading.Thread(target=lambda: result.update(code=watcher.run()), daemon=True)
    thread.start()
    return thread, result


def wait_until(condition):
    deadline = time.monotonic() + DEADLINE
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


def entries(parent):
    return sorted(p.name for p in parent.glob("*/*"))


def approval(folder):
    path = folder / "approval_status.txt"
    return path.read_text().strip() if path.is_file() else None


def companies(confirmer):
    return [a.company_name for a in confirmer.seen]


def test_the_next_posting_is_asked_about_while_a_cv_is_written(config, workspace, typist):
    api = Slow()
    clipboard = Clipboard()
    confirmer = ScriptedConfirmer(Decision(submit=True))
    clipboard.copy(posting("Alpha"))
    thread, result = in_background(build(api, config, workspace, Keyboard(typist), confirmer,
                                         clipboard))
    try:
        assert api.writing.wait(DEADLINE), "Alpha's CV is being written"
        clipboard.copy(posting("Beta"))
        wait_until(lambda: len(confirmer.seen) == 2)
        assert entries(workspace.cv) == [], "Beta was asked about before Alpha's CV was done"
    finally:
        api.release.set()
    wait_until(lambda: len(entries(workspace.cv)) == 2)
    typist.type("q")
    thread.join(DEADLINE)

    assert not thread.is_alive()
    assert result["code"] == 0
    assert companies(confirmer) == ["Alpha", "Beta"]
    assert entries(workspace.cv) == ["Alpha_Head_of_IT", "Beta_Head_of_IT"]
    assert not any(workspace.working.iterdir())


def test_q_lets_the_cv_in_hand_finish_and_the_queued_one_waits(config, workspace, typist):
    api = Slow()
    clipboard = Clipboard()
    clipboard.copy(posting("Alpha"))
    thread, result = in_background(build(api, config, workspace, Keyboard(typist),
                                         ScriptedConfirmer(Decision(submit=True)), clipboard))
    beta = workspace.working / "Beta_Head_of_IT"
    try:
        assert api.writing.wait(DEADLINE)
        clipboard.copy(posting("Beta"))
        wait_until(lambda: approval(beta) == "APPROVED")
        typist.type("q")
        time.sleep(0.2)
        assert thread.is_alive(), "q waits for the CV being written"
    finally:
        api.release.set()
    thread.join(DEADLINE)

    assert not thread.is_alive()
    assert result["code"] == 0
    assert entries(workspace.cv) == ["Alpha_Head_of_IT"]
    assert approval(beta) == "APPROVED", "written at the next start"


def test_leftovers_come_first_asked_about_or_written(config, workspace, typist):
    make_job(workspace.working, "Asked_Before", approval="PENDING")
    make_job(workspace.working, "Approved_Before", approval="APPROVED",
             **{"analysis.json": analysis(company_name="Approved Co").model_dump_json()})
    clipboard = Clipboard()
    clipboard.copy(posting("Alpha"))
    confirmer = ScriptedConfirmer(Decision(submit=False), Decision(submit=True))
    thread, result = in_background(build(ByFirstLine(), config, workspace, Keyboard(typist),
                                         confirmer, clipboard))
    wait_until(lambda: len(entries(workspace.cv)) == 2)
    typist.type("q")
    thread.join(DEADLINE)

    assert result["code"] == 0
    assert companies(confirmer) == ["Acme Corp", "Alpha"], \
        "the unanswered leftover first, the approved one not again"
    assert entries(workspace.discarded) == ["Asked_Before"]
    assert entries(workspace.cv) == ["Alpha_Head_of_IT", "Approved_Before"]


def test_quitting_at_the_question_leaves_the_posting_pending(config, workspace, typist, capsys):
    clipboard = Clipboard()
    clipboard.copy(posting("Alpha"))
    thread, result = in_background(build(ByFirstLine(), config, workspace, Keyboard(typist),
                                         ScriptedConfirmer(Decision(submit=False, quit=True)),
                                         clipboard))
    thread.join(DEADLINE)

    assert not thread.is_alive(), "q at the question stops it; no second q needed"
    assert result["code"] == 0
    assert approval(workspace.working / "Alpha_Head_of_IT") == "PENDING"
    assert entries(workspace.cv) == [] and entries(workspace.discarded) == []
    assert "1 posting(s) wait in working/" in capsys.readouterr().out


def test_a_failed_analysis_makes_the_exit_code_1(config, workspace, typist):
    api = ByFirstLine(fail_on="analyze")
    clipboard = Clipboard()
    clipboard.copy(posting("Alpha"))
    watcher = build(api, config, workspace, Keyboard(typist), ScriptedConfirmer(), clipboard)
    thread, result = in_background(watcher)
    wait_until(lambda: watcher.working.failures == 1)
    typist.type("q")
    thread.join(DEADLINE)
    assert result["code"] == 1


# --- the listener on its own ---------------------------------------------------------
def listener(workspace, keyboard, *, assume_yes=False):
    console = Console()
    working = WorkingProcessor(workspace, None, build_tracker(workspace.root, False), console)
    intake = Intake(Calls(FakeApi()), None, working, ScriptedConfirmer(), console)
    return Listener(Clipboard(), intake, working, keyboard, console, assume_yes=assume_yes,
                    poll_seconds=0.01)


def test_the_end_of_input_stops_it(workspace):
    done = threading.Thread(target=listener(workspace, typed()).run, args=(threading.Event(),),
                            daemon=True)
    done.start()
    done.join(DEADLINE)
    assert not done.is_alive(), "nobody is left to answer"


def test_with_yes_the_end_of_input_does_not(workspace):
    """An unattended run may have no keyboard at all."""
    stop = threading.Event()
    running = threading.Thread(target=listener(workspace, typed(), assume_yes=True).run,
                               args=(stop,), daemon=True)
    running.start()
    time.sleep(0.1)
    assert running.is_alive()
    stop.set()
    running.join(DEADLINE)
    assert not running.is_alive()


def test_anything_but_q_gets_a_hint(workspace, capsys):
    listener(workspace, typed("hello", "q")).run(threading.Event())
    assert IDLE_HINT in capsys.readouterr().out
