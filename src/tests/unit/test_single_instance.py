"""Only one nudl per user session.

A second instance would add a second tray icon, burn another ~50 MB of RAM and a pile of
threads, fail to claim the already-held hotkey, and — worst — run a second clipboard
watcher that sees the first's writes and cleans them again. Two processes taking turns
rewriting the clipboard is the silent mangling this product exists to prevent.

These tests use their OWN mutex name, not the production one. Sharing it would make the
suite fail whenever nudl is genuinely running on the machine — a test that breaks
because the product works is worse than no test at all.
"""

from __future__ import annotations

import subprocess
import sys
import time

import win32api

from src.app import SINGLE_INSTANCE_MUTEX, acquire_single_instance

MUTEX = "nudl-test-mutex-do-not-use-in-production"


def test_the_production_name_is_not_what_the_tests_claim() -> None:
    """Guards the isolation above: if these ever converge, the suite gets flaky."""
    assert MUTEX != SINGLE_INSTANCE_MUTEX


def test_first_caller_gets_the_mutex() -> None:
    handle = acquire_single_instance(MUTEX)
    try:
        assert handle is not None
    finally:
        if handle is not None:
            win32api.CloseHandle(handle)


def test_second_caller_is_refused_while_the_first_holds_it() -> None:
    first = acquire_single_instance(MUTEX)
    assert first is not None
    try:
        assert acquire_single_instance(MUTEX) is None, "a second instance got in"
    finally:
        win32api.CloseHandle(first)


def test_the_mutex_is_released_when_the_holder_exits() -> None:
    first = acquire_single_instance(MUTEX)
    assert first is not None
    win32api.CloseHandle(first)  # simulate the process exiting

    second = acquire_single_instance(MUTEX)
    try:
        assert second is not None, "the mutex was not released"
    finally:
        if second is not None:
            win32api.CloseHandle(second)


def test_a_killed_process_releases_it() -> None:
    """A crashed nudl must not lock the user out of ever starting it again.

    This is why it's a kernel mutex and not a PID file: Windows reclaims it when the
    process dies, however it dies. A stale PID file would strand the user forever.
    """
    # The handle must be BOUND, not discarded: pywin32 closes a PyHANDLE on garbage
    # collection, so `CreateMutex(...)` without an assignment releases it immediately.
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import win32event, time;"
            f" h = win32event.CreateMutex(None, False, {MUTEX!r});"
            f" time.sleep(30)",
        ]
    )
    try:
        for _ in range(50):
            refused = acquire_single_instance(MUTEX)
            if refused is None:
                break
            win32api.CloseHandle(refused)
            time.sleep(0.1)
        else:
            raise AssertionError("the child never claimed the mutex")
    finally:
        holder.kill()  # killed outright — no chance to clean up after itself
        holder.wait(timeout=5)

    handle = None
    for _ in range(50):
        handle = acquire_single_instance(MUTEX)
        if handle is not None:
            break
        time.sleep(0.1)
    try:
        assert handle is not None, "a killed instance left the mutex locked forever"
    finally:
        if handle is not None:
            win32api.CloseHandle(handle)
