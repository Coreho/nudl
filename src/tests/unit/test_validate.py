"""The validator's own tripwires.

The standing gate is `test_the_shipped_rules_have_no_errors`. Everything else here checks
that the validator tells the truth about one rule; that test checks that the rules we
actually ship are still worth shipping. If somebody lands a pattern that would break
signed links, strip a deliberately absent key, or silently disable a provider, this is
where it stops — before a release, rather than in a user's clipboard.

Pure Python by design: `validate.py` imports nothing from the Windows layer, so these run
on any platform and need no `conftest.py` entry. Do not name the Windows extension
package in this file — `test_conftest_paths.py` scans the raw source for it, so even a
mention in prose makes the guard believe this module needs Windows.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import clean, validate

GOOD_RULES = {
    "schema_version": "1.0",
    "global_tracker_keys": ["^utm_.*$", "fbclid"],
    "redirect_wrappers": [{"host": "l.example.com", "path": "/l.php", "param": "u"}],
    "referral": [],
    "providers": [
        {
            "name": "example",
            "urlPattern": "(^|\\.)example\\.com$",
            "exceptions": [],
            "rules": ["ref_"],
            "rawRules": [],
            "referral": [],
        }
    ],
}


def _levels(findings: list[validate.Finding]) -> set[str]:
    return {finding.level for finding in findings}


def _text(findings: list[validate.Finding]) -> str:
    return " | ".join(finding.message for finding in findings)


def _write(path: Path, rules: dict) -> Path:
    path.write_text(json.dumps(rules), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------------------


def test_the_shipped_rules_have_no_errors() -> None:
    """The rules nudl ships must pass its own validator. No exceptions, no allowlist."""
    rules = json.loads(clean.BUNDLED_RULES_PATH.read_text(encoding="utf-8"))

    errors = [f for f in validate.check_rules(rules) if f.level == "error"]

    assert errors == [], "\n".join(f.describe() for f in errors)


# ---------------------------------------------------------------------------------------
# One pattern at a time
# ---------------------------------------------------------------------------------------


def test_a_pattern_that_does_not_compile_is_an_error() -> None:
    """The engine skips it, so the rule the user thinks they wrote does not exist."""
    findings = validate.check_pattern("^utm_[a-z$", "global_tracker_keys[0]")

    assert _levels(findings) == {"error"}
    assert "not a valid pattern" in _text(findings)
    # Nothing else is knowable about it, so exactly one finding — not a pile of noise.
    assert len(findings) == 1


def test_an_empty_pattern_is_an_error() -> None:
    assert _levels(validate.check_pattern("  ")) == {"error"}


@pytest.mark.parametrize("pattern", ["token", "^x-amz-.*$", "signature", "expires"])
def test_a_pattern_matching_a_signed_key_is_an_error(pattern: str) -> None:
    """Stripping any part of a signed URL turns the whole link into a 403."""
    findings = validate.check_pattern(pattern)

    assert "error" in _levels(findings)
    assert "signed-request key" in _text(findings)


@pytest.mark.parametrize("pattern", ["sk", "si", "check_in"])
def test_a_pattern_matching_a_never_strip_key_warns_with_the_reason(pattern: str) -> None:
    """The reason is the point: the user needs to know WHY it was left out."""
    findings = validate.check_pattern(pattern)

    assert "warning" in _levels(findings)
    assert "error" not in _levels(findings)
    assert clean.NEVER_STRIP[pattern] in _text(findings)
    assert "deliberately absent" in _text(findings)


def test_a_pattern_matching_everything_is_an_error() -> None:
    """`^.*$` is not a rule, it is a decision to strip every parameter on every link."""
    findings = validate.check_pattern("^.*$", "global_tracker_keys[0]")

    errors = [f for f in findings if f.level == "error"]
    assert errors
    assert any("every ordinary functional key" in f.message for f in errors)


def test_a_functional_key_warns_and_says_which() -> None:
    """`id` is not a tracker anywhere, and `sid` — Booking.com's session id — contains it.

    Both warnings matter for the same reason: `id` is harmless as the exact key it is, but
    the day somebody widens it to `^.*id.*$` it starts eating `sid` too.
    """
    findings = validate.check_pattern("id")

    assert _levels(findings) == {"warning"}
    assert "functional key(s) id" in _text(findings)
    assert "sid" in _text(findings)
    assert "substring" in _text(findings)


def test_an_ordinary_tracker_pattern_is_clean() -> None:
    assert validate.check_pattern("^utm_.*$") == []
    assert validate.check_pattern("fbclid") == []
    assert validate.check_pattern("pd_rd_*") == []


# ---------------------------------------------------------------------------------------
# A whole rule set
# ---------------------------------------------------------------------------------------


def test_a_newer_schema_version_is_an_error() -> None:
    """A file written for a future nudl would be half-read, which is worse than refused."""
    findings = validate.check_rules({"schema_version": "2.0", "global_tracker_keys": []})

    errors = [f for f in findings if f.level == "error"]
    assert [f.where for f in errors] == ["schema_version"]
    assert "understands 1.0" in errors[0].message


def test_a_provider_pattern_that_does_not_compile_is_an_error() -> None:
    rules = {"providers": [{"name": "broken", "urlPattern": "(^|\\.example\\.com$"}]}

    findings = validate.check_rules(rules)

    assert any(f.level == "error" and "does not compile" in f.message for f in findings)
    # The name, not just the index — a user should not have to count providers.
    assert any("broken" in f.where for f in findings)


def test_a_provider_pattern_that_matches_anything_is_an_error() -> None:
    """Too broad is the dangerous direction: site rules firing on every site."""
    rules = {"providers": [{"name": "greedy", "urlPattern": ".*", "rules": ["ref_"]}]}

    findings = validate.check_rules(rules)

    assert any(f.level == "error" and "example.invalid" in f.message for f in findings)


def test_a_provider_with_no_url_pattern_is_an_error() -> None:
    """The engine skips it entirely, so its rules never fire and nobody notices."""
    findings = validate.check_rules({"providers": [{"name": "orphan", "rules": ["fbclid"]}]})

    assert any(f.level == "error" and "skips this provider" in f.message for f in findings)


def test_raw_rules_are_checked_for_a_pattern_and_a_replacement() -> None:
    rules = {
        "providers": [
            {
                "name": "example",
                "urlPattern": "(^|\\.)example\\.com$",
                "rawRules": [{"pattern": "(["}, {"pattern": "/ref/[^/]+"}],
            }
        ]
    }

    findings = validate.check_rules(rules)

    assert any(f.level == "error" and "does not compile" in f.message for f in findings)
    assert sum(f.level == "warning" and "no replacement" in f.message for f in findings) == 2


def test_a_broken_exception_regex_is_an_error() -> None:
    rules = {
        "providers": [
            {"name": "e", "urlPattern": "(^|\\.)example\\.com$", "exceptions": ["(unclosed"]}
        ]
    }

    findings = validate.check_rules(rules)

    assert any(f.level == "error" and "protects nothing" in f.message for f in findings)


@pytest.mark.parametrize(
    ("wrapper", "expected"),
    [
        ({"path": "/l.php", "param": "u"}, "has no host"),
        ({"host": "l.example.com", "path": "/l.php"}, "has no param"),
        ({"host": "l.example.com", "param": "u"}, "must start with '/'"),
        ({"host": "l.example.com", "path": "l.php", "param": "u"}, "must start with '/'"),
    ],
)
def test_a_broken_wrapper_is_an_error(wrapper: dict, expected: str) -> None:
    findings = validate.check_rules({"redirect_wrappers": [wrapper]})

    assert any(f.level == "error" and expected in f.message for f in findings)


def test_the_good_rule_set_is_silent() -> None:
    assert validate.check_rules(GOOD_RULES) == []


def test_wrong_types_are_reported_instead_of_crashing() -> None:
    """This is a file a human hand-edited. Every field may be the wrong shape."""
    rules = {
        "global_tracker_keys": "fbclid",
        "referral": [7],
        "providers": ["nope"],
        "redirect_wrappers": [None],
    }

    findings = validate.check_rules(rules)

    assert _levels(findings) == {"error"}
    assert len(findings) == 4
    assert validate.check_rules([]) != []  # type: ignore[arg-type]


# ---------------------------------------------------------------------------------------
# Testing a pattern against a URL
# ---------------------------------------------------------------------------------------


def test_test_pattern_reports_what_the_engine_really_did() -> None:
    """`tag` surviving is the assertion that matters.

    Amazon's provider rules DO strip `tag`, so if this ran against the shipped rule set
    instead of a synthetic one it would disappear — and the user would blame their own
    pattern for a removal it had nothing to do with.
    """
    url = "https://www.amazon.com/dp/B08X?utm_source=google&tag=aff-20&v=123"

    result = validate.test_pattern("^utm_.*$", url)

    assert result.removed == ["utm_source"]
    assert result.kept == ["tag", "v"]
    assert result.after == "https://www.amazon.com/dp/B08X?tag=aff-20&v=123"
    assert result.findings == []


def test_test_pattern_explains_doing_nothing() -> None:
    """ "Nothing happened" with no reason is what sends a user hunting for a bug."""
    signed = validate.test_pattern("token", "https://cdn.example.com/f?token=abc&utm_source=x")

    assert signed.removed == []
    assert signed.after == signed.before
    assert "signed-request key" in signed.describe()
    assert "refuses to touch" in signed.describe()


def test_test_pattern_can_run_inside_a_real_rule_set() -> None:
    """Passing rules shows the pattern working alongside everything else in the file."""
    url = "https://www.amazon.com/dp/B08X?tag=aff-20&mine=1"

    result = validate.test_pattern("mine", url, rules=clean.load_rules())

    assert sorted(result.removed) == ["mine", "tag"]


# ---------------------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------------------


def test_check_passes_on_a_good_file(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    path = _write(tmp_path / "rules.json", GOOD_RULES)

    code = validate.main(["--check", "--rules", str(path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "No problems found." in out
    assert str(path) in out
    assert "2 global keys, 1 providers, 1 wrappers" in out


def test_check_passes_on_the_shipped_file(capsys: pytest.CaptureFixture) -> None:
    """The acceptance check a release runs: `python -m src.validate --check`."""
    code = validate.main(["--check", "--rules", str(clean.BUNDLED_RULES_PATH)])

    assert capsys.readouterr().out
    assert code == 0


def test_check_fails_on_a_broken_provider_regex(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    rules = json.loads(json.dumps(GOOD_RULES))
    rules["providers"][0]["urlPattern"] = "(^|\\.example\\.com$"
    path = _write(tmp_path / "rules.json", rules)

    code = validate.main(["--check", "--rules", str(path)])

    out = capsys.readouterr().out
    assert code == 1
    assert "does not compile" in out
    assert "1 error, 0 warnings" in out


def test_check_reports_a_file_that_nudl_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The file exists, the user wrote it, nudl is ignoring it. That is the whole question."""
    path = tmp_path / "rules.json"
    path.write_text("{ not json at all", encoding="utf-8")

    code = validate.main(["--check", "--rules", str(path)])

    out = capsys.readouterr().out
    assert code == 1
    assert "is not valid JSON" in out
    # And it must be obvious that the counts shown belong to the fallback, not to them.
    assert "Source:   bundled" in out


def test_check_does_not_treat_a_missing_file_as_a_problem(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A custom rules file is opt-in. Not having one is the normal state, not an error.

    What gets checked in that case is the bundled set, because that is what is genuinely
    live — so the report may carry warnings, but not one error.
    """
    code = validate.main(["--check", "--rules", str(tmp_path / "absent.json")])

    out = capsys.readouterr().out
    assert code == 0
    assert "does not exist" in out
    assert [line for line in out.splitlines() if line.startswith("error")] == []


def test_test_mode_prints_the_before_and_after(capsys: pytest.CaptureFixture) -> None:
    url = "https://www.amazon.com/dp/B08X?tag=aff-20&psc=1"

    code = validate.main(["--test", "tag", "--url", url])

    out = capsys.readouterr().out
    assert code == 0
    assert f"Before:   {url}" in out
    assert "After:    https://www.amazon.com/dp/B08X?psc=1" in out
    assert "Removed:  tag" in out
    assert "Kept:     psc" in out


# ---------------------------------------------------------------------------------------
# The REPL, and the guarantee that makes it safe
# ---------------------------------------------------------------------------------------


def _session(tmp_path: Path) -> tuple[validate.Session, Path, Path]:
    active = _write(tmp_path / "rules.json", {"global_tracker_keys": ["fbclid"]})
    backup = tmp_path / "rules.json.bak"
    backup.write_text("last known good", encoding="utf-8")
    session = validate.Session(rules={"global_tracker_keys": ["mine"]}, reserved=(active, backup))
    return session, active, backup


def test_save_refuses_to_write_the_active_rules_file(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The hard guarantee. A tool that eats the file it was asked to check is worse than
    no tool at all."""
    session, active, backup = _session(tmp_path)

    validate.run_command(session, f'save "{active}"')
    validate.run_command(session, f'save "{backup}"')
    # Spelled differently, same file — the refusal must not be that easy to walk around.
    validate.run_command(session, f'save "{tmp_path / "sub" / ".." / "rules.json"}"')

    out = capsys.readouterr().out
    assert out.count("refused") == 3
    assert json.loads(active.read_text(encoding="utf-8")) == {"global_tracker_keys": ["fbclid"]}
    assert backup.read_text(encoding="utf-8") == "last known good"


def test_save_writes_anywhere_else(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    session, _, _ = _session(tmp_path)
    target = tmp_path / "experiment" / "mine.json"

    validate.run_command(session, f'save "{target}"')

    assert "wrote" in capsys.readouterr().out
    assert json.loads(target.read_text(encoding="utf-8")) == {"global_tracker_keys": ["mine"]}


def test_add_and_remove_touch_only_the_copy(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    session, active, _ = _session(tmp_path)

    validate.run_command(session, "add sk")
    validate.run_command(session, "remove mine")

    out = capsys.readouterr().out
    assert session.rules["global_tracker_keys"] == ["sk"]
    assert "in memory only" in out
    # Adding a deliberately absent key must say so at the moment it is added.
    assert clean.NEVER_STRIP["sk"] in out
    assert json.loads(active.read_text(encoding="utf-8")) == {"global_tracker_keys": ["fbclid"]}


def test_a_typo_does_not_end_the_session(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    """A validator that dies on a stray quote sends the user back to editing blind."""
    session, _, _ = _session(tmp_path)

    assert validate.run_command(session, 'test "unclosed') is True
    assert validate.run_command(session, "bogus") is True
    assert validate.run_command(session, "") is True
    assert validate.run_command(session, "test") is True
    assert validate.run_command(session, "quit") is False

    out = capsys.readouterr().out
    assert "No closing quotation" in out
    assert "unknown command 'bogus'" in out
    assert "usage: test" in out


def test_the_url_is_remembered_across_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    session, _, _ = _session(tmp_path)

    validate.run_command(session, 'test fbclid --url "https://e.com/a?fbclid=x&id=1"')
    capsys.readouterr()
    validate.run_command(session, "test id")

    assert session.url == "https://e.com/a?fbclid=x&id=1"
    assert "Removed:  id" in capsys.readouterr().out


def test_wrapper_and_raw_report_the_engines_verdict(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    session, _, _ = _session(tmp_path)
    wrapped = "https://l.facebook.com/l.php?u=https%3A%2F%2Fe.com%2Fa"

    validate.run_command(session, f'wrapper l.facebook.com /l.php u --url "{wrapped}"')
    validate.run_command(session, "wrapper l.facebook.com /wrong u")
    validate.run_command(session, 'raw "e" "eeee"')

    out = capsys.readouterr().out
    assert "Target: https://e.com/a" in out
    assert "wrapper path is '/wrong'" in out
    # A rawRule may only ever shorten a URL; the engine discards anything else.
    assert "longer than the original" in out
