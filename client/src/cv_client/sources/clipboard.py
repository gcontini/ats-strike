"""The clipboard as a source of job descriptions."""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Optional

import pyperclip

# pyperclip.paste() returns "" both for an empty clipboard and for a helper
# that never reached the display (it discards xclip's stderr), so the loop
# below cannot tell "nothing copied yet" from "there is no clipboard here":
# it waits forever for a change that can never arrive. Ask the helper itself,
# once, at startup. These are how the helpers say the display is unreachable;
# an *empty* clipboard fails differently ("target STRING not available",
# "No selection") and is not a problem.
UNREACHABLE_DISPLAY = ("open display", "wayland")

NO_CLIPBOARD_HINT = """  On Linux the clipboard needs a helper: apt install xclip (X11) or
  wl-clipboard (Wayland), and a reachable DISPLAY/WAYLAND_DISPLAY.
  Over SSH, forward X11 (ssh -X) or use `cv watch` instead, which
  needs no clipboard at all."""


def clipboard_error() -> Optional[str]:
    """Return why the clipboard cannot be read, or None when it can be.

    The helper and the order are pyperclip's own (``determine_clipboard``),
    so what is probed here is what ``pyperclip.paste()`` actually runs.
    """
    if os.name == "nt" or os.uname().sysname == "Darwin":
        return None  # both have a clipboard without a helper binary
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste"):
        probe = ["wl-paste", "--no-newline"]
    elif os.environ.get("DISPLAY") and shutil.which("xclip"):
        probe = ["xclip", "-selection", "c", "-o"]
    elif os.environ.get("DISPLAY") and shutil.which("xsel"):
        probe = ["xsel", "-b", "-o"]
    else:
        return (
            "no clipboard here: neither DISPLAY nor WAYLAND_DISPLAY names a "
            "display with a helper installed (xclip, xsel, wl-clipboard)"
        )

    try:
        done = subprocess.run(probe, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"{probe[0]} could not be run: {exc}"

    stderr = (done.stderr or "").strip()
    if done.returncode != 0 and any(
        phrase in stderr.lower() for phrase in UNREACHABLE_DISPLAY
    ):
        return f"{probe[0]} cannot reach the display: {stderr}"
    return None


class ClipboardSource:
    """The clipboard's text each time it changes — a
    :class:`~cv_client.stages.listener.JDSource`.

    Whatever is on the clipboard at startup counts as new. Whether the text
    is a posting at all is decided by the free check and then the server.
    """

    def __init__(self) -> None:
        self._seen = ""

    def poll(self) -> Optional[str]:
        current = (pyperclip.paste() or "").strip()
        if not current or current == self._seen:
            return None
        self._seen = current
        return current


__all__ = ["ClipboardSource", "clipboard_error", "NO_CLIPBOARD_HINT"]
