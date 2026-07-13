"""The skip list in conftest.py must actually name real files.

This exists because it did not. `collect_ignore` listed bare filenames while every Win32
test lives in `unit/`, so it pointed at paths that have never existed and quietly ignored
nothing at all. Nobody noticed, and nobody could have: the skip list only does anything
off-Windows, and nudl is developed on Windows. A guard that is only load-bearing on a
machine you never run is a guard that needs a test on the machine you do.
"""

from __future__ import annotations

from pathlib import Path

from src.tests import conftest

TESTS_DIR = Path(conftest.__file__).parent
UNIT_DIR = TESTS_DIR / "unit"

# Importing any of these pulls in pywin32, so a test module that touches one cannot even
# be collected on Linux. `src.app` is on the list because it imports the other four.
WIN32_IMPORTS = (
    "src.app",
    "src.clipboard",
    "src.hidden_window",
    "src.hotkey",
    "src.toast",
    "src.tray",
    "win32",
    "pywintypes",
)


def test_every_skipped_path_exists() -> None:
    for relative in conftest.WINDOWS_ONLY:
        assert (TESTS_DIR / relative).is_file(), (
            f"conftest skips {relative!r}, which does not exist — the skip list is a no-op "
            f"and the suite will die at collection off-Windows"
        )


def test_every_win32_test_module_is_skipped() -> None:
    """The list must not go stale. A new Win32 test that nobody adds here breaks Linux."""
    for module in sorted(UNIT_DIR.glob("test_*.py")):
        if module.name == Path(__file__).name:
            continue  # this file names the Win32 modules in order to look for them
        source = module.read_text(encoding="utf-8")
        needs_windows = any(name in source for name in WIN32_IMPORTS)
        listed = f"unit/{module.name}" in conftest.WINDOWS_ONLY

        if needs_windows and not listed:
            raise AssertionError(
                f"{module.name} imports Win32 but is missing from conftest.WINDOWS_ONLY — "
                f"collection will fail off-Windows"
            )
        if listed and not needs_windows:
            raise AssertionError(
                f"{module.name} is skipped off-Windows but does not need Win32 — "
                f"it should be running there"
            )
