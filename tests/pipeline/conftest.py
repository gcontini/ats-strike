"""Fixtures shared by the pipeline tests.

Nothing here needs an API key, a network or a real model: the pipeline talks
to a :class:`FakeLLM` — a real selector over a fake transport — that replays
canned replies and records what it was asked, which is how the prompt-assembly assertions stay honest.
"""

from __future__ import annotations

import json

import pytest

from cv_pipeline.bundle import CandidateInputs, ResourceBundle, default_bundle

from pipeline_helpers import EXAMPLE_CANDIDATE, GOLDEN, FakeLLM, sample_cv


@pytest.fixture(scope="session")
def bundle() -> ResourceBundle:
    """The server's own shipped defaults."""
    return default_bundle()


@pytest.fixture(scope="session")
def candidate_data() -> dict:
    """The example candidate's contact details: what the template prints."""
    return json.loads((EXAMPLE_CANDIDATE / "candidate_data.json").read_text())


@pytest.fixture(scope="session")
def candidate(candidate_data) -> CandidateInputs:
    """The fictional example candidate's set, as a client would post it."""
    return CandidateInputs(
        profile=json.loads((EXAMPLE_CANDIDATE / "candidate_profile.json").read_text()),
        preferences=(EXAMPLE_CANDIDATE / "candidate_preferences.md").read_text(),
        data=candidate_data,
    )


@pytest.fixture(scope="session")
def fixture_document() -> dict:
    """A document whose strings exercise every escaping rule."""
    raw = json.loads((GOLDEN / "cv_fixture.json").read_text())
    return {**raw["cv"], **raw["candidate"]}


@pytest.fixture
def sample_document(candidate_data) -> dict:
    """One flat namespace: what the model wrote, your own data over the top."""
    return {**sample_cv(), **dict(candidate_data)}


@pytest.fixture
def fake_llm() -> FakeLLM:
    """Every role, fake. Set ``fake_llm[role].replies`` per test."""
    return FakeLLM()
