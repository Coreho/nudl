"""CP0 tripwire corpus — the standing compliance gate for nudl.

This file is the specification of `clean()` in executable form. Per
BUILD-PLAN-CLAUDE.md CP0 and SPEC-CLAUDE.md §13, the build is not shippable if any
assertion here fails.

The corpus is deliberately weighted toward the things that would BREAK a link the
user needed, not toward the things nudl cleans well. Roughly a third of the cases
below are "looks like tracking but isn't" tripwires.
"""

import pytest

from src.clean import clean

# --------------------------------------------------------------------------------------
# The corpus: (label, input, expected output)
#
# `expected is input` (same object) is not required — equality is byte equality.
# --------------------------------------------------------------------------------------

CORPUS: list[tuple[str, str, str]] = [
    # -- Tracking params get stripped -----------------------------------------------
    (
        "utm suite, functional param kept",
        "https://example.com/a?utm_source=nl&utm_medium=email&id=42",
        "https://example.com/a?id=42",
    ),
    (
        "query becomes empty -> no dangling '?'",
        "https://example.com/a?utm_source=nl",
        "https://example.com/a",
    ),
    (
        "fbclid",
        "https://example.com/p?fbclid=IwAR123",
        "https://example.com/p",
    ),
    (
        "google click ids, page kept",
        "https://shop.example.com/x?gclid=a&dclid=b&gbraid=c&wbraid=d&msclkid=e&page=2",
        "https://shop.example.com/x?page=2",
    ),
    (
        "mailchimp ids stripped, 'ref' is referral (off by default) so kept",
        "https://news.example.com/story?mc_cid=abc&mc_eid=def&ref=home",
        "https://news.example.com/story?ref=home",
    ),
    (
        "igshid",
        "https://www.instagram.com/p/abc/?igshid=xyz",
        "https://www.instagram.com/p/abc/",
    ),
    (
        "hubspot",
        "https://example.com/e?_hsenc=p2A&_hsmi=123&x=1",
        "https://example.com/e?x=1",
    ),
    (
        "vero + olytics",
        "https://example.com/n?vero_id=1&oly_enc_id=2&oly_anon_id=3&article=9",
        "https://example.com/n?article=9",
    ),
    # -- Amazon ----------------------------------------------------------------------
    (
        "amazon affiliate tag + ref_, psc kept",
        "https://www.amazon.com/dp/B08X?tag=aff-20&ref_=nb&psc=1",
        "https://www.amazon.com/dp/B08X?psc=1",
    ),
    (
        "amazon pd_rd_*/pf_rd_* wildcards, th kept",
        "https://www.amazon.com/dp/B08X?pd_rd_w=a&pd_rd_r=b&pf_rd_p=c&th=1",
        "https://www.amazon.com/dp/B08X?th=1",
    ),
    (
        "amazon.co.uk qid/sr (provider scoped by TLD-agnostic pattern)",
        "https://www.amazon.co.uk/dp/B01?qid=1699999999&sr=8-1&psc=1",
        "https://www.amazon.co.uk/dp/B01?psc=1",
    ),
    # -- Redirect wrappers unwrap ----------------------------------------------------
    (
        "facebook l.php unwraps to the real target",
        "https://l.facebook.com/l.php?u=https%3A%2F%2Fsite.com%2Fx&h=AT1",
        "https://site.com/x",
    ),
    (
        "unwrap THEN clean the target's own trackers",
        "https://l.facebook.com/l.php?u=https%3A%2F%2Fsite.com%2Fx%3Futm_source%3Dfb&h=AT1",
        "https://site.com/x",
    ),
    (
        "google /url?q=",
        "https://www.google.com/url?q=https%3A%2F%2Fexample.com%2Fdoc&sa=D",
        "https://example.com/doc",
    ),
    (
        "google /url?url=",
        "https://www.google.com/url?url=https%3A%2F%2Fexample.com%2Fb",
        "https://example.com/b",
    ),
    (
        "out.reddit.com",
        "https://out.reddit.com/t3_abc?url=https%3A%2F%2Fexample.com%2Fz",
        "https://example.com/z",
    ),
    (
        "steam linkfilter",
        "https://steamcommunity.com/linkfilter/?url=https%3A%2F%2Fexample.com%2Fgame",
        "https://example.com/game",
    ),
    (
        "tumblr redirect (?z=)",
        "https://t.umblr.com/redirect?z=https%3A%2F%2Fexample.com%2Fpost&t=abc",
        "https://example.com/post",
    ),
    (
        "nested wrappers: google -> facebook -> real",
        "https://www.google.com/url?q=https%3A%2F%2Fl.facebook.com%2Fl.php%3Fu%3D"
        "https%253A%252F%252Fsite.com%252Ffinal",
        "https://site.com/final",
    ),
    # -- Functional params PRESERVED (the whole point) --------------------------------
    (
        "youtube v + t preserved, feature stripped",
        "https://www.youtube.com/watch?v=abc123&t=90&feature=share",
        "https://www.youtube.com/watch?v=abc123&t=90",
    ),
    (
        "youtube list + index preserved",
        "https://www.youtube.com/watch?v=abc&list=PL123&index=2&utm_source=nl",
        "https://www.youtube.com/watch?v=abc&list=PL123&index=2",
    ),
    (
        "youtu.be si preserved (v0 keeps si everywhere)",
        "https://youtu.be/abc?si=xyz",
        "https://youtu.be/abc?si=xyz",
    ),
    (
        "spotify si preserved — removing it has broken shared-playlist flows",
        "https://open.spotify.com/track/xyz?si=abcd",
        "https://open.spotify.com/track/xyz?si=abcd",
    ),
    (
        "amazon search keywords preserved — stripping it yields a blank search page",
        "https://www.amazon.com/s?keywords=usb+c+cable&tag=aff-20",
        "https://www.amazon.com/s?keywords=usb+c+cable",
    ),
    # -- Signed URLs: hard guard, byte-identical -------------------------------------
    (
        "AWS SigV4 — untouched even though it carries a utm_source",
        "https://files.example.com/f?X-Amz-Signature=abc&X-Amz-Expires=60&utm_source=nl",
        "https://files.example.com/f?X-Amz-Signature=abc&X-Amz-Expires=60&utm_source=nl",
    ),
    (
        "generic ?sig=",
        "https://cdn.example.com/v?sig=abc&utm_source=x",
        "https://cdn.example.com/v?sig=abc&utm_source=x",
    ),
    (
        "?token=",
        "https://api.example.com/r?token=abc&utm_source=x",
        "https://api.example.com/r?token=abc&utm_source=x",
    ),
    (
        "?expires= + ?policy=",
        "https://dl.example.com/f?expires=1699&policy=xyz&fbclid=q",
        "https://dl.example.com/f?expires=1699&policy=xyz&fbclid=q",
    ),
    (
        "?hmac=",
        "https://example.com/webhook?hmac=deadbeef&utm_source=x",
        "https://example.com/webhook?hmac=deadbeef&utm_source=x",
    ),
    # -- No network un-shortening -----------------------------------------------------
    (
        "bit.ly is NOT resolved",
        "https://bit.ly/3abc",
        "https://bit.ly/3abc",
    ),
    (
        "t.co is NOT resolved",
        "https://t.co/abc123",
        "https://t.co/abc123",
    ),
    # -- Passthrough ------------------------------------------------------------------
    (
        "already-clean URL",
        "https://example.com/page?id=1",
        "https://example.com/page?id=1",
    ),
    (
        "no query at all",
        "https://example.com/",
        "https://example.com/",
    ),
    (
        "not a URL",
        "not a url, just text",
        "not a url, just text",
    ),
    (
        "empty string",
        "",
        "",
    ),
    (
        "non-http scheme (mailto)",
        "mailto:a@b.com?utm_source=x",
        "mailto:a@b.com?utm_source=x",
    ),
    (
        "non-http scheme (ftp)",
        "ftp://example.com/f?utm_source=x",
        "ftp://example.com/f?utm_source=x",
    ),
    (
        "scheme-relative / no netloc",
        "/path/only?utm_source=x",
        "/path/only?utm_source=x",
    ),
    # -- TRIPWIRES: looks like tracking, isn't ----------------------------------------
    (
        "TRIPWIRE: a VALUE containing 'utm_source' must survive; only the KEY matches",
        "https://example.com/a?utm_source=x&redirect=utm_source_page",
        "https://example.com/a?redirect=utm_source_page",
    ),
    (
        "TRIPWIRE: 'gclid_backup' is not 'gclid' — exact keys are exact",
        "https://example.com/a?gclid=1&gclid_backup=2",
        "https://example.com/a?gclid_backup=2",
    ),
    (
        "TRIPWIRE: google SEARCH for a URL must not be unwrapped (path is /search)",
        "https://www.google.com/search?q=https%3A%2F%2Fexample.com",
        "https://www.google.com/search?q=https%3A%2F%2Fexample.com",
    ),
    (
        "TRIPWIRE: an arbitrary host's ?u= is not a wrapper — never unwrap it",
        "https://example.com/r?u=https%3A%2F%2Fevil.com",
        "https://example.com/r?u=https%3A%2F%2Fevil.com",
    ),
    (
        "TRIPWIRE: blank values keep their '='",
        "https://example.com/a?q=&utm_source=x",
        "https://example.com/a?q=",
    ),
    (
        "TRIPWIRE: param order is preserved, not sorted",
        "https://example.com/a?z=1&utm_source=x&a=2",
        "https://example.com/a?z=1&a=2",
    ),
    (
        "TRIPWIRE: %20 must NOT be re-encoded to '+' — kept pairs are byte-exact",
        "https://example.com/s?q=hello%20world&fbclid=x",
        "https://example.com/s?q=hello%20world",
    ),
    (
        "TRIPWIRE: fragment survives",
        "https://example.com/d?utm_source=x#section-2",
        "https://example.com/d#section-2",
    ),
    (
        "TRIPWIRE: 'source' is not 'utm_source'",
        "https://example.com/a?source=newsletter",
        "https://example.com/a?source=newsletter",
    ),
    (
        "TRIPWIRE: tracker keys match case-insensitively",
        "https://example.com/a?UTM_SOURCE=x&id=1",
        "https://example.com/a?id=1",
    ),
    (
        "TRIPWIRE: ^ss_.*$ is anchored — 'class_ss_id' is not an ss_ tracker",
        "https://example.com/a?ss_source=x&class_ss_id=2",
        "https://example.com/a?class_ss_id=2",
    ),
    (
        "TRIPWIRE: signed-URL guard beats unwrapping — reddit's ?token= wins, no-op",
        "https://out.reddit.com/t3_a?url=https%3A%2F%2Fexample.com%2Fz&token=xyz",
        "https://out.reddit.com/t3_a?url=https%3A%2F%2Fexample.com%2Fz&token=xyz",
    ),
    (
        "TRIPWIRE: port and userinfo in netloc survive untouched",
        "https://user:pw@example.com:8443/a?utm_source=x&id=1",
        "https://user:pw@example.com:8443/a?id=1",
    ),
]


@pytest.mark.parametrize(
    ("url", "expected"),
    [pytest.param(url, expected, id=label) for label, url, expected in CORPUS],
)
def test_corpus(url: str, expected: str) -> None:
    assert clean(url) == expected


def test_corpus_is_big_enough() -> None:
    """SPEC-CLAUDE.md §13 mandates ~40 hand-vetted URLs with 8-10 tripwires."""
    assert len(CORPUS) >= 40
    tripwires = [c for c in CORPUS if c[0].startswith("TRIPWIRE")]
    assert len(tripwires) >= 8


# --------------------------------------------------------------------------------------
# Hard guarantees that are properties, not single cases
# --------------------------------------------------------------------------------------


def test_clean_is_idempotent() -> None:
    """Cleaning a cleaned URL changes nothing further."""
    for _, url, _ in CORPUS:
        once = clean(url)
        assert clean(once) == once, f"not idempotent: {url}"


def test_clean_never_raises() -> None:
    """Fail-safe: any input, no exception. Ever."""
    hostile = [
        "",
        " ",
        "http://",
        "https://",
        "://nope",
        "http://[",
        "https://example.com/?%",
        "https://example.com/?=&=&",
        "https://example.com/?a=%zz",
        "не url",
        "https://" + "a" * 5000,
        "\x00\x01",
        "http://example.com/?" + "&".join(f"utm_{i}=1" for i in range(500)),
    ]
    for url in hostile:
        clean(url)  # must not raise


def test_unwrap_cycle_terminates() -> None:
    """A wrapper pointing at itself must not hang or blow the stack."""
    self_ref = "https://l.facebook.com/l.php?u=https%3A%2F%2Fl.facebook.com%2Fl.php"
    result = clean(self_ref)
    assert result.startswith("http")


def test_unwrap_depth_is_bounded() -> None:
    """Deeply nested wrappers stop at the depth cap and still return a usable URL."""
    from urllib.parse import quote

    url = "https://site.com/deep"
    for _ in range(6):
        url = "https://l.facebook.com/l.php?u=" + quote(url, safe="")
    result = clean(url)
    assert result.startswith("http")


def test_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """SC-006 / Constitution II: the cleaning path opens zero sockets."""
    import socket

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("clean() attempted a network connection")

    monkeypatch.setattr(socket, "socket", explode)
    monkeypatch.setattr(socket, "create_connection", explode)
    for _, url, _ in CORPUS:
        clean(url)


def test_result_is_always_a_string() -> None:
    for _, url, _ in CORPUS:
        assert isinstance(clean(url), str)
