"""The tray icon and its menu.

The icon is always visible on purpose. A clipboard-reading tool that hides itself is
exactly what a user should be suspicious of; nudl is not hiding.

`pystray.Icon.run()` owns the main thread (see `app.py` for the threading layout).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

import pystray

logger = logging.getLogger(__name__)

#: Rendered by `tools/make_icon.py` at build time and shipped beside this module.
_ICO_PATH = Path(__file__).with_name("nudl.ico")


class _IcoBytes:
    """The pre-rendered icon, shaped like the one thing pystray asks of an icon.

    pystray's Win32 backend does exactly two things with `Icon.icon`: it tests it for
    truthiness, and it calls `.save(fp, format="ICO")` — see `pystray._util.
    serialized_image`, which writes that to a temp file and hands the path to
    `LoadImageW`. It never needs a real `PIL.Image`. Satisfying that single method is
    what lets nudl ship the icon Pillow already rendered at build time and leave
    Pillow's 12.8 MB of codecs out of the download entirely.

    This leans on a private pystray detail, so `test_tray_icon.py` pins the contract
    against the real `serialized_image`: an upgrade that changes it fails the suite
    rather than a user's tray.
    """

    __slots__ = ("_data",)

    def __init__(self, data: bytes) -> None:
        self._data = data

    def save(self, fp: BinaryIO, format: str | None = None, **_: object) -> None:
        fp.write(self._data)


def _tray_icon() -> _IcoBytes:
    """Load the built icon. Read lazily so importing this module needs no asset."""
    return _IcoBytes(_ICO_PATH.read_bytes())


class Tray:
    """Wraps pystray so `app.py` never has to think about menu plumbing."""

    def __init__(
        self,
        get_mode: Callable[[], str],
        set_mode: Callable[[str], None],
        get_run_at_startup: Callable[[], bool],
        toggle_run_at_startup: Callable[[], None],
        undo: Callable[[], bool],
        can_undo: Callable[[], bool],
        open_settings: Callable[[], None],
        open_rules: Callable[[], None],
        validate_rules: Callable[[], None],
        open_log: Callable[[], None],
        show_about: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        self._get_mode = get_mode
        self._set_mode = set_mode
        self._on_exit = on_exit

        self.icon = pystray.Icon(
            "nudl",
            icon=_tray_icon(),
            title="nudl — clean links",
            menu=pystray.Menu(
                # The toast lasts seconds and the hotkey window is shorter still. Miss
                # both and the original was gone — even though nudl still had it. Here it
                # stays available for as long as undoing is actually safe, and greys out
                # the moment it isn't, so the menu never offers an action that would do
                # nothing.
                pystray.MenuItem(
                    "Undo last clean",
                    lambda: undo(),
                    enabled=lambda _: can_undo(),
                    default=True,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "Mode",
                    pystray.Menu(
                        pystray.MenuItem(
                            "Hotkey",
                            lambda: self._set_mode("hotkey"),
                            checked=lambda _: self._get_mode() == "hotkey",
                            radio=True,
                        ),
                        pystray.MenuItem(
                            "Automatic",
                            lambda: self._set_mode("auto"),
                            checked=lambda _: self._get_mode() == "auto",
                            radio=True,
                        ),
                    ),
                ),
                pystray.MenuItem(
                    "Start with Windows",
                    lambda: toggle_run_at_startup(),
                    checked=lambda _: get_run_at_startup(),
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Settings…", lambda: open_settings()),
                # The rules file is the whole product's behaviour in one editable document,
                # and it was previously reachable only by a user who already knew the
                # undocumented config key existed. "Validate rules" sits directly under it
                # because editing a JSON file by hand and having no way to check it is how
                # a user ends up on silently-fallen-back rules without knowing.
                pystray.MenuItem("Rules…", lambda: open_rules()),
                pystray.MenuItem("Validate rules", lambda: validate_rules()),
                pystray.MenuItem("View log", lambda: open_log()),
                pystray.MenuItem("About", lambda: show_about()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Exit", self._exit),
            ),
        )

    def _exit(self) -> None:
        self._on_exit()
        self.icon.stop()

    def run(self) -> None:
        """Blocks the calling thread (must be the main thread)."""
        self.icon.run()
