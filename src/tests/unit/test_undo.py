"""Undo, from all three entry points: the hotkey, the toast button, the tray.

The dangerous case is not "undo fails". It's "undo succeeds when it shouldn't" — firing
after the user has copied something else, and silently destroying it. An undo that eats
your clipboard is a worse bug than the one it was undoing.
"""

from __future__ import annotations

import time

import pytest

from src import app as app_module
from src.app import LastClean, NudlApp

UGLY = "https://example.com/a?utm_source=x&id=1"
CLEAN = "https://example.com/a?id=1"


class FakeClipboard:
    """Stands in for the Win32 clipboard."""

    ClipboardBusy = app_module.clipboard.ClipboardBusy
    ClipboardChanged = app_module.clipboard.ClipboardChanged
    Snapshot = app_module.clipboard.Snapshot
    # Mirror the real constant (0x031D). A double that quietly disagrees with the thing it
    # doubles is a trap primed for whoever first drives `_pump()` through this fake.
    WM_CLIPBOARDUPDATE = app_module.clipboard.WM_CLIPBOARDUPDATE

    def __init__(self, text: str | None = None) -> None:
        self.text = text
        self.sequence = 1
        self.busy = False
        self.busy_on_write = False  # a clipboard that reads fine but will not be written

    def get_text(self) -> str | None:
        if self.busy:
            raise self.ClipboardBusy("locked")
        return self.text

    def set_text(
        self, text: str, expect_sequence: int | None = None, html: bytes | None = None
    ) -> int:
        if self.busy or self.busy_on_write:
            raise self.ClipboardBusy("locked")
        if expect_sequence is not None and expect_sequence != self.sequence:
            raise self.ClipboardChanged("the clipboard changed under us")
        self.text = text
        self.html = html
        self.sequence += 1
        return self.sequence

    def get_text_and_sequence(self) -> tuple[str | None, int]:
        return self.get_text(), self.sequence

    def read_snapshot(self, *, want_html: bool = False) -> Snapshot:
        return self.Snapshot(
            text=self.get_text(),
            sequence=self.sequence,
            html=self.html if want_html else None,
        )

    def source_app(self) -> str | None:
        return None

    html: bytes | None = None

    def copy_as_the_user(self, text: str) -> None:
        """What happens when a human presses Ctrl+C: new text, and the sequence moves."""
        self.text = text
        self.sequence += 1

    OwnWriteGuard = app_module.clipboard.OwnWriteGuard
    as_single_url = staticmethod(app_module.clipboard.as_single_url)


@pytest.fixture
def nudl(monkeypatch: pytest.MonkeyPatch, tmp_path) -> tuple[NudlApp, FakeClipboard]:
    monkeypatch.setenv("APPDATA", str(tmp_path))
    fake = FakeClipboard()
    monkeypatch.setattr(app_module, "clipboard", fake)

    instance = NudlApp()
    monkeypatch.setattr(instance.ui, "show_toast", lambda *a, **k: None)
    return instance, fake


def _pretend_we_cleaned(instance: NudlApp, fake: FakeClipboard, *, age: float = 0.0) -> None:
    fake.text = CLEAN
    instance._last_clean = LastClean(UGLY, CLEAN, time.monotonic() - age)


def test_undo_restores_the_exact_original(nudl) -> None:
    instance, fake = nudl
    _pretend_we_cleaned(instance, fake)

    assert instance.undo() is True
    assert fake.text == UGLY


def test_undo_REFUSES_if_the_user_has_copied_something_else(nudl) -> None:
    """The bug that would eat your clipboard.

    You clean a link, then copy a paragraph you actually care about, then hit Undo on a
    toast that is still lingering. A naive undo overwrites your paragraph with an old URL.
    """
    instance, fake = nudl
    _pretend_we_cleaned(instance, fake)
    fake.text = "something the user copied afterwards and cares about"

    assert instance.undo() is False
    assert fake.text == "something the user copied afterwards and cares about"


def test_undo_with_nothing_to_undo_is_a_no_op(nudl) -> None:
    instance, fake = nudl
    fake.text = "whatever"
    assert instance.undo() is False
    assert fake.text == "whatever"


def test_undo_is_not_repeatable(nudl) -> None:
    """Undoing twice must not put the cleaned link back."""
    instance, fake = nudl
    _pretend_we_cleaned(instance, fake)

    assert instance.undo() is True
    assert instance.undo() is False
    assert fake.text == UGLY


def test_a_busy_clipboard_does_not_lose_the_original(nudl) -> None:
    instance, fake = nudl
    _pretend_we_cleaned(instance, fake)
    fake.busy = True

    assert instance.undo() is False

    fake.busy = False
    assert instance.undo() is True, "a transient busy clipboard threw the undo away"
    assert fake.text == UGLY


def test_a_busy_WRITE_does_not_lose_the_original(nudl) -> None:
    """The read succeeds, the write fails. The undo must survive to be retried.

    This is the half the busy-clipboard test above never reached, and it was broken:
    `_last_clean` was cleared *before* the write, so a write that failed left the user
    told "could not undo" and permanently unable to try again — the original gone for
    good. Losing the thing undo exists to protect, in the failure branch of undo itself.
    """
    instance, fake = nudl
    _pretend_we_cleaned(instance, fake)
    fake.busy_on_write = True

    assert instance.undo() is False
    assert fake.text == CLEAN, "a failed write must not have changed anything"
    assert instance.can_undo() is True, "the undo was thrown away on a failed write"

    fake.busy_on_write = False
    assert instance.undo() is True
    assert fake.text == UGLY


def test_undo_does_not_clobber_something_copied_mid_undo(nudl, monkeypatch) -> None:
    """The TOCTOU: undo checks the clipboard, then writes. What lands in the gap?

    Undo reads the clipboard, sees its own cleaned link, and commits to restoring the
    original. If the user copies something in the microseconds before the write lands, a
    blind write destroys it. nudl passes the sequence number it read down into the write
    and Windows refuses the swap if the clipboard moved — so the user's copy survives,
    which is the one thing nudl may never get wrong.
    """
    instance, fake = nudl
    _pretend_we_cleaned(instance, fake)

    precious = "the thing the user copied and would like to keep"
    real_get = fake.get_text_and_sequence

    def copy_something_right_after_the_check() -> tuple[str | None, int]:
        got = real_get()
        fake.copy_as_the_user(precious)  # the race, made deterministic
        return got

    monkeypatch.setattr(fake, "get_text_and_sequence", copy_something_right_after_the_check)

    assert instance.undo() is False
    assert fake.text == precious, "undo overwrote what the user copied mid-undo"


# -- the tray entry's enabled state ------------------------------------------------


def test_can_undo_is_false_before_anything_is_cleaned(nudl) -> None:
    instance, _ = nudl
    assert instance.can_undo() is False


def test_can_undo_is_true_right_after_a_clean(nudl) -> None:
    instance, fake = nudl
    _pretend_we_cleaned(instance, fake)
    assert instance.can_undo() is True


def test_can_undo_ignores_the_clock(nudl) -> None:
    """The 3s limit belongs to the HOTKEY, which is ambiguous. The tray entry is not."""
    instance, fake = nudl
    _pretend_we_cleaned(instance, fake, age=600.0)
    assert instance.can_undo() is True, "the tray entry expired, but it has no clock"


def test_can_undo_goes_false_once_the_clipboard_moves_on(nudl) -> None:
    instance, fake = nudl
    _pretend_we_cleaned(instance, fake)
    fake.text = "user copied something else"
    assert instance.can_undo() is False


# -- the hotkey's time window ------------------------------------------------------


def test_the_hotkey_undoes_within_the_window(nudl) -> None:
    instance, fake = nudl
    _pretend_we_cleaned(instance, fake, age=1.0)
    assert instance._undo_if_pending() is True
    assert fake.text == UGLY


def test_the_hotkey_does_NOT_undo_once_the_window_lapses(nudl) -> None:
    """Past the window, pressing the hotkey means 'clean', not 'undo'."""
    instance, fake = nudl
    _pretend_we_cleaned(instance, fake, age=app_module.UNDO_WINDOW_SECONDS + 1)
    assert instance._undo_if_pending() is False
    assert fake.text == CLEAN
