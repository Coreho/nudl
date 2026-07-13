"""Only one nudl per user session.

A second instance would add a second tray icon, fail to claim the already-held hotkey,
and — once auto-watch lands — run a second clipboard watcher that sees the first's
writes and cleans them again. Two processes taking turns rewriting the clipboard is the
silent mangling this product exists to prevent.
"""

from __future__ import annotations

import subprocess
import sys
import time

import win32api

from src.app import acquire_single_instance


def test_first_caller_gets_the_mutex() -> None:
    handle = acquire_single_instance()
    try:
        assert handle is not None
    finally:
        if handle is not None:
            win32api.CloseHandle(handle)


def test_second_caller_is_refused_while_the_first_holds_it() -> None:
    first = acquire_single_instance()
    assert first is not None
    try:
        assert acquire_single_instance() is None, "a second instance got in"
    finally:
        win32api.CloseHandle(first)


def test_the_mutex_is_released_when_the_holder_exits() -> None:
    """A crashed nudl must not lock the user out of ever starting it again.

    This is why it's a kernel mutex and not a PID file: Windows reclaims it when the
    process dies, however it dies.
    """
    first = acquire_single_instance()
    assert first is not None
    win32api.CloseHandle(first)  # simulate the process exiting

    second = acquire_single_instance()
    try:
        assert second is not None, "the mutex was not released"
    finally:
        if second is not None:
            win32api.CloseHandle(second)


def test_a_killed_process_releases_it() -> None:
    """Kill a real process holding the mutex; the next caller must still get in."""
    # The handle must be BOUND, not discarded: pywin32 closes a PyHANDLE on garbage
    # collection, so `CreateMutex(...)` without an assignment releases it immediately.
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import win32event, time;"
            " h = win32event.CreateMutex(None, False, 'nudl-single-instance-mutex');"
            " time.sleep(30)",
        ]
    )
    try:
        # Wait for the child to claim it, then confirm we are locked out.
        for _ in range(50):
            refused = acquire_single_instance()
            if refused is None:
                break
            win32api.CloseHandle(refused)
            time.sleep(0.1)
        else:
            raise AssertionError("the child never claimed the mutex")
    finally:
        holder.kill()
        holder.wait(timeout=5)

    # The holder is gone (killed, not cleanly exited). The mutex must be free.
    handle = None
    for _ in range(50):
        handle = acquire_single_instance()
        if handle is not None:
            break
        time.sleep(0.1)
    try:
        assert handle is not None, "a killed instance left the mutex locked forever"
    finally:
        if handle is not None:
            win32api.CloseHandle(handle)
