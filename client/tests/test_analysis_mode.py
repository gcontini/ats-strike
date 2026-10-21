"""``cv analysis``: one posting in, its analysis out, nothing else."""

from __future__ import annotations

import shutil
from dataclasses import replace

import pytest
from cv_contracts import JDAnalysis

from cv_client import cli
from cv_client.modes import analysis as analysis_mode
from cv_client.stages.jobfolder import classify

from conftest import EXAMPLE_CANDIDATE, JD_TEXT, analysis

JOB = "Acme_Corp_Head_of_IT"


@pytest.fixture
def posting(tmp_path):
    jd = tmp_path / "posting.txt"
    jd.write_text(JD_TEXT)
    return jd


@pytest.fixture(autouse=True)
def one_api(api, monkeypatch):
    monkeypatch.setattr(analysis_mode, "HttpApi", lambda *a, **kw: api)
    return api


def test_json_goes_to_stdout_and_nothing_is_detected(posting, config, api, tmp_path, capsys):
    assert analysis_mode.run(config, posting, tmp_path / "out") == 0

    assert JDAnalysis.model_validate_json(capsys.readouterr().out) == analysis()
    assert api.calls == ["analyze"]
    assert not (tmp_path / "out").exists(), "json writes no folder"


def test_your_own_analysis_prompt_is_sent(posting, config, api, tmp_path):
    prompt = tmp_path / "sys_prompt_analysis.txt"
    prompt.write_text("MY PROMPT")
    config = replace(config, files={**config.files, "sys_prompt_analysis.txt": prompt})
    analysis_mode.run(config, posting, tmp_path / "out")

    assert api.seen_analysis_prompt == "MY PROMPT"


def test_folder_holds_the_posting_and_its_analysis_as_watch_takes_them(posting, config,
                                                                       tmp_path):
    out = tmp_path / "out"
    assert analysis_mode.run(config, posting, out, output_format="folder") == 0

    assert sorted(p.name for p in out.iterdir()) == [JOB], "no dot folder left behind"
    folder = out / JOB
    assert sorted(p.name for p in folder.iterdir()) == ["analysis.json", "jd.txt"]
    assert (folder / "jd.txt").read_text() == JD_TEXT
    assert classify(folder)[0] != "error"


def test_a_folder_of_the_same_name_is_replaced(posting, config, tmp_path):
    out = tmp_path / "out"
    (out / JOB).mkdir(parents=True)
    (out / JOB / "old.txt").write_text("from before")

    analysis_mode.run(config, posting, out, output_format="folder")

    assert sorted(p.name for p in out.iterdir()) == [JOB]
    assert sorted(p.name for p in (out / JOB).iterdir()) == ["analysis.json", "jd.txt"]


def test_a_half_written_folder_from_a_crashed_run_is_cleared(posting, config, tmp_path):
    out = tmp_path / "out"
    (out / f".{JOB}").mkdir(parents=True)
    (out / f".{JOB}" / "stale.txt").write_text("half-written")

    analysis_mode.run(config, posting, out, output_format="folder")

    assert sorted(p.name for p in out.iterdir()) == [JOB]
    assert sorted(p.name for p in (out / JOB).iterdir()) == ["analysis.json", "jd.txt"]


def test_a_missing_out_folder_is_created(posting, config, tmp_path):
    out = tmp_path / "a" / "b"
    analysis_mode.run(config, posting, out, output_format="folder")
    assert (out / JOB / "analysis.json").is_file()


def test_a_missing_posting_stops_before_the_server(config, api, tmp_path):
    with pytest.raises(SystemExit):
        analysis_mode.run(config, tmp_path / "nope.txt", tmp_path / "out")
    assert api.calls == []


def test_a_failure_is_reported_on_stderr_with_its_request(posting, config, api, tmp_path,
                                                          capsys):
    api.fail_on = "analyze"
    assert analysis_mode.run(config, posting, tmp_path / "out", output_format="folder") == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "analyze failed" in captured.err
    assert "request id: failed-request" in captured.err
    assert not (tmp_path / "out").exists()


def test_out_defaults_to_incoming_under_the_data_dir(posting, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ATS_CONFIG", raising=False)
    data = tmp_path / "data"
    data.mkdir()
    for name in ("candidate_profile.json", "candidate_preferences.md"):
        shutil.copy(EXAMPLE_CANDIDATE / name, data / name)

    code = cli.main(["analysis", "--in", str(posting), "--data-dir", str(data),
                     "--output-format", "folder", "-v"])

    assert code == 0
    assert (data / "incoming" / JOB / "analysis.json").is_file()
    captured = capsys.readouterr()
    assert captured.out == f"✅ {data / 'incoming' / JOB}\n", "only the result on stdout"
    assert captured.err.startswith("version "), "-v says which build on stderr"
