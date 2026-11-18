"""ats-strike's CV pipeline 2.0: a store compiled once per profile, and a CV per job from it.

A library, as v1 is: it takes its inputs in memory and returns its outputs; no HTTP, no
contracts, and no configuration read except at build time. It uses v1's infrastructure
(``pipeline_v1.models``, ``.observability``, ``.errors``) and none of v1's steps.

- ``store`` — the store's shapes, the checks a valid one passes, and the view the user reads.
- ``items`` — :class:`ItemCompiler`: the profile's experiences cut into base items and stories.

Its prompts and its own ``models.toml`` are ``resources/v2``, packaged as
``pipeline_v2.resources``.
"""

import importlib.resources
from pathlib import Path

from .items import ItemCompiler
from .store import Items, items_markdown, validate

#: The model roles of ``resources/v2/models.toml``, growing with each sub-plan.
ROLES = ("compile",)


def resources_dir() -> Path:
    """The ``resources/`` folder inside the installed package.

    ``importlib.resources`` in full: importing the subpackage binds the name ``resources`` here.
    """
    return Path(str(importlib.resources.files("pipeline_v2.resources")))


__all__ = ["ROLES", "ItemCompiler", "Items", "items_markdown", "resources_dir", "validate"]
