"""Model selection: ``models.toml`` in, one long-lived chat model per role out.

resumix calls six models, one per job, all declared in ``resources/models.toml``:

``detect``
    Is this text a job posting? YES/NO.
``analysis``
    The JD analysis: scored against the profile, facts extracted.
``letter``
    The cover letter.
``cv``
    The tailored CV, the large one.
``review``
    The content review of each CV against the master profile, in plain text.
``highlight``
    The ``**bold**`` pass over the finished CV.

Every role calls ``[provider]``, unless it sets ``use_alternate_provider = N``
(N ≥ 2), which calls the same env var names with ``N`` appended instead
(``MODEL_API_KEY2`` / ``MODEL_BASE_URL2``). ``model_provider`` names the
provider (:mod:`.standard`, :mod:`.deepseek`) that builds the role's chat model.
The pipeline and the API import from here and never from LangChain.
"""

from .config import MODEL_ROLES, ModelConfig, ModelSpec, load_model_config
from .selector import ATTEMPTS, ModelSelector, Usage, build_selector

__all__ = [
    "ATTEMPTS",
    "MODEL_ROLES",
    "ModelConfig",
    "ModelSelector",
    "ModelSpec",
    "Usage",
    "build_selector",
    "load_model_config",
]
