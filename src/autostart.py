"""Start with Windows — one value under HKCU's Run key, and nothing else.

A tray tool that does not come back after a reboot is a tool the user forgets they
installed. That is the whole reason this module exists.

HKCU, not HKLM: it needs no elevation and touches nobody but this user. A Run value, not
a Startup-folder shortcut or a scheduled task: Run values are what Task Manager's
*Startup apps* tab lists, so the user can see nudl there and switch it off in the place
Windows users already look. Anything cleverer would be harder to find and harder to
undo, which is the wrong trade for a program that reads the clipboard.

`value_name` is a parameter everywhere so the tests can work on a value of their own.
Sharing the real one would rewrite the developer's actual autostart every test run.
"""

from __future__ import annotations

import logging
import sys
import sysconfig
import winreg
from pathlib import Path

logger = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "nudl"


def command() -> str:
    """The command line that starts THIS nudl's tray app, with no console window."""
    if getattr(sys, "frozen", False):
        # The packaged build: sys.executable is nudlw.exe itself.
        return f'"{sys.executable}"'

    # pip / pipx: the `nudlw` gui-script sits in the environment's Scripts folder, and it
    # works from any working directory — a Run value starts in System32, not the repo.
    script = Path(sysconfig.get_path("scripts")) / "nudlw.exe"
    if script.exists():
        return f'"{script}"'

    # A bare checkout. pythonw, never python: python opens a console window at every logon.
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    package = __name__.rpartition(".")[0]  # "src" in a checkout, "nudl" once installed
    return f'"{pythonw if pythonw.exists() else python}" -m {package}.app'


def registered(value_name: str = VALUE_NAME) -> str | None:
    """The command currently in the Run key under `value_name`, or None."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _kind = winreg.QueryValueEx(key, value_name)
    except FileNotFoundError:
        return None
    return value if isinstance(value, str) else None


def enable(value_name: str = VALUE_NAME) -> None:
    """Start this nudl at logon. Raises OSError if the registry refuses."""
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, command())


def disable(value_name: str = VALUE_NAME) -> None:
    """Stop starting at logon. Already off is not an error. Raises OSError otherwise."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
    except FileNotFoundError:
        pass


def _target(cmd: str) -> Path:
    """The executable a Run command points at: its first, possibly quoted, token."""
    cmd = cmd.strip()
    if cmd.startswith('"'):
        return Path(cmd[1:].partition('"')[0])
    return Path(cmd.partition(" ")[0])


def sync(enabled: bool, value_name: str = VALUE_NAME) -> bool:
    """Make the Run key agree with the setting, at startup. True if it now does.

    When the setting is on, an existing value is rewritten only if what it points at has
    gone — a portable zip unzipped somewhere new, an old version folder deleted. A value
    that still points at a real nudl is left alone, even when it is not THIS nudl: a
    developer running a checkout would otherwise steal the autostart from the installed
    copy every time they tried a change. Switching it on from the tray always writes;
    that is someone asking for this copy, specifically.
    """
    try:
        if not enabled:
            disable(value_name)
            return True
        current = registered(value_name)
        if current is None or not _target(current).exists():
            enable(value_name)
        return True
    except OSError:
        logger.warning("could not update the Run key", exc_info=True)
        return False
