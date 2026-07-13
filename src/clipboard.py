"""Clipboard read/write, with the retries the Win32 clipboard demands.

`OpenClipboard` takes a global, system-wide lock that any other process may be holding
at the moment we ask. Failing to open is *normal*, not exceptional — so every access
retries with backoff, and a genuine failure is reported rather than crashing the pump.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import win32clipboard
import win32con

logger = logging.getLogger(__name__)

_OPEN_ATTEMPTS = 10
_OPEN_BACKOFF_SECONDS = 0.02

#: Sent to every listener window whenever the clipboard's contents change.
#: pywin32 exposes neither this constant nor the listener API, so we go to user32.
WM_CLIPBOARDUPDATE = 0x031D

_user32 = ctypes.windll.user32

# Configure types for robustness on 64-bit platforms
_user32.AddClipboardFormatListener.argtypes = [ctypes.wintypes.HWND]
_user32.AddClipboardFormatListener.restype = ctypes.wintypes.BOOL
_user32.RemoveClipboardFormatListener.argtypes = [ctypes.wintypes.HWND]
_user32.RemoveClipboardFormatListener.restype = ctypes.wintypes.BOOL


class ClipboardBusy(RuntimeError):
    """Another process held the clipboard lock for longer than we were willing to wait."""


@contextmanager
def _opened(hwnd: int = 0) -> Iterator[None]:
    for attempt in range(_OPEN_ATTEMPTS):
        try:
            win32clipboard.OpenClipboard(hwnd)
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
    if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
        return None
    with _opened():
        try:
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
        except (TypeError, OSError):
            return None


def get_text_and_sequence() -> tuple[str | None, int]:
    """Atomically retrieve the text and the sequence number while holding the lock."""
    if not win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
        return None, sequence_number()
    with _opened():
        seq = sequence_number()
        try:
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return text, seq
        except (TypeError, OSError):
            return None, seq


def set_text(text: str, hwnd: int = 0) -> int:
    """Replace the clipboard with `text`. Returns the resulting sequence number."""
    with _opened(hwnd):
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        return sequence_number()


def add_format_listener(hwnd: int) -> bool:
    """Ask Windows to post WM_CLIPBOARDUPDATE to `hwnd` on every clipboard change.

    Event-driven, so nudl is asleep when nothing is happening — no polling loop
    burning a timer 4x a second forever.
    """
    if not _user32.AddClipboardFormatListener(hwnd):
        logger.error("AddClipboardFormatListener failed: %s", ctypes.WinError())
        return False
    return True


def remove_format_listener(hwnd: int) -> None:
    _user32.RemoveClipboardFormatListener(hwnd)


class OwnWriteGuard:
    """Tells nudl's own clipboard writes apart from the user's copies.

    This is the single most dangerous thing in auto-watch. nudl writes to the
    clipboard; Windows announces that write to every listener — including nudl. Treat
    that echo as a fresh copy and you have an infinite loop rewriting the user's
    clipboard forever.

    Two independent signals, because either alone has a hole:

    * **The sequence number.** Windows bumps a global counter on every change. Record
      it right after our own write and the echo is identifiable. A boolean "I am
      writing now" flag cannot do this — it races.
    * **The text we wrote.** The sequence number is recorded *after* `SetClipboardData`
      returns, and Undo is written from the Tk thread while the pump thread is free to
      process the echo in between. Arming with the text *before* the write closes that
      window.

    The guard is **one-shot**: matching consumes it. Otherwise, after an Undo restored
    an ugly link, that link would stay permanently un-cleanable — copy it again and
    nudl would mistake it for its own echo and ignore it forever.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sequence: int | None = None
        self._text: str | None = None

    def arm(self, text: str) -> None:
        """Call immediately BEFORE writing `text` to the clipboard."""
        with self._lock:
            self._text = text

    def disarm(self) -> None:
        """Release the armed state if the write was aborted or failed."""
        with self._lock:
            self._text = None
            self._sequence = None

    def confirm(self, sequence: int) -> None:
        """Call immediately AFTER the write, with the resulting sequence number."""
        with self._lock:
            if self._text is None:
                return  # already matched and cleared by is_own_write
            self._sequence = sequence

    def is_own_write(self, sequence: int, text: str | None) -> bool:
        """True if this clipboard change was ours. Consumes the guard on a match."""
        with self._lock:
            own = (self._sequence is not None and sequence == self._sequence) or (
                self._text is not None and text is not None and text == self._text
            )
            if own:
                self._sequence = None
                self._text = None
            return own


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
