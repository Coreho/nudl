"""Clipboard read/write, with the retries the Win32 clipboard demands.

`OpenClipboard` takes a global, system-wide lock that any other process may be holding
at the moment we ask. Failing to open is *normal*, not exceptional — so every access
retries with backoff, and a genuine failure is reported rather than crashing the pump.

It does NOT, however, lock out other threads of the *same* process. A second thread in
this process can call `OpenClipboard` while the first is mid-session, and it succeeds —
and then its `CloseClipboard` closes the first thread's session, so the first thread's
next call dies with ERROR_CLIPBOARD_NOT_OPEN. Measured: three threads, sixty sessions
each, 147 failures out of 180.

nudl touches the clipboard from three threads — the message pump (cleaning), pystray
(`can_undo()` reads the clipboard on every menu render), and Tk (the toast's Undo button)
— so this is not theoretical. Right-clicking the tray while a clean is in flight was
enough to close the pump's session out from under it, fail the write, and drop the clean
on the floor with nothing but a log line to show for it. That looks, from the outside,
exactly like "nudl stopped stripping links".

Hence `_session`: Windows will not serialise nudl's own threads, so nudl does it.
"""

from __future__ import annotations

import contextlib
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

#: Held for the whole of every clipboard session — open, read/write, close — so that no
#: two of nudl's threads can ever have one in flight at the same time. Reentrant only as
#: cheap insurance; nothing nests today.
_session = threading.RLock()

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


class ClipboardChanged(RuntimeError):
    """The clipboard moved on between the read and the write. Nothing was written."""


@contextmanager
def _opened(hwnd: int = 0) -> Iterator[None]:
    # `_session` first, and held across the whole block: see the module docstring. Another
    # PROCESS holding the clipboard is what the retry loop is for. Another THREAD of ours
    # holding it is what this lock is for, and Windows will not tell them apart.
    with _session:
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


def set_text(text: str, hwnd: int = 0, expect_sequence: int | None = None) -> int:
    """Replace the clipboard with `text`. Returns the resulting sequence number.

    `expect_sequence` makes this a compare-and-swap, and undo depends on it. Undo reads
    the clipboard, decides the cleaned link is still there, and only then writes the
    original back — and in the gap between those two steps the user can copy something
    they care about, which a blind write would destroy. That is the one failure nudl is
    not allowed to have.

    The check has to happen *inside* `_opened`, and that is the whole trick: while we
    hold the clipboard open, Windows lets no other process open it, so nothing can slip
    in between the comparison and the write. Checking the sequence number before calling
    this would just be the same race with extra steps.

    The write is deliberately NOT marked `CanIncludeInClipboardHistory = 0` or
    `ExcludeClipboardContentFromMonitorProcessing`. Those are for passwords. The tracked
    link is already in Win+V history and in any clipboard manager — the app it was
    copied from put it there — so hiding nudl's version would leave the dirty copy as the
    only one the user can find again, and protect nothing.
    """
    # `_session` is taken out here as well as inside `_opened` (it is reentrant) so that
    # it is still held when the sequence number is read below, after the close. Released
    # in between, another of nudl's own threads could write first, and this would report
    # that write's number as ours.
    with _session:
        with _opened(hwnd):
            if expect_sequence is not None and sequence_number() != expect_sequence:
                raise ClipboardChanged("the clipboard changed between the read and the write")
            previous = _text_while_open()
            win32clipboard.EmptyClipboard()
            try:
                win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
            except Exception:
                # EmptyClipboard has already run. Put back what was there rather than
                # leave the user with nothing at all to paste.
                if previous is not None:
                    with contextlib.suppress(Exception):
                        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, previous)
                raise

        # Read the sequence number AFTER the clipboard is closed, and not a moment before.
        # Windows goes on bumping the counter through CloseClipboard — a write measured from
        # inside the open clipboard reports 2257 when the listener will be told 2260. Reading
        # it early is how the own-write guard ends up armed with a number that can never
        # match, which is exactly what it was doing: a "dual-signal" guard running on one
        # signal, with the text comparison quietly carrying auto-watch on its own.
        return sequence_number()


def _text_while_open() -> str | None:
    """The clipboard's text, for a caller that already holds it open."""
    try:
        if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
            return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
    except (TypeError, OSError):
        pass
    return None


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
    if not _user32.RemoveClipboardFormatListener(hwnd):
        # Not fatal — we are shutting down — but a silent failure here means Windows is
        # still posting WM_CLIPBOARDUPDATE to a window that no longer exists.
        logger.warning("RemoveClipboardFormatListener failed: %s", ctypes.WinError())


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
        self._generation: int = 0

    def arm(self, text: str) -> int:
        """Call immediately BEFORE writing `text` to the clipboard. Returns a token."""
        with self._lock:
            self._generation += 1
            self._text = text
            self._sequence = None
            return self._generation

    def disarm(self) -> None:
        """Release the armed state if the write was aborted or failed."""
        with self._lock:
            self._text = None
            self._sequence = None

    def confirm(self, token: int, sequence: int) -> None:
        """Call immediately AFTER the write, with the generation token and sequence."""
        with self._lock:
            if token != self._generation or self._text is None:
                return  # stale token or already consumed
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
