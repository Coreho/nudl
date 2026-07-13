"""The tripwire corpus — the standing compliance gate for nudl.

This file is the specification of `clean()` in executable form. If any assertion here
fails, the build is not shippable.

The corpus is deliberately weighted toward the things that would BREAK a link the
user needed, not toward the things nudl cleans well. Roughly a quarter of the cases
below are "looks like tracking but isn't" tripwires.
"""

import pytest

from src.clean import MAX_UNWRAP_DEPTH, clean

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
    # -- The share buttons people actually copy from ----------------------------------
    (
        "amazon share-button payload (content-id, sprefix, crid)",
        "https://www.amazon.com/dp/B08X?content-id=amzn1.sym.abc&sprefix=usb%2Caps&crid=2ABC&psc=1",
        "https://www.amazon.com/dp/B08X?psc=1",
    ),
    (
        "linkedin share",
        "https://www.linkedin.com/posts/someone_activity-123"
        "?utm_source=share&utm_medium=member_desktop&trk=public_post",
        "https://www.linkedin.com/posts/someone_activity-123",
    ),
    (
        "tiktok share",
        "https://www.tiktok.com/@user/video/123?is_from_webapp=1&sender_device=pc",
        "https://www.tiktok.com/@user/video/123",
    ),
    (
        "reddit share",
        "https://www.reddit.com/r/python/comments/abc/title/"
        "?share_id=xyz&utm_source=share&utm_medium=web2x",
        "https://www.reddit.com/r/python/comments/abc/title/",
    ),
    (
        "substack",
        "https://example.substack.com/p/post?r=abc123&utm_campaign=post&triedRedirect=true",
        "https://example.substack.com/p/post",
    ),
    (
        "ebay tracking params",
        "https://www.ebay.com/itm/123456?_trksid=p2047675&_trkparms=abc&hash=item2a1b",
        "https://www.ebay.com/itm/123456?hash=item2a1b",
    ),
    (
        "etsy",
        "https://www.etsy.com/listing/123/thing?click_key=abc&click_sum=def&ref=hp_rv&frs=1",
        "https://www.etsy.com/listing/123/thing",
    ),
    (
        "twitch",
        "https://www.twitch.tv/somestreamer?tt_content=text_link&tt_medium=live_embed",
        "https://www.twitch.tv/somestreamer",
    ),
    (
        "steam snr",
        "https://store.steampowered.com/app/440/Team_Fortress_2/?snr=1_7_7_230_150_1",
        "https://store.steampowered.com/app/440/Team_Fortress_2/",
    ),
    (
        "nyt share id",
        "https://www.nytimes.com/2026/01/01/us/article.html?smid=url-share&smtyp=cur",
        "https://www.nytimes.com/2026/01/01/us/article.html",
    ),
    (
        "guardian CMP",
        "https://www.theguardian.com/world/2026/jan/01/story?CMP=Share_iOSApp_Other",
        "https://www.theguardian.com/world/2026/jan/01/story",
    ),
    (
        "aliexpress spm soup",
        "https://www.aliexpress.com/item/123.html"
        "?spm=a2g0o.detail&algo_pvid=abc&pdp_npi=xyz&gatewayAdapt=glo2usa",
        "https://www.aliexpress.com/item/123.html",
    ),
    (
        "airbnb impression ids stripped, dates kept",
        "https://www.airbnb.com/rooms/123"
        "?source_impression_id=p3_abc&federated_search_id=xyz"
        "&check_in=2026-08-01&check_out=2026-08-05",
        "https://www.airbnb.com/rooms/123?check_in=2026-08-01&check_out=2026-08-05",
    ),
    (
        "cross-site click ids ride along to the destination site",
        "https://example.com/p?ttclid=a&twclid=b&li_fat_id=c&yclid=d&epik=e&id=9",
        "https://example.com/p?id=9",
    ),
    # -- TRIPWIRES: these LOOK like trackers and stripping them breaks the link --------
    (
        "TRIPWIRE: Medium's ?sk= is a FRIEND LINK — strip it and you paywall the article",
        "https://medium.com/@writer/an-article-abc123?sk=9f8e7d6c5b4a",
        "https://medium.com/@writer/an-article-abc123?sk=9f8e7d6c5b4a",
    ),
    (
        "TRIPWIRE: eBay's ?hash= identifies the ITEM; only the _trk* params are tracking",
        "https://www.ebay.com/itm/123?hash=item2a1b3c",
        "https://www.ebay.com/itm/123?hash=item2a1b3c",
    ),
    (
        "TRIPWIRE: Instagram's img_index picks WHICH photo in the carousel",
        "https://www.instagram.com/p/abc/?igshid=xyz&img_index=3",
        "https://www.instagram.com/p/abc/?img_index=3",
    ),
    (
        "TRIPWIRE: Booking's sid is a session id; only aid (the affiliate tag) goes",
        "https://www.booking.com/hotel/gb/x.html?aid=1234567&sid=abcdef123456",
        "https://www.booking.com/hotel/gb/x.html?sid=abcdef123456",
    ),
    (
        "TRIPWIRE: Reddit's ?context= is how many parent comments to show",
        "https://www.reddit.com/r/x/comments/a/b/c/?context=3&share_id=zzz",
        "https://www.reddit.com/r/x/comments/a/b/c/?context=3",
    ),
    (
        "TRIPWIRE: Airbnb check_in/check_out ARE the booking",
        "https://www.airbnb.com/rooms/9?check_in=2026-08-01&check_out=2026-08-05",
        "https://www.airbnb.com/rooms/9?check_in=2026-08-01&check_out=2026-08-05",
    ),
    (
        "TRIPWIRE: YouTube 'si' survives even sitting next to a stripped 'feature'",
        "https://youtu.be/abc?si=xyz&feature=share",
        "https://youtu.be/abc?si=xyz",
    ),
    (
        "TRIPWIRE: a ';' makes the pair ambiguous — dropping it could destroy 'id=5'",
        "https://example.com/a?fbclid=x;id=5",
        "https://example.com/a?fbclid=x;id=5",
    ),
    (
        "TRIPWIRE: ...but an unambiguous tracker beside it still goes",
        "https://example.com/a?fbclid=x;id=5&utm_source=nl&page=2",
        "https://example.com/a?fbclid=x;id=5&page=2",
    ),
    (
        "TRIPWIRE: a ';' inside a KEPT param is none of our business",
        "https://example.com/a?filter=a;b;c&utm_source=nl",
        "https://example.com/a?filter=a;b;c",
    ),
    (
        "wrapper param matching is case-insensitive",
        "https://l.facebook.com/l.php?U=https%3A%2F%2Fsite.com%2Fx",
        "https://site.com/x",
    ),
]


@pytest.mark.parametrize(
    ("url", "expected"),
    [pytest.param(url, expected, id=label) for label, url, expected in CORPUS],
)
def test_corpus(url: str, expected: str) -> None:
    assert clean(url) == expected


def test_corpus_is_big_enough() -> None:
    """The gate: at least 40 hand-vetted URLs, at least 8 of them tripwires."""
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


def test_unwrap_of_a_wrapper_pointing_at_a_bare_wrapper_terminates() -> None:
    """`l.php?u=<the bare l.php endpoint>` unwraps once and stops — it must not re-enter.

    This used to be called a "cycle" test. It is not one, and it is worth being honest
    about why: a true A->B->A cycle is not constructible in a URL, because each level has
    to physically contain the next, so the string would have to be infinite. The visited
    set in `clean()` is belt-and-braces against a *rule* that unwraps to itself, which is
    what this asserts: one unwrap, then a clean stop at a target with no `u` param.
    """
    self_ref = "https://l.facebook.com/l.php?u=https%3A%2F%2Fl.facebook.com%2Fl.php"
    assert clean(self_ref) == "https://l.facebook.com/l.php"


def test_unwrap_depth_is_bounded() -> None:
    """Six nested wrappers, a cap of three: exactly three come off, and three remain.

    `startswith("http")` is not an assertion, it is a formality — the *input* starts with
    "http" too, so a clean() that ignored the cap entirely and echoed its argument back
    would sail through it. Pin the exact residue instead: that is the only thing that can
    tell "correctly bounded" apart from "completely broken".
    """
    from urllib.parse import quote

    def wrap(url: str) -> str:
        return "https://l.facebook.com/l.php?u=" + quote(url, safe="")

    target = "https://site.com/deep"
    nested = target
    for _ in range(6):
        nested = wrap(nested)

    # Three unwraps off a six-deep nest leaves a three-deep nest.
    expected = target
    for _ in range(6 - MAX_UNWRAP_DEPTH):
        expected = wrap(expected)

    result = clean(nested)
    assert result != nested, "clean() returned its input — no unwrapping happened at all"
    assert result == expected


def test_makes_no_network_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The privacy promise, enforced: the cleaning path opens zero sockets."""
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
