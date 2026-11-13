"""DeepSeek's own API, api.deepseek.com."""

from __future__ import annotations

from typing import Any, Dict

from langchain_deepseek import ChatDeepSeek

from .base import Provider


class DeepSeekProvider(Provider):
    """``ChatDeepSeek``, with DeepSeek's ``thinking: {type}`` switch."""

    chat_class = ChatDeepSeek

    def thinking(self, mode: str) -> Dict[str, Any]:
        if mode == "auto":
            return {}
        return {"thinking": {"type": "enabled" if mode == "on" else "disabled"}}
