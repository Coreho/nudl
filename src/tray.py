"""The tray icon and its menu.

The icon is always visible on purpose. A clipboard-reading tool that hides itself is
exactly what a user should be suspicious of; nudl is not hiding.

`pystray.Icon.run()` owns the main thread (see `app.py` for the threading layout).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pystray
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

_BG = (22, 24, 29, 255)
_ACCENT = (76, 183, 130, 255)


def _icon_image(size: int = 64) -> Image.Image:
    """A green 'n' on a dark rounded tile — legible at 16px in the tray."""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=_BG)

    # Draw the 'n' as strokes rather than text: font availability varies by machine,
    # and a missing glyph would leave an empty tray icon.
    weight = max(3, size // 10)
    left, right = size // 4, size - size // 4
    top, bottom = size // 3, size - size // 4
    draw.line((left, top, left, bottom), fill=_ACCENT, width=weight)
    draw.line((right, top + weight, right, bottom), fill=_ACCENT, width=weight)
    draw.arc(
        (left, top - weight, right, top + (bottom - top) // 2),
        start=180,
        end=360,
        fill=_ACCENT,
        width=weight,
    )
    return image


class Tray:
    """Wraps pystray so `app.py` never has to think about menu plumbing."""

    def __init__(
        self,
        get_mode: Callable[[], str],
        set_mode: Callable[[str], None],
        undo: Callable[[], bool],
        can_undo: Callable[[], bool],
        open_settings: Callable[[], None],
        open_log: Callable[[], None],
        show_about: Callable[[], None],
        on_exit: Callable[[], None],
    ) -> None:
        self._get_mode = get_mode
        self._set_mode = set_mode
        self._on_exit = on_exit

        self.icon = pystray.Icon(
            "nudl",
            icon=_icon_image(),
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
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Settings…", lambda: open_settings()),
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
