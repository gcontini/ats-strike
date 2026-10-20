"""One long-lived chat model per role, and the two ways to call it.

:meth:`ModelSelector.call_llm` sends a system prompt and a conversation to a
role and returns the reply as the type asked for: a pydantic model, ``bool``
(YES/NO), ``str``, or a JSON ``list`` or ``dict``.
:meth:`ModelSelector.call_tools` asks for the reply as calls of one tool and
returns their arguments. A reply that is empty, cut off by the token cap or not
that type is retried, with what was wrong appended to the conversation, up to
:data:`ATTEMPTS` calls in all. Every call logs what was sent and what it cost,
and adds its tokens and time to the :class:`Usage` it is handed — from as many
threads at once as the CV pipeline runs nodes, hence the lock.
"""

from __future__ import annotations

import copy
import json
import logging
import re
import time
from dataclasses import dataclass, fields
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Mapping, Optional, Tuple, Type, TypeVar

import httpx
from dotenv import load_dotenv
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.utils.json import parse_json_markdown
from openai import LengthFinishReasonError
from pydantic import BaseModel, ValidationError
from resumix_contracts import ModelCall

from ..observability import LOGGER_ROOT, current_run, current_stage
from ..pipeline.errors import ModelOutputError
from ..pipeline.parsing import raw_excerpt
from .base import Provider
from .config import ModelConfig, load_model_config
from .deepseek import DeepSeekProvider
from .standard import StandardProvider

logger = logging.getLogger(f"{LOGGER_ROOT}.models")

#: Calls per :meth:`ModelSelector.call_llm`: the first and two corrections.
ATTEMPTS = 3

#: ``model_provider`` in ``models.toml`` → the provider that builds the role.
#: A new provider is a new file and one line here.
PROVIDERS: Dict[str, Provider] = {"standard": StandardProvider(), "deepseek": DeepSeekProvider()}

#: Request timeout when ``[defaults]`` declares no ``timeout_seconds``.
DEFAULT_TIMEOUT = 600

#: Guards every :class:`Usage` update: the CV pipeline's parallel nodes share one.
_LEDGER = Lock()

T = TypeVar("T")


@dataclass
class Usage:
    """What model calls cost: every attempt counts, failed ones included.

    Handed to :meth:`ModelSelector.call_llm` rather than returned by it,
    because a call that ends in an exception has still been paid for. A
    step's cost is ``usage - mark``, where ``mark`` is a copy taken before it.
    """

    calls: int = 0
    input_tokens: int = 0
    thinking_tokens: int = 0
    output_tokens: int = 0  # the visible reply; thinking is counted apart
    seconds: float = 0.0

    def __sub__(self, other: "Usage") -> "Usage":
        return Usage(*(getattr(self, f.name) - getattr(other, f.name) for f in fields(self)))


def _describe(error: Exception) -> str:
    """What was wrong, one line per problem, worded for the model.

    Pydantic's own message buries the field name under the whole echoed
    payload; a model shown that resends the same object unchanged.
    """
    if isinstance(error, ValidationError):
        return "\n".join(
            f"- {'.'.join(map(str, e['loc'])) or '(whole object)'}: {e['msg']}"
            for e in error.errors()
        )
    return f"- {type(error).__name__}: {error}"


class ModelSelector:
    """Every role's chat model, built once from ``models.toml`` and kept.

    ``http_client`` replaces the HTTP client under every chat model — the
    seam the tests use, so request assembly stays under test.
    """

    def __init__(self, config: ModelConfig, *, http_client: Optional[httpx.Client] = None) -> None:
        self.specs = config.models
        timeout = float(config.defaults.get("timeout_seconds", DEFAULT_TIMEOUT))
        self._chat = {}
        for role, spec in config.models.items():
            api_key, base_url = config.endpoint(spec)
            self._chat[role] = PROVIDERS[spec.model_provider].chat_model(
                spec,
                api_key=api_key,
                base_url=base_url,
                timeout=timeout,
                max_output_tokens=spec.max_output_tokens or config.defaults.get("max_output_tokens"),
                http_client=http_client,
            )
            logger.info("  🧠 %s: %s (%s, %s)", role, spec.model, spec.model_provider, spec.api)
        #: (role, output type or ("tools", tool)) -> (runnable, text appended to
        #: the system prompt).
        self._wrapped: Dict[Tuple[str, Any], Tuple[Any, str]] = {}

    def with_extra_body(self, bodies: Mapping[str, Dict[str, Any]]) -> "ModelSelector":
        """A copy where each role in ``bodies`` sends that ``extra_body`` and no other setting.

        A client's ``<role>_extra_body``. It supersedes every request setting
        ``models.toml`` gives the role — temperature, the thinking switch,
        ``reasoning_effort``, the output cap and its own ``extra_body`` — so
        besides the model and the messages, what the client sent is all that
        goes out. The endpoint, the provider and how a reply is asked for stay.
        The new chat models share the originals' HTTP clients. Returns ``self``
        when there is nothing to change.
        """
        if not bodies:
            return self
        cleared = {"temperature": None, "reasoning_effort": None, "max_tokens": None}
        new = copy.copy(self)
        new._chat = {**self._chat, **{
            role: self._chat[role].model_copy(update={**cleared, "extra_body": dict(body)})
            for role, body in bodies.items()
        }}
        new._wrapped = {key: value for key, value in self._wrapped.items() if key[0] not in bodies}
        return new

    def _wrapper(self, role: str, output_type: Any) -> Tuple[Any, str]:
        """The runnable that answers ``output_type`` on ``role``, built once.

        A pydantic type goes through ``with_structured_output`` with the
        role's ``structured_output`` method. ``json_mode`` puts no schema in
        the request, so LangChain's format instructions carry it in the
        system prompt instead. Any other type is the plain chat model: a JSON
        ``list`` or ``dict`` is asked for by the prompt, and parsed here.
        """
        key = (role, output_type)
        if key not in self._wrapped:
            chat, method = self._chat[role], self.specs[role].structured_output
            if isinstance(output_type, type) and issubclass(output_type, BaseModel):
                instructions = ""
                if method == "json_mode":
                    parser = PydanticOutputParser(pydantic_object=output_type)
                    instructions = "\n\n" + parser.get_format_instructions()
                runnable = chat.with_structured_output(output_type, method=method, include_raw=True)
                self._wrapped[key] = (runnable, instructions)
            else:
                self._wrapped[key] = (chat, "")
        return self._wrapped[key]

    def call_llm(
        self,
        model_name: str,
        output_type: Type[T],
        system_prompt: str,
        messages: List[Dict[str, str]],
        *,
        usage: Optional[Usage] = None,
    ) -> T:
        """Ask ``model_name`` and return its reply as ``output_type``.

        ``messages`` are ``{"role", "content"}`` dicts; an empty
        ``system_prompt`` sends no system message. Raises
        :class:`ModelOutputError` after :data:`ATTEMPTS` unusable replies;
        provider errors propagate unchanged.
        """
        runnable, instructions = self._wrapper(model_name, output_type)
        return self._ask(model_name, runnable, output_type, system_prompt + instructions,
                         messages, usage, tools=False)

    def call_tools(
        self,
        role: str,
        tool: Type[BaseModel],
        system_prompt: str,
        messages: List[Dict[str, str]],
        *,
        usage: Optional[Usage] = None,
    ) -> List[Dict[str, Any]]:
        """Ask ``role`` to answer with calls of ``tool``; their arguments, in order.

        ``tool`` is only a schema — its title is the tool's name, its
        docstring the description the model reads — and nothing here runs it:
        the caller applies the arguments. Several calls may come in one reply.
        A reply that calls nothing (an "OK") is ``[]``; an empty or cut-off
        one is retried as in :meth:`call_llm`.
        """
        key = (role, ("tools", tool))
        if key not in self._wrapped:
            bound = self._chat[role].bind_tools([tool], parallel_tool_calls=True)
            self._wrapped[key] = (bound, "")
        return self._ask(role, self._wrapped[key][0], list, system_prompt, messages, usage,
                         tools=True)

    def _ask(
        self, role: str, runnable: Any, output_type: Any, system: str,
        messages: List[Dict[str, str]], usage: Optional[Usage], *, tools: bool,
    ) -> Any:
        """The calls of one request: the first, and a correction per unusable reply."""
        usage = usage if usage is not None else Usage()
        history = ([{"role": "system", "content": system}] if system else []) + list(messages)
        for attempt in range(1, ATTEMPTS + 1):
            value, reply, problem = self._attempt(role, runnable, history, output_type, usage,
                                                  tools=tools)
            if problem is None:
                return value
            logger.warning(
                "  ✗ [%s] reply %d/%d rejected:\n%s\n    reply: %s",
                role, attempt, ATTEMPTS, problem, raw_excerpt(reply),
            )
            if reply:
                history.append({"role": "assistant", "content": reply})
            history.append({
                "role": "user",
                "content": f"Your previous reply was rejected:\n{problem}\n\n"
                "Reply again with the complete, corrected output only.",
            })
        raise ModelOutputError(
            f"{role}: no usable reply in {ATTEMPTS} attempts:\n{problem}",
            stage=current_stage(),
        )

    def _attempt(
        self, role: str, runnable: Any, history: List[Dict[str, str]], output_type: Any,
        usage: Usage, *, tools: bool,
    ) -> Tuple[Any, str, Optional[str]]:
        """One call: ``(value, reply, problem)``, ``problem`` ``None`` when ``value`` is usable."""
        chat = self._chat[role]
        if tools:
            asked = "tools"
        else:
            asked = "-" if runnable is chat else self.specs[role].structured_output
        logger.info(
            "  → [%s] %s | api=%s, structured_output=%s, max_output_tokens=%s, temperature=%s, "
            "reasoning_effort=%s, extra_body=%s, input=%d msgs/%d chars",
            role, chat.model_name, self.specs[role].api, asked,
            chat.max_tokens, chat.temperature, chat.reasoning_effort,
            chat.extra_body, len(history), sum(len(m["content"]) for m in history),
        )
        logger.debug("  → [%s] messages: %s", role, history)

        started = time.perf_counter()
        out = error = None
        try:
            out = runnable.invoke(history)
        except LengthFinishReasonError as e:  # .parse() on a reply the token cap cut off
            error = e
        except ValidationError as e:  # strict json_schema: the SDK validates inside the call
            error = e
        finally:
            seconds = time.perf_counter() - started
            with _LEDGER:
                usage.calls += 1
                usage.seconds += seconds

        raw = out["raw"] if isinstance(out, dict) else out
        if isinstance(error, LengthFinishReasonError):
            u = error.completion.usage
            thinking = getattr(u.completion_tokens_details, "reasoning_tokens", None) or 0
            tokens = (u.prompt_tokens, thinking, u.completion_tokens - thinking)
            finish = "length"
        elif raw is not None:
            meta = raw.usage_metadata or {}
            thinking = (meta.get("output_token_details") or {}).get("reasoning", 0)
            tokens = (meta.get("input_tokens", 0), thinking, meta.get("output_tokens", 0) - thinking)
            finish = raw.response_metadata.get("finish_reason") or raw.response_metadata.get("status")
        else:
            tokens, finish = (0, 0, 0), None
        with _LEDGER:
            usage.input_tokens += tokens[0]
            usage.thinking_tokens += tokens[1]
            usage.output_tokens += tokens[2]

        # The reply as text: its content, or the tool call(s) it made instead.
        calls = [call["args"] for call in raw.tool_calls] if raw is not None else []
        reply = ""
        if raw is not None:
            if calls:
                reply = json.dumps(calls if tools else calls[0])
            else:
                reply = raw.text
        logger.info(
            "  ⏱ [%s] %s %.1fs | input=%d, thinking=%d, output=%d, finish=%s, content=%d chars",
            role, chat.model_name, seconds, *tokens, finish, len(reply),
        )
        current_run().add_call(ModelCall(
            role=role, model=chat.model_name, seconds=seconds,
            input_tokens=tokens[0], thinking_tokens=tokens[1], output_tokens=tokens[2],
        ))
        logger.debug("  ↩ [%s] content: %s", role, reply)

        if isinstance(error, ValidationError):
            return None, "", _describe(error)
        if finish in ("length", "incomplete"):
            return None, "", "- it was cut off by the output token limit"
        if not reply.strip():
            return None, "", "- it was empty"
        if tools:
            return calls, reply, None
        if isinstance(out, dict):
            if out["parsing_error"] is not None:
                error = out["parsing_error"]
                return None, reply, _describe(error.__cause__ or error)
            if out["parsed"] is None:
                return None, reply, f"- it is not a {output_type.__name__}"
            return out["parsed"], reply, None
        if output_type is bool:
            # The first word: "**YES**" and "'Yes.'" are how "YES" often comes back.
            word = re.match(r"[^A-Za-z]*([A-Za-z]+)", reply)
            word = word.group(1).upper() if word else ""
            if word not in ("YES", "NO"):
                return None, reply, "- expected YES or NO"
            return word == "YES", reply, None
        if output_type in (list, dict):
            # LangChain's JSON output parser, as adapticv-ai used it: a fence
            # around the JSON, or text before the fence, is fine.
            try:
                value = parse_json_markdown(reply)
            except ValueError as e:
                return None, reply, _describe(e)
            if not isinstance(value, output_type):
                kind = "list" if output_type is list else "object"
                return None, reply, f"- it is not a JSON {kind}"
            return value, reply, None
        return reply, reply, None


def build_selector(resources_dir: Optional[Path] = None) -> ModelSelector:
    """The selector the server runs on: ``.env`` loaded, then every role built."""
    load_dotenv()
    return ModelSelector(load_model_config(resources_dir))
