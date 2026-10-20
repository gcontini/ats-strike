"""Settings, from four places with one order of precedence.

    command line  >  environment  >  resumix.toml  >  file found in the
    current folder  >  the server's own default

Nothing here reads the network or the job folders; it only answers "what did
the user configure, and where are their files".
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .discovery import discover_files, discover_images, find_config

#: What --cover-letter accepts.
COVER_LETTER_MODES = ("no", "yes", "letter_only")

#: What every new posting needs, with no server-side default. The modes that
#: write CVs on a thread check them before anything starts, so a missing one
#: fails with a clear message rather than on a background thread.
REQUIRED_FILES = ("candidate_profile.json", "candidate_data.json", "candidate_preferences.md")

#: What a [files] entry is called in resumix.toml, and the file it names.
#: Short keys because TOML reads ``candidate_profile.json = "x"`` as a dotted
#: key (a table named ``candidate_profile``), which is not what anyone means.
CONFIG_KEYS = {
    "profile": "candidate_profile.json",
    "candidate_data": "candidate_data.json",
    "preferences": "candidate_preferences.md",
    "template": "resume.tex.jinja",
    "prompt_jd_evaluation": "sys_prompt_jd_evaluation.txt",
    "prompt_profile_scoring": "sys_prompt_profile_scoring.txt",
    "prompt_format_selection": "sys_prompt_format_selection.txt",
    "prompt_cv_body": "sys_prompt_cv_body.txt",
    "schema_cv_body": "cv_body_schema.json",
    "prompt_cv_duties": "sys_prompt_cv_duties.txt",
    "schema_cv_duties": "cv_duties_schema.json",
    "prompt_validation_body": "sys_prompt_validation_body.txt",
    "prompt_validation_duties": "sys_prompt_validation_duties.txt",
    "prompt_highlight": "sys_prompt_highlight.txt",
    "prompt_letter": "sys_prompt_letter.txt",
    "prompt_analysis": "sys_prompt_analysis.txt",
}

#: The CV's prompt and schema files, mapped to the multipart part name the
#: server expects: the file name without its extension.
PROMPT_FILES = {
    name: name.rsplit(".", 1)[0]
    for name in (
        "sys_prompt_jd_evaluation.txt",
        "sys_prompt_profile_scoring.txt",
        "sys_prompt_format_selection.txt",
        "sys_prompt_cv_body.txt",
        "cv_body_schema.json",
        "sys_prompt_cv_duties.txt",
        "cv_duties_schema.json",
        "sys_prompt_validation_body.txt",
        "sys_prompt_validation_duties.txt",
        "sys_prompt_highlight.txt",
    )
}
LETTER_PROMPT = "sys_prompt_letter.txt"


class ConfigError(RuntimeError):
    """The configuration cannot produce a working client."""


@dataclass(frozen=True)
class Config:
    """Everything a mode needs to know before it starts."""

    server_url: str = "http://localhost:8080"
    token: Optional[str] = None
    #: Model role -> the request settings it runs on instead of the server's,
    #: from ``[extra_body.<role>]``: sent as ``<role>_extra_body`` with every
    #: request, and read by the endpoints that have that role.
    extra_body: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    #: Page limit the CV must fit. None means the server's default.
    pages: Optional[int] = None
    cover_letter: str = "no"
    #: How long to follow a CV job before giving up on it.
    timeout: float = 1800.0
    #: --verbose: print what the server reports about each finished step.
    verbose: bool = False
    #: Known file name -> where it was found. Missing means "server default".
    files: Mapping[str, Path] = field(default_factory=dict)
    #: Image file name -> where it was found. Sent under exactly that name.
    images: Mapping[str, Path] = field(default_factory=dict)
    #: Where the config came from, for the startup summary.
    config_path: Optional[Path] = None

    # --- accessors the modes use -------------------------------------------
    def path(self, name: str) -> Optional[Path]:
        return self.files.get(name)

    def require(self, name: str) -> Path:
        """A file with no server-side default, so its absence is fatal."""
        path = self.files.get(name)
        if path is None:
            raise ConfigError(
                f"{name} is required but was not found. Put it in the current "
                f"folder, or name it in {self.config_path or 'resumix.toml'}."
            )
        return path

    def image_parts(self) -> Dict[str, bytes]:
        """The images to send, by the name the template includes them under."""
        return {name: path.read_bytes() for name, path in self.images.items()}

    def prompt_overrides(self) -> Dict[str, str]:
        """The CV prompts and schemas the user supplied, as multipart parts."""
        return {
            part: self.files[name].read_text(encoding="utf-8")
            for name, part in PROMPT_FILES.items()
            if name in self.files
        }


def load_config(
    *,
    config_file: Optional[Path] = None,
    data_dir: Optional[Path] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> Config:
    """Build the :class:`Config` for this run.

    ``overrides`` holds the command-line values (``None`` where the flag was
    not given), so they are applied last and win.
    """
    path = find_config(config_file)
    raw: Dict[str, Any] = {}
    if path is not None:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))

    files = discover_files(data_dir)
    # An explicit path in [files] overrides discovery, and must exist.
    for key, value in (raw.get("files") or {}).items():
        name = CONFIG_KEYS.get(key)
        if name is None:
            raise ConfigError(
                f"{path}: unknown entry files.{key} "
                f"(known: {', '.join(sorted(CONFIG_KEYS))})"
            )
        candidate = Path(value).expanduser()
        if not candidate.is_absolute() and path is not None:
            candidate = (path.parent / candidate).resolve()
        if not candidate.is_file():
            raise ConfigError(f"{path}: files.{key} does not exist: {candidate}")
        files[name] = candidate

    config = Config(
        server_url=raw.get("server_url", Config.server_url),
        token=raw.get("token"),
        extra_body=dict(raw.get("extra_body") or {}),
        pages=raw.get("pages"),
        cover_letter=raw.get("cover_letter", Config.cover_letter),
        timeout=float(raw.get("timeout", Config.timeout)),
        verbose=bool(raw.get("verbose", Config.verbose)),
        files=files,
        images=discover_images(data_dir),
        config_path=path,
    )

    env_changes = {
        key: value
        for key, value in (
            ("server_url", os.getenv("RESUMIX_API_URL")),
            ("token", os.getenv("RESUMIX_API_TOKEN")),
        )
        if value
    }
    cli_changes = {k: v for k, v in (overrides or {}).items() if v is not None}
    config = replace(config, **{**env_changes, **cli_changes})

    if config.cover_letter not in COVER_LETTER_MODES:
        raise ConfigError(
            f"cover_letter must be one of {', '.join(COVER_LETTER_MODES)}; "
            f"got {config.cover_letter!r}"
        )
    return config


__all__ = ["Config", "ConfigError", "load_config", "COVER_LETTER_MODES",
           "CONFIG_KEYS", "LETTER_PROMPT"]
