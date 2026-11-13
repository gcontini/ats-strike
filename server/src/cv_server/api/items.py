"""Things to store: what the pipeline hands over, as the wire carries it.

A step or the CV comes from the pipeline's sink; a log line from the run's
log; the last line of every reply that paid for a model call adds them up.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from cv_contracts import StoreItem


def output_item(output: Any) -> StoreItem:
    """A pipeline output (a step, the CV) as a thing to store."""
    return StoreItem.from_content(output.location, output.type, output.name, output.content)


def _line(ts: datetime, level: str, stage: str, message: str) -> StoreItem:
    return StoreItem(
        location="CV", type="log",
        text=f"{ts.isoformat(timespec='seconds')} {level:<7} [{stage}] {message}",
    )


def log_item(entry: Any) -> StoreItem:
    """One line of a run's log as a thing to store."""
    return _line(entry.ts, entry.level, entry.stage, entry.message)


def total_item(total: Any) -> StoreItem:
    """The run's model calls added up: the last log line of a reply."""
    return _line(
        datetime.now(timezone.utc), "INFO", "run",
        f"Σ {total.calls} model call(s), {total.seconds:.1f}s | input={total.input_tokens}, "
        f"thinking={total.thinking_tokens}, output={total.output_tokens}",
    )


def dump(item: StoreItem) -> dict:
    """An item as an event's data: the absent half of text/base64 left out."""
    return item.model_dump(mode="json", exclude_none=True)


__all__ = ["output_item", "log_item", "total_item", "dump"]
