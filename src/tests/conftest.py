"""Let the pure-Python suite run anywhere.

`clean.py` — the entire product — has no Windows dependency and is testable on any
platform. The Win32 modules (clipboard, hotkey, the message window, and `app.py`, which
imports all three at module scope) obviously are not.

Without this, importing them on Linux raises at COLLECTION time and the whole suite dies,
including the corpus that actually matters. A contributor on a Mac could not run the
tests at all. Skipping is the right failure.

Two things this file used to get wrong, both of which made it a no-op:

  * `collect_ignore` entries resolve relative to THIS directory, and every Win32 test
    lives in `unit/`. Listing bare filenames pointed at `src/tests/test_hotkey.py`, which
    has never existed, so nothing was ever ignored.
  * The `pytest_collection_modifyitems` fallback ran *after* collection — and collection
    is precisely where the ImportError fires. A marker added to an item that could not be
    imported is a marker on nothing.

Hence: paths relative to this file, verified by `test_conftest_paths.py`, which fails
loudly if a listed file is moved or renamed. This file is only exercised off-Windows, so
without that test its next breakage would also be silent.
"""

from __future__ import annotations

import sys

# Anything that imports pywin32, directly or through `src.app`. The integration tests
# drive the real Win32 clipboard by definition, so all of them are on the list.
WINDOWS_ONLY = (
    "integration/test_real_app.py",
    "integration/test_real_clipboard.py",
    "unit/test_autostart.py",
    "unit/test_clipboard_filter.py",
    "unit/test_hotkey.py",
    "unit/test_log_format.py",
    "unit/test_own_write_guard.py",
    "unit/test_rules_management.py",
    "unit/test_security.py",
    "unit/test_single_instance.py",
    "unit/test_tray_icon.py",
    "unit/test_undo.py",
)

collect_ignore = [] if sys.platform == "win32" else list(WINDOWS_ONLY)
