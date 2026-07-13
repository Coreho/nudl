"""Let the pure-Python suite run anywhere.

`clean.py` — the entire product — has no Windows dependency and is testable on any
platform. The Win32 modules (clipboard, hotkey, the message window) obviously are not.

Without this, importing them on Linux raises at COLLECTION time and the whole suite
dies, including the corpus that actually matters. A contributor on a Mac could not run
the tests at all. Skipping is the right failure.
"""

from __future__ import annotations

import sys

import pytest

WINDOWS_ONLY = (
    "test_single_instance.py",
    "test_hotkey.py",
    "test_clipboard_filter.py",
    "test_own_write_guard.py",
    "test_log_format.py",
)

collect_ignore = [] if sys.platform == "win32" else list(WINDOWS_ONLY)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if sys.platform == "win32":
        return
    skip = pytest.mark.skip(reason="requires Win32")
    for item in items:
        if any(name in str(item.fspath) for name in WINDOWS_ONLY):
            item.add_marker(skip)
