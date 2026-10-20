"""Model selection: ``models.toml`` in, one long-lived chat model per role out.

resumix calls one model per job, all declared in ``resources/models.toml``:

``detect``
    Is this text a job posting? YES/NO.
``analysis``
    The JD analysis: scored against the profile, facts extracted.
``letter``
    The cover letter.
``jd_evaluation``, ``profile_scoring``, ``format_selection``, ``cv``, ``validation``, ``highlight``
    The six steps of a CV (:mod:`resumix_server.pipeline.cv_generator`): the
    JD's key points, the profile scored against them, the format, the body
    and the experiences, their validation, and the ``**bold**`` pass.

Every role calls ``[provider]``, unless it sets ``use_alternate_provider = N``
(N ≥ 2), which calls the same env var names with ``N`` appended instead
(``MODEL_API_KEY2`` / ``MODEL_BASE_URL2``). ``model_provider`` names the
provider (:mod:`.standard`, :mod:`.deepseek`) that builds the role's chat model.
The pipeline and the API call models through here and never through LangChain's
model classes; the CV pipeline orders its steps with LangGraph.
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
