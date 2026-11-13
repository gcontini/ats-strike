"""The schema the CV model writes to: what it insists on, what it tolerates."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from cv_pipeline.cv_schema import TailoredCVData

from pipeline_helpers import sample_cv_data


def test_it_enforces_the_template_s_limits():
    """The page limit and the template both depend on these bounds."""
    with pytest.raises(ValidationError):
        sample_cv_data(skills=["only", "three", "here"])       # min 6
    with pytest.raises(ValidationError):
        sample_cv_data(experiences=[])                         # min 3
    no_role_summary = sample_cv_data().model_dump()["experiences"]
    del no_role_summary[0]["role_summary"]
    with pytest.raises(ValidationError):
        sample_cv_data(experiences=no_role_summary)            # required
    no_duties = sample_cv_data().model_dump()["experiences"]
    no_duties[0]["duties"] = []
    with pytest.raises(ValidationError):
        sample_cv_data(experiences=no_duties)                  # an empty list breaks \begin{itemize}


def test_a_field_nobody_declared_is_kept_rather_than_dropped():
    """Your own prompt and your own template can agree on something this
    schema has never heard of; it is not in the way."""
    data = TailoredCVData.model_validate(
        {**sample_cv_data().model_dump(), "availability": "Immediate"}
    )
    assert data.model_dump()["availability"] == "Immediate"


def test_an_extra_field_inside_an_experience_survives_too():
    cv = sample_cv_data().model_dump()
    cv["experiences"][0]["tech_stack"] = ["Go", "Kubernetes"]
    dumped = TailoredCVData.model_validate(cv).model_dump()
    assert dumped["experiences"][0]["tech_stack"] == ["Go", "Kubernetes"]


def test_the_schema_the_model_is_asked_for_is_closed():
    """Accepting an extra field and inviting one are different things: an
    open schema is ``additionalProperties: true``, which a strict json_schema
    endpoint refuses outright."""
    schema = TailoredCVData.model_json_schema()
    assert schema["additionalProperties"] is False
    assert all(d["additionalProperties"] is False for d in schema["$defs"].values())
