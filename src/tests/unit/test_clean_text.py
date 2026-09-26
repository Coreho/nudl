"""Links inside text: the hotkey on a whole message, and the command line on a whole file.

`clean_result` is specified by `test_clean.py`; every link here goes through it, guards
and all. What is tested here is the part that is new and dangerous: finding where a link
ends inside prose, and replacing it without disturbing a single character around it.
"""

from __future__ import annotations

import pytest

from src.clean import MAX_TEXT_LENGTH, clean_text

CASES: list[tuple[str, str, str]] = [
    (
        "a link in a sentence, sentence punctuation left where it was",
        "See https://youtu.be/abc?si=x&t=4. Then reply!",
        "See https://youtu.be/abc?t=4. Then reply!",
    ),
    (
        "several links, each cleaned, the text between them untouched",
        "a https://e.com/1?fbclid=x b\thttps://e.com/2?utm_source=y&id=2 c",
        "a https://e.com/1 b\thttps://e.com/2?id=2 c",
    ),
    (
        "CRLF line endings survive byte for byte",
        "line one https://e.com/?utm_source=x\r\nline two\r\n",
        "line one https://e.com/\r\nline two\r\n",
    ),
    (
        "Markdown link: the closing parenthesis belongs to the Markdown",
        "[the video](https://youtu.be/abc?si=x)",
        "[the video](https://youtu.be/abc)",
    ),
    (
        "Markdown bold keeps its asterisks",
        "**https://e.com/a?utm_source=nl**",
        "**https://e.com/a**",
    ),
    (
        "TRIPWIRE: a Wikipedia parenthesis inside the link is kept",
        "(see https://en.wikipedia.org/wiki/Foo_(bar)?utm_source=x)",
        "(see https://en.wikipedia.org/wiki/Foo_(bar))",
    ),
    (
        "angle-bracketed link",
        "<https://e.com/a?gclid=1&page=2>",
        "<https://e.com/a?page=2>",
    ),
    (
        "an HTML attribute ends at its quote",
        '<a href="https://e.com/a?fbclid=1">x</a>',
        '<a href="https://e.com/a">x</a>',
    ),
    (
        "TRIPWIRE: a signed link inside text is left alone",
        "download https://bucket.s3.amazonaws.com/f?X-Amz-Signature=abc&utm_source=x now",
        "download https://bucket.s3.amazonaws.com/f?X-Amz-Signature=abc&utm_source=x now",
    ),
    (
        "TRIPWIRE: a Medium friend link inside text keeps its sk",
        "read https://medium.com/@a/b?sk=abc123 please",
        "read https://medium.com/@a/b?sk=abc123 please",
    ),
    (
        "the same link twice is cleaned twice",
        "https://e.com/?utm_source=a and https://e.com/?utm_source=a",
        "https://e.com/ and https://e.com/",
    ),
    (
        "no links at all",
        "nothing to see here, not even www.example.com",
        "nothing to see here, not even www.example.com",
    ),
]


@pytest.mark.parametrize(("label", "text", "expected"), CASES, ids=[c[0] for c in CASES])
def test_links_in_text(label: str, text: str, expected: str) -> None:
    assert clean_text(text).result == expected


def test_it_reports_every_link_it_changed_and_what_came_off() -> None:
    result = clean_text("a https://youtu.be/x?si=1 b https://e.com/?id=2 c https://e.com/?fbclid=3")
    assert [link.result for link in result.links] == ["https://youtu.be/x", "https://e.com/"]
    assert result.params_removed == ["si", "fbclid"]
    assert result.changed


def test_nothing_changed_is_reported_as_nothing() -> None:
    result = clean_text("https://e.com/?id=2")
    assert not result.changed
    assert result.links == []


def test_an_enormous_clipboard_is_not_scanned() -> None:
    text = "https://e.com/?utm_source=x " + "a" * MAX_TEXT_LENGTH
    assert clean_text(text).result == text


def test_text_cleaning_opens_no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def explode(*_args, **_kwargs):
        raise AssertionError("clean_text tried to open a socket")

    monkeypatch.setattr(socket, "socket", explode)
    clean_text(" ".join(f"https://e{i}.com/?utm_source={i}" for i in range(50)))
