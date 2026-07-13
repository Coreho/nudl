"""The audit log exists so a suspicious user can check nudl's work.

That only works if a human can actually read it. These tests pin the layout.
"""

from __future__ import annotations

from src.app import format_log_entry, redact
from src.clean import CleanResult

STAMP = "2026-07-13 01:09:16"


def test_credentials_are_redacted_before_they_hit_disk() -> None:
    """The audit log is plaintext and lives forever. A URL can legally carry a password.

    Logging it would turn nudl's trust feature into a credential leak.
    """
    entry = format_log_entry(
        CleanResult(
            original="https://alice:hunter2@intranet.corp/doc?utm_source=x&id=1",
            result="https://alice:hunter2@intranet.corp/doc?id=1",
            params_removed=["utm_source"],
        ),
        STAMP,
    )
    assert "hunter2" not in entry
    assert "alice" not in entry
    assert "***@intranet.corp" in entry
    assert "id=1" in entry  # the log still has to be useful


def test_secret_bearing_params_never_reach_the_log() -> None:
    """A one-time OAuth code is not a tracker, so nudl leaves it in your link — correctly.

    But the audit log is an append-only plaintext file that lives forever, and writing the
    code into it verbatim would quietly turn nudl's trust feature into a credential store.
    The param NAME survives so the log still shows what the link was carrying; the value
    does not.
    """
    entry = format_log_entry(
        CleanResult(
            original="https://app.example.com/callback?code=SECRET-OAUTH-CODE&utm_source=n",
            result="https://app.example.com/callback?code=SECRET-OAUTH-CODE",
            params_removed=["utm_source"],
        ),
        STAMP,
    )
    assert "SECRET-OAUTH-CODE" not in entry
    assert "code=***" in entry
    assert "removed 1 tracker: utm_source" in entry


def test_redaction_leaves_an_ordinary_link_byte_for_byte() -> None:
    """Over-redacting is its own failure: a log nobody can read is a log nobody trusts."""
    url = "https://www.amazon.com/dp/B08X7QK2P?psc=1&th=1"
    assert redact(url) == url


def test_a_rawrule_rewrite_is_not_called_a_redirect() -> None:
    """Same host, nothing named: that is a rawRule, not an unwrap. Say so.

    An audit log is the one place a convenient lie is least affordable — it is the file
    the suspicious user opens precisely because they do not take nudl's word for it.
    """
    entry = format_log_entry(
        CleanResult(
            original="https://example.com/watch#t=30&utm_source=x",
            result="https://example.com/watch#t=30",
        ),
        STAMP,
    )
    assert "rewrote the link" in entry
    assert "unwrapped a redirect" not in entry


def test_redact_leaves_ordinary_urls_alone() -> None:
    url = "https://example.com/a?id=1"
    assert redact(url) == url


def test_redact_survives_junk() -> None:
    assert redact("not a url") == "not a url"
    assert redact("") == ""


def test_a_normal_clean_reads_as_english() -> None:
    entry = format_log_entry(
        CleanResult(
            original="https://www.amazon.com/dp/B08X?tag=aff-20&ref_=nb&psc=1",
            result="https://www.amazon.com/dp/B08X?psc=1",
            params_removed=["tag", "ref_"],
        ),
        STAMP,
    )
    assert entry == (
        "[2026-07-13 01:09:16]  removed 2 trackers: tag, ref_\n"
        "    before  https://www.amazon.com/dp/B08X?tag=aff-20&ref_=nb&psc=1\n"
        "    after   https://www.amazon.com/dp/B08X?psc=1\n"
        "\n"
    )


def test_one_tracker_is_singular() -> None:
    entry = format_log_entry(
        CleanResult(original="a", result="b", params_removed=["fbclid"]), STAMP
    )
    assert "removed 1 tracker: fbclid" in entry
    assert "1 trackers" not in entry


def test_an_unwrap_removes_no_params_and_says_so() -> None:
    """A redirect unwrap changes the link without stripping any key."""
    entry = format_log_entry(
        CleanResult(
            original="https://l.facebook.com/l.php?u=https%3A%2F%2Fsite.com%2Fx",
            result="https://site.com/x",
        ),
        STAMP,
    )
    assert "unwrapped a redirect" in entry
    assert "removed 0" not in entry


def test_before_and_after_are_stacked_for_eyeball_diffing() -> None:
    entry = format_log_entry(
        CleanResult(original="LONG_BEFORE", result="SHORT_AFTER", params_removed=["utm_source"]),
        STAMP,
    )
    lines = entry.splitlines()
    assert lines[1].startswith("    before  ")
    assert lines[2].startswith("    after   ")
    # The URLs must start at the same column, or the difference is not scannable.
    assert lines[1].index("LONG_BEFORE") == lines[2].index("SHORT_AFTER")


def test_entries_are_separated_by_a_blank_line() -> None:
    entry = format_log_entry(CleanResult(original="a", result="b", params_removed=["x"]), STAMP)
    assert entry.endswith("\n\n")


def test_is_plain_ascii() -> None:
    """A stray '·' renders as mojibake in half the editors on Windows."""
    entry = format_log_entry(
        CleanResult(
            original="https://a.com/x", result="https://a.com", params_removed=["utm_source"]
        ),
        STAMP,
    )
    entry.encode("ascii")  # raises if anything non-ASCII crept in
