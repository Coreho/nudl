"""Config must survive whatever the user does to config.json."""

from __future__ import annotations

import json
from pathlib import Path

from src import config


def test_missing_file_yields_defaults(tmp_path: Path) -> None:
    assert config.load(tmp_path / "nope.json") == config.DEFAULTS


def test_corrupt_json_yields_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert config.load(path) == config.DEFAULTS


def test_non_object_json_yields_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('["a", "list"]', encoding="utf-8")
    assert config.load(path) == config.DEFAULTS


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    cfg = dict(config.DEFAULTS)
    cfg["mode"] = "auto"
    cfg["exceptions"] = ["example.com"]
    cfg["first_run_complete"] = True
    config.save(cfg, path)
    assert config.load(path) == cfg


def test_unknown_keys_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mode": "auto", "wat": 1}), encoding="utf-8")
    loaded = config.load(path)
    assert loaded["mode"] == "auto"
    assert "wat" not in loaded


def test_wrong_types_fall_back_per_key(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(
        json.dumps({"mode": "auto", "exceptions": "not-a-list", "run_at_startup": "yes"}),
        encoding="utf-8",
    )
    loaded = config.load(path)
    assert loaded["mode"] == "auto"  # the good key survives
    assert loaded["exceptions"] == []  # the bad ones fall back
    assert loaded["run_at_startup"] is False


def test_invalid_mode_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"mode": "telepathy"}), encoding="utf-8")
    assert config.load(path)["mode"] == "hotkey"


def test_blank_exception_domains_are_dropped(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"exceptions": ["example.com", "", "  "]}), encoding="utf-8")
    assert config.load(path)["exceptions"] == ["example.com"]


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    config.save(dict(config.DEFAULTS), path)
    assert path.exists()
    assert list(tmp_path.glob("*.tmp")) == []
