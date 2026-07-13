"""A message-only window (`HWND_MESSAGE`) to host the Win32 message pump.

nudl has no visible window, but `RegisterHotKey` and `AddClipboardFormatListener` both
deliver their messages *to a window*. A message-only window is the supported way to own
a message queue without owning any pixels: it never renders, never appears in the
taskbar, and never steals focus.

The window and its pump must live on the same thread — see `MessageWindow.pump()`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import win32api
import win32con
import win32gui

logger = logging.getLogger(__name__)

HWND_MESSAGE = -3

Handler = Callable[[int, int], None]


class MessageWindow:
    """A hidden window that dispatches Win32 messages to registered handlers."""

    def __init__(self, class_name: str = "nudlMessageWindow") -> None:
        self._handlers: dict[int, list[Handler]] = {}

        wndclass = win32gui.WNDCLASS()
        wndclass.lpszClassName = class_name
        wndclass.lpfnWndProc = self._wnd_proc
        wndclass.hInstance = win32api.GetModuleHandle(None)
        self._atom = win32gui.RegisterClass(wndclass)

        self.hwnd: int = win32gui.CreateWindowEx(
            0,
            self._atom,
            class_name,
            0,
            0,
            0,
            0,
            0,
            HWND_MESSAGE,  # <- parent: makes this message-only
            0,
            wndclass.hInstance,
            None,
        )

    def on(self, message: int, handler: Handler) -> None:
        """Call `handler(wparam, lparam)` whenever `message` arrives."""
        self._handlers.setdefault(message, []).append(handler)

    def _wnd_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        for handler in self._handlers.get(message, ()):
            try:
                handler(wparam, lparam)
            except Exception:  # noqa: BLE001 — a bad handler must not kill the pump
                logger.exception("handler for message 0x%04X raised", message)

        if message == win32con.WM_DESTROY:
            win32gui.PostQuitMessage(0)
            return 0
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def pump(self) -> None:
        """Run the message loop. Blocks until `stop()`. Call on the owning thread."""
        win32gui.PumpMessages()

    def stop(self) -> None:
        """Ask the pump to exit. Safe to call from any thread."""
        try:
            win32gui.PostMessage(self.hwnd, win32con.WM_CLOSE, 0, 0)
        except Exception:  # noqa: BLE001 — already torn down
            logger.debug("stop() on an already-closed window", exc_info=True)
