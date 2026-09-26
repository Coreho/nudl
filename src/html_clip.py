"""Cleaning the links inside formatted text — the clipboard's "HTML Format".

Copy a paragraph from a web page, an email or a chat app and the clipboard holds two
versions: plain text, and HTML. Paste into anything that understands formatting and the
HTML is what lands, links and all. Those links often never appear in the plain text at
all — "click here", pointing at a URL with six trackers on it. Cleaning only the plain
text would clean the copy nobody pastes.

The format is a small ASCII header with byte offsets, then the HTML:

    Version:0.9
    StartHTML:0000000105
    EndHTML:0000000275
    StartFragment:0000000141
    EndFragment:0000000239
    <html><body><!--StartFragment--><a href="...">...</a><!--EndFragment--></body></html>

Every offset after an edit has to be recomputed, or the pasting app reads the wrong
bytes. Anything that does not parse exactly is refused (None), and the caller falls back
to plain text: guessing at a malformed header is how a paste turns into garbage.

Pure: no Win32 here, so every line of it is testable on any platform.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable

from .clean import CleanResult, find_links

#: The offsets a header may carry. StartSelection/EndSelection are optional and old.
_OFFSET = re.compile(
    rb"^(StartHTML|EndHTML|StartFragment|EndFragment|StartSelection|EndSelection):(-?\d+)\r?$",
    re.M,
)
_TAG = re.compile(r"(<[^>]*>)")
_HREF = re.compile(r"""(\bhref\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE | re.DOTALL)

CleanLink = Callable[[str], CleanResult]


def clean_html_format(data: bytes, clean_link: CleanLink) -> tuple[bytes, list[CleanResult]] | None:
    """Every link in a CF_HTML payload through `clean_link`: (new bytes, what changed).

    Returns None when the payload is not a header nudl can rebuild exactly. When nothing
    changed, the bytes that come back are the bytes that went in.
    """
    # Offsets are read from the header alone — the part before the first tag — so a page
    # that happens to contain the text "EndHTML:5" cannot pass itself off as one.
    head = data[: data.find(b"<")] if b"<" in data else data
    offsets = {key.decode(): (int(value), len(value)) for key, value in _OFFSET.findall(head)}
    try:
        start_html, _ = offsets["StartHTML"]
        end_html, _ = offsets["EndHTML"]
        start_frag, _ = offsets["StartFragment"]
        end_frag, _ = offsets["EndFragment"]
    except KeyError:
        return None
    if end_html == -1:  # allowed by the spec: "no HTML context"; the fragment runs to the end
        end_html = len(data)
    if not 0 < start_html <= start_frag <= end_frag <= end_html <= len(data):
        return None

    changed: list[CleanResult] = []
    parts = []
    for chunk in (data[start_html:start_frag], data[start_frag:end_frag], data[end_frag:end_html]):
        try:
            markup = chunk.decode("utf-8")
        except UnicodeDecodeError:
            return None
        cleaned, found = clean_markup(markup, clean_link)
        changed += found
        parts.append(cleaned.encode("utf-8"))
    if not changed:
        return data, []

    pre, fragment, post = parts
    new = {
        "StartHTML": start_html,
        "StartFragment": start_html + len(pre),
        "EndFragment": start_html + len(pre) + len(fragment),
        "EndHTML": start_html + len(pre) + len(fragment) + len(post),
    }
    new["StartSelection"], new["EndSelection"] = new["StartFragment"], new["EndFragment"]

    def renumber(match: re.Match[bytes]) -> bytes:
        if match.group(2) == b"-1":
            return match.group(0)  # "no such offset" stays exactly that
        key = match.group(1).decode()
        width = len(match.group(2))
        value = str(new[key]).zfill(width)
        if len(value) != width:
            raise _Unrebuildable  # the header would change length and move every offset
        return f"{key}:{value}".encode() + (b"\r" if match.group(0).endswith(b"\r") else b"")

    try:
        header = _OFFSET.sub(renumber, data[:start_html])
    except _Unrebuildable:
        return None
    return header + pre + fragment + post + data[end_html:], changed


def clean_markup(markup: str, clean_link: CleanLink) -> tuple[str, list[CleanResult]]:
    """Clean the links in an HTML string: `href` attributes, and links written as text.

    Only a link that actually changed is rewritten, and only its own characters; every
    other byte of the markup survives untouched. Values are unescaped before cleaning
    (`&amp;` is `&` to the engine) and escaped again after.
    """
    changed: list[CleanResult] = []

    def one(raw: str, *, quote: bool) -> str:
        url = html.unescape(raw)
        result = clean_link(url)
        if not result.changed:
            return raw
        changed.append(result)
        return html.escape(result.result, quote=quote)

    def href(match: re.Match[str]) -> str:
        return match.group(1) + match.group(2) + one(match.group(3), quote=True) + match.group(2)

    pieces = []
    for piece in _TAG.split(markup):
        if piece.startswith("<"):
            pieces.append(_HREF.sub(href, piece))
            continue
        cursor, out = 0, []
        for start, end in find_links(piece):
            out += [piece[cursor:start], one(piece[start:end], quote=False)]
            cursor = end
        out.append(piece[cursor:])
        pieces.append("".join(out))
    return "".join(pieces), changed


class _Unrebuildable(Exception):
    pass
