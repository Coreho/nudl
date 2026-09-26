"""Hotkey parsing. A typo'd chord must fail loudly, never silently bind the wrong key."""

from __future__ import annotations

import pytest
import win32con

from src import hotkey


def test_default_chord() -> None:
    modifiers, vk = hotkey.parse("ctrl+alt+v")
    assert modifiers & hotkey.MOD_CONTROL
    assert modifiers & hotkey.MOD_ALT
    assert not modifiers & hotkey.MOD_SHIFT
    assert vk == ord("V")


def test_norepeat_is_always_set() -> None:
    """Holding the chord down must fire once, not autorepeat into a clean-storm."""
    modifiers, _ = hotkey.parse("ctrl+alt+v")
    assert modifiers & hotkey.MOD_NOREPEAT


def test_is_case_and_space_insensitive() -> None:
    assert hotkey.parse(" CTRL + Alt + V ") == hotkey.parse("ctrl+alt+v")


def test_aliases() -> None:
    assert hotkey.parse("control+shift+a") == hotkey.parse("ctrl+shift+a")
    assert hotkey.parse("win+alt+z") == hotkey.parse("super+alt+z")


def test_function_keys() -> None:
    _, vk = hotkey.parse("ctrl+f9")
    assert vk == win32con.VK_F9


def test_named_keys() -> None:
    _, vk = hotkey.parse("ctrl+alt+space")
    assert vk == win32con.VK_SPACE


def test_digits() -> None:
    _, vk = hotkey.parse("ctrl+alt+7")
    assert vk == ord("7")


@pytest.mark.parametrize(
    "combo",
    [
        "",
        "   ",
        "ctrl+alt",  # modifiers only
        "v",  # no modifier: would steal 'v' from every app on the system
        "ctrl+alt+nonsense",
        "ctrl+f99",
        "ctrl+alt+v+x",  # two real keys
    ],
)
def test_bad_chords_raise(combo: str) -> None:
    with pytest.raises(hotkey.InvalidHotkey):
        hotkey.parse(combo)


@pytest.mark.parametrize(
    "combo",
    ["ctrl+c", "ctrl+v", "alt+tab", "ctrl+alt+tab", "alt+f4", "win+l", "win+tab", "ctrl+alt+del"],
)
def test_system_shortcuts_are_refused(combo: str) -> None:
    """Named keys included: the Tab rows once held a tab CHARACTER, so alt+tab slipped by."""
    with pytest.raises(hotkey.InvalidHotkey, match="reserved"):
        hotkey.parse(combo)


@pytest.mark.parametrize("combo", ["ctrl+alt+v", "ctrl+shift+tab", "alt+shift+f4", "ctrl+alt+l"])
def test_near_misses_are_still_allowed(combo: str) -> None:
    """The denylist matches the exact modifier set; one extra modifier is a different chord."""
    hotkey.parse(combo)
