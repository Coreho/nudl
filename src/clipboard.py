"""Clipboard read/write, with the retries the Win32 clipboard demands.

`OpenClipboard` takes a global, system-wide lock that any other process may be holding
at the moment we ask. Failing to open is *normal*, not exceptional — so every access
retries with backoff, and a genuine failure is reported rather than crashing the pump.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager

import win32clipboard
import win32con

logger = logging.getLogger(__name__)

_OPEN_ATTEMPTS = 10
_OPEN_BACKOFF_SECONDS = 0.02


class ClipboardBusy(RuntimeError):
    """Another process held the clipboard lock for longer than we were willing to wait."""


@contextmanager
def _opened() -> Iterator[None]:
    for attempt in range(_OPEN_ATTEMPTS):
        try:
            win32clipboard.OpenClipboard()
            break
        except Exception:  # noqa: BLE001 — pywin32 raises a bare win32 error
            time.sleep(_OPEN_BACKOFF_SECONDS * (attempt + 1))
    else:
        raise ClipboardBusy("clipboard stayed locked by another process")

    try:
        yield
    finally:
        try:
            win32clipboard.CloseClipboard()
        except Exception:  # noqa: BLE001 — nothing useful to do if close fails
            logger.debug("CloseClipboard failed", exc_info=True)


def sequence_number() -> int:
    """Bumped by Windows on every clipboard change — including nudl's own writes.

    This is how auto-watch (CP2) tells nudl's own write apart from the user's copy. A
    boolean "I am writing now" flag races; a sequence number cannot.
    """
    return win32clipboard.GetClipboardSequenceNumber()


def get_text() -> str | None:
    """The clipboard's Unicode text, or None if it holds something else (or nothing)."""
    with _opened():
        if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return None
        try:
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        except (TypeError, OSError):
            return None


def set_text(text: str) -> int:
    """Replace the clipboard with `text`. Returns the resulting sequence number."""
    with _opened():
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
    return sequence_number()


def as_single_url(text: str | None) -> str | None:
    """`text` if it is one bare http(s) URL and nothing else, otherwise None.

    This is the gate that keeps auto-watch from ever clobbering something the user
    wanted: a paragraph that merely *contains* a link, a code snippet, a file path —
    all rejected. Only a lone URL token is eligible for cleaning.
    """
    if not text:
        return None
    candidate = text.strip()
    if not candidate:
        return None
    if any(character.isspace() for character in candidate):
        return None  # a paragraph, not a link
    if not candidate.lower().startswith(("http://", "https://")):
        return None
    return candidate
