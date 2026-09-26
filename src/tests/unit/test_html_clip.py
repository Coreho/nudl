"""Formatted text on the clipboard: cleaning the links in "HTML Format" without breaking it.

Two things have to hold at once. The links — including the ones hiding in an `href`
behind "click here" — come out clean. And the payload stays a payload every app can
still read: every byte offset in the header has to point at the right byte afterwards,
or the paste turns into garbage. A header nudl cannot rebuild exactly is refused.
"""

from __future__ import annotations

import re

from src import clean, html_clip

HEADER = (
    "Version:0.9\r\n"
    "StartHTML:{sh:010d}\r\n"
    "EndHTML:{eh:010d}\r\n"
    "StartFragment:{sf:010d}\r\n"
    "EndFragment:{ef:010d}\r\n"
    "SourceURL:https://example.com/page\r\n"
)


def cf_html(fragment: str) -> bytes:
    """A payload shaped like Chrome's, with correct offsets."""
    pre, post = "<html><body>\r\n<!--StartFragment-->", "<!--EndFragment-->\r\n</body></html>"
    blank = HEADER.format(sh=0, eh=0, sf=0, ef=0).encode()
    sh = len(blank)
    sf = sh + len(pre.encode())
    ef = sf + len(fragment.encode())
    eh = ef + len(post.encode())
    return HEADER.format(sh=sh, eh=eh, sf=sf, ef=ef).encode() + (pre + fragment + post).encode()


def fragment_of(data: bytes) -> str:
    """Read the fragment back the way a pasting app does: by the header's offsets."""
    sf = int(re.search(rb"StartFragment:(\d+)", data).group(1))
    ef = int(re.search(rb"EndFragment:(\d+)", data).group(1))
    return data[sf:ef].decode("utf-8")


def _clean(url: str) -> clean.CleanResult:
    return clean.clean_result(url)


def test_a_link_behind_link_text_is_cleaned() -> None:
    """The plain-text copy says "the video". Only the HTML knows the URL — and its si."""
    data = cf_html('<a href="https://youtu.be/abc?si=xyz&amp;t=4">the video</a>')
    new, changed = html_clip.clean_html_format(data, _clean)
    assert fragment_of(new) == '<a href="https://youtu.be/abc?t=4">the video</a>'
    assert [r.params_removed for r in changed] == [["si"]]


def test_every_offset_still_points_at_the_right_bytes() -> None:
    data = cf_html('see <a href="https://e.com/?fbclid=1&amp;id=2">e</a> ✓ ünïcode')
    new, _ = html_clip.clean_html_format(data, _clean)
    assert fragment_of(new) == 'see <a href="https://e.com/?id=2">e</a> ✓ ünïcode'
    eh = int(re.search(rb"EndHTML:(\d+)", new).group(1))
    sh = int(re.search(rb"StartHTML:(\d+)", new).group(1))
    assert new[sh:].startswith(b"<html>")
    assert eh == len(new)
    assert len(new) == len(data) - len("fbclid=1&amp;")


def test_a_link_written_as_text_is_cleaned_too() -> None:
    data = cf_html("<p>https://e.com/a?utm_source=nl&amp;page=2</p>")
    new, _ = html_clip.clean_html_format(data, _clean)
    assert fragment_of(new) == "<p>https://e.com/a?page=2</p>"


def test_nothing_to_clean_returns_the_same_bytes() -> None:
    data = cf_html('<a href="https://e.com/?id=2">e</a>')
    assert html_clip.clean_html_format(data, _clean) == (data, [])


def test_attributes_other_than_href_are_never_touched() -> None:
    data = cf_html('<img src="https://e.com/p.png?utm_source=x" alt="https://e.com/?fbclid=1">')
    assert html_clip.clean_html_format(data, _clean) == (data, [])


def test_a_malformed_header_is_refused_not_guessed_at() -> None:
    good = cf_html('<a href="https://e.com/?fbclid=1">e</a>')
    assert html_clip.clean_html_format(good.replace(b"StartFragment", b"Start"), _clean) is None
    assert html_clip.clean_html_format(b"not a header at all", _clean) is None
    broken = re.sub(rb"EndHTML:\d+", b"EndHTML:0000099999", good)
    assert html_clip.clean_html_format(broken, _clean) is None


def test_a_signed_link_in_formatted_text_is_left_alone() -> None:
    data = cf_html(
        '<a href="https://b.s3.amazonaws.com/f?X-Amz-Signature=a&amp;utm_source=x">f</a>'
    )
    assert html_clip.clean_html_format(data, _clean) == (data, [])


def test_end_html_minus_one_means_to_the_end_and_stays_minus_one() -> None:
    """The spec allows EndHTML:-1 for "no context". It must survive as exactly that."""
    fragment = '<a href="https://e.com/?fbclid=1">e</a>'
    header = (
        "Version:0.9\r\nStartHTML:{sh:010d}\r\nEndHTML:-1\r\n"
        "StartFragment:{sf:010d}\r\nEndFragment:{ef:010d}\r\n"
    )
    sh = len(header.format(sh=0, sf=0, ef=0))
    pre = "<html><body><!--StartFragment-->"
    sf = sh + len(pre)
    ef = sf + len(fragment)
    data = (header.format(sh=sh, sf=sf, ef=ef) + pre + fragment + "<!--EndFragment-->").encode()
    new, changed = html_clip.clean_html_format(data, _clean)
    assert b"EndHTML:-1\r\n" in new
    assert fragment_of(new) == '<a href="https://e.com/">e</a>'
    assert len(changed) == 1


def test_offsets_written_with_fewer_digits_keep_their_width() -> None:
    """Some apps write 8-digit offsets, not 10. Changing the width moves every byte."""
    data = cf_html('<a href="https://e.com/?fbclid=1">e</a>')
    narrow = re.sub(rb"(Start|End)(HTML|Fragment):00(\d{8})", rb"\1\2:\3", data)
    shift = len(data) - len(narrow)
    narrow = re.sub(
        rb"(Start|End)(HTML|Fragment):(\d{8})",
        lambda m: m.group(1) + m.group(2) + b":" + b"%08d" % (int(m.group(3)) - shift),
        narrow,
    )
    new, _ = html_clip.clean_html_format(narrow, _clean)
    assert re.search(rb"StartFragment:\d{8}\r\n", new)
    assert fragment_of(new) == '<a href="https://e.com/">e</a>'
