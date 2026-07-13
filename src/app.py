"""nudl — wiring, threading, and the undo contract.

Threading layout (SPEC-CLAUDE.md §7.2):

  main thread      pystray's icon loop. Owns the tray menu. Blocks in `Tray.run()`.
  "nudl-pump"      the Win32 message pump on the hidden message-only window. Owns the
                   hotkey registration and every clipboard write, because
                   `RegisterHotKey` must be called on the thread that pumps it.
  "nudl-ui"        Tk's loop, for the toast and the first-run dialog. Tk is
                   thread-affine, so it gets a thread of its own.

Shared state (the last clean, for undo) is guarded by a lock.

The rule that outranks everything here: **a no-op is silent, and every real change is
loud and reversible.** If nudl did not change the link, the user must not be able to
tell nudl ran at all.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import win32con

from src import clean, clipboard, config, hotkey
from src.hidden_window import MessageWindow
from src.toast import OverlayUI
from src.tray import Tray

logger = logging.getLogger(__name__)

HOTKEY_ID = 1

#: Press the hotkey again within this many seconds of a clean to undo it.
UNDO_WINDOW_SECONDS = 3.0

#: Rotate the audit log at this size (a fuller rotation scheme lands in CP3).
LOG_MAX_BYTES = 512 * 1024


@dataclass(frozen=True)
class LastClean:
    original: str
    cleaned: str
    at: float


class NudlApp:
    def __init__(self) -> None:
        self.config = config.load()
        self.rules = clean.load_rules(config.rules_path())
        self.ui = OverlayUI()
        self.window: MessageWindow | None = None

        self._lock = threading.Lock()
        self._last_clean: LastClean | None = None

    # -- lifecycle ---------------------------------------------------------------

    def run(self) -> None:
        self.ui.start()

        if not self.config["first_run_complete"]:
            self._first_run()

        pump = threading.Thread(target=self._pump, name="nudl-pump", daemon=True)
        pump.start()

        tray = Tray(
            get_mode=lambda: self.config["mode"],
            set_mode=self._set_mode,
            open_settings=self._open_settings,
            open_log=self._open_log,
            show_about=self._show_about,
            on_exit=self._shutdown,
        )
        tray.run()  # blocks the main thread until Exit

    def _first_run(self) -> None:
        """Ask once, remember forever (FR-010)."""
        self.config["mode"] = self.ui.ask_mode(default=self.config["mode"])
        self.config["first_run_complete"] = True
        config.save(self.config)

    def _pump(self) -> None:
        self.window = MessageWindow()
        self.window.on(win32con.WM_HOTKEY, lambda _w, _l: self.on_hotkey())
        try:
            hotkey.register(self.window.hwnd, HOTKEY_ID, self.config["hotkey"])
        except (hotkey.InvalidHotkey, hotkey.HotkeyUnavailable) as exc:
            # Losing the hotkey is survivable — the tray still works — but the user has
            # to be told, or nudl looks silently broken.
            logger.error("hotkey unavailable: %s", exc)
            self.ui.show_toast(f"nudl — {exc}. Pick another in Settings.", seconds=10)
        self.window.pump()

    def _shutdown(self) -> None:
        if self.window is not None:
            hotkey.unregister(self.window.hwnd, HOTKEY_ID)
            self.window.stop()
        self.ui.stop()

    # -- the hotkey ----------------------------------------------------------------

    def on_hotkey(self) -> None:
        """Clean what's on the clipboard — or undo the last clean."""
        if self._undo_if_pending():
            return

        text = clipboard.get_text()
        url = clipboard.as_single_url(text)
        if url is None:
            self.ui.show_toast("nudl — no link on the clipboard", seconds=3)
            return

        self.apply_clean(url)

    def apply_clean(self, url: str) -> None:
        """Clean `url` and, only if that changed something, write it back loudly."""
        result = clean.clean_result(
            url,
            rules=self.rules,
            exceptions=self.config["exceptions"],
            strip_referral=self.config["strip_referral"],
        )
        if not result.changed:
            return  # a no-op is silent: no toast, no clipboard write (FR-008)

        try:
            clipboard.set_text(result.result)
        except clipboard.ClipboardBusy:
            logger.warning("could not write the cleaned link; clipboard was locked")
            self.ui.show_toast("nudl — clipboard busy, link left alone", seconds=3)
            return

        with self._lock:
            self._last_clean = LastClean(result.original, result.result, time.monotonic())

        self._log(result)
        self.ui.show_toast(self._describe(result), on_undo=self.undo)

    @staticmethod
    def _describe(result: clean.CleanResult) -> str:
        count = len(result.params_removed)
        if count == 1:
            return "nudl — removed 1 tracker"
        if count > 1:
            return f"nudl — removed {count} trackers"
        return "nudl — unwrapped the redirect"  # changed, but nothing was stripped

    # -- undo ----------------------------------------------------------------------

    def _undo_if_pending(self) -> bool:
        """Hotkey pressed again within the undo window → undo instead of cleaning."""
        with self._lock:
            last = self._last_clean
        if last is None or time.monotonic() - last.at > UNDO_WINDOW_SECONDS:
            return False
        # Only undo if the clipboard still holds what nudl put there. If the user has
        # copied something else since, the undo would clobber it.
        if clipboard.get_text() != last.cleaned:
            return False
        self.undo()
        return True

    def undo(self) -> None:
        """Restore the exact original clipboard content (FR-009)."""
        with self._lock:
            last = self._last_clean
            self._last_clean = None
        if last is None:
            return
        try:
            clipboard.set_text(last.original)
        except clipboard.ClipboardBusy:
            self.ui.show_toast("nudl — clipboard busy, could not undo", seconds=3)
            return
        self.ui.show_toast("nudl — undone", seconds=2)

    # -- tray actions --------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        if mode == self.config["mode"]:
            return
        self.config["mode"] = mode
        config.save(self.config)
        self.ui.show_toast(f"nudl — {'automatic' if mode == 'auto' else 'hotkey'} mode", seconds=2)

    def _open_settings(self) -> None:
        path = config.config_path()
        if not path.exists():
            config.save(self.config)
        self._open(path)

    def _open_log(self) -> None:
        path = config.log_path()
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
        self._open(path)

    def _show_about(self) -> None:
        self.ui.show_toast("nudl — local only. Your links never leave this machine.", seconds=6)

    @staticmethod
    def _open(path: Path) -> None:
        try:
            os.startfile(path)  # noqa: S606 — opening the user's own config/log
        except OSError:
            logger.exception("could not open %s", path)

    # -- the audit log -------------------------------------------------------------

    def _log(self, result: clean.CleanResult) -> None:
        """Append `timestamp · original → removed → result` locally. Never leaves the box."""
        path = config.log_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                path.replace(path.with_suffix(".log.1"))
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            removed = ",".join(result.params_removed) or "-"
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(f"{stamp} · {result.original} · {removed} · {result.result}\n")
        except OSError:
            logger.exception("could not write the audit log")


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    NudlApp().run()


if __name__ == "__main__":
    main()
