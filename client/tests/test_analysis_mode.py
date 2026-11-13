"""``cv analysis``: one posting in, its analysis out, nothing else."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cv_client import cli
from cv_client.modes import analysis as analysis_mode
from cv_client.stages.jobfolder import classify

from conftest import JD_TEXT, LOG_LINE, FakeApi, analysis, make_server

JOB = "Acme_Corp_Head_of_IT"


@pytest.fixture
def posting(tmp_path):
    jd = tmp_path / "posting.txt"
    jd.write_text(JD_TEXT)
    return jd


@pytest.fixture
def apis(api, monkeypatch):
    """Each server's fake, by its URL: the mode's own HttpApi, replaced."""
    by_url = {"http://v11.invalid": api, "http://v05.invalid": FakeApi()}
    monkeypatch.setattr("cv_client.modes.HttpApi", lambda url, **kw: by_url[url])
    return by_url


def test_json_goes_to_stdout_and_nothing_is_detected(posting, config, api, apis, tmp_path,
                                                     capsys):
    assert analysis_mode.run(config, posting, tmp_path / "out") == 0

    assert json.loads(capsys.readouterr().out) == analysis().model_dump(exclude_unset=True)
    assert api.calls == ["analyze"]
    assert api.seen_job == {"jd.txt": JD_TEXT.encode()}
    assert not (tmp_path / "out").exists(), "json writes no folder"


def test_the_server_folder_is_what_is_sent(posting, config, server, api, apis, tmp_path):
    (server.folder / "sys_prompt_analysis.txt").write_text("MY PROMPT")
    analysis_mode.run(config, posting, tmp_path / "out")

    assert api.seen_pack["sys_prompt_analysis.txt"] == b"MY PROMPT"
    assert "server.toml" not in api.seen_pack


def test_server_ver_is_used_and_the_default_never_is(posting, config, servers_dir, api, apis,
                                                     tmp_path):
    v05 = make_server(servers_dir, "v05")
    analysis_mode.run(replace(config, targets=(v05,)), posting, tmp_path / "out")

    assert apis["http://v05.invalid"].calls == ["analyze"]
    assert api.calls == []


def test_folder_holds_the_posting_and_its_analysis_as_watch_takes_them(posting, config, apis,
                                                                       tmp_path):
    out = tmp_path / "out"
    assert analysis_mode.run(config, posting, out, output_format="folder") == 0

    assert sorted(p.name for p in out.iterdir()) == [JOB], "no dot folder left behind"
    folder = out / JOB
    assert sorted(p.name for p in folder.iterdir()) == ["analysis.json", "jd.txt"]
    assert (folder / "jd.txt").read_text() == JD_TEXT
    assert classify(folder)[0] != "error"


def test_a_folder_of_the_same_name_is_replaced(posting, config, apis, tmp_path):
    out = tmp_path / "out"
    (out / JOB).mkdir(parents=True)
    (out / JOB / "old.txt").write_text("from before")

    analysis_mode.run(config, posting, out, output_format="folder")

    assert sorted(p.name for p in out.iterdir()) == [JOB]
    assert sorted(p.name for p in (out / JOB).iterdir()) == ["analysis.json", "jd.txt"]


def test_a_half_written_folder_from_a_crashed_run_is_cleared(posting, config, apis, tmp_path):
    out = tmp_path / "out"
    (out / f".{JOB}").mkdir(parents=True)
    (out / f".{JOB}" / "stale.txt").write_text("half-written")

    analysis_mode.run(config, posting, out, output_format="folder")

    assert sorted(p.name for p in out.iterdir()) == [JOB]
    assert sorted(p.name for p in (out / JOB).iterdir()) == ["analysis.json", "jd.txt"]


def test_a_missing_out_folder_is_created(posting, config, apis, tmp_path):
    out = tmp_path / "a" / "b"
    analysis_mode.run(config, posting, out, output_format="folder")
    assert (out / JOB / "analysis.json").is_file()


def test_a_missing_posting_stops_before_the_server(config, api, apis, tmp_path):
    with pytest.raises(SystemExit):
        analysis_mode.run(config, tmp_path / "nope.txt", tmp_path / "out")
    assert api.calls == []


def test_a_failure_is_reported_on_stderr_with_its_request(posting, config, api, apis, tmp_path,
                                                          capsys):
    api.fail_on = "analyze"
    assert analysis_mode.run(config, posting, tmp_path / "out", output_format="folder") == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "analyze failed" in captured.err
    assert "request id: failed-request" in captured.err
    assert not (tmp_path / "out").exists()


def test_the_server_s_log_lines_show_on_stderr_with_verbose_only(posting, config, apis,
                                                                 tmp_path, capsys):
    analysis_mode.run(config, posting, tmp_path / "out")
    assert LOG_LINE not in capsys.readouterr().err
    analysis_mode.run(replace(config, verbose=True), posting, tmp_path / "out")
    assert f"  v11 {LOG_LINE}" in capsys.readouterr().err


def test_out_defaults_to_incoming_under_the_data_dir(posting, servers_dir, server, apis,
                                                     tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ATS_CONFIG", raising=False)
    (servers_dir / "cv.toml").write_text('servers = ["v11"]\n')

    code = cli.main(["analysis", "--in", str(posting), "--data-dir", str(servers_dir),
                     "--output-format", "folder", "-v"])

    assert code == 0
    assert (servers_dir / "incoming" / JOB / "analysis.json").is_file()
    captured = capsys.readouterr()
    assert captured.out == f"✅ {servers_dir / 'incoming' / JOB}\n", "only the result on stdout"
    assert captured.err.startswith("version "), "-v says which build on stderr"


def test_server_ver_on_the_command_line(posting, servers_dir, server, apis, tmp_path,
                                        monkeypatch, capsys):
    make_server(servers_dir, "v05")
    (servers_dir / "cv.toml").write_text('servers = ["v11", "v05"]\n')

    assert cli.main(["analysis", "--in", str(posting), "--data-dir", str(servers_dir),
                     "--server-ver", "v05"]) == 0
    assert apis["http://v05.invalid"].calls == ["analyze"]
    assert apis["http://v11.invalid"].calls == []
