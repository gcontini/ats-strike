"""ats-strike's CV pipeline 2.0: a store compiled once per profile, and a CV per job from it.

A library, as v1 is: it takes its inputs in memory and returns its outputs; no HTTP, no
contracts, and no configuration read except at build time. It is built on :mod:`common` (the
models, the observability, the errors) and imports nothing from ``pipeline_v1``. Served at
``/v2``.

- ``facade`` — :class:`Pipeline`, the only thing a server calls: the store completed from the
  pack's ``cache/`` under the time budget, then the CV for one job; and a render of an edited CV.
- ``store`` — the items' shapes, the checks a valid one passes, and the view the user reads.
- ``items`` — :class:`ItemCompiler`: the profile's experiences cut into base items and stories.
- ``tree_store`` — the competence tree's and the market's shapes, leaf status, checks and view;
  the shipped market joined with the profile's own terms.
- ``tree`` — :class:`TreeBuilder`: the tree shaped from the items and the market, and each
  experience's items linked to its leaves.
- ``terms`` — pure text helpers: whole-word term matching, compound phrases, copy detection.
- ``pool_store`` — the pool's shapes, its derived values (lines, leaves, terms, weight), checks and view.
- ``pool`` — :class:`PoolWriter`: everything a CV prints from the experiences, written once — per
  story a ladder of bullets and, for a long story, groups; per experience a role summary and a
  compact line; for the profile, skill phrases and summary frames.
- ``pool_review`` — :class:`PoolReviewer`: v1's duties validation of the pool, one experience at a
  time; code refuses a replacement that adds printed lines to a piece, and moves a bullet that
  gets shorter down to the rung it fills.
- ``allocate`` — :class:`Allocator`: a job description's requirements placed on the tree in one
  call, code's rules on the reply, and each node's points.
- ``select`` — the selection for one job, no model: which experiences are full or compact, which
  story at which rung or group, the skills, the summary frame, the headline, and the scores
  (keywords and narrative); the greedy fills the page, then a refill pass.
- ``document`` — a plan into the template's namespace: the frame filled in, each experience full
  or compact. Pure.
- ``varnish`` — :class:`Varnisher`: three calls between the plan and the render: one wording per
  slot and one title per experience, the headline and the summary's sentence, and the job's
  missing terms put into the summary and the bullets; code's guards on each reply.
- ``highlight`` — the inputs ``common``'s highlighter reads (key points, the CV with each bullet's
  scores, the evidence), built from the varnished document, and its markers laid back. Pure.
- ``render`` — the document compiled by ``common``'s renderer; while over two pages, a story
  steps down a rung (or one goes) and it renders again; the page's lines read back.

Its prompts, its template, the market list's corpus part (``market.json``, built by
``scripts/v2_market.py``) and its own ``models.toml`` are ``resources/v2``, packaged as
``pipeline_v2.resources``.

A server imports from here and never from a submodule.
"""

from .allocate import Allocation, Allocator, allocation_markdown, points
from .facade import ROLES, Pipeline, resources_dir
from .items import ItemCompiler
from .pool import PoolWriter
from .pool_review import PoolReviewer, review_markdown
from .pool_store import Pool, pool_checks, pool_dump, pool_markdown, validate_pool
from .select import Plan, plan_markdown
from .store import Items, items_markdown, validate
from .tree import TreeBuilder
from .tree_store import Market, Tree, leaf_status, tree_dump, tree_markdown, validate_tree
from .varnish import Varnish, Varnisher, varnish_markdown

__all__ = [
    "ROLES",
    "Allocation",
    "Allocator",
    "ItemCompiler",
    "Items",
    "Market",
    "Pipeline",
    "Plan",
    "Pool",
    "PoolReviewer",
    "PoolWriter",
    "Tree",
    "TreeBuilder",
    "Varnish",
    "Varnisher",
    "allocation_markdown",
    "items_markdown",
    "leaf_status",
    "plan_markdown",
    "points",
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
    "varnish_markdown",
]
