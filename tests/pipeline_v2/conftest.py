"""Fixtures of the pipeline 2.0 tests.

They share v1's test double: :class:`FakeLLM`, a real ``ModelSelector`` over a
fake transport, from ``tests/pipeline_v1/pipeline_helpers.py``.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline_v1"))

from pipeline_helpers import EXAMPLE_CANDIDATE, FakeLLM  # noqa: E402
from v2_helpers import cut  # noqa: E402

from pipeline_v2 import ROLES, ItemCompiler, Items, resources_dir  # noqa: E402


@pytest.fixture
def profile() -> dict:
    """The fictional example candidate's profile, fresh for each test."""
    return json.loads((EXAMPLE_CANDIDATE / "candidate_profile.json").read_text())


@pytest.fixture(scope="session")
def candidate_data() -> dict:
    return json.loads((EXAMPLE_CANDIDATE / "candidate_data.json").read_text())


@pytest.fixture(scope="session")
def example_items() -> Items:
    """The example candidate's items, built by a fake compile: the role summary as context, each
    duty one action, two duties per story. Copy it before changing it."""
    llm = FakeLLM(roles=ROLES)
    llm["compile"].replies = [cut]
    prompt = (resources_dir() / "sys_prompt_items.txt").read_text(encoding="utf-8")
    profile = json.loads((EXAMPLE_CANDIDATE / "candidate_profile.json").read_text())
    return ItemCompiler(llm=llm, system_prompt=prompt).compile(profile, today=date(2026, 11, 18))
