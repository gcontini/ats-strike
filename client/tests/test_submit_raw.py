"""``cv submit-raw``: a job description in, files in this folder out."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from cv_client.modes import submit_raw

from conftest import JD_TEXT, PDF, TEX, FakeApi, document, make_server


@pytest.fixture
def here(tmp_path, monkeypatch):
    """Run in an empty directory: the mode writes to wherever you stand."""
    work = tmp_path / "here"
    work.mkdir()
    monkeypatch.chdir(work)
    jd = work / "posting.txt"
    jd.write_text(JD_TEXT)
    return jd


@pytest.fixture
def apis(api, monkeypatch):
    by_url = {"http://v11.invalid": api, "http://v05.invalid": FakeApi()}
    monkeypatch.setattr("cv_client.modes.HttpApi", lambda url, **kw: by_url[url])
    return by_url


def test_without_an_output_it_writes_a_pdf_named_after_the_posting(here, config, apis):
    assert submit_raw.run(config, here, []) == 0
    assert (here.parent / "posting.pdf").read_bytes() == PDF


def test_a_second_run_counts_up_rather_than_replacing_yesterdays_cv(here, config, apis):
    submit_raw.run(config, here, [])
    submit_raw.run(config, here, [])
    assert (here.parent / "posting_1.pdf").is_file()


def test_the_suffix_is_the_whole_instruction(here, config, apis):
    submit_raw.run(config, here, [Path("cv.pdf"), Path("cv.json"), Path("cv.tex")])

    assert (here.parent / "cv.pdf").read_bytes() == PDF
    assert json.loads((here.parent / "cv.json").read_text()) == document()
    assert (here.parent / "cv.tex").read_text() == TEX


def test_a_name_you_chose_is_overwritten_without_asking(here, config, apis):
    (here.parent / "cv.pdf").write_bytes(b"old")
    submit_raw.run(config, here, [Path("cv.pdf")])
    assert (here.parent / "cv.pdf").read_bytes() == PDF


def test_an_unknown_suffix_is_refused_before_anything_is_spent(here, config, api, apis):
    with pytest.raises(SystemExit):
        submit_raw.run(config, here, [Path("cv.docx")])
    assert api.calls == []


def test_it_only_writes_the_cv(here, config, api, apis):
    """No detection, no analysis, no letter, no spreadsheet, no folders."""
    submit_raw.run(config, here, [])

    assert api.calls == ["create_cv", "events", "acknowledge"]
    assert api.seen_job == {"jd.txt": JD_TEXT.encode()}
    assert sorted(p.name for p in here.parent.iterdir()) == ["posting.pdf", "posting.txt"]


def test_server_ver_writes_it_instead_of_the_default(here, config, servers_dir, api, apis):
    v05 = make_server(servers_dir, "v05")
    submit_raw.run(replace(config, targets=(v05,)), here, [])
    assert apis["http://v05.invalid"].calls == ["create_cv", "events", "acknowledge"]
    assert api.calls == []


def test_log_lines_show_with_verbose_only(here, config, apis, capsys):
    submit_raw.run(config, here, [])
    assert "Σ 1 model call(s)" not in capsys.readouterr().out
    submit_raw.run(replace(config, verbose=True), here, [])
    assert "  v11 2026-01-15T09:00:05 INFO    [run] Σ 1 model call(s)" in (
        capsys.readouterr().out)


def test_new_writes_the_cv_again(here, config, api, apis):
    assert submit_raw.run(config, here, [], new=True) == 0
    assert api.calls == ["acknowledge", "create_cv", "events", "acknowledge"]


def test_a_missing_posting_stops_before_the_server(config, api, apis, tmp_path):
    with pytest.raises(SystemExit):
        submit_raw.run(config, tmp_path / "nope.txt", [])
    assert api.calls == []


def test_a_failed_job_is_reported_with_its_request_id(here, config, api, apis, capsys):
    api.fail_on = "create_cv"
    with pytest.raises(SystemExit):
        submit_raw.run(config, here, [])
    assert "failed-request" in capsys.readouterr().err
