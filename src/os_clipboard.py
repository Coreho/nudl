"""The clipboard on whatever OS this is — for `nudl --clipboard`, not for the tray.

The tray app has `clipboard.py`: Win32, retries, a sequence number, an own-write guard,
because it lives next to the clipboard for hours. The command line touches it once and
exits, so it borrows whatever the OS already ships for the job:

    Windows  nudl's own clipboard.py (pywin32 is installed with nudl on Windows)
    macOS    pbpaste / pbcopy
    Wayland  wl-paste / wl-copy          (the wl-clipboard package)
    X11      xclip, or else xsel

Text only. A link, or a message with links in it, is text; anything richer is left to the
tray app, which knows how to keep the formatting.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys


class Unavailable(RuntimeError):
    """There is no clipboard nudl can reach from here, and the message says what to install."""


def read() -> str | None:
    """The clipboard's text, or None if it holds something else or nothing."""
    if sys.platform == "win32":
        from . import clipboard

        return clipboard.get_text()
    command = _commands()[0]
    try:
        done = subprocess.run(  # noqa: S603 — fixed argv, no shell
            command, capture_output=True, encoding="utf-8", env=_env(), check=False
        )
    except OSError as exc:
        raise Unavailable(f"could not run {command[0]}: {exc}") from exc
    if done.returncode != 0:
        return None  # an empty or non-text clipboard, in every one of these tools
    return done.stdout


def write(text: str) -> None:
    """Replace the clipboard with `text`."""
    if sys.platform == "win32":
        from . import clipboard

        clipboard.set_text(text)
        return
    command = _commands()[1]
    try:
        # stdout/stderr to DEVNULL, not captured: xclip and wl-copy fork a child that keeps
        # serving the clipboard after they return, and a captured pipe would keep this call
        # waiting for that child to exit — which is never.
        subprocess.run(  # noqa: S603 — fixed argv, no shell
            command,
            input=text,
            encoding="utf-8",
            env=_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise Unavailable(f"could not write the clipboard with {command[0]}: {exc}") from exc


def _commands() -> tuple[list[str], list[str]]:
    """(read argv, write argv) for this machine, or Unavailable saying what to install."""
    if sys.platform == "darwin":
        return ["pbpaste"], ["pbcopy"]
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-paste") and shutil.which("wl-copy"):
        return ["wl-paste", "--no-newline"], ["wl-copy"]
    if shutil.which("xclip"):
        xclip = ["xclip", "-selection", "clipboard"]
        return [*xclip, "-o"], [*xclip, "-i"]
    if shutil.which("xsel"):
        return ["xsel", "--clipboard", "--output"], ["xsel", "--clipboard", "--input"]
    wanted = "wl-clipboard" if os.environ.get("WAYLAND_DISPLAY") else "xclip (or xsel)"
    raise Unavailable(f"no clipboard tool found — install {wanted}")


def _env() -> dict[str, str]:
    """pbpaste and pbcopy pick their encoding from the locale; pin it to UTF-8."""
    env = dict(os.environ)
    if sys.platform == "darwin":
        env.setdefault("LANG", "en_US.UTF-8")
    return env
