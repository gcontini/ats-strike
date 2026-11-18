"""Fixtures of the pipeline 2.0 tests.

They share v1's test double: :class:`FakeLLM`, a real ``ModelSelector`` over a
fake transport, from ``tests/pipeline_v1/pipeline_helpers.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline_v1"))

from pipeline_helpers import EXAMPLE_CANDIDATE  # noqa: E402


@pytest.fixture
def profile() -> dict:
    """The fictional example candidate's profile, fresh for each test."""
    return json.loads((EXAMPLE_CANDIDATE / "candidate_profile.json").read_text())


@pytest.fixture(scope="session")
def candidate_data() -> dict:
    return json.loads((EXAMPLE_CANDIDATE / "candidate_data.json").read_text())
