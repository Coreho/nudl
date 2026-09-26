"""Overlay UI: the toast (with Undo) and the one-time first-run mode picker.

**Why not a native Windows toast?** Because Focus Assist / Do-Not-Disturb silently
suppresses them. A suppressed toast would take the Undo affordance down with it, and
"every change is loud and reversible" is the promise the whole product rests on. So
nudl draws its own always-on-top window, which nothing can quietly swallow.

Tk is thread-affine: every widget call must happen on the thread that created the root.
So this module owns one long-lived UI thread and everything is marshalled onto it
through a queue. Callers (the tray on the main thread, the message pump on its own
thread) just call `show_toast()` / `ask_first_run()` from wherever they are.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import queue
import threading
import tkinter as tk
from collections.abc import Callable

logger = logging.getLogger(__name__)

_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_TOPMOST = 0x00000008


def _no_activate(win: tk.Toplevel) -> None:
    """Stop the toast from stealing focus.

    A popup that grabs focus while you are mid-sentence is worse than no popup: nudl is
    supposed to be a tool you forget is running. WS_EX_NOACTIVATE tells Windows to show
    the window without ever making it the active one.
    """
    try:
        hwnd = int(win.frame(), 16)  # Tk gives the HWND as a hex string on Windows
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_NOACTIVATE | _WS_EX_TOPMOST)
    except Exception:  # noqa: BLE001 — cosmetic; never break the toast over this
        logger.debug("could not apply WS_EX_NOACTIVATE", exc_info=True)


_BG = "#16181d"
_FG = "#e8eaed"
_MUTED = "#9aa0a6"
_ACCENT = "#4cb782"

TOAST_SECONDS = 6.0


class OverlayUI:
    """Owns the Tk thread. Start once; call from any thread thereafter."""

    def __init__(self) -> None:
        self._queue: queue.Queue[Callable[[], None]] = queue.Queue()
        self._ready = threading.Event()
        self._root: tk.Tk | None = None
        self._toast: tk.Toplevel | None = None
        self._thread: threading.Thread | None = None

    # -- lifecycle ---------------------------------------------------------------

    def start(self, timeout: float = 5.0) -> None:
        self._thread = threading.Thread(target=self._run, name="nudl-ui", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("the UI thread did not come up")

    def _run(self) -> None:
        root = tk.Tk()
        root.withdraw()  # the root itself is never shown; it just owns the loop
        self._root = root
        self._ready.set()
        root.after(50, self._drain)
        root.mainloop()

    def _drain(self) -> None:
        assert self._root is not None
        while True:
            try:
                action = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                action()
            except Exception:  # noqa: BLE001 — a broken widget must not kill the UI
                logger.exception("overlay UI action failed")
        self._root.after(50, self._drain)

    def _post(self, action: Callable[[], None]) -> None:
        self._queue.put(action)

    def stop(self) -> None:
        if self._root is not None:
            self._post(self._root.quit)

    # -- toast -------------------------------------------------------------------

    def show_toast(
        self,
        message: str,
        on_undo: Callable[[], None] | None = None,
        seconds: float = TOAST_SECONDS,
    ) -> None:
        """Show a toast. `on_undo` adds an Undo button and is called on click."""
        self._post(lambda: self._build_toast(message, on_undo, seconds))

    def _build_toast(
        self, message: str, on_undo: Callable[[], None] | None, seconds: float
    ) -> None:
        assert self._root is not None
        self._destroy_toast()

        win = tk.Toplevel(self._root)
        win.overrideredirect(True)  # no title bar, no chrome
        win.attributes("-topmost", True)
        win.configure(bg=_BG)
        self._toast = win

        frame = tk.Frame(win, bg=_BG, padx=16, pady=12)
        frame.pack()

        tk.Label(frame, text=message, bg=_BG, fg=_FG, font=("Segoe UI", 10), anchor="w").pack(
            side="left"
        )

        if on_undo is not None:

            def undo_clicked() -> None:
                self._destroy_toast()
                try:
                    on_undo()
                except Exception:  # noqa: BLE001
                    logger.exception("undo failed")

            tk.Button(
                frame,
                text="Undo",
                command=undo_clicked,
                bg=_BG,
                fg=_ACCENT,
                activebackground=_BG,
                activeforeground=_FG,
                relief="flat",
                borderwidth=0,
                font=("Segoe UI", 10, "bold"),
                cursor="hand2",
            ).pack(side="left", padx=(14, 0))

        # Bottom-right, clear of the taskbar. Measured after layout so the box fits
        # the text rather than the text fitting a guessed box.
        win.update_idletasks()
        x = win.winfo_screenwidth() - win.winfo_width() - 24
        y = win.winfo_screenheight() - win.winfo_height() - 72
        win.geometry(f"+{x}+{y}")

        _no_activate(win)

        # Dismiss THIS window, not "whatever is current". Tk keeps an `after` timer alive
        # even after its widget is destroyed, so an expiring timer from a replaced toast
        # would otherwise kill the toast that replaced it — taking its Undo button with
        # it, seconds after it appeared.
        win.after(int(seconds * 1000), lambda: self._dismiss(win))

    def _dismiss(self, win: tk.Toplevel) -> None:
        with contextlib.suppress(tk.TclError):  # already gone
            win.destroy()
        if self._toast is win:
            self._toast = None

    def _destroy_toast(self) -> None:
        if self._toast is not None:
            self._dismiss(self._toast)

    # -- first-run mode picker ---------------------------------------------------

    def ask_first_run(
        self, default: str = "hotkey", start_with_windows: bool = True
    ) -> tuple[str, bool]:
        """Blocking. Returns (mode, start_with_windows). Closing the dialog keeps the defaults.

        The autostart question is asked HERE, once, as a visible checkbox, rather than
        switched on silently or left buried in the tray menu. Silently would be a clipboard
        tool quietly adding itself to startup; buried is how nobody ever finds it, and
        nudl is gone after the first reboot.
        """
        chosen: dict[str, object] = {}
        done = threading.Event()
        self._post(lambda: self._build_mode_dialog(chosen, done, default, start_with_windows))

        # A bare wait() would be a deadlock waiting for a bad day: if the UI thread is
        # dead, nothing will ever drain the queue, nothing will ever set `done`, and nudl
        # hangs on first run having never reached the tray — an invisible zombie process.
        # The user, though, may take as long as they like. So: wait on a person forever,
        # wait on a corpse not at all.
        while not done.wait(0.25):
            if self._thread is None or not self._thread.is_alive():
                logger.error("the UI thread died before the mode picker was answered")
                return default, start_with_windows
        return (
            str(chosen.get("mode", default)),
            bool(chosen.get("start_with_windows", start_with_windows)),
        )

    def _build_mode_dialog(
        self,
        chosen: dict[str, object],
        done: threading.Event,
        default: str,
        start_with_windows: bool,
    ) -> None:
        assert self._root is not None
        win = tk.Toplevel(self._root)
        win.title("nudl")
        win.configure(bg=_BG, padx=28, pady=24)
        win.resizable(False, False)
        win.attributes("-topmost", True)
        autostart = tk.BooleanVar(win, value=start_with_windows)

        def choose(mode: str) -> None:
            chosen["mode"] = mode
            chosen["start_with_windows"] = autostart.get()
            with contextlib.suppress(tk.TclError):
                win.destroy()
            done.set()

        win.protocol("WM_DELETE_WINDOW", lambda: choose(default))

        tk.Label(
            win,
            text="How should nudl work?",
            bg=_BG,
            fg=_FG,
            font=("Segoe UI", 14, "bold"),
        ).pack(anchor="w")
        tk.Label(
            win,
            text="You can change this any time from the tray menu.",
            bg=_BG,
            fg=_MUTED,
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(4, 18))

        for mode, title, blurb in (
            ("hotkey", "Hotkey", "Clean the link when I press a shortcut.  (recommended)"),
            ("auto", "Automatic", "Clean links as I copy them."),
        ):
            row = tk.Frame(win, bg=_BG)
            row.pack(fill="x", pady=4)
            tk.Button(
                row,
                text=title,
                width=12,
                command=lambda m=mode: choose(m),
                bg=_ACCENT if mode == default else "#2a2e35",
                fg="#0d0f12" if mode == default else _FG,
                activebackground=_ACCENT,
                relief="flat",
                borderwidth=0,
                font=("Segoe UI", 10, "bold"),
                cursor="hand2",
                pady=6,
            ).pack(side="left")
            tk.Label(row, text=blurb, bg=_BG, fg=_MUTED, font=("Segoe UI", 9), anchor="w").pack(
                side="left", padx=(12, 0)
            )

        tk.Checkbutton(
            win,
            text="Start nudl when Windows starts",
            variable=autostart,
            bg=_BG,
            fg=_FG,
            activebackground=_BG,
            activeforeground=_FG,
            selectcolor="#2a2e35",
            font=("Segoe UI", 9),
            cursor="hand2",
            borderwidth=0,
            highlightthickness=0,
        ).pack(anchor="w", pady=(16, 0))

        win.update_idletasks()
        x = (win.winfo_screenwidth() - win.winfo_width()) // 2
        y = (win.winfo_screenheight() - win.winfo_height()) // 3
        win.geometry(f"+{x}+{y}")
        win.focus_force()
