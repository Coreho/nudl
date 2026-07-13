"""`as_single_url` is the gate that stops auto-watch clobbering things the user wanted.

Everything that is not a lone http(s) token must be rejected. When this function is
wrong, nudl eats someone's paragraph.
"""

from __future__ import annotations

import pytest

from src.clipboard import as_single_url

ACCEPTED = [
    "https://example.com/a?utm_source=x",
    "http://example.com",
    "HTTPS://EXAMPLE.COM/SHOUTING",
    "  https://example.com/padded  ",  # surrounding whitespace is trimmed, not content
]

REJECTED = [
    None,
    "",
    "   ",
    "just some text",
    "check this out: https://example.com/a",  # a paragraph CONTAINING a link
    "https://example.com/a and https://example.com/b",  # two links
    "https://example.com/a\nhttps://example.com/b",  # newline-separated
    "ftp://example.com/f",  # non-http scheme
    "mailto:someone@example.com",
    "example.com/no-scheme",
    "C:\\Users\\me\\file.txt",
    "  \t\n  ",
]


@pytest.mark.parametrize("text", ACCEPTED)
def test_accepts_a_lone_url(text: str) -> None:
    assert as_single_url(text) == text.strip()


@pytest.mark.parametrize("text", REJECTED)
def test_rejects_everything_else(text: str | None) -> None:
    assert as_single_url(text) is None


def test_trailing_newline_is_trimmed_not_rejected() -> None:
    """Copying from a terminal or an editor often appends a newline."""
    assert as_single_url("https://example.com/a\n") == "https://example.com/a"
