"""ats-strike's CV pipeline 2.0: a store compiled once per profile, and a CV per job from it.

A library, as v1 is: it takes its inputs in memory and returns its outputs; no HTTP, no
contracts, and no configuration read except at build time. It uses v1's infrastructure
(``pipeline_v1.models``, ``.observability``, ``.errors``) and none of v1's steps.

- ``store`` — the items' shapes, the checks a valid one passes, and the view the user reads.
- ``items`` — :class:`ItemCompiler`: the profile's experiences cut into base items and stories.
- ``tree_store`` — the competence tree's and the market's shapes, leaf status, checks and view.
- ``tree`` — :class:`TreeBuilder`: the tree shaped from the items and the market, and each
  experience's items linked to its leaves.
- ``terms`` — pure text helpers: whole-word term matching, compound phrases, copy detection.

Its prompts and its own ``models.toml`` are ``resources/v2``, packaged as
``pipeline_v2.resources``.
"""

import importlib.resources
from pathlib import Path

from .items import ItemCompiler
from .store import Items, items_markdown, validate
from .tree import TreeBuilder
from .tree_store import Market, Tree, leaf_status, tree_dump, tree_markdown, validate_tree

#: The model roles of ``resources/v2/models.toml``, growing with each sub-plan.
ROLES = ("compile",)


def resources_dir() -> Path:
    """The ``resources/`` folder inside the installed package.

    ``importlib.resources`` in full: importing the subpackage binds the name ``resources`` here.
    """
    return Path(str(importlib.resources.files("pipeline_v2.resources")))


__all__ = [
    "ROLES",
    "ItemCompiler",
    "Items",
    "Market",
    "Tree",
    "TreeBuilder",
    "items_markdown",
    "leaf_status",
    "resources_dir",
    "tree_dump",
    "tree_markdown",
    "validate",
    "validate_tree",
]
