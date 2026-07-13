"""Fixtures for the tests that touch the REAL Windows clipboard.

Everything else in the suite drives a FakeClipboard. That is fast and deterministic, and
it is also how a test double quietly drifts out of agreement with the thing it doubles —
which has happened here already. These tests exist to catch that: they use no fakes.

Two obligations follow from touching the real clipboard:

  * Give it back. The clipboard belongs to the user, not to the test suite, and a test run
    that eats what they had copied is a test run that has done the exact thing this
    program promises never to do.
  * Do not run alongside a live nudl. A real nudl in auto mode is a second writer racing
    every one of these tests. Detect it and skip, rather than fail mysteriously.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator

import pytest

if sys.platform != "win32":  # pragma: no cover — collection is skipped off-Windows
    pytest.skip("requires the Win32 clipboard", allow_module_level=True)

import win32api  # noqa: E402
import win32event  # noqa: E402
import winerror  # noqa: E402

from src import clipboard  # noqa: E402
from src.app import SINGLE_INSTANCE_MUTEX  # noqa: E402


def _nudl_is_running() -> bool:
    handle = win32event.CreateMutex(None, False, SINGLE_INSTANCE_MUTEX)
    running = win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS
    if handle:
        win32api.CloseHandle(handle)
    return running


@pytest.fixture(autouse=True)
def _not_alongside_a_live_nudl() -> None:
    if _nudl_is_running():
        pytest.skip("a real nudl is running and would race these tests — close it first")


@pytest.fixture(autouse=True)
def preserve_the_users_clipboard() -> Iterator[None]:
    """Snapshot the clipboard, hand it back afterwards. It is not ours to keep."""
    try:
        before = clipboard.get_text()
    except clipboard.ClipboardBusy:
        pytest.skip("the clipboard is locked by another process")

    try:
        yield
    finally:
        if before is not None:
            # Best effort, and never mask the real failure: a test that already failed
            # should report why, not be buried under a clipboard error on the way out.
            with contextlib.suppress(Exception):
                clipboard.set_text(before)
