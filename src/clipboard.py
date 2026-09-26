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
from dataclasses import dataclass
from functools import cache
from pathlib import Path

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

#: The clipboard format browsers, Office and chat apps put formatted text in. Links in it
#: live in `href`s, often behind link text, where the plain-text copy never shows them.
HTML_FORMAT = "HTML Format"

#: How an app says "clipboard tools, keep out" — password managers, mostly. The first is
#: Microsoft's documented flag; the second is the older convention KeePass and friends
#: use. Either one means nudl does not so much as read what was copied.
_PRIVATE_MARKERS = ("ExcludeClipboardContentFromMonitorProcessing", "Clipboard Viewer Ignore")
#: A DWORD 0 here keeps content out of Win+V history. Password managers set it on every
#: secret they copy, so nudl treats it as the same request.
_HISTORY_FLAG = "CanIncludeInClipboardHistory"


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


@cache
def _format(name: str) -> int:
    """The id Windows assigns a named clipboard format — the same for every process."""
    return win32clipboard.RegisterClipboardFormat(name)


@dataclass(frozen=True)
class Snapshot:
    """One read of the clipboard: everything nudl needs to decide, taken under one lock."""

    text: str | None
    sequence: int
    #: The raw "HTML Format" bytes, when asked for and present.
    html: bytes | None = None
    #: The app that copied this asked clipboard tools to keep out. Nothing else was read.
    private: bool = False


def read_snapshot(*, want_html: bool = False) -> Snapshot:
    """Read the clipboard once — unless the copying app marked it private.

    The private check comes FIRST, before a single byte of content is read: a password
    manager asking tools to stay out means exactly that, and "nudl looked, saw it wasn't a
    link, and ignored it" is not the same as not looking.
    """
    with _opened():
        sequence = sequence_number()
        if _marked_private():
            return Snapshot(text=None, sequence=sequence, private=True)
        text = _text_while_open()
        html = _data_while_open(_format(HTML_FORMAT)) if want_html else None
    return Snapshot(text=text, sequence=sequence, html=html)


def _marked_private() -> bool:
    """For a caller that already holds the clipboard open."""
    if any(win32clipboard.IsClipboardFormatAvailable(_format(n)) for n in _PRIVATE_MARKERS):
        return True
    flag = _data_while_open(_format(_HISTORY_FLAG))
    return flag is not None and len(flag) >= 4 and int.from_bytes(flag[:4], "little") == 0


def _data_while_open(fmt: int) -> bytes | None:
    try:
        if win32clipboard.IsClipboardFormatAvailable(fmt):
            data = win32clipboard.GetClipboardData(fmt)
            return data if isinstance(data, bytes) else None
    except (TypeError, OSError):
        pass
    return None


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


def set_text(
    text: str, hwnd: int = 0, expect_sequence: int | None = None, html: bytes | None = None
) -> int:
    """Replace the clipboard with `text` (and `html`, if given). Returns the new sequence.

    Every other format on the clipboard is dropped, as it is by any app that copies. When
    nudl cleans formatted text it passes the cleaned HTML back in, so the formatting — and
    the links behind it, cleaned — survive the round trip.

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
                if html is not None:
                    win32clipboard.SetClipboardData(_format(HTML_FORMAT), html)
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


# -- which app copied this ---------------------------------------------------------------

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_kernel32 = ctypes.windll.kernel32
_user32.GetClipboardOwner.restype = ctypes.wintypes.HWND
_user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = [
    ctypes.wintypes.HWND,
    ctypes.POINTER(ctypes.wintypes.DWORD),
]
_kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [
    ctypes.wintypes.DWORD,
    ctypes.wintypes.BOOL,
    ctypes.wintypes.DWORD,
]
_kernel32.QueryFullProcessImageNameW.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.wintypes.DWORD,
    ctypes.wintypes.LPWSTR,
    ctypes.POINTER(ctypes.wintypes.DWORD),
]
_kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]


def source_app() -> str | None:
    """The exe name ("Code.exe") of the app the current clipboard content came from.

    The clipboard's owner window when there is one; otherwise the foreground window,
    which at the moment of a copy is almost always the app that copied. None when
    Windows won't say — and then nothing is skipped, because nudl cannot tell.
    """
    hwnd = _user32.GetClipboardOwner() or _user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = ctypes.wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return None
    handle = _kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return None
    try:
        size = ctypes.wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not _kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return Path(buffer.value).name
    finally:
        _kernel32.CloseHandle(handle)


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
