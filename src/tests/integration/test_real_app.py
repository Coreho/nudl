"""nudl end to end: a real message pump, a real clipboard listener, a real clipboard.

This is the layer that has never been tested. `_pump()` has never been executed by a test;
`add_format_listener` has never been called by one; the auto-watch feedback loop — nudl
writes the clipboard, Windows announces the write to nudl, nudl cleans it again, forever —
has only ever been reasoned about.

The load-bearing assertion in the auto-watch tests is not "the link got cleaned". It is
that the clipboard **settles**: the sequence number stops moving. A feedback loop would
still produce a correctly cleaned link — while spinning forever underneath it.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from src import clipboard
from src import config as config_module
from src.app import NudlApp

pytestmark = pytest.mark.windows

UGLY = "https://www.amazon.com/dp/B08X7QK2P?tag=aff-20&utm_source=nl&psc=1"
CLEAN = "https://www.amazon.com/dp/B08X7QK2P?psc=1"

# Deliberately obscure: the suite must not fight the user's real nudl, their IDE, or a
# chord anything else is likely to already own.
TEST_HOTKEY = "ctrl+alt+shift+f9"


class RecordingUI:
    """Stands in for the Tk overlay. The toasts are not what these tests are about."""

    def __init__(self) -> None:
        self.toasts: list[str] = []

    def show_toast(self, text: str, seconds: int = 3, **_: object) -> None:
        self.toasts.append(text)

    def stop(self) -> None:
        pass


def _settled(timeout: float = 2.0) -> tuple[str | None, int]:
    """Wait for the clipboard to stop changing, and report where it came to rest.

    A quiet clipboard reaches this in one pass. A clipboard with nudl chasing its own tail
    never does, which is the point.
    """
    deadline = time.monotonic() + timeout
    _, last = clipboard.get_text_and_sequence()
    stable_since = time.monotonic()

    while time.monotonic() < deadline:
        time.sleep(0.05)
        text, sequence = clipboard.get_text_and_sequence()
        if sequence != last:
            last = sequence
            stable_since = time.monotonic()
            continue
        if time.monotonic() - stable_since > 0.35:
            return text, sequence

    raise AssertionError(
        "the clipboard never settled — nudl is still writing to it, which is the "
        "auto-watch feedback loop"
    )


@pytest.fixture
def nudl(tmp_path, monkeypatch: pytest.MonkeyPatch) -> Iterator[NudlApp]:
    """A real NudlApp with a real pump thread. Config and log land in tmp_path."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    config_module.save({**config_module.DEFAULTS, "hotkey": TEST_HOTKEY})

    app = NudlApp()
    app.ui = RecordingUI()  # the only substitution in this file

    thread = threading.Thread(target=app._pump, name="nudl-pump-test", daemon=True)
    thread.start()

    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if app.window is not None and app.window.hwnd:
            break
        time.sleep(0.02)
    else:
        pytest.fail("the pump thread never brought up its window")

    try:
        yield app
    finally:
        app._shutdown()
        thread.join(timeout=3)
        assert not thread.is_alive(), "the pump thread did not exit on shutdown"


# -- hotkey mode --------------------------------------------------------------------


def test_the_hotkey_cleans_the_real_clipboard(nudl: NudlApp) -> None:
    clipboard.set_text(UGLY)
    nudl.on_hotkey()

    assert clipboard.get_text() == CLEAN


def test_undo_puts_the_real_original_back(nudl: NudlApp) -> None:
    clipboard.set_text(UGLY)
    nudl.on_hotkey()
    assert clipboard.get_text() == CLEAN

    assert nudl.undo() is True
    assert clipboard.get_text() == UGLY, "undo did not restore the exact original"


def test_undo_refuses_once_the_user_has_copied_something_else(nudl: NudlApp) -> None:
    """The clipboard-eating bug, against the real clipboard.

    Clean a link, then copy something you care about, then undo. The original URL must NOT
    come back over the top of it. A missed tracker is a bad day; this would be a data loss.
    """
    clipboard.set_text(UGLY)
    nudl.on_hotkey()

    precious = "the thing the user copied and would like to keep"
    clipboard.set_text(precious)

    assert nudl.undo() is False
    assert clipboard.get_text() == precious, "undo ate what the user had copied"


def test_a_clean_link_is_left_completely_alone(nudl: NudlApp) -> None:
    """A no-op must be silent AND must not touch the clipboard at all."""
    clipboard.set_text(CLEAN)
    _, before = clipboard.get_text_and_sequence()

    nudl.on_hotkey()

    text, after = clipboard.get_text_and_sequence()
    assert text == CLEAN
    assert after == before, "nudl rewrote the clipboard with an identical value"


def test_the_hotkey_cleans_the_link_inside_a_paragraph_and_nothing_else(nudl: NudlApp) -> None:
    """Pressing the hotkey is asking, so a whole copied message gets its links cleaned —
    and every other character of it comes back exactly as it was."""
    prose = "Look at https://example.com/?utm_source=x when you get a chance.\r\n  Thanks!"
    clipboard.set_text(prose)

    nudl.on_hotkey()

    assert clipboard.get_text() == (
        "Look at https://example.com/ when you get a chance.\r\n  Thanks!"
    )


def test_the_hotkey_leaves_a_paragraph_with_nothing_to_clean_alone(nudl: NudlApp) -> None:
    prose = "Look at https://example.com/?id=2 when you get a chance"
    clipboard.set_text(prose)
    _, before = clipboard.get_text_and_sequence()

    nudl.on_hotkey()

    assert clipboard.get_text() == prose
    assert clipboard.get_text_and_sequence()[1] == before, "rewrote a clipboard it didn't change"


# -- auto-watch: the real listener, the real loop ------------------------------------


def test_auto_watch_cleans_a_copied_link_and_the_clipboard_SETTLES(nudl: NudlApp) -> None:
    """The feedback loop, or the absence of one.

    nudl writes the cleaned link to the clipboard. Windows dutifully announces that write
    to every listener, nudl included. Without the own-write guard, nudl would see its own
    output as a fresh copy, clean it again, write again — forever.

    Note what is being asserted. "The link is clean" would pass even while nudl span in an
    infinite loop underneath it. That the clipboard STOPS MOVING is the real assertion.
    """
    nudl.config["mode"] = "auto"

    clipboard.set_text(UGLY)  # the user pressing Ctrl+C
    text, _ = _settled()

    assert text == CLEAN


def test_auto_watch_leaves_an_already_clean_link_alone(nudl: NudlApp) -> None:
    nudl.config["mode"] = "auto"

    clipboard.set_text(CLEAN)
    _, before = clipboard.get_text_and_sequence()
    text, after = _settled()

    assert text == CLEAN
    assert after == before, "nudl rewrote a link that needed no cleaning"


def test_auto_watch_survives_five_links_in_a_row(nudl: NudlApp) -> None:
    """The unreproduced report: "after the first link it stops stripping."

    If there is a state machine that latches after one clean — a guard that stays armed, a
    listener that falls off — this is the shape of test that catches it.
    """
    nudl.config["mode"] = "auto"

    for i in range(5):
        clipboard.set_text(f"https://www.amazon.com/dp/B0{i}?tag=aff-20&utm_source=nl&psc=1")
        text, _ = _settled()
        assert text == f"https://www.amazon.com/dp/B0{i}?psc=1", (
            f"link {i + 1} of 5 was not cleaned — nudl stopped stripping after "
            f"{i} link(s)"
        )


def test_auto_watch_then_undo_then_auto_watch_again(nudl: NudlApp) -> None:
    """Undo puts a DIRTY link back on a clipboard nudl is actively watching.

    This is the subtlest write in the program: without the own-write guard, auto-watch sees
    the restored ugly link, cleans it, and undo becomes impossible by construction. After
    that, nudl must still clean the next link the user copies.
    """
    nudl.config["mode"] = "auto"

    clipboard.set_text(UGLY)
    assert _settled()[0] == CLEAN

    assert nudl.undo() is True
    restored, _ = _settled()
    assert restored == UGLY, "auto-watch re-cleaned the link the user had just undone"

    # And nudl is not wedged: the next copy still gets cleaned.
    clipboard.set_text("https://www.etsy.com/listing/123?utm_campaign=x&ref=hp")
    assert _settled()[0] == "https://www.etsy.com/listing/123"


def test_a_paragraph_never_wakes_auto_watch(nudl: NudlApp) -> None:
    nudl.config["mode"] = "auto"

    prose = "Here you go: https://example.com/?utm_source=x — let me know"
    clipboard.set_text(prose)
    _, before = clipboard.get_text_and_sequence()
    text, after = _settled()

    assert text == prose
    assert after == before


def test_hotkey_mode_ignores_the_clipboard_entirely(nudl: NudlApp) -> None:
    """In hotkey mode the listener is registered but must do nothing on its own."""
    assert nudl.config["mode"] == "hotkey"

    clipboard.set_text(UGLY)
    text, _ = _settled()

    assert text == UGLY, "nudl cleaned the clipboard in hotkey mode, without being asked"


# -- the audit log, written by the real app ------------------------------------------


def test_the_clean_is_written_to_the_audit_log(nudl: NudlApp) -> None:
    clipboard.set_text(UGLY)
    nudl.on_hotkey()

    entry = config_module.log_path().read_text(encoding="utf-8")
    assert "removed 2 trackers: tag, utm_source" in entry
    assert UGLY in entry
    assert CLEAN in entry


def test_a_secret_never_reaches_the_real_log_file(nudl: NudlApp) -> None:
    """An OAuth code stays in the user's link and stays out of the file on disk."""
    with_code = "https://app.example.com/callback?code=SECRET-OAUTH-CODE&utm_source=nl"
    clipboard.set_text(with_code)
    nudl.on_hotkey()

    assert "SECRET-OAUTH-CODE" in clipboard.get_text(), "nudl stripped a param it must keep"

    entry = config_module.log_path().read_text(encoding="utf-8")
    assert "SECRET-OAUTH-CODE" not in entry, "the OAuth code was written to disk"
    assert "code=***" in entry
