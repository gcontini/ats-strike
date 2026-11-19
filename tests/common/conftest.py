"""The common tests share v1's test double: :class:`FakeLLM`, a real
``ModelSelector`` over a fake transport, from ``tests/pipeline_v1/pipeline_helpers.py``."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline_v1"))
