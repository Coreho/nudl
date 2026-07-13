"""The own-write guard is the most dangerous thing in auto-watch.

nudl writes the clipboard; Windows announces that write to every listener, including
nudl. Mistake the echo for a fresh copy and you get an infinite loop rewriting the
user's clipboard forever. These tests pin down every way that can go wrong.
"""

from __future__ import annotations

from src.clipboard import OwnWriteGuard

CLEAN = "https://example.com/a?id=42"
UGLY = "https://example.com/a?utm_source=x&id=42"


def test_a_write_we_made_is_recognised_by_sequence_number() -> None:
    guard = OwnWriteGuard()
    guard.arm(CLEAN)
    guard.confirm(100)
    assert guard.is_own_write(100, CLEAN) is True


def test_a_users_copy_is_not_ours() -> None:
    guard = OwnWriteGuard()
    guard.arm(CLEAN)
    guard.confirm(100)
    assert guard.is_own_write(101, UGLY) is False


def test_recognised_by_text_even_if_the_sequence_number_is_unknown() -> None:
    """The race that the text check exists to close.

    Undo is written from the Tk thread. The pump thread can process the resulting
    WM_CLIPBOARDUPDATE *before* `confirm()` records the sequence number. Arming with the
    text before the write is what makes that window safe.
    """
    guard = OwnWriteGuard()
    guard.arm(CLEAN)  # armed, but confirm() has not run yet
    assert guard.is_own_write(999, CLEAN) is True


def test_the_guard_is_one_shot() -> None:
    """Matching consumes it — otherwise Undo poisons the link forever.

    Undo restores the ugly original. That arms the guard with the ugly text. If the
    guard were sticky, copying that same ugly link again would look like nudl's own
    echo and auto-watch would ignore it — permanently un-cleanable.
    """
    guard = OwnWriteGuard()
    guard.arm(UGLY)  # this is what undo() does
    guard.confirm(100)

    assert guard.is_own_write(100, UGLY) is True  # the echo of the undo: ignore it
    assert guard.is_own_write(101, UGLY) is False  # user copies it again: CLEAN IT


def test_a_fresh_guard_owns_nothing() -> None:
    assert OwnWriteGuard().is_own_write(1, CLEAN) is False


def test_non_text_clipboard_content_is_not_ours() -> None:
    """An image or a file copied to the clipboard reads back as None."""
    guard = OwnWriteGuard()
    guard.arm(CLEAN)
    guard.confirm(100)
    assert guard.is_own_write(101, None) is False


def test_consecutive_writes_track_the_latest() -> None:
    guard = OwnWriteGuard()
    guard.arm("first")
    guard.confirm(100)
    guard.arm("second")
    guard.confirm(101)

    assert guard.is_own_write(100, "first") is False  # stale echo, no longer ours
    guard.arm("second")
    guard.confirm(101)
    assert guard.is_own_write(101, "second") is True


def test_a_FAILED_write_must_not_poison_the_guard() -> None:
    """The bug: arm, then the clipboard write fails. The guard stays armed.

    Later the user copies that exact link by hand. The guard matches, nudl thinks it's
    its own echo, and refuses to clean it — a link that can never be cleaned, because of
    a write that never happened. `disarm()` is what makes a failed write harmless.
    """
    guard = OwnWriteGuard()
    guard.arm(CLEAN)
    # ... clipboard.set_text() raises ClipboardBusy here; nothing reached the clipboard.
    guard.disarm()

    assert guard.is_own_write(500, CLEAN) is False, "a failed write poisoned the guard"


def test_confirm_after_a_match_does_not_re_arm() -> None:
    """The race that `confirm`'s early return closes.

    Undo writes from the Tk thread. The pump thread can process the echo — and match it
    by text — before `confirm()` records the sequence number. If `confirm()` then wrote
    that sequence number in anyway, the guard would be left armed with a stale number.
    """
    guard = OwnWriteGuard()
    guard.arm(CLEAN)
    assert guard.is_own_write(999, CLEAN) is True  # matched by text, guard consumed

    guard.confirm(999)  # the late confirm from the write that already echoed

    assert guard.is_own_write(999, CLEAN) is False, "confirm re-armed a consumed guard"


def test_rapid_copies_never_loop() -> None:
    """Simulate the copy -> clean -> echo cycle ten times over.

    Every echo must be absorbed, and every genuine user copy must get through. If the
    guard leaked even once, the echo would be treated as a copy and nudl would clean its
    own output — the infinite loop.
    """
    guard = OwnWriteGuard()
    sequence = 0
    cleaned = 0

    for i in range(10):
        # The user copies a dirty link.
        sequence += 1
        user_text = f"https://example.com/{i}?utm_source=x"
        assert guard.is_own_write(sequence, user_text) is False, "a user copy was swallowed"

        # nudl cleans it and writes back.
        cleaned_text = f"https://example.com/{i}"
        guard.arm(cleaned_text)
        sequence += 1
        guard.confirm(sequence)
        cleaned += 1

        # Windows echoes nudl's own write straight back at it.
        assert guard.is_own_write(sequence, cleaned_text) is True, "THE LOOP: echo not caught"

    assert cleaned == 10
