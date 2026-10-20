"""models.toml and its RESUMIX_<ROLE>_<FIELD> overrides."""

import pytest
from pydantic import ValidationError

from resumix_server.models import MODEL_ROLES, load_model_config

TOML = """
[provider]
api_key_env = "PROVIDER_KEY"
base_url_env = "PROVIDER_URL"
base_url = "https://provider.example/v1"

[defaults]
max_output_tokens = 8000

[models.detect]
model = "detect-1"
[models.analysis]
model = "analysis-1"
temperature = 0.1
[models.letter]
model = "letter-1"
[models.jd_evaluation]
model = "jd-evaluation-1"
[models.profile_scoring]
model = "profile-scoring-1"
[models.format_selection]
model = "format-selection-1"
[models.cv]
model = "cv-1"
structured_output = "json_schema"
extra_body = { thinking_budget = 6000 }
[models.validation]
model = "validation-1"
use_alternate_provider = 2
[models.highlight]
model = "highlight-1"
"""


@pytest.fixture
def load(tmp_path):
    def load(toml=TOML):
        (tmp_path / "models.toml").write_text(toml)
        return load_model_config(tmp_path)
    return load


def test_parses_every_role_with_its_defaults(load):
    config = load()
    assert tuple(config.models) == MODEL_ROLES
    detect = config.models["detect"]
    assert (detect.model_provider, detect.api, detect.structured_output, detect.thinking) == (
        "standard", "chat_completions", "json_mode", "auto"
    )
    assert config.models["cv"].extra_body == {"thinking_budget": 6000}
    assert config.defaults["max_output_tokens"] == 8000


def test_env_overrides_any_key_with_its_type(load, monkeypatch):
    monkeypatch.setenv("RESUMIX_CV_TEMPERATURE", "0.7")
    monkeypatch.setenv("RESUMIX_CV_API", "responses")
    monkeypatch.setenv("RESUMIX_CV_USE_ALTERNATE_PROVIDER", "3")
    monkeypatch.setenv("RESUMIX_CV_EXTRA_BODY", '{"enable_search": true}')
    cv = load().models["cv"]
    assert (cv.temperature, cv.api, cv.use_alternate_provider) == (0.7, "responses", 3)
    assert cv.extra_body == {"enable_search": True}


def test_an_empty_env_value_drops_the_key(load, monkeypatch):
    monkeypatch.setenv("RESUMIX_ANALYSIS_TEMPERATURE", "")
    assert load().models["analysis"].temperature is None


def test_a_key_nothing_reads_is_rejected(load):
    with pytest.raises(ValidationError, match="web_search"):
        load(TOML.replace('model = "letter-1"', 'model = "letter-1"\nweb_search = true'))


def test_provider_one_prefers_the_env_url_over_the_literal(load, monkeypatch):
    monkeypatch.setenv("PROVIDER_KEY", "k")
    config = load()
    assert config.endpoint(config.models["cv"]) == ("k", "https://provider.example/v1")
    monkeypatch.setenv("PROVIDER_URL", "https://env.example/v1")
    assert config.endpoint(config.models["cv"]) == ("k", "https://env.example/v1")


def test_provider_n_reads_suffixed_vars_and_never_the_literal(load, monkeypatch):
    monkeypatch.setenv("PROVIDER_KEY2", "k2")
    config = load()
    with pytest.raises(RuntimeError, match="PROVIDER_URL2"):
        config.endpoint(config.models["validation"])
    monkeypatch.setenv("PROVIDER_URL2", "https://two.example/v1")
    assert config.endpoint(config.models["validation"]) == ("k2", "https://two.example/v1")


def test_a_missing_key_is_named(load, monkeypatch):
    monkeypatch.delenv("PROVIDER_KEY", raising=False)
    config = load()
    with pytest.raises(RuntimeError, match="PROVIDER_KEY"):
        config.endpoint(config.models["cv"])
