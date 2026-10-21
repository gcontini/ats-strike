"""What a provider is: the chat model class, and how it spells the thinking switch."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Type

import httpx
from langchain_openai.chat_models.base import BaseChatOpenAI

from .config import ModelSpec


class Provider(ABC):
    """Turns one role's :class:`ModelSpec` into its long-lived chat model.

    It translates and never validates: what the role declares is what is
    sent, and a combination the endpoint refuses fails on its first call.
    """

    #: The LangChain chat model this provider is called through.
    chat_class: Type[BaseChatOpenAI]

    @abstractmethod
    def thinking(self, mode: str) -> Dict[str, Any]:
        """The ``extra_body`` that turns thinking on or off; ``{}`` for ``"auto"``."""

    def chat_model(
        self,
        spec: ModelSpec,
        *,
        api_key: str,
        base_url: str,
        timeout: float,
        max_output_tokens: Optional[int],
        http_client: Optional[httpx.Client] = None,
    ) -> BaseChatOpenAI:
        """Build the role's client: every setting goes in here, once.

        ``spec.extra_body`` is merged over the thinking switch, so a key you
        declare wins over the one derived from ``thinking``.
        """
        return self.chat_class(
            model=spec.model,
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
            max_retries=1,
            temperature=spec.temperature,
            reasoning_effort=spec.reasoning_effort,
            max_tokens=max_output_tokens,
            extra_body={**self.thinking(spec.thinking), **spec.extra_body} or None,
            use_responses_api=spec.api == "responses",
            http_client=http_client,
        )
