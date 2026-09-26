"""The nudl command line: URLs in (arguments or stdin), clean URLs out.

Pure and offline, like the engine it wraps — and the one part of nudl that runs on macOS
and Linux too, where the OS supplies the clipboard and the hotkey instead of nudl:

    pbpaste | nudl | pbcopy                   macOS
    wl-paste | nudl | wl-copy                 Wayland
    Get-Clipboard | nudl | Set-Clipboard      PowerShell

The tray app is a separate entry point, `nudlw`, and it is Windows-only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from . import __version__, clean

EXAMPLES = """\
examples:
  nudl "https://youtu.be/dQw4w9WgXcQ?si=Ab12&t=42"
  Get-Clipboard | nudl | Set-Clipboard      (PowerShell)
  pbpaste | nudl | pbcopy                   (macOS)
  xclip -o | nudl | xclip -i                (Linux, X11)
  wl-paste | nudl | wl-copy                 (Linux, Wayland)

nudl never makes a network call. The Windows tray app is `nudlw`."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nudl",
        description="Strip tracking junk off any URL — the same real URL, trimmed.",
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="URLs to clean (if not provided, read from stdin)",
    )
    parser.add_argument(
        "--rules",
        metavar="PATH",
        help="path to custom rules.json (default: bundled rules)",
    )
    parser.add_argument(
        "--exceptions",
        metavar="DOMAIN",
        action="append",
        help="domain or subdomain to exclude from cleaning",
    )
    parser.add_argument(
        "--strip-referral",
        action="store_true",
        help="also strip referral keys (from providers)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print additional information about what was cleaned",
    )
    parser.add_argument("--version", action="version", version=f"nudl {__version__}")

    args = parser.parse_args(argv)

    rules = None
    if args.rules:
        load = clean.load_rules_verbose(args.rules)
        if load.error:
            print(
                f"nudl: warning: {args.rules} {load.error} - using bundled rules instead",
                file=sys.stderr,
            )
        rules = load.rules

    def clean_url(url: str) -> str:
        return clean.clean(
            url,
            rules=rules,
            exceptions=args.exceptions or [],
            strip_referral=args.strip_referral,
        )

    urls_to_process = args.urls

    if not urls_to_process:
        # Nothing piped in. Reading anyway would sit on a blank console waiting for an EOF
        # nobody knows to type — which, to someone who double-clicked nudl.exe, looks
        # exactly like a program that has hung.
        if sys.stdin is None or sys.stdin.isatty():
            if _double_clicked():
                return _start_tray()
            parser.print_usage(sys.stderr)
            print(
                "nudl: give it a URL, or pipe some in (see `nudl --help`). "
                "The tray app is `nudlw`.",
                file=sys.stderr,
            )
            return 2
        stdin_data = sys.stdin.read().strip()
        if not stdin_data:
            parser.error("no URLs provided and stdin is empty")
        urls_to_process = stdin_data.splitlines()

    for url in urls_to_process:
        url = url.strip()
        if not url:
            continue

        cleaned = clean_url(url)

        if args.verbose:
            if cleaned == url:
                print(f"UNCHANGED: {url}")
            else:
                print(f"CLEANED:    {url}  ->  {cleaned}")
        else:
            print(cleaned)

    return 0


def tray() -> None:
    """`nudlw`: the tray app. Windows-only — everywhere else, say so instead of a traceback."""
    if sys.platform != "win32":
        sys.exit("nudlw: the tray app is Windows-only. Use `nudl` in a pipe; see `nudl --help`.")
    from .app import main as tray_main

    tray_main()


def _double_clicked() -> bool:
    """True when the packaged nudl.exe was opened from Explorer or a shortcut.

    Windows then creates a console for nudl alone, so nudl is the only process attached
    to it. Run from a terminal there is always at least one more: the shell.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    import ctypes

    attached = (ctypes.c_uint32 * 2)()
    return ctypes.windll.kernel32.GetConsoleProcessList(attached, 2) == 1


def _start_tray() -> int:
    """Hand a double-click over to nudlw.exe, which is what the person meant to open.

    Until 0.2.0, nudl.exe WAS the tray app, so every shortcut, pinned taskbar icon and
    habit points at it. Starting the tray from here keeps all of those working.
    """
    tray_exe = Path(sys.executable).with_name("nudlw.exe")
    if not tray_exe.exists():
        print("nudl: the tray app (nudlw.exe) is missing from this folder.", file=sys.stderr)
        return 1
    subprocess.Popen(  # noqa: S603 — our own sibling executable, no shell, no arguments
        [str(tray_exe)],
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
