"""nudl ships an icon FILE, not a Pillow render. Pin everything that rests on that.

Pillow was 12.79 MB of the 44 MB download — a 7.52 MB AVIF decoder, 2.07 MB of FreeType,
and WebP and colour-management codecs — to draw a rounded rectangle and three lines. The
lines are now rendered once by `tools/make_icon.py` at build time, and `tray.py` hands
pystray an `_IcoBytes` wrapper around the resulting file.

That works because pystray's Win32 backend asks exactly two things of `Icon.icon`: that
it be truthy, and that it answer `.save(fp, format="ICO")` — see
`pystray._util.serialized_image`, which writes the result to a temp file and passes the
path to `LoadImageW`. Both are private details of a third-party library.

So these tests assert the contract against the REAL pystray. If an upgrade changes it,
this fails in the suite rather than in a user's system tray, where the only symptom is an
icon that silently never appears.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pystray._util import serialized_image

from src.tray import _ICO_PATH, _IcoBytes, _tray_icon

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_pystray_writes_our_bytes_through_untouched() -> None:
    """The contract that replaces Pillow: `.save()` must be all pystray needs."""
    payload = b"\x00\x00\x01\x00 not really an icon, but bytes are bytes"

    with serialized_image(_IcoBytes(payload), "ICO") as path:
        assert Path(path).read_bytes() == payload


def test_the_shipped_icon_survives_the_real_pystray_path() -> None:
    """End to end: the actual icon, through the actual serialisation pystray performs."""
    with serialized_image(_tray_icon(), "ICO") as path:
        assert Path(path).read_bytes() == _ICO_PATH.read_bytes()


def test_the_shim_is_truthy() -> None:
    """`Icon.visible = True` raises ValueError on a falsy icon, and the tray never shows.

    A `__len__` or `__bool__` added to `_IcoBytes` later would trip that, so pin it.
    """
    assert _IcoBytes(b"")
    assert _tray_icon()


def test_the_shipped_icon_is_a_multi_size_ico() -> None:
    """Multi-size is the point: Windows picks 16px for the tray instead of downsampling.

    A single-image .ico would still work and would still look worse, which is exactly the
    kind of regression that never gets noticed.
    """
    data = _ICO_PATH.read_bytes()

    assert data[:4] == b"\x00\x00\x01\x00", "not an ICO header — the build wrote garbage"
    assert int.from_bytes(data[4:6], "little") >= 5, "too few sizes to cover tray and taskbar"


def test_the_app_import_graph_stays_pillow_free() -> None:
    """The 12.79 MB comes straight back if anything reachable from the app imports PIL.

    PyInstaller bundles what it can reach by walking imports from `run_nudl.py`, so this
    is the guard on the download size itself — checked in a clean interpreter, because
    the test session has Pillow loaded for other reasons.
    """
    probe = "import src.app, sys; print(any(m.split('.')[0] == 'PIL' for m in sys.modules))"
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        check=False,
    )

    assert result.returncode == 0, f"the probe could not import src.app:\n{result.stderr}"
    assert result.stdout.strip() == "False", (
        "something reachable from src.app imports Pillow again — that is 12.79 MB back "
        "in the download. Render at build time in tools/make_icon.py instead."
    )
