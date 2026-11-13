"""``cv render``: a cv.json or a cv.tex in, the PDF beside it out."""

from __future__ import annotations

import pytest

from cv_client.modes import render as render_mode

from conftest import PDF


@pytest.fixture(autouse=True)
def served(api, monkeypatch):
    monkeypatch.setattr("cv_client.modes.HttpApi", lambda url, **kw: api)
    return api


@pytest.mark.parametrize("source, sent", [("cv.json", "cv.json"), ("edited.tex", "cv.tex")])
def test_the_file_is_sent_by_its_suffix_and_only_the_pdf_is_kept(config, api, tmp_path,
                                                                  source, sent):
    path = tmp_path / source
    path.write_text("{}")

    assert render_mode.run(config, path) == 0

    assert api.seen_job == {sent: b"{}"}
    assert "candidate_data.json" in api.seen_pack
    assert path.with_suffix(".pdf").read_bytes() == PDF
    assert {p.name for p in tmp_path.iterdir() if p.is_file()} == {
        path.with_suffix(".pdf").name, source}


def test_output_names_the_pdf(config, tmp_path):
    path = tmp_path / "cv.json"
    path.write_text("{}")
    render_mode.run(config, path, output=tmp_path / "mine.pdf")
    assert (tmp_path / "mine.pdf").read_bytes() == PDF


def test_another_suffix_is_refused_before_the_server(config, api, tmp_path):
    path = tmp_path / "cv.txt"
    path.write_text("x")
    with pytest.raises(SystemExit):
        render_mode.run(config, path)
    assert api.calls == []
