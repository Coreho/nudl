"""Rules the user owns: which file nudl runs on, and whether it says so.

The failure this file guards is not "nudl crashes on a bad rules.json" — the engine
already refuses to do that. It is the quiet one: the user edits their rules, nudl silently
falls back to something else, and every link afterwards comes back cleaned by rules the
user did not write. The tray icon looks exactly the same either way.

So each test here asks one of two questions. Which rule set is actually live? And was the
user told?
"""

from __future__ import annotations

import json
import os

import pytest

from src import clean
from src import config as config_module
from src.app import NudlApp

#: A rule set that is valid, minimal, and unmistakably NOT the bundled one: nothing
#: shipped with nudl strips `my_tracker`, so if that key comes off a URL, the user's own
#: file is the one being applied.
CUSTOM: dict[str, object] = {
    "schema_version": "1.0",
    "last_updated": "2026-08-01",
    "global_tracker_keys": ["my_tracker"],
    "redirect_wrappers": [],
    "referral": [],
    "providers": [],
}

#: Truncated JSON. What a half-finished hand edit looks like.
BROKEN = '{\n  "global_tracker_keys": ["my_tracker",\n'

MARKED_URL = "https://example.com/a?my_tracker=1&id=2"


@pytest.fixture
def rules_file(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Point the whole config dir at tmp_path and hand back the live rules path."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
    return config_module.rules_path()


@pytest.fixture
def build(monkeypatch: pytest.MonkeyPatch, rules_file):
    """Build a NudlApp *after* the test has laid out the files on disk.

    A factory rather than a plain fixture because most of these tests are about what
    happens at load time, which means the file has to exist before `NudlApp()` runs — and
    several of them build twice, to prove what the second launch does with what the first
    one left behind.

    Every toast is captured instead of shown: half of what is under test here is the
    wording nudl uses to admit it fell back.
    """

    def _build() -> tuple[NudlApp, list[str]]:
        instance = NudlApp()
        said: list[str] = []
        monkeypatch.setattr(instance.ui, "show_toast", lambda message, **_: said.append(message))
        return instance, said

    return _build


def _write(path, rules: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rules, indent=2), encoding="utf-8")


def _strips_my_tracker(instance: NudlApp) -> bool:
    result = clean.clean_result(MARKED_URL, rules=instance.rules)
    return result.params_removed == ["my_tracker"]


# -- which file is live ------------------------------------------------------------


def test_a_valid_custom_file_is_the_one_used(build, rules_file) -> None:
    _write(rules_file, CUSTOM)
    instance, _ = build()

    assert instance._rules_load.source == "custom"
    assert instance._rules_load.path == rules_file
    assert instance._rules_load.error is None
    assert _strips_my_tracker(instance), "the custom rule set loaded but is not being applied"


def test_a_good_load_leaves_a_byte_identical_backup(build, rules_file) -> None:
    _write(rules_file, CUSTOM)
    instance, _ = build()

    backup = config_module.rules_backup_path(instance.config)
    assert backup.exists()
    assert backup.read_bytes() == rules_file.read_bytes()


def test_the_backup_is_not_rewritten_when_it_already_matches(build, rules_file) -> None:
    """Starting nudl must not touch a file on disk for no reason."""
    _write(rules_file, CUSTOM)
    instance, _ = build()
    backup = config_module.rules_backup_path(instance.config)
    # Backdated by hand: a same-second rewrite could otherwise land on an identical
    # mtime and this test would pass while the file was being churned every launch.
    os.utime(backup, (0, 0))

    build()

    assert backup.stat().st_mtime == 0, "the backup was rewritten with identical bytes"


def test_a_broken_file_runs_on_the_backup_and_LEAVES_THE_USERS_FILE_ALONE(
    build, rules_file
) -> None:
    """The whole point of the backup, and the reason it is not a rename-and-restore.

    A user is halfway through editing rules.json when they restart nudl. The file does not
    parse. nudl must (a) keep working on their customisations rather than silently
    reverting to stock, and (b) not lay a finger on the file they are still editing —
    "restoring" over a half-finished edit would destroy work nobody asked it to touch.
    """
    _write(rules_file, CUSTOM)
    build()  # the first launch is what leaves the known-good copy behind

    rules_file.write_text(BROKEN, encoding="utf-8")
    instance, _ = build()

    assert instance._rules_load.source == "backup"
    assert _strips_my_tracker(instance), "fell back past the user's own rules"
    assert rules_file.read_text(encoding="utf-8") == BROKEN, "nudl overwrote the user's edit"
    assert instance._rules_load.error is not None


def test_a_broken_file_with_no_backup_falls_back_to_bundled(build, rules_file) -> None:
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(BROKEN, encoding="utf-8")
    instance, _ = build()

    assert instance._rules_load.source == "bundled"
    assert instance._rules_load.error is not None
    assert not config_module.rules_backup_path(instance.config).exists(), (
        "backed up a file that never parsed"
    )


def test_no_rules_file_at_all_is_not_an_error_worth_mentioning(build) -> None:
    """The default install. Bundled rules are the intended state, not a fallback to report."""
    instance, said = build()

    instance._warn_about_rules()

    assert instance._rules_load.source == "bundled"
    assert said == [], "nudl nagged about a rules file the user never asked for"


def test_a_future_schema_version_is_refused_and_the_toast_says_why(build, rules_file) -> None:
    """A rules file from a newer nudl. Applying it would mean ignoring fields silently."""
    _write(rules_file, {**CUSTOM, "schema_version": "2.0"})
    instance, said = build()

    assert instance._rules_load.source == "bundled"
    assert not _strips_my_tracker(instance)
    instance._warn_about_rules()
    assert "schema_version 2.0" in said[0]
    assert "Using the bundled rules." in said[0]


# -- was the user told -------------------------------------------------------------


def test_the_fallback_toast_names_the_file_and_the_reason(build, rules_file) -> None:
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(BROKEN, encoding="utf-8")
    instance, said = build()

    instance._warn_about_rules()

    assert said, "nudl fell back to the bundled rules and said nothing"
    assert said[0].startswith("nudl — rules.json is not valid JSON")
    assert said[0].endswith("Using the bundled rules.")


def test_the_backup_toast_says_it_is_running_on_the_last_good_version(build, rules_file) -> None:
    _write(rules_file, CUSTOM)
    build()
    rules_file.write_text(BROKEN, encoding="utf-8")
    instance, said = build()

    instance._warn_about_rules()

    assert said[0].endswith("Using the last version that worked.")


def test_a_long_reason_is_truncated_for_the_toast(build, rules_file) -> None:
    """A toast is a few words wide; a JSON error is not. The full text is in the log."""
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(BROKEN, encoding="utf-8")
    instance, said = build()
    instance._rules_load = clean.RulesLoad(
        instance.rules, "bundled", None, "is not valid JSON (" + "x" * 300 + ")"
    )

    instance._warn_about_rules()

    assert "…" in said[0]
    assert len(said[0]) < 160


# -- Rules… --------------------------------------------------------------------------


def test_open_rules_seeds_the_file_from_the_bundled_set(build, rules_file, monkeypatch) -> None:
    """A first-time user gets the commented bundled file, not a blank one to invent from."""
    instance, _ = build()
    opened: list = []
    monkeypatch.setattr(instance, "_open", opened.append)

    instance._open_rules()

    assert rules_file.read_bytes() == clean.BUNDLED_RULES_PATH.read_bytes()
    assert opened == [rules_file]


def test_open_rules_never_clobbers_what_is_already_there(build, rules_file, monkeypatch) -> None:
    """Even a file that does not parse. Especially a file that does not parse: that is a
    user mid-edit, and it is the one thing they cannot get back."""
    rules_file.parent.mkdir(parents=True, exist_ok=True)
    rules_file.write_text(BROKEN, encoding="utf-8")
    instance, _ = build()
    monkeypatch.setattr(instance, "_open", lambda _path: None)

    instance._open_rules()

    assert rules_file.read_text(encoding="utf-8") == BROKEN


# -- Validate rules ------------------------------------------------------------------


def test_validate_adopts_the_edited_file_and_reports_the_counts(build, rules_file) -> None:
    """Validating and then NOT applying would leave nudl on rules the user has replaced."""
    _write(rules_file, CUSTOM)
    instance, said = build()
    _write(rules_file, {**CUSTOM, "global_tracker_keys": ["my_tracker", "other_tracker"]})

    instance._validate_rules()

    assert instance.rules["global_tracker_keys"] == ["my_tracker", "other_tracker"]
    assert instance._rules_load.source == "custom"
    assert said[-1] == "nudl — rules reloaded: 2 global keys, 0 providers, 0 wrappers"
    assert config_module.rules_backup_path(instance.config).read_bytes() == rules_file.read_bytes()


def test_validate_on_a_broken_file_changes_nothing(build, rules_file) -> None:
    _write(rules_file, CUSTOM)
    instance, said = build()
    live = instance.rules
    good = config_module.rules_backup_path(instance.config).read_bytes()
    rules_file.write_text(BROKEN, encoding="utf-8")

    instance._validate_rules()

    assert instance.rules is live, "a file that does not parse replaced the working rules"
    assert said[-1].startswith("nudl — rules.json is not valid JSON")
    assert said[-1].endswith("Still using the rules already loaded.")
    assert config_module.rules_backup_path(instance.config).read_bytes() == good


def test_validate_answers_about_the_USERS_file_not_the_backup(build, rules_file) -> None:
    """No `backup=` on this path: falling back would answer a question nobody asked."""
    _write(rules_file, CUSTOM)
    instance, said = build()
    rules_file.write_text(BROKEN, encoding="utf-8")

    instance._validate_rules()

    assert "is not valid JSON" in said[-1], "validate reported on the backup, hiding the answer"


# -- About ---------------------------------------------------------------------------


def test_about_shows_when_the_live_rules_were_updated(build, rules_file) -> None:
    _write(rules_file, CUSTOM)
    instance, said = build()

    instance._show_about()

    assert said[-1] == (
        "nudl — local only. Your links never leave this machine. Rules updated 2026-08-01."
    )


def test_about_omits_the_date_when_the_rules_carry_none(build, rules_file) -> None:
    _write(rules_file, {k: v for k, v in CUSTOM.items() if k != "last_updated"})
    instance, said = build()

    instance._show_about()

    assert said[-1] == "nudl — local only. Your links never leave this machine."
