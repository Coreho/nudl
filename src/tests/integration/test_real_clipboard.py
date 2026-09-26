"""The Win32 clipboard, for real. No FakeClipboard anywhere in this file.

`clipboard.py` is the module with the most Windows in it and, until now, the least direct
coverage: every test that exercised it did so through a double. That double turned out to
be lying — it kept a `set_text` signature the real one had stopped having, and four tests
went on passing against it. These tests have nothing to lie with.
"""

from __future__ import annotations

import time

import pytest

from src import clipboard

pytestmark = pytest.mark.windows

UGLY = "https://www.amazon.com/dp/B08X7QK2P?tag=aff-20&utm_source=nl&psc=1"
CLEAN = "https://www.amazon.com/dp/B08X7QK2P?psc=1"


def test_a_round_trip_returns_exactly_what_went_in() -> None:
    clipboard.set_text(UGLY)
    assert clipboard.get_text() == UGLY


def test_the_sequence_number_moves_on_every_write() -> None:
    """The whole own-write guard rests on this being true of the real Windows counter."""
    _, first = clipboard.get_text_and_sequence()
    clipboard.set_text("one")
    _, second = clipboard.get_text_and_sequence()
    clipboard.set_text("two")
    _, third = clipboard.get_text_and_sequence()

    assert first != second != third, "the clipboard sequence number did not move on a write"


def test_set_text_reports_the_sequence_a_LISTENER_will_see() -> None:
    """The bug that made the own-write guard a dual-signal guard running on one signal.

    `set_text` used to read the sequence number while the clipboard was still open, and
    Windows goes on bumping the counter through CloseClipboard: the write reported 2257
    when every listener was about to be told 2260. So `confirm(set_text(...))` armed the
    guard with a number that could never match, and the sequence half of the guard never
    fired once — the text comparison had been carrying auto-watch by itself.

    Nothing failed visibly, which is why three reviewers read past it. A real clipboard
    caught it on the first run.
    """
    produced = clipboard.set_text(UGLY)
    _, observed = clipboard.get_text_and_sequence()
    assert produced == observed, (
        "set_text reported a sequence number the listener will never see — the own-write "
        "guard is armed with a number that cannot match"
    )


def test_the_guard_matches_on_the_SEQUENCE_alone() -> None:
    """Prove the sequence signal works without leaning on the text comparison.

    If the sequence half is broken, this test is the only thing that says so: every other
    guard test passes on the text match alone, which is precisely how the bug hid.
    """
    guard = clipboard.OwnWriteGuard()
    token = guard.arm(CLEAN)
    guard.confirm(token, clipboard.set_text(CLEAN))

    _, sequence = clipboard.get_text_and_sequence()
    # Text deliberately withheld: only the sequence number can answer this.
    assert guard.is_own_write(sequence, None) is True, "the sequence signal is dead"


# -- the compare-and-swap that undo depends on ------------------------------------


def test_a_stale_sequence_refuses_the_write_and_changes_nothing() -> None:
    """The real bug this exists to prevent: undo overwriting what the user just copied.

    Undo reads the clipboard, decides its cleaned link is still there, and only then
    writes the original back. If the user copies something in that gap, a blind write
    destroys it. The sequence number is compared inside the clipboard lock, so the write
    is refused instead.
    """
    _, stale = clipboard.get_text_and_sequence()

    precious = "the thing the user copied and would like to keep"
    clipboard.set_text(precious)  # the user, getting in first

    with pytest.raises(clipboard.ClipboardChanged):
        clipboard.set_text("nudl stomping on it", expect_sequence=stale)

    assert clipboard.get_text() == precious, "the refused write changed the clipboard anyway"


def test_a_current_sequence_allows_the_write() -> None:
    clipboard.set_text(CLEAN)
    _, current = clipboard.get_text_and_sequence()

    clipboard.set_text(UGLY, expect_sequence=current)
    assert clipboard.get_text() == UGLY


# -- the own-write guard, against real writes and a real counter -------------------


def test_the_guard_recognises_a_real_write_of_ours() -> None:
    guard = clipboard.OwnWriteGuard()
    token = guard.arm(CLEAN)
    guard.confirm(token, clipboard.set_text(CLEAN))

    text, sequence = clipboard.get_text_and_sequence()
    assert guard.is_own_write(sequence, text) is True


def test_the_guard_does_not_claim_a_write_that_was_not_ours() -> None:
    """If this ever returned True, auto-watch would go silent and stop cleaning."""
    guard = clipboard.OwnWriteGuard()
    token = guard.arm(CLEAN)
    guard.confirm(token, clipboard.set_text(CLEAN))

    # The user copies something. Different text, different sequence number.
    clipboard.set_text("https://example.com/something-the-user-copied?utm_source=x")
    text, sequence = clipboard.get_text_and_sequence()

    assert guard.is_own_write(sequence, text) is False


def test_the_guard_is_one_shot() -> None:
    """A guard that stayed armed would swallow the next identical copy the user made."""
    guard = clipboard.OwnWriteGuard()
    token = guard.arm(CLEAN)
    guard.confirm(token, clipboard.set_text(CLEAN))

    text, sequence = clipboard.get_text_and_sequence()
    assert guard.is_own_write(sequence, text) is True
    assert guard.is_own_write(sequence, text) is False, "the guard fired twice"


# -- what the clipboard is allowed to contain --------------------------------------


def test_a_paragraph_on_the_real_clipboard_is_not_a_url() -> None:
    clipboard.set_text("Hey, take a look at https://example.com?utm_source=x when you can")
    assert clipboard.as_single_url(clipboard.get_text()) is None


def test_a_bare_url_with_whitespace_around_it_still_counts() -> None:
    clipboard.set_text(f"  {UGLY}\r\n")
    assert clipboard.as_single_url(clipboard.get_text()) == UGLY


def test_an_empty_clipboard_is_survivable() -> None:
    clipboard.set_text("")
    text = clipboard.get_text()
    assert clipboard.as_single_url(text) is None


def test_the_listener_registers_and_unregisters_against_a_real_window() -> None:
    from src.hidden_window import MessageWindow

    window = MessageWindow()
    try:
        assert clipboard.add_format_listener(window.hwnd) is True
        clipboard.remove_format_listener(window.hwnd)  # must not warn; hwnd is still live
    finally:
        window.close()


def test_writing_a_very_long_url_survives_the_round_trip() -> None:
    long_url = "https://example.com/?" + "&".join(f"a{i}=1" for i in range(500))
    clipboard.set_text(long_url)
    assert clipboard.get_text() == long_url


def test_unicode_survives_the_round_trip() -> None:
    """CF_UNICODETEXT, not CF_TEXT — a link with non-ASCII in it must come back intact."""
    url = "https://ru.wikipedia.org/wiki/Пример?utm_source=x"
    clipboard.set_text(url)
    assert clipboard.get_text() == url


def test_repeated_writes_do_not_drift() -> None:
    """Five clean/undo cycles worth of writes. The clipboard must hold exactly the last."""
    for i in range(5):
        clipboard.set_text(f"{UGLY}&n={i}")
    assert clipboard.get_text() == f"{UGLY}&n=4"
    time.sleep(0.05)
    assert clipboard.get_text() == f"{UGLY}&n=4", "the clipboard changed after we stopped writing"


# -- what other apps put there, and how nudl reads it ---------------------------------------


def _copy_like_a_password_manager(text: str, marker: str) -> None:
    import win32clipboard
    import win32con

    # Through nudl's own retrying open: another process holding the clipboard for a moment
    # is normal, and a bare OpenClipboard made this helper, not nudl, fail now and then.
    with clipboard._opened():
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        fmt = win32clipboard.RegisterClipboardFormat(marker)
        payload = b"\x00\x00\x00\x00" if marker == "CanIncludeInClipboardHistory" else b"\x01"
        win32clipboard.SetClipboardData(fmt, payload)


@pytest.mark.parametrize(
    "marker",
    [
        "ExcludeClipboardContentFromMonitorProcessing",
        "Clipboard Viewer Ignore",
        "CanIncludeInClipboardHistory",
    ],
)
def test_a_copy_marked_private_is_seen_as_private_and_not_read(marker: str) -> None:
    _copy_like_a_password_manager("hunter2 https://e.com/?utm_source=x", marker)
    snapshot = clipboard.read_snapshot(want_html=True)
    assert snapshot.private is True
    assert snapshot.text is None, "nudl read the text of a copy it was asked not to read"


def test_an_ordinary_copy_is_not_private() -> None:
    clipboard.set_text(UGLY)
    snapshot = clipboard.read_snapshot()
    assert snapshot.private is False
    assert snapshot.text == UGLY


def test_formatted_text_goes_out_and_comes_back_byte_for_byte() -> None:
    html = b"Version:0.9\r\nStartHTML:0000000097\r\nEndHTML:0000000140\r\n<html>x</html>"
    clipboard.set_text("x", html=html)
    snapshot = clipboard.read_snapshot(want_html=True)
    assert snapshot.text == "x"
    assert snapshot.html is not None and snapshot.html.rstrip(b"\x00") == html


def test_nudl_dash_dash_clipboard_cleans_the_real_clipboard(capsys, monkeypatch, tmp_path) -> None:
    from src import cli

    monkeypatch.setenv("APPDATA", str(tmp_path))  # never the developer's real config

    clipboard.set_text(f"see {UGLY} now")
    assert cli.main(["--clipboard"]) == 0
    assert clipboard.get_text() == f"see {CLEAN} now"
    assert "removed 2 trackers" in capsys.readouterr().err
