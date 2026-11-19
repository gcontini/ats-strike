"""ats-strike's CV pipeline 2.0: a store compiled once per profile, and a CV per job from it.

A library, as v1 is: it takes its inputs in memory and returns its outputs; no HTTP, no
contracts, and no configuration read except at build time. It is built on :mod:`common` (the
models, the observability, the errors) and imports nothing from ``pipeline_v1``.

- ``store`` — the items' shapes, the checks a valid one passes, and the view the user reads.
- ``items`` — :class:`ItemCompiler`: the profile's experiences cut into base items and stories.
- ``tree_store`` — the competence tree's and the market's shapes, leaf status, checks and view.
- ``tree`` — :class:`TreeBuilder`: the tree shaped from the items and the market, and each
  experience's items linked to its leaves.
- ``terms`` — pure text helpers: whole-word term matching, compound phrases, copy detection.
- ``pool_store`` — the pool's shapes, its derived values (lines, leaves, terms, weight), checks and view.
- ``pool`` — :class:`PoolWriter`: everything a CV prints from the experiences, written once — per
  story a ladder of bullets and, for a long story, groups; per experience a role summary and a
  compact line; for the profile, skill phrases and summary frames.
- ``pool_review`` — :class:`PoolReviewer`: v1's duties validation of the pool, one experience at a
  time; code refuses a replacement that changes a piece's printed lines.

Its prompts and its own ``models.toml`` are ``resources/v2``, packaged as
``pipeline_v2.resources``.
"""

import importlib.resources
from pathlib import Path

from .items import ItemCompiler
from .pool import PoolWriter
from .pool_review import PoolReviewer, review_markdown
from .pool_store import Pool, pool_checks, pool_dump, pool_markdown, validate_pool
from .store import Items, items_markdown, validate
from .tree import TreeBuilder
from .tree_store import Market, Tree, leaf_status, tree_dump, tree_markdown, validate_tree

#: The model roles of ``resources/v2/models.toml``, growing with each sub-plan.
ROLES = ("compile", "validation")


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
    "Pool",
    "PoolReviewer",
    "PoolWriter",
    "Tree",
    "TreeBuilder",
    "items_markdown",
    "leaf_status",
    "pool_checks",
    "pool_dump",
    "pool_markdown",
    "resources_dir",
    "review_markdown",
    "tree_dump",
    "tree_markdown",
    "validate",
    "validate_pool",
    "validate_tree",
]
