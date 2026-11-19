"""Model selection: a ``models.toml`` in, one long-lived chat model per role out.

Each pipeline declares its roles in its own ``models.toml`` and passes that
file and its role list in: nothing here knows which jobs a pipeline has.

Every role calls ``[provider]``, unless it sets ``use_alternate_provider = N``
(N ≥ 2), which calls the same env var names with ``N`` appended instead
(``MODEL_API_KEY2`` / ``MODEL_BASE_URL2``). ``model_provider`` names the
provider (:mod:`.standard`, :mod:`.deepseek`) that builds the role's chat model.
The pipelines call models through here and never through LangChain's model
classes.
"""

from .config import ModelConfig, ModelSpec, load_model_config
from .selector import ATTEMPTS, ModelSelector, Usage, build_selector

__all__ = [
    "ATTEMPTS",
    "ModelConfig",
    "ModelSelector",
    "ModelSpec",
    "Usage",
    "build_selector",
    "load_model_config",
]
