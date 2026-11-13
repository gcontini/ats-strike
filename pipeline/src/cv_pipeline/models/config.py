"""``models.toml`` in, one :class:`ModelSpec` per role out.

Every key of a ``[models.<role>]`` table can be overridden by an env var named
``RESUMIX_<ROLE>_<FIELD>`` (``RESUMIX_CV_TEMPERATURE``), so a Docker deployment
can tune a role without editing the file. An empty value drops the key, as if
the table never declared it; ``RESUMIX_<ROLE>_EXTRA_BODY`` is JSON.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict

from ..defaults import default_path

MODELS_FILE = "models.toml"

#: The six jobs resumix has a model for, in the order a run uses them.
MODEL_ROLES = ("detect", "analysis", "letter", "cv", "review", "highlight")


class ModelSpec(BaseModel):
    """One ``[models.<role>]`` table, after its env overrides."""

    model_config = ConfigDict(extra="forbid")

    model: str
    model_provider: str = "standard"
    api: Literal["chat_completions", "responses"] = "chat_completions"
    structured_output: Literal["json_schema", "function_calling", "json_mode"] = "json_mode"
    temperature: Optional[float] = None
    thinking: Literal["auto", "on", "off"] = "auto"
    reasoning_effort: Optional[str] = None
    max_output_tokens: Optional[int] = None
    extra_body: Dict[str, Any] = {}
    use_alternate_provider: int = 1


@dataclass(frozen=True)
class ModelConfig:
    """Parsed ``models.toml``: ``[provider]``, ``[defaults]`` and the roles."""

    provider: Dict[str, str]
    defaults: Dict[str, Any]
    models: Dict[str, ModelSpec]

    def endpoint(self, spec: ModelSpec) -> Tuple[str, str]:
        """API key and base URL of provider ``N = spec.use_alternate_provider``.

        1 is ``[provider]`` itself; N ≥ 2 reads its env var names with ``N``
        appended (``MODEL_API_KEY2`` / ``MODEL_BASE_URL2``), and never its
        literal ``base_url``. Raises ``RuntimeError`` naming what is not set.
        """
        n = spec.use_alternate_provider
        suffix = str(n) if n > 1 else ""
        key_env = self.provider["api_key_env"] + suffix
        url_env = self.provider.get("base_url_env", "base_url") + suffix
        key = os.getenv(key_env)
        url = os.getenv(url_env) or (self.provider.get("base_url") if n == 1 else None)
        for value, name in ((key, key_env), (url, url_env)):
            if not value:
                raise RuntimeError(f"{name} is not set. Set it in your .env.")
        return key, url


def _overridden(role: str, table: Dict[str, Any]) -> Dict[str, Any]:
    """``table`` with the role's ``RESUMIX_<ROLE>_<FIELD>`` env vars applied."""
    table = dict(table)
    for name in ModelSpec.model_fields:
        value = os.getenv(f"RESUMIX_{role.upper()}_{name.upper()}")
        if value == "":
            table.pop(name, None)
        elif value is not None:
            table[name] = json.loads(value) if name == "extra_body" else value
    return table


def load_model_config(resources_dir: Optional[Path] = None) -> ModelConfig:
    """Parse ``models.toml``: from ``resources_dir`` when given, else the shipped copy."""
    path = Path(resources_dir) / MODELS_FILE if resources_dir else default_path(MODELS_FILE)
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    return ModelConfig(
        provider=raw["provider"],
        defaults=raw.get("defaults", {}),
        models={
            role: ModelSpec(**_overridden(role, raw["models"][role])) for role in MODEL_ROLES
        },
    )
