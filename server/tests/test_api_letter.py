"""POST /v1/letter."""

from __future__ import annotations

import json

from server_helpers import Failing, a_job, logs


def test_returns_the_letter_and_what_it_logged(client, stub):
    data = client.post("/v1/letter", files=a_job()).json()["data"]
    assert data["text"] == stub.text
    assert "Σ 1 model call(s)" in logs(data["items"])[-1]


def test_the_letter_is_handed_the_whole_request(client, stub):
    parts = a_job(**{"analysis.json": b'{"company_name": "Acme"}'})
    client.post("/v1/letter", files=parts, data={"settings": json.dumps({"x": 1})})
    name, pack, job, settings = stub.calls[-1]
    assert name == "letter"
    assert job["analysis.json"] == b'{"company_name": "Acme"}'
    assert settings == {"x": 1}


def test_a_letter_that_never_validates_is_a_502(client, stub):
    stub.failure = Failing("model_output", "no valid cover letter", stage="letter.generate")
    response = client.post("/v1/letter", files=a_job())
    assert response.status_code == 502
    assert "letter.generate" in response.json()["error"]
    assert "data" not in response.json(), "a failure carries no items"
