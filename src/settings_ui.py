"""The Settings window — every option in config.json, without opening config.json.

"Settings…" used to open a JSON file in Notepad. That is fine for someone who reads
JSON, and a wall for everyone else: the exception list, the referral toggle and the
hotkey were all there and all effectively hidden.

Tk, like the toast, and built on the same UI thread (`toast.OverlayUI` owns it).
`parse_form` is the part with the rules in it, and it is pure: what a user typed goes in,
config values or a plain-English complaint come out, and no widget is involved.
"""

from __future__ import annotations

import contextlib
import re
import tkinter as tk
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import hotkey

_BG = "#16181d"
_FIELD = "#22252b"
_FG = "#e8eaed"
_MUTED = "#9aa0a6"
_ACCENT = "#4cb782"
_ERROR = "#ed6a5e"

_DOMAIN = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_APP = re.compile(r"^[^\\/:*?\"<>|]+$")


@dataclass(frozen=True)
class SettingsForm:
    """What the window shows, and what the user typed — raw, before any checking."""

    mode: str
    hotkey: str
    run_at_startup: bool
    strip_referral: bool
    exceptions: str  # one domain per line
    skip_apps: str  # one exe name per line

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> SettingsForm:
        return cls(
            mode=cfg["mode"],
            hotkey=cfg["hotkey"],
            run_at_startup=cfg["run_at_startup"],
            strip_referral=cfg["strip_referral"],
            exceptions="\n".join(cfg["exceptions"]),
            skip_apps="\n".join(cfg["skip_apps"]),
        )


def parse_form(form: SettingsForm) -> tuple[dict[str, Any], str | None]:
    """Config values from the form, or ({}, what is wrong) — said so a person can fix it.

    Forgiving about how things are typed, strict about what they mean: a pasted
    `https://www.MyBank.com/login` becomes `mybank.com`, `code` becomes `code.exe`, but
    a hotkey Windows would refuse, or a "domain" with a space in it, is sent back.
    """
    combo = " ".join(form.hotkey.split()).lower()
    try:
        hotkey.parse(combo)
    except hotkey.InvalidHotkey as exc:
        return {}, f"Hotkey: {exc}"

    domains: list[str] = []
    for line in _lines(form.exceptions):
        domain = _as_domain(line)
        if domain is None:
            return {}, f"“{line}” isn't a website address. Try something like mybank.com."
        if domain not in domains:
            domains.append(domain)

    apps: list[str] = []
    for line in _lines(form.skip_apps):
        name = line.replace("/", "\\").rsplit("\\", 1)[-1]
        if not _APP.match(name):
            return {}, f"“{line}” isn't an app name. Try something like Code.exe."
        if "." not in name:
            name += ".exe"
        if name.lower() not in (a.lower() for a in apps):
            apps.append(name)

    return {
        "mode": form.mode if form.mode in ("hotkey", "auto") else "hotkey",
        "hotkey": combo,
        "run_at_startup": form.run_at_startup,
        "strip_referral": form.strip_referral,
        "exceptions": domains,
        "skip_apps": apps,
    }, None


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _as_domain(text: str) -> str | None:
    host = text.strip().lower()
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", host)  # a pasted link, not a domain
    host = host.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.rsplit("@", 1)[-1].split(":", 1)[0]
    host = host.removeprefix("*.").removeprefix(".").removeprefix("www.")
    return host if _DOMAIN.match(host) else None


def build(
    root: tk.Misc,
    form: SettingsForm,
    on_save: Callable[[SettingsForm], str | None],
    on_rules: Callable[[], None],
    on_config_file: Callable[[], None],
) -> tk.Toplevel:
    """The window itself. Save calls `on_save`; an error it returns is shown, not raised."""
    win = tk.Toplevel(root)
    win.title("nudl — Settings")
    win.configure(bg=_BG, padx=26, pady=22)
    win.resizable(False, False)
    win.attributes("-topmost", True)

    mode = tk.StringVar(win, value=form.mode)
    startup = tk.BooleanVar(win, value=form.run_at_startup)
    referral = tk.BooleanVar(win, value=form.strip_referral)

    def heading(text: str, top: int = 16) -> None:
        tk.Label(win, text=text, bg=_BG, fg=_FG, font=("Segoe UI", 10, "bold")).pack(
            anchor="w", pady=(top, 4)
        )

    def hint(text: str) -> None:
        tk.Label(win, text=text, bg=_BG, fg=_MUTED, font=("Segoe UI", 9), justify="left").pack(
            anchor="w", pady=(0, 2)
        )

    def check(text: str, variable: tk.BooleanVar) -> None:
        tk.Checkbutton(
            win,
            text=text,
            variable=variable,
            bg=_BG,
            fg=_FG,
            activebackground=_BG,
            activeforeground=_FG,
            selectcolor=_FIELD,
            font=("Segoe UI", 9),
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2",
        ).pack(anchor="w", pady=1)

    def field(value: str, height: int = 1, width: int = 46) -> tk.Text | tk.Entry:
        common: dict[str, Any] = {
            "bg": _FIELD,
            "fg": _FG,
            "insertbackground": _FG,
            "relief": "flat",
            "font": ("Segoe UI", 10),
            "highlightthickness": 1,
            "highlightbackground": "#2e3238",
            "highlightcolor": _ACCENT,
        }
        widget: tk.Text | tk.Entry
        if height == 1:
            widget = tk.Entry(win, width=width, **common)
            widget.insert(0, value)
        else:
            widget = tk.Text(win, width=width, height=height, wrap="none", **common)
            widget.insert("1.0", value)
        widget.pack(anchor="w", fill="x", ipady=3)
        return widget

    tk.Label(win, text="Settings", bg=_BG, fg=_FG, font=("Segoe UI", 14, "bold")).pack(anchor="w")

    heading("How nudl works", top=10)
    for value, text in (
        ("hotkey", "Hotkey — clean the link when I press the shortcut"),
        ("auto", "Automatic — clean links the moment I copy them"),
    ):
        tk.Radiobutton(
            win,
            text=text,
            value=value,
            variable=mode,
            bg=_BG,
            fg=_FG,
            activebackground=_BG,
            activeforeground=_FG,
            selectcolor=_FIELD,
            font=("Segoe UI", 9),
            borderwidth=0,
            highlightthickness=0,
            cursor="hand2",
        ).pack(anchor="w", pady=1)

    heading("Shortcut")
    hotkey_field = field(form.hotkey, width=24)
    hint("Like ctrl+alt+v or ctrl+shift+l. Pressing it again right after a clean undoes it.")

    heading("Options")
    check("Start nudl when Windows starts", startup)
    check("Also remove referral codes (like Amazon's tag=)", referral)

    heading("Never touch links to these sites")
    exceptions_field = field(form.exceptions, height=3)
    hint("One per line, like mybank.com. Subdomains are included.")

    heading("In automatic mode, ignore copies from these apps")
    apps_field = field(form.skip_apps, height=3)
    hint("One per line, like Code.exe. The hotkey still works everywhere.")

    error = tk.Label(win, text="", bg=_BG, fg=_ERROR, font=("Segoe UI", 9), wraplength=420)
    error.pack(anchor="w", pady=(10, 0))

    def text_of(widget: tk.Text | tk.Entry) -> str:
        return widget.get("1.0", "end") if isinstance(widget, tk.Text) else widget.get()

    def save() -> None:
        problem = on_save(
            SettingsForm(
                mode=mode.get(),
                hotkey=text_of(hotkey_field),
                run_at_startup=startup.get(),
                strip_referral=referral.get(),
                exceptions=text_of(exceptions_field),
                skip_apps=text_of(apps_field),
            )
        )
        if problem:
            error.configure(text=problem)
            return
        with contextlib.suppress(tk.TclError):
            win.destroy()

    def button(parent: tk.Misc, text: str, command: Callable[[], None], primary: bool = False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=_ACCENT if primary else "#2a2e35",
            fg="#0d0f12" if primary else _FG,
            activebackground=_ACCENT,
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 9, "bold" if primary else "normal"),
            cursor="hand2",
            padx=12,
            pady=5,
        )

    footer = tk.Frame(win, bg=_BG)
    footer.pack(fill="x", pady=(12, 0))
    button(footer, "Edit my rules…", on_rules).pack(side="left")
    button(footer, "Open config file", on_config_file).pack(side="left", padx=(6, 0))
    button(footer, "Save", save, primary=True).pack(side="right")
    button(footer, "Cancel", win.destroy).pack(side="right", padx=(0, 6))

    win.bind("<Escape>", lambda _e: win.destroy())
    win.update_idletasks()
    x = (win.winfo_screenwidth() - win.winfo_width()) // 2
    y = (win.winfo_screenheight() - win.winfo_height()) // 3
    win.geometry(f"+{x}+{y}")
    win.focus_force()
    return win
