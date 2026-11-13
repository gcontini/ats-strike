"""``cv submit``: one posting, one CV, and back only once it is filed."""

from __future__ import annotations

import threading
import time

from dataclasses import replace

import pytest

from cv_client.jdanalysis import JDAnalysis
from cv_client.modes import submit

from conftest import JD_TEXT, FakeApi, analysis, make_job, make_server

DEADLINE = 10.0


@pytest.fixture
def apis(api, monkeypatch):
    """Each server's fake, by its URL: the mode's own HttpApi, replaced."""
    by_url = {"http://v11.invalid": api, "http://v05.invalid": FakeApi()}
    monkeypatch.setattr("cv_client.modes.HttpApi", lambda url, **kw: by_url[url])
    return by_url


@pytest.fixture
def served(api, apis):
    return api


@pytest.fixture
def jd(tmp_path):
    path = tmp_path / "posting.txt"
    path.write_text(JD_TEXT, encoding="utf-8")
    return path


def entries(parent):
    return sorted(p.name for p in parent.glob("*/*"))


def test_the_cv_is_filed_before_it_returns(served, config, jd, workspace):
    code = submit.run(config, jd, workspace.root, assume_yes=True)

    assert code == 0
    assert entries(workspace.cv) == ["Acme_Corp_Head_of_IT"]
    assert not any(workspace.working.iterdir())
    assert served.calls == ["detect", "analyze", "create_cv", "events", "acknowledge"]
    assert jd.read_text() == JD_TEXT, "a file named on the command line is not an inbox"


def test_an_analysis_file_skips_detection_and_analysis(served, config, jd, workspace, tmp_path):
    given = tmp_path / "analysis.json"
    given.write_text(analysis(match_percentage=64).model_dump_json(), encoding="utf-8")

    assert submit.run(config, jd, workspace.root, analysis=given, assume_yes=True) == 0

    assert served.calls == ["create_cv", "events", "acknowledge"]
    [delivered] = workspace.cv.glob("*/Acme_Corp_Head_of_IT")
    kept = JDAnalysis.model_validate_json((delivered / "analysis.json").read_text())
    assert kept.match_percentage == 64, "the analysis given, not a new one"
    assert given.exists()


def test_an_invalid_analysis_file_stops_before_anything(served, config, jd, workspace, tmp_path,
                                                        capsys):
    given = tmp_path / "analysis.json"
    given.write_text("{}", encoding="utf-8")

    with pytest.raises(SystemExit):
        submit.run(config, jd, workspace.root, analysis=given, assume_yes=True)

    assert served.calls == []
    assert not any(workspace.working.iterdir())
    assert "not a valid analysis" in capsys.readouterr().err


def test_new_drops_the_run_the_server_has_first(served, config, jd, workspace):
    assert submit.run(config, jd, workspace.root, assume_yes=True, new=True) == 0
    assert served.calls == ["detect", "analyze", "acknowledge", "create_cv", "events",
                            "acknowledge"]
    assert entries(workspace.cv) == ["Acme_Corp_Head_of_IT"]


def test_a_failed_cv_makes_the_exit_code_1(served, config, jd, workspace):
    served.fail_on = "create_cv"
    assert submit.run(config, jd, workspace.root, assume_yes=True) == 1
    assert len(entries(workspace.error)) == 1


def test_two_servers_write_one_after_the_other_and_one_analysis_is_made(
        served, apis, config, servers_dir, jd, workspace):
    v05 = make_server(servers_dir, "v05")
    config = replace(config, targets=(config.default, v05))

    assert submit.run(config, jd, workspace.root, assume_yes=True) == 0

    assert served.calls == ["detect", "analyze", "create_cv", "events", "acknowledge"]
    assert apis["http://v05.invalid"].calls == ["create_cv", "events", "acknowledge"]
    [delivered] = workspace.cv.glob("*/Acme_Corp_Head_of_IT")
    assert len(list(delivered.glob("cv_*.pdf"))) == 2


def test_one_server_failing_still_delivers_and_exits_1(served, apis, config, servers_dir, jd,
                                                       workspace):
    v05 = make_server(servers_dir, "v05")
    apis["http://v05.invalid"].fail_on = "create_cv"
    config = replace(config, targets=(config.default, v05))

    assert submit.run(config, jd, workspace.root, assume_yes=True) == 1
    assert entries(workspace.cv) == ["Acme_Corp_Head_of_IT"]


def test_leftovers_are_left_alone(served, config, jd, workspace):
    """A one-off run must not start by asking about an earlier one."""
    make_job(workspace.working, "Left_Over", approval="APPROVED",
             **{"analysis.json": analysis(company_name="Other Co").model_dump_json()})
    assert submit.run(config, jd, workspace.root, assume_yes=True) == 0
    assert [p.name for p in workspace.working.iterdir()] == ["Left_Over"]


# --- the question, on the terminal ------------------------------------------------------
def in_background(config, jd, workspace):
    result: dict = {}
    thread = threading.Thread(
        target=lambda: result.update(code=submit.run(config, jd, workspace.root)), daemon=True)
    thread.start()
    return thread, result


def wait_for(capsys, text):
    shown, deadline = "", time.monotonic() + DEADLINE
    while text not in shown:
        assert time.monotonic() < deadline, f"never shown: {text}"
        shown += capsys.readouterr().out
        time.sleep(0.01)


def test_you_are_asked_before_anything_is_spent(served, config, jd, workspace, typist,
                                                 monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", typist)
    thread, result = in_background(config, jd, workspace)

    wait_for(capsys, "Paste the posting URL")
    assert "create_cv" not in served.calls
    typist.type("y")
    thread.join(DEADLINE)

    assert result["code"] == 0
    assert entries(workspace.cv) == ["Acme_Corp_Head_of_IT"]


def test_quitting_leaves_the_posting_for_the_next_start(served, config, jd, workspace, typist,
                                                       monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", typist)
    thread, result = in_background(config, jd, workspace)

    wait_for(capsys, "Paste the posting URL")
    typist.type("q")
    thread.join(DEADLINE)

    assert result["code"] == 0
    [left] = workspace.working.iterdir()
    assert (left / "approval_status.txt").read_text().strip() == "PENDING"
    assert "create_cv" not in served.calls
    assert "waits in working/" in capsys.readouterr().out
