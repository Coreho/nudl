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


def test_a_valid_partial_rule_set_is_accepted(tmp_path: Path) -> None:
    """A user trimming the rules down to just what they want is legitimate."""
    mine = tmp_path / "rules.json"
    mine.write_text(json.dumps({"global_tracker_keys": ["fbclid"]}), encoding="utf-8")

    rules = clean.load_rules(mine)

    assert clean.clean("https://e.com/a?fbclid=x&id=1", rules=rules) == "https://e.com/a?id=1"
    # utm_source is NOT in their list, so it survives. Their file, their rules.
    assert (
        clean.clean("https://e.com/a?utm_source=x", rules=rules) == "https://e.com/a?utm_source=x"
    )


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
