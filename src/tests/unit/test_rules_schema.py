"""The rule loader must never lie about which rules are live.

Two failure modes are guarded here. The first is a rule file written for a future nudl
being half-read by this one: it declares fields this engine does not know, the engine
ignores them, and the user gets a tray icon that looks healthy while enforcing half a
rule set. The second is the loader falling back without saying so — a user who typos
their rules.json and is told nothing goes hunting through a file that was never even
opened.

The last test in this file is the standing tripwire: no pattern in the SHIPPED rule set
may ever match a key in `NEVER_STRIP`. `test_clean.py` catches those nine keys through
specific URLs; this catches them at the source, so a pattern broad enough to swallow one
fails the moment it is added rather than the moment a user's link breaks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src import clean

#: Enough of a rule set to be accepted, distinctive enough that we can tell it apart from
#: the bundled defaults when it is the one that loaded.
MINE: dict[str, Any] = {"global_tracker_keys": ["mine_only_tracker"]}
YOURS: dict[str, Any] = {"global_tracker_keys": ["backup_only_tracker"]}


def write(path: Path, rules: dict[str, Any]) -> Path:
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


def corrupt(path: Path) -> Path:
    path.write_text("{ not json at all", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------
# The schema version gate
# ---------------------------------------------------------------------------------------


def test_a_file_with_no_schema_version_is_accepted(tmp_path: Path) -> None:
    """Back-compat is the entire reason the default exists.

    Every rules.json written before this feature shipped — including one a user has been
    hand-editing for months — has no schema_version. Refusing those would break working
    installs on upgrade, which is exactly the kind of self-inflicted outage this project
    refuses to ship.
    """
    load = clean.load_rules_verbose(write(tmp_path / "rules.json", MINE))

    assert load.source == "custom"
    assert load.error is None
    assert load.rules["global_tracker_keys"] == ["mine_only_tracker"]


def test_the_current_schema_version_is_accepted(tmp_path: Path) -> None:
    rules = dict(MINE, schema_version="1.0")
    load = clean.load_rules_verbose(write(tmp_path / "rules.json", rules))

    assert load.source == "custom"
    assert load.ok is True


def test_a_newer_schema_version_is_refused_and_says_so(tmp_path: Path) -> None:
    """The dangerous one.

    A 2.0 file was written for an engine that understands fields this one would skip
    straight past. Reading it anyway means enforcing whatever subset happens to overlap
    and telling the user nothing — nudl looking healthy while cleaning less than the
    user's file says it should.
    """
    rules = dict(MINE, schema_version="2.0")
    load = clean.load_rules_verbose(write(tmp_path / "rules.json", rules))

    assert load.source == "bundled"
    assert load.ok is False
    assert "2.0" in (load.error or "")
    assert "schema_version" in (load.error or "")


def test_an_older_schema_version_is_accepted(tmp_path: Path) -> None:
    """Only NEWER is a refusal. An older file describes a subset this engine can read."""
    rules = dict(MINE, schema_version="0.9")
    load = clean.load_rules_verbose(write(tmp_path / "rules.json", rules))

    assert load.source == "custom"
    assert load.error is None


def test_a_non_string_schema_version_is_refused_with_a_reason(tmp_path: Path) -> None:
    """`"schema_version": 1.0` is a plausible hand-edit and is not a version string.

    Comparing a float against the parsed version parts would raise, and a raise here
    means the whole file is dropped with no explanation.
    """
    rules = dict(MINE, schema_version=1.0)
    load = clean.load_rules_verbose(write(tmp_path / "rules.json", rules))

    assert load.source == "bundled"
    assert "non-string schema_version" in (load.error or "")


def test_an_unparseable_schema_version_is_refused_with_a_reason(tmp_path: Path) -> None:
    rules = dict(MINE, schema_version="banana")
    load = clean.load_rules_verbose(write(tmp_path / "rules.json", rules))

    assert load.source == "bundled"
    assert "unreadable schema_version" in (load.error or "")
    assert "banana" in (load.error or "")


# ---------------------------------------------------------------------------------------
# Where the rules came from, and why they are not the ones you asked for
# ---------------------------------------------------------------------------------------


def test_a_valid_custom_file_reports_itself_as_the_source(tmp_path: Path) -> None:
    path = write(tmp_path / "rules.json", MINE)
    load = clean.load_rules_verbose(path)

    assert load.source == "custom"
    assert load.path == path
    assert load.error is None
    assert load.ok is True


def test_no_path_at_all_reports_the_bundled_rules() -> None:
    load = clean.load_rules_verbose()

    assert load.source == "bundled"
    assert load.error is None
    assert load.path == clean.BUNDLED_RULES_PATH


def test_a_broken_file_with_a_good_backup_runs_on_the_backup(tmp_path: Path) -> None:
    """The user keeps their customisations for this run instead of reverting to stock.

    Note what the error describes: the PRIMARY file. The backup loaded fine — reporting
    anything about it would send the user editing the wrong file.
    """
    primary = corrupt(tmp_path / "rules.json")
    backup = write(tmp_path / "rules.json.bak", YOURS)

    load = clean.load_rules_verbose(primary, backup=backup)

    assert load.source == "backup"
    assert load.path == backup
    assert load.rules["global_tracker_keys"] == ["backup_only_tracker"]
    assert "is not valid JSON" in (load.error or "")


def test_a_broken_file_with_a_broken_backup_falls_all_the_way_to_bundled(
    tmp_path: Path,
) -> None:
    primary = corrupt(tmp_path / "rules.json")
    backup = corrupt(tmp_path / "rules.json.bak")

    load = clean.load_rules_verbose(primary, backup=backup)

    assert load.source == "bundled"
    assert load.ok is False
    assert "is not valid JSON" in (load.error or "")


def test_a_broken_file_with_no_backup_falls_back_to_bundled(tmp_path: Path) -> None:
    load = clean.load_rules_verbose(corrupt(tmp_path / "rules.json"))

    assert load.source == "bundled"
    assert load.error is not None


def test_a_working_file_beats_its_backup(tmp_path: Path) -> None:
    """A backup must never win over a file that loads.

    The backup is last-known-good, not preferred: silently running yesterday's rules on
    top of a file the user just fixed would make their edits look like they did nothing.
    """
    primary = write(tmp_path / "rules.json", MINE)
    backup = write(tmp_path / "rules.json.bak", YOURS)

    load = clean.load_rules_verbose(primary, backup=backup)

    assert load.source == "custom"
    assert load.rules["global_tracker_keys"] == ["mine_only_tracker"]


def test_the_distinct_failures_are_distinguishable(tmp_path: Path) -> None:
    """One generic "your rules were ignored" is a message a user cannot act on."""
    missing = clean.load_rules_verbose(tmp_path / "nope.json")
    assert "does not exist" in (missing.error or "")

    invalid = clean.load_rules_verbose(corrupt(tmp_path / "bad.json"))
    assert "is not valid JSON" in (invalid.error or "")

    # `providers` a string passes a naive "some key is a list" check, then raises deep in
    # the pipeline on every URL — the silent no-op this shape check exists to stop.
    shape = write(tmp_path / "shape.json", {"global_tracker_keys": [], "providers": "oops"})
    assert "is not a rule set" in (clean.load_rules_verbose(shape).error or "")

    future = write(tmp_path / "future.json", dict(MINE, schema_version="2.0"))
    assert "needs schema_version 2.0" in (clean.load_rules_verbose(future).error or "")


# ---------------------------------------------------------------------------------------
# The old API, unchanged
# ---------------------------------------------------------------------------------------


def test_load_rules_still_returns_a_plain_dict(tmp_path: Path) -> None:
    rules = clean.load_rules(write(tmp_path / "rules.json", MINE))

    assert isinstance(rules, dict)
    assert rules["global_tracker_keys"] == ["mine_only_tracker"]


def test_load_rules_still_falls_back_on_a_corrupt_file(tmp_path: Path) -> None:
    """Routing the legacy entry point through the new loader must not change what callers
    already depend on: a dict that cleans, never an exception and never an empty rule set.
    """
    rules = clean.load_rules(corrupt(tmp_path / "rules.json"))

    assert isinstance(rules, dict)
    assert clean.clean("https://e.com/a?utm_source=x&id=1", rules=rules) == "https://e.com/a?id=1"


# ---------------------------------------------------------------------------------------
# Counting what is actually loaded
# ---------------------------------------------------------------------------------------


def test_the_bundled_rules_summarise_to_their_shipped_counts() -> None:
    """Hardcoded on purpose.

    These numbers are what the tray reports back to the user. If someone edits rules.json
    the count moves and this test makes them look at it, which is the point — a rule
    quietly vanishing from the shipped set is invisible any other way.
    """
    summary = clean.summarize(clean.load_rules())

    assert summary.global_keys == 26
    assert summary.providers == 16
    assert summary.wrappers == 9
    assert summary.referral == 4
    assert summary.schema_version == "1.0"
    assert summary.last_updated == "2026-07-13"


def test_describe_reads_as_a_sentence() -> None:
    summary = clean.summarize(clean.load_rules())
    assert summary.describe() == "26 global keys, 16 providers, 9 wrappers"


def test_wrong_typed_fields_summarise_to_zero_instead_of_raising() -> None:
    """The counter runs on whatever the user hand-edited, including nonsense.

    A raise here would take out the tray menu that displays it — nudl dying because
    somebody typed a number where a list goes.
    """
    summary = clean.summarize({"providers": 5, "global_tracker_keys": "nope"})

    assert summary.global_keys == 0
    assert summary.providers == 0
    assert summary.provider_keys == 0
    assert summary.wrappers == 0
    assert summary.referral == 0


def test_a_missing_last_updated_is_none() -> None:
    assert clean.summarize(MINE).last_updated is None


def test_a_non_string_last_updated_is_none() -> None:
    """Better no date than `2026` rendered as a date the user might trust."""
    assert clean.summarize(dict(MINE, last_updated=20260713)).last_updated is None


# ---------------------------------------------------------------------------------------
# The tripwire
# ---------------------------------------------------------------------------------------


def shipped_patterns() -> list[tuple[str, str]]:
    """Every key pattern in the bundled rule set, paired with where it came from."""
    rules = clean.load_rules()
    found = [("global_tracker_keys", p) for p in rules.get("global_tracker_keys", [])]
    found += [("referral", p) for p in rules.get("referral", [])]
    for provider in rules.get("providers", []):
        name = provider.get("name", "?")
        found += [(f"providers[{name}].rules", p) for p in provider.get("rules", [])]
        found += [(f"providers[{name}].referral", p) for p in provider.get("referral", [])]
    return [(where, p) for where, p in found if isinstance(p, str)]


def test_no_shipped_pattern_matches_a_never_strip_key() -> None:
    """The standing tripwire.

    `test_clean.py` proves the nine keys survive specific URLs. This proves it from the
    other end, against the rules themselves, so a pattern broad enough to swallow one
    fails here the moment it is written — a new `^s.*$` would eat `si`, `sk` and `sid`
    and break shared Spotify links, Medium friend links and Booking sessions at once.
    """
    patterns = shipped_patterns()
    assert patterns, "the bundled rule set has no patterns — the tripwire is not armed"

    offences = [
        f"{where}: pattern {pattern!r} matches NEVER_STRIP key {key!r} — {why}"
        for where, pattern in patterns
        for key, why in clean.NEVER_STRIP.items()
        if clean._key_matcher(pattern).fullmatch(key)
    ]

    assert not offences, "\n".join(offences)
