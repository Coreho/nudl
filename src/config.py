"""User configuration — `%AppData%\\nudl\\config.json`.

A corrupt or hand-mangled config must never stop nudl from running: unknown keys are
ignored, wrong-typed values fall back to their default, and an unreadable file yields
the defaults wholesale.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

APP_NAME = "nudl"

DEFAULTS: dict[str, Any] = {
    "mode": "hotkey",  # "hotkey" | "auto"
    "hotkey": "ctrl+alt+v",
    "run_at_startup": False,
    "exceptions": [],  # domains that pass through untouched (FR-011)
    "strip_referral": False,  # off-by-default referral-param toggle
    "rules_path": "rules.json",
    "first_run_complete": False,  # the mode dialog is shown exactly once (FR-010)
}

MODES = ("hotkey", "auto")


def config_dir() -> Path:
    base = os.environ.get("APPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Roaming") / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.json"


def log_path() -> Path:
    return config_dir() / "clean.log"


def rules_path(cfg: dict[str, Any] | None = None) -> Path:
    """The rule file nudl actually reads.

    Must resolve inside the config dir — absolute paths and UNC paths are rejected
    so a tampered config.json cannot redirect reads (and the os.startfile that
    follows) to an arbitrary file or remote share.
    """
    name = str((cfg or DEFAULTS).get("rules_path") or DEFAULTS["rules_path"])
    base = config_dir().resolve()
    candidate = (base / name).resolve()
    if name.startswith("\\\\") or not candidate.is_relative_to(base):
        logger.warning("rules_path %r escapes %s; using the default", name, base)
        candidate = base / DEFAULTS["rules_path"]
    return candidate


def rules_backup_path(cfg: dict[str, Any] | None = None) -> Path:
    """The last rule file that loaded cleanly, kept beside it as `<name>.bak`."""
    active = rules_path(cfg)
    return active.with_name(active.name + ".bak")


def load(path: str | Path | None = None) -> dict[str, Any]:
    """Read config, falling back to defaults for anything missing or malformed."""
    cfg = dict(DEFAULTS)
    try:
        with open(Path(path) if path else config_path(), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return cfg

    if not isinstance(data, dict):
        return cfg

    for key, default in DEFAULTS.items():
        value = data.get(key, default)
        # A wrong-typed value is a corrupt value: take the default rather than crash
        # later. bool is checked first because bool is a subclass of int.
        if isinstance(default, bool):
            ok = isinstance(value, bool)
        else:
            ok = isinstance(value, type(default))
        cfg[key] = value if ok else default

    if cfg["mode"] not in MODES:
        cfg["mode"] = DEFAULTS["mode"]
    cfg["exceptions"] = [str(d) for d in cfg["exceptions"] if isinstance(d, str) and d.strip()]
    return cfg


def save(cfg: dict[str, Any], path: str | Path | None = None) -> None:
    """Write config atomically, so a crash mid-write cannot corrupt it."""
    target = Path(path) if path else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: cfg.get(key, default) for key, default in DEFAULTS.items()}

    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    os.replace(tmp, target)
