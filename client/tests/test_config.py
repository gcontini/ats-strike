"""cv.toml, the servers it lists, and which of them a run uses."""

from __future__ import annotations

import pytest

from cv_client.config import ConfigError, load_config

from conftest import make_server


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A folder that stands in for the current folder."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ATS_CONFIG", raising=False)
    return tmp_path


def two_servers(home, *, toml='servers = ["v11", "v05"]\ndefault_server = "v11"\n'):
    make_server(home, "v11")
    make_server(home, "v05")
    (home / "cv.toml").write_text(toml)


def test_without_a_flag_only_the_default_writes(home):
    two_servers(home)
    config = load_config()
    assert [s.name for s in config.targets] == ["v11"]
    assert config.default.name == "v11"


def test_named_servers_write_and_the_default_is_still_there(home):
    two_servers(home)
    config = load_config(selected=["v05"], default_needed=True)
    assert [s.name for s in config.targets] == ["v05"]
    assert config.default.name == "v11"
    assert [s.name for s in config.servers()] == ["v05", "v11"]


def test_a_default_server_that_is_not_listed_stops_the_client(home):
    two_servers(home, toml='servers = ["v11", "v05"]\ndefault_server = "v20"\n')
    with pytest.raises(ConfigError, match="default_server v20: no such server"):
        load_config()


def test_the_only_server_is_the_default_without_saying_so(home):
    make_server(home, "v11")
    (home / "cv.toml").write_text('servers = ["v11"]\n')
    config = load_config()
    assert [s.name for s in config.targets] == ["v11"]
    assert config.default.name == "v11" and config.default.default


def test_an_unlisted_server_ver_stops_the_client(home):
    two_servers(home)
    with pytest.raises(ConfigError, match="--server_ver v20: no such server"):
        load_config(selected=["v20"])


def test_a_listed_folder_that_is_missing_stops_the_client(home):
    make_server(home, "v11")
    (home / "cv.toml").write_text('servers = ["v11", "v05"]\ndefault_server = "v11"\n')
    with pytest.raises(ConfigError, match="has no server.toml"):
        load_config()


def test_a_server_toml_without_a_url_stops_the_client(home):
    make_server(home, "v11")
    (home / "v11" / "server.toml").write_text("timeout = 60\n")
    (home / "cv.toml").write_text('servers = ["v11"]\n')
    with pytest.raises(ConfigError, match="has no url"):
        load_config()


def test_a_mode_that_needs_the_default_stops_without_one(home):
    two_servers(home, toml='servers = ["v11", "v05"]\n')
    with pytest.raises(ConfigError, match="no default_server is set"):
        load_config()
    with pytest.raises(ConfigError, match="no default_server is set"):
        load_config(selected=["v05"], default_needed=True)


def test_a_mode_that_names_its_server_needs_no_default(home):
    two_servers(home, toml='servers = ["v11", "v05"]\n')
    config = load_config(selected=["v05"])
    assert [s.name for s in config.targets] == ["v05"]
    assert config.default is None


def test_no_cv_toml_says_so(home):
    with pytest.raises(ConfigError, match="no cv.toml found"):
        load_config()


def test_server_folders_are_relative_to_cv_toml(home, monkeypatch):
    data = home / "data"
    make_server(data, "v11")
    (data / "cv.toml").write_text('servers = ["v11"]\n')
    monkeypatch.chdir(home)

    assert load_config(data_dir=data).default.folder == data / "v11"
    assert load_config(config_file=data / "cv.toml").default.folder == data / "v11"


def test_ats_config_names_the_file(home, monkeypatch):
    data = home / "data"
    make_server(data, "v11")
    (data / "cv.toml").write_text('servers = ["v11"]\n')
    monkeypatch.setenv("ATS_CONFIG", str(data / "cv.toml"))
    assert load_config().config_path == data / "cv.toml"


def test_server_toml_is_read_whole(home):
    make_server(home, "v11")
    (home / "v11" / "server.toml").write_text(
        'url = "https://cv.example"\ntoken = "abc"\ntimeout = 60\n'
        '[settings]\npages = 2\n[settings.extra_body.cv]\ntemperature = 0.4\n')
    (home / "cv.toml").write_text('servers = ["v11"]\n')

    server = load_config().default

    assert (server.name, server.url, server.token, server.timeout) == (
        "v11", "https://cv.example", "abc", 60.0)
    assert server.settings == {"pages": 2, "extra_body": {"cv": {"temperature": 0.4}}}


def test_the_command_line_beats_the_file(home):
    two_servers(home, toml='servers = ["v11"]\ncover_letter = "yes"\n')
    assert load_config(overrides={"cover_letter": "no"}).cover_letter == "no"
    assert load_config(overrides={"cover_letter": None}).cover_letter == "yes"


def test_a_bad_cover_letter_mode_is_refused(home):
    two_servers(home)
    with pytest.raises(ConfigError, match="cover_letter"):
        load_config(overrides={"cover_letter": "maybe"})


@pytest.mark.parametrize("letter, selected, lanes", [
    ("no", [], ["v11"]),
    ("no", ["v05"], ["v05"]),
    ("yes", ["v05"], ["v05", "v11"]),
    ("letter_only", ["v05"], ["v05", "v11"]),
    ("yes", ["v11", "v05"], ["v11", "v05"]),
])
def test_lanes_are_the_targets_and_the_default_when_it_writes_the_letter(
        home, letter, selected, lanes):
    two_servers(home)
    config = load_config(selected=selected, default_needed=True,
                         overrides={"cover_letter": letter})
    assert [s.name for s in config.lanes()] == lanes
