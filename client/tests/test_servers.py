"""A server folder: what it is sent."""

from __future__ import annotations

from cv_client.servers import read_server

from conftest import make_server


def test_the_pack_is_every_file_but_server_toml_subfolders_included(tmp_path):
    server = make_server(tmp_path, "v11", files=("candidate_profile.json",))
    (server.folder / "cache" / "steps").mkdir(parents=True)
    (server.folder / "cache" / "steps" / "pool.json").write_bytes(b"[]")
    (server.folder / "sys_prompt_cv_body.txt").write_text("MINE")

    pack = server.pack()

    assert sorted(pack) == ["cache/steps/pool.json", "candidate_profile.json",
                            "sys_prompt_cv_body.txt"]
    assert pack["sys_prompt_cv_body.txt"] == b"MINE"


def test_only_the_top_server_toml_is_left_out(tmp_path):
    server = make_server(tmp_path, "v11", files=())
    (server.folder / "cache").mkdir()
    (server.folder / "cache" / "server.toml").write_text("x")
    assert list(server.pack()) == ["cache/server.toml"]


def test_the_pack_is_read_when_asked_for(tmp_path):
    server = make_server(tmp_path, "v11", files=())
    (server.folder / "notes.md").write_text("one")
    first = server.pack()
    (server.folder / "notes.md").write_text("two")
    assert (first["notes.md"], server.pack()["notes.md"]) == (b"one", b"two")


def test_a_server_is_named_after_its_folder(tmp_path):
    make_server(tmp_path, "v05")
    server = read_server(tmp_path / "v05")
    assert (server.name, server.default, server.timeout, server.settings) == (
        "v05", False, 1800.0, {})
