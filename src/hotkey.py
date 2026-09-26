"""Global hotkey via `RegisterHotKey` — never `SetWindowsHookEx`.

A low-level keyboard hook is *the* keylogger heuristic every AV engine looks for, and
it is the single biggest reason a small clipboard tool gets flagged. `RegisterHotKey`
asks Windows to deliver one specific chord, and nothing else — nudl never sees another
keystroke. The cost is that `WM_HOTKEY` is posted to a *window*, which is why we run a
hidden message-only window (see `hidden_window.py`).

`RegisterHotKey` must be called on the same thread that pumps the window's messages.
"""

from __future__ import annotations

import contextlib
import re

import pywintypes
import win32con
import win32gui

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000  # holding the chord fires once, not on key-repeat

_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
    "cmd": MOD_WIN,
}

_NAMED_KEYS = {
    "space": win32con.VK_SPACE,
    "enter": win32con.VK_RETURN,
    "return": win32con.VK_RETURN,
    "tab": win32con.VK_TAB,
    "esc": win32con.VK_ESCAPE,
    "escape": win32con.VK_ESCAPE,
    "insert": win32con.VK_INSERT,
    "delete": win32con.VK_DELETE,
    "del": win32con.VK_DELETE,
    "home": win32con.VK_HOME,
    "end": win32con.VK_END,
    "pgup": win32con.VK_PRIOR,
    "pgdn": win32con.VK_NEXT,
    "up": win32con.VK_UP,
    "down": win32con.VK_DOWN,
    "left": win32con.VK_LEFT,
    "right": win32con.VK_RIGHT,
}


class InvalidHotkey(ValueError):
    """The configured hotkey string cannot be parsed."""


def parse(combo: str) -> tuple[int, int]:
    """`"ctrl+alt+v"` -> `(modifier_flags, virtual_key_code)`.

    Raises InvalidHotkey rather than guessing, so a typo'd config surfaces loudly at
    startup instead of silently registering the wrong chord.
    """
    tokens = [t.strip().lower() for t in str(combo).split("+") if t.strip()]
    if not tokens:
        raise InvalidHotkey(f"empty hotkey: {combo!r}")

    modifiers = 0
    key: str | None = None
    for token in tokens:
        if token in _MODIFIERS:
            modifiers |= _MODIFIERS[token]
        elif key is None:
            key = token
        else:
            raise InvalidHotkey(f"more than one non-modifier key in {combo!r}")

    if key is None:
        raise InvalidHotkey(f"no key in {combo!r} — modifiers alone cannot be a hotkey")
    if not modifiers:
        # A bare key would steal that key from every app on the system.
        raise InvalidHotkey(f"{combo!r} needs at least one modifier")

    # Denylist chords that would hijack a system-wide shortcut the user relies on.
    # Any single-modifier editing accelerator (ctrl+c, ctrl+v, etc.) or a chord
    # Windows reserves (alt+tab, win+l) must be refused outright. Matching is on
    # the exact modifier set — ctrl+alt+v is NOT ctrl+v and is perfectly fine.
    #
    # Named keys are listed by NAME, as parse() produces them. frozenset("cvx") is a set
    # of letters, which is what the single-letter rows want; frozenset("\t") was a set
    # holding a tab character, which "tab" never equals, so alt+tab went straight through.
    _DENYLIST = {
        frozenset({MOD_CONTROL}): frozenset("cvxazsptfnb"),
        frozenset({MOD_ALT}): frozenset({"tab", "f4", "esc", "escape"}),
        frozenset({MOD_WIN}): frozenset("ldrev") | {"tab"},
        frozenset({MOD_WIN, MOD_CONTROL}): frozenset("d"),
        frozenset({MOD_ALT, MOD_CONTROL}): frozenset({"tab", "del", "delete"}),
    }
    key_lower = key.lower()
    mod_set = frozenset(m for m in (MOD_CONTROL, MOD_ALT, MOD_SHIFT, MOD_WIN) if modifiers & m)
    if mod_set in _DENYLIST and key_lower in _DENYLIST[mod_set]:
        raise InvalidHotkey(f"{combo!r} is a reserved system shortcut and cannot be used")

    return modifiers | MOD_NOREPEAT, _virtual_key(key)


def _virtual_key(key: str) -> int:
    # `isascii` is not decoration: 'ß'.upper() is 'SS', and ord() of a 2-char string is a
    # TypeError, not the InvalidHotkey this module promises. Non-ASCII keys have no VK
    # code anyway, so let them fall through to the raise below.
    if len(key) == 1 and key.isascii() and (key.isalpha() or key.isdigit()):
        return ord(key.upper())
    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]
    if match := re.fullmatch(r"f(\d{1,2})", key):
        number = int(match.group(1))
        if 1 <= number <= 24:
            return win32con.VK_F1 + number - 1
    raise InvalidHotkey(f"unknown key: {key!r}")


def register(hwnd: int, hotkey_id: int, combo: str) -> None:
    """Claim `combo` system-wide. Raises if another app already owns the chord."""
    modifiers, vk = parse(combo)
    try:
        win32gui.RegisterHotKey(hwnd, hotkey_id, modifiers, vk)
    except pywintypes.error as exc:
        # Only a genuine Win32 failure means "someone else owns this chord". A TypeError
        # or a bad hwnd is a bug in nudl, and reporting it as a chord conflict would send
        # whoever debugs it looking for a program that isn't there.
        raise HotkeyUnavailable(f"{combo!r} is already held by another application") from exc


def unregister(hwnd: int, hotkey_id: int) -> None:
    with contextlib.suppress(Exception):  # already gone; nothing to do
        win32gui.UnregisterHotKey(hwnd, hotkey_id)


class HotkeyUnavailable(RuntimeError):
    """Another application already owns this chord."""
