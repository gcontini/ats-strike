"""The servers a CV is written with: one folder each, listed in ``cv.toml``.

A server folder holds everything that server is sent: your candidate files,
any prompt, template or image you want instead of its own, and a ``cache/``
it fills itself. The one file it is not sent is its ``server.toml``: where
the server is, its token, its timeout and the ``[settings]``
table forwarded to it untouched. The client reads no other file name: what
the files mean is the server's business.

The default server, named by ``default_server`` in ``cv.toml``, analyses every
posting and writes the cover letter; the others only write CVs. With one
server listed, that is the default. A server's name
is its folder's name.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

#: The file in a server folder that is read here and never sent.
SERVER_FILE = "server.toml"

#: How long to follow a CV job before giving up on it, by default.
DEFAULT_TIMEOUT = 1800.0


class ConfigError(RuntimeError):
    """The configuration cannot produce a working client."""


@dataclass(frozen=True)
class Server:
    """One server folder, and what its ``server.toml`` says."""

    name: str
    folder: Path
    url: str
    token: Optional[str] = None
    #: Analyses postings and writes the letter: ``default_server`` in ``cv.toml``.
    default: bool = False
    #: Seconds to follow one CV job.
    timeout: float = DEFAULT_TIMEOUT
    #: ``[settings]``: sent with every request as it is; its meaning is the server's.
    settings: Mapping[str, Any] = field(default_factory=dict)

    def pack(self) -> Dict[str, bytes]:
        """Every file under the folder but ``server.toml``, read now, by its
        path relative to the folder with ``/``."""
        files: Dict[str, bytes] = {}
        for path in sorted(self.folder.rglob("*")):
            name = path.relative_to(self.folder).as_posix()
            if path.is_file() and name != SERVER_FILE:
                files[name] = path.read_bytes()
        return files


def read_server(folder: Path) -> Server:
    """The server a folder describes."""
    path = folder / SERVER_FILE
    if not path.is_file():
        raise ConfigError(f"{folder} has no {SERVER_FILE}")
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
    if not raw.get("url"):
        raise ConfigError(f"{path} has no url")
    return Server(
        name=folder.name,
        folder=folder,
        url=raw["url"],
        token=raw.get("token") or None,
        timeout=float(raw.get("timeout", DEFAULT_TIMEOUT)),
        settings=dict(raw.get("settings") or {}),
    )


def load_servers(
    base: Path,
    names: Sequence[str],
    *,
    default: Optional[str] = None,
    selected: Sequence[str] = (),
    default_needed: bool = False,
) -> Tuple[Tuple[Server, ...], Optional[Server]]:
    """The servers this run writes CVs with, and the default.

    ``names`` are the folders ``cv.toml`` lists, relative to ``base``: each is
    read now, used or not. ``default`` is the folder name ``default_server``
    gives; with one server listed it may be left out. ``selected`` are the ``--server_ver`` names; without any, the default is the one target. ``default_needed`` is for the modes
    that analyse postings, which the default does whatever the targets are.
    """
    servers = [read_server((base / name).resolve()) for name in names]
    by_name = {server.name: server for server in servers}
    if default is None and len(servers) == 1:
        default = servers[0].name
    default_server = None
    if default is not None:
        if default not in by_name:
            raise ConfigError(
                f"default_server {default}: no such server "
                f"(listed: {', '.join(by_name) or 'none'})"
            )
        default_server = by_name[default] = replace(by_name[default], default=True)
    for name in selected:
        if name not in by_name:
            raise ConfigError(
                f"--server_ver {name}: no such server "
                f"(listed: {', '.join(by_name) or 'none'})"
            )
    if default_server is None and (default_needed or not selected):
        raise ConfigError(
            "no default_server is set in cv.toml"
            + ("" if servers else " (no server is listed at all)")
        )
    targets = tuple(by_name[name] for name in selected) if selected else (default_server,)
    return targets, default_server


__all__ = ["Server", "ConfigError", "SERVER_FILE", "DEFAULT_TIMEOUT", "read_server",
           "load_servers"]
