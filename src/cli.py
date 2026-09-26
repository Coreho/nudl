"""The nudl command line: text in, the same text out with every link in it cleaned.

Pure and offline, like the engine it wraps — and the one part of nudl that runs on macOS
and Linux too, where the OS supplies the hotkey instead of nudl:

    nudl --clipboard                          any OS: clean the clipboard in place
    Get-Clipboard | nudl | Set-Clipboard      or as a filter
    nudl < notes.md > clean.md                every link in a file

It reads the same config and rules file as the tray app, so a rule you add applies
wherever you use nudl. The tray app itself is a separate entry point, `nudlw`, and it is
Windows-only.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import __version__, clean, config

EXAMPLES = """\
examples:
  nudl --clipboard                          clean the link(s) on the clipboard, in place
  nudl "https://youtu.be/dQw4w9WgXcQ?si=Ab12&t=42"
  Get-Clipboard | nudl | Set-Clipboard      (PowerShell)
  pbpaste | nudl | pbcopy                   (macOS)
  nudl < notes.md > clean.md                every link in a file; everything else untouched

Bind `nudl --clipboard` to a key and you have nudl's hotkey on any OS.
Your rules file and settings are read from {config_dir}.
nudl never makes a network call. The Windows tray app is `nudlw`."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="nudl",
        description="Strip tracking junk off every link in the text you give it.",
        epilog=EXAMPLES.format(config_dir=config.config_dir()),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="links, or text containing links (if not provided, read from stdin)",
    )
    parser.add_argument(
        "-c",
        "--clipboard",
        action="store_true",
        help="clean the clipboard in place instead of reading arguments or stdin",
    )
    parser.add_argument(
        "--rules",
        metavar="PATH",
        help="your rules file, layered on the bundled rules (default: the one in "
        f"{config.config_dir()})",
    )
    parser.add_argument(
        "--exceptions",
        metavar="DOMAIN",
        action="append",
        help="domain or subdomain to leave alone (adds to the ones in your config)",
    )
    parser.add_argument(
        "--strip-referral",
        action="store_true",
        help="also strip referral keys, like Amazon's tag=",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="report every link that changed, on stderr",
    )
    parser.add_argument("--version", action="version", version=f"nudl {__version__}")

    args = parser.parse_args(argv)
    cfg = config.load()
    options: dict[str, Any] = {
        "rules": _rules(args.rules, cfg),
        "exceptions": cfg["exceptions"] + (args.exceptions or []),
        "strip_referral": args.strip_referral or cfg["strip_referral"],
    }

    if args.clipboard:
        return _clean_clipboard(options, verbose=args.verbose)

    if args.text:
        for text in args.text:
            result = clean.clean_text(text, **options)
            _report(result, verbose=args.verbose)
            print(result.result)
        return 0

    # Nothing piped in. Reading anyway would sit on a blank console waiting for an EOF
    # nobody knows to type — which, to someone who double-clicked nudl.exe, looks
    # exactly like a program that has hung.
    if sys.stdin is None or sys.stdin.isatty():
        if _double_clicked():
            return _start_tray()
        parser.print_usage(sys.stderr)
        print(
            "nudl: give it a link, pipe some text in, or use --clipboard (see `nudl --help`). "
            "The tray app is `nudlw`.",
            file=sys.stderr,
        )
        return 2

    text = sys.stdin.read()
    if not text.strip():
        parser.error("no links provided and stdin is empty")
    result = clean.clean_text(text, **options)
    _report(result, verbose=args.verbose)
    # Exactly what came in, minus the trackers: a filter that also reflowed whitespace or
    # dropped blank lines would be mangling the file it was asked to leave alone.
    sys.stdout.write(result.result)
    return 0


def _rules(explicit: str | None, cfg: dict[str, Any]) -> dict[str, Any]:
    """Bundled rules, with the user's file layered on — the same set the tray runs on.

    A missing file at the default location is the normal case and says nothing. A file
    the user named on the command line, or one that exists and is broken, gets a warning
    on stderr: stdout is feeding a pipe, and must stay nothing but the result.
    """
    path = Path(explicit) if explicit else config.rules_path(cfg)
    load = clean.load_rules_verbose(path)
    if load.error is not None and (explicit or path.exists()):
        print(f"nudl: warning: {path} {load.error} - using the bundled rules", file=sys.stderr)
    return load.rules


def _report(result: clean.TextCleanResult, *, verbose: bool) -> None:
    if not verbose:
        return
    for link in result.links:
        removed = ", ".join(link.params_removed) or "a redirect wrapper"
        print(f"nudl: {link.original}\n   -> {link.result}   ({removed})", file=sys.stderr)


def _clean_clipboard(options: dict[str, Any], *, verbose: bool) -> int:
    """`nudl --clipboard`: the hotkey, for any OS. Only writes when something changed.

    Untouched means untouched: a clipboard with nothing to clean is not rewritten, so
    whatever else is on it — an image, formatting — survives a key pressed by mistake.
    """
    from . import os_clipboard

    try:
        text = os_clipboard.read()
    except os_clipboard.Unavailable as exc:
        print(f"nudl: {exc}", file=sys.stderr)
        return 1
    if not text:
        print("nudl: there is no text on the clipboard", file=sys.stderr)
        return 1

    result = clean.clean_text(text, **options)
    _report(result, verbose=verbose)
    if not result.changed:
        print("nudl: nothing to clean", file=sys.stderr)
        return 0
    try:
        os_clipboard.write(result.result)
    except Exception as exc:  # noqa: BLE001 — any backend failure, reported the same way
        print(f"nudl: could not write the clipboard: {exc}", file=sys.stderr)
        return 1
    removed = result.params_removed
    print(
        f"nudl: removed {len(removed)} tracker{'s' if len(removed) != 1 else ''}"
        + (f": {', '.join(removed)}" if removed else " (unwrapped a redirect)"),
        file=sys.stderr,
    )
    return 0


def tray() -> None:
    """`nudlw`: the tray app. Windows-only — everywhere else, say so instead of a traceback."""
    if sys.platform != "win32":
        sys.exit("nudlw: the tray app is Windows-only. Use `nudl --clipboard`; see `nudl --help`.")
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
