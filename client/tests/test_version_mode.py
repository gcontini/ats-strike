"""``cv version``: the build, and whether the server is one it can talk to."""

from __future__ import annotations

import httpx

from cv_client import __version__
from cv_client import cli
from cv_client.api import HttpApi
from cv_client.config import Config
from cv_client.modes import version as version_mode


def _server(version: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "request_id": "r1", "ok": True,
            "data": {"status": "ok", "version": version, "pdflatex": True,
                     "models": {}, "auth_required": False},
        })
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://server.invalid")


def _run(monkeypatch, server_version: str) -> int:
    real = HttpApi
    monkeypatch.setattr(version_mode, "HttpApi",
                        lambda url, token=None: real(url, token=token, client=_server(server_version)))
    config = Config(server_url="http://server.invalid")
    return version_mode.run(config)


def _major_apart() -> str:
    return f"{int(__version__.split('.')[0]) + 1}.0.0"


def test_a_matching_server_is_reported_and_exits_zero(monkeypatch, capsys):
    assert _run(monkeypatch, __version__) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"version {__version__}"
    assert f"v{__version__}" in out[1] and "matches" in out[1]


def test_a_different_major_exits_one(monkeypatch, capsys):
    assert _run(monkeypatch, _major_apart()) == 1
    assert "refuses to talk to it" in capsys.readouterr().out


def test_a_different_minor_still_exits_zero(monkeypatch, capsys):
    major, minor, *_ = __version__.split(".")
    assert _run(monkeypatch, f"{major}.{int(minor) + 1}.0") == 0
    assert "minor versions differ" in capsys.readouterr().out


def test_an_unreachable_server_is_not_a_failure(monkeypatch, capsys):
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")
    down = httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://server.invalid")
    monkeypatch.setattr(version_mode, "HttpApi",
                        lambda url, token=None: HttpApi(url, token=token, client=down))

    assert version_mode.run(Config(server_url="http://server.invalid")) == 0

    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"version {__version__}"
    assert out[1].startswith("server: not checked")


def test_a_broken_cv_toml_does_not_stop_the_answer(tmp_path, monkeypatch, capsys):
    broken = tmp_path / "cv.toml"
    broken.write_text("= broken [")
    assert cli.main(["version", "--config", str(broken)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"version {__version__}"
    assert out[1].startswith("server: not checked")
