"""DashScope (Qwen, hosted DeepSeek) and any other OpenAI-compatible endpoint."""

from __future__ import annotations

from typing import Any, Dict

from langchain_openai import ChatOpenAI

from .base import Provider


class StandardProvider(Provider):
    """``ChatOpenAI``, with DashScope's ``enable_thinking`` switch."""

    chat_class = ChatOpenAI

    def thinking(self, mode: str) -> Dict[str, Any]:
        return {} if mode == "auto" else {"enable_thinking": mode == "on"}
