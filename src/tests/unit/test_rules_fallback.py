"""A broken rule file must never make nudl silently stop cleaning.

The dangerous failure here isn't a crash — it's nudl sitting in the tray looking healthy
while quietly cleaning nothing at all. That's the one a user would never notice.
"""

from __future__ import annotations

import json
from pathlib import Path

from src import clean


def test_a_corrupt_user_file_falls_back_to_the_bundled_rules(tmp_path: Path) -> None:
    bad = tmp_path / "rules.json"
    bad.write_text("{ not json at all", encoding="utf-8")

    rules = clean.load_rules(bad)

    assert clean.clean("https://e.com/a?utm_source=x&id=1", rules=rules) == "https://e.com/a?id=1"


def test_a_missing_file_falls_back(tmp_path: Path) -> None:
    rules = clean.load_rules(tmp_path / "nope.json")
    assert clean.clean("https://e.com/a?fbclid=x&id=1", rules=rules) == "https://e.com/a?id=1"


def test_a_rule_set_with_a_WRONG_TYPED_key_is_rejected(tmp_path: Path) -> None:
    """The bug this exists for.

    `_rules_are_sane` used to accept this: `global_tracker_keys` is a list, so `any()`
    was happy. Then `providers` — a string — would be iterated deep in the pipeline,
    raise AttributeError on every URL, get swallowed by the fail-safe, and nudl would
    return every link untouched. Forever. Silently.
    """
    bad = tmp_path / "rules.json"
    bad.write_text(
        json.dumps({"global_tracker_keys": ["utm_source"], "providers": "oops"}),
        encoding="utf-8",
    )

    rules = clean.load_rules(bad)

    # It must have been REJECTED in favour of the bundled defaults...
    assert rules is not None
    assert isinstance(rules.get("providers"), list)
    # ...and cleaning must still work.
    assert clean.clean("https://e.com/a?utm_source=x&id=1", rules=rules) == "https://e.com/a?id=1"


def test_a_small_user_file_adds_to_the_bundled_rules_rather_than_replacing_them(
    tmp_path: Path,
) -> None:
    """The file holds only what the user wants on top. Every bundled rule still applies.

    It used to replace the bundled set outright, so a user who once added one key was
    frozen on that version's rules forever — later releases' trackers passed them by.
    """
    mine = tmp_path / "rules.json"
    mine.write_text(json.dumps({"global_tracker_keys": ["my_tracker"]}), encoding="utf-8")

    rules = clean.load_rules(mine)

    assert clean.clean("https://e.com/a?my_tracker=x&id=1", rules=rules) == "https://e.com/a?id=1"
    assert clean.clean("https://e.com/a?utm_source=x", rules=rules) == "https://e.com/a"


def test_keep_switches_a_bundled_rule_off_even_inside_a_regex(tmp_path: Path) -> None:
    """`keep` is how a user overrides the bundled set — and it beats `^utm_.*$`."""
    mine = tmp_path / "rules.json"
    mine.write_text(json.dumps({"keep": ["utm_campaign", "FBCLID"]}), encoding="utf-8")

    rules = clean.load_rules(mine)

    assert (
        clean.clean("https://e.com/a?utm_source=x&utm_campaign=y&fbclid=z", rules=rules)
        == "https://e.com/a?utm_campaign=y&fbclid=z"
    )


def test_a_user_provider_with_a_bundled_name_extends_it(tmp_path: Path) -> None:
    mine = tmp_path / "rules.json"
    mine.write_text(
        json.dumps({"providers": [{"name": "youtube", "rules": ["ab_channel"]}]}),
        encoding="utf-8",
    )

    rules = clean.load_rules(mine)

    youtube = [p for p in rules["providers"] if p["name"] == "youtube"]
    assert len(youtube) == 1, "the provider was duplicated instead of extended"
    assert (
        clean.clean("https://www.youtube.com/watch?v=a&ab_channel=b&si=c", rules=rules)
        == "https://www.youtube.com/watch?v=a"
    )


def test_junk_entries_in_a_user_file_are_dropped_not_fatal(tmp_path: Path) -> None:
    """One dict in a list of patterns must not make every clean fail."""
    mine = tmp_path / "rules.json"
    mine.write_text(
        json.dumps({"global_tracker_keys": [{"oops": 1}, 7, "my_tracker"]}), encoding="utf-8"
    )

    rules = clean.load_rules(mine)

    assert clean.clean("https://e.com/a?my_tracker=x&fbclid=y", rules=rules) == "https://e.com/a"


def test_emergency_rules_still_clean_the_obvious_offenders() -> None:
    """If even the BUNDLED file is unreadable, nudl must still do something useful."""
    rules = clean.EMERGENCY_RULES
    assert clean.clean("https://e.com/a?utm_source=x&fbclid=y&id=1", rules=rules) == (
        "https://e.com/a?id=1"
    )


def test_the_sanity_check_rejects_junk() -> None:
    assert clean._rules_are_sane({"providers": "not a list"}) is False
    assert clean._rules_are_sane({"global_tracker_keys": {}}) is False
    assert clean._rules_are_sane([]) is False
    assert clean._rules_are_sane("nope") is False
    assert clean._rules_are_sane({"unrelated": 1}) is False
    assert clean._rules_are_sane({"global_tracker_keys": ["utm_source"]}) is True
