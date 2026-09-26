"""Security-focused tests — adversarial inputs, ReDoS bounds, credential redaction."""

from __future__ import annotations

import time

import pytest

from src import clean
from src.app import _is_secret_key, redact

# ---------------------------------------------------------------------------------------
# ReDoS bounding
# ---------------------------------------------------------------------------------------


CATASTROPHIC_PATTERNS = [
    "^(a+)+$",
    "^(a|a)*$",
    "^(a*)*$",
    "^(.*a){15}$",
    "^(\\w+\\s?)*$",
    "(a+)+",
    "(x+x+)+y",
]


@pytest.mark.parametrize("pattern", CATASTROPHIC_PATTERNS)
def test_catastrophic_pattern_cannot_hang_the_engine(pattern: str) -> None:
    """A rules.json a user hand-edited must never freeze the clipboard thread."""
    rules = {
        "global_tracker_keys": [pattern],
        "providers": [],
        "redirect_wrappers": [],
        "referral": [],
    }
    url = "https://x.com/?" + "a" * 40 + "!=1"
    start = time.perf_counter()
    clean.clean(url, rules=rules)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0, f"pattern {pattern!r} took {elapsed:.2f}s — ReDoS"


def test_long_url_is_rejected() -> None:
    """URLs beyond MAX_URL_LENGTH are returned untouched."""
    url = "https://x.com/?" + "a" * 5000
    result = clean.clean(url)
    assert result == url


def test_long_query_key_is_not_matched() -> None:
    """Query keys beyond MAX_KEY_LENGTH cannot trigger a match."""
    key = "a" * 300
    url = f"https://x.com/?{key}=1"
    rules = {
        "global_tracker_keys": ["^a+$"],
        "providers": [],
        "redirect_wrappers": [],
        "referral": [],
    }
    result = clean.clean(url, rules=rules)
    assert result == url  # key too long to match, URL untouched


# ---------------------------------------------------------------------------------------
# Control character rejection
# ---------------------------------------------------------------------------------------


def test_control_characters_rejected_by_is_absolute_http() -> None:
    """CR/LF/TAB must not pass _is_absolute_http (which would let them reach clipboard/log)."""
    assert clean._is_absolute_http("https://good.com\n?token=1") is False
    assert clean._is_absolute_http("https://good.com\r\n/x") is False
    assert clean._is_absolute_http("https://good.com\t?x=1") is False
    assert clean._is_absolute_http("https://good.com\x00/x") is False


# ---------------------------------------------------------------------------------------
# rawRules host/scheme safety
# ---------------------------------------------------------------------------------------


def test_rawrule_cannot_change_host() -> None:
    """A rawRule that retargets to another domain must be rejected."""
    rules = {
        "global_tracker_keys": [],
        "providers": [
            {
                "name": "evil",
                "urlPattern": "bank.example",
                "rules": [],
                "rawRules": [{"pattern": "^https://bank\\.example/", "replacement": "https://evil.io/"}],
            }
        ],
        "redirect_wrappers": [],
        "referral": [],
    }
    url = "https://bank.example/transfer?utm_source=x"
    result = clean.clean(url, rules=rules)
    assert "evil.io" not in result


def test_rawrule_cannot_downgrade_https() -> None:
    """A rawRule that downgrades https→http must be rejected."""
    rules = {
        "global_tracker_keys": [],
        "providers": [
            {
                "name": "downgrade",
                "urlPattern": "bank.example",
                "rules": [],
                "rawRules": [{"pattern": "^https://", "replacement": "http://"}],
            }
        ],
        "redirect_wrappers": [],
        "referral": [],
    }
    url = "https://bank.example/x?utm_source=1"
    result = clean.clean(url, rules=rules)
    assert result.startswith("https://")


# ---------------------------------------------------------------------------------------
# Credential redaction
# ---------------------------------------------------------------------------------------


def test_fragment_secrets_are_masked() -> None:
    """OAuth implicit-flow tokens in the fragment must be masked before logging."""
    result = redact("https://app.example.com/cb?utm_source=n#access_token=SECRET-JWT&id_token=abc")
    assert "SECRET-JWT" not in result
    assert "abc" not in result  # id_token value also masked


def test_semicolon_separated_secrets_are_masked() -> None:
    """Secrets after a ; separator must still be masked."""
    result = redact("https://x.com/?a=1;code=SUPERSECRET&utm_source=n")
    assert "SUPERSECRET" not in result


def test_percent_encoded_secret_keys_are_masked() -> None:
    """Percent-encoded secret key names must still be masked."""
    result = redact("https://x.com/?%63ode=SUPERSECRET")
    assert "SUPERSECRET" not in result


def test_userinfo_always_masked() -> None:
    """user:pass@host must always have credentials masked."""
    result = redact("https://user:hunter2@example.com/path")
    assert "hunter2" not in result
    assert "***@" in result


def test_is_secret_key_matches_prefixes() -> None:
    """Keys matching secret prefixes are detected."""
    assert _is_secret_key("x-amz-signature") is True
    assert _is_secret_key("oauth_token") is True
    assert _is_secret_key("x-api-key") is True
    assert _is_secret_key("aws_access_key") is True


def test_is_secret_key_matches_substrings() -> None:
    """Keys containing secret substrings are detected."""
    assert _is_secret_key("reset_token") is True
    assert _is_secret_key("my_secret_value") is True
    assert _is_secret_key("apikey_value") is True


# ---------------------------------------------------------------------------------------
# Wrapper unwrapping safety
# ---------------------------------------------------------------------------------------


def test_wrapper_rejects_userinfo_targets() -> None:
    """Unwrapping to a target with userinfo (phishing display) must be refused."""
    rules = {
        "global_tracker_keys": [],
        "providers": [
            {
                "name": "fb",
                "urlPattern": "facebook.com",
                "rules": [],
                "rawRules": [],
            }
        ],
        "redirect_wrappers": [
            {"host": "facebook.com", "path": "/l.php", "param": "u"}
        ],
        "referral": [],
    }
    url = "https://l.facebook.com/l.php?u=https%3A%2F%2Fuser%3Apass%40evil.com%2F"
    result = clean.clean(url, rules=rules)
    assert "user:pass@" not in result


def test_wrapper_rejects_private_ip_targets() -> None:
    """Unwrapping to a loopback/private IP must be refused."""
    rules = {
        "global_tracker_keys": [],
        "providers": [
            {
                "name": "fb",
                "urlPattern": "facebook.com",
                "rules": [],
                "rawRules": [],
            }
        ],
        "redirect_wrappers": [
            {"host": "facebook.com", "path": "/l.php", "param": "u"}
        ],
        "referral": [],
    }
    url = "https://l.facebook.com/l.php?u=http%3A%2F%2F127.0.0.1%2Fadmin"
    result = clean.clean(url, rules=rules)
    assert result == url  # unwrap was blocked, original URL returned untouched


# ---------------------------------------------------------------------------------------
# No-op guard returns original URL
# ---------------------------------------------------------------------------------------


def test_signed_url_noop_preserves_original_host() -> None:
    """A URL classified as reason_noop=signed_url must keep its original host."""
    url = "https://l.facebook.com/l.php?u=https%3A%2F%2Fevil.example%2Fpwn%3Ftoken%3D1"
    result = clean.clean_result(url)
    assert result.result == url  # returned untouched
    assert result.reason_noop == "signed_url"


def test_exception_noop_preserves_original_host() -> None:
    """A URL classified as reason_noop=exception must keep its original host."""
    url = "https://mybank.com/payment?utm_source=tracker"
    result = clean.clean_result(url, exceptions=["mybank.com"])
    assert result.result == url  # returned untouched
    assert result.reason_noop == "exception"


def test_signed_url_takes_precedence_over_exception() -> None:
    """A signed URL on an excepted domain is still protected (signed > exception)."""
    url = "https://mybank.com/payment?token=SECRET"
    result = clean.clean_result(url, exceptions=["mybank.com"])
    assert result.result == url  # returned untouched
    assert result.reason_noop == "signed_url"


# ---------------------------------------------------------------------------------------
# Provider urlPattern safety
# ---------------------------------------------------------------------------------------


def test_provider_pattern_does_not_match_attack_domain() -> None:
    """A bare `amazon` pattern must not match `notamazon.com` or `evilamazon.com`."""
    rules = {
        "global_tracker_keys": [],
        "providers": [
            {
                "name": "amazon",
                "urlPattern": "amazon",
                "rules": ["tag"],
                "rawRules": [],
            }
        ],
        "redirect_wrappers": [],
        "referral": [],
    }
    # Without anchoring, `amazon` would match `notamazon.com` via re.search.
    # Our anchoring wraps it to (?:^|\.)amazon so it only matches on a boundary.
    url = "https://notamazon.com/dp/B08X?tag=aff-20"
    result = clean.clean(url, rules=rules)
    assert "tag=aff-20" in result  # tag NOT stripped — not a real Amazon domain

    url2 = "https://www.amazon.com/dp/B08X?tag=aff-20"
    result2 = clean.clean(url2, rules=rules)
    assert "tag=aff-20" not in result2  # tag IS stripped — real Amazon domain


# ---------------------------------------------------------------------------------------
# What the tray is allowed to open
# ---------------------------------------------------------------------------------------


def test_every_file_the_tray_offers_is_one_it_will_actually_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Settings…, Rules… and View log each end in `_open`, and each must get through.

    The allowlist once said `.json` only, and View log — the audit trail the README points
    people at — silently did nothing. Nothing failed; the menu item just stopped working.
    """
    from src import app as app_module
    from src import config

    monkeypatch.setenv("APPDATA", str(tmp_path))
    opened: list[str] = []
    monkeypatch.setattr(app_module.os, "startfile", lambda p: opened.append(str(p)), raising=False)

    for path in (config.config_path(), config.rules_path(), config.log_path()):
        app_module.NudlApp._open(path)

    assert opened == [
        str(config.config_path()),
        str(config.rules_path()),
        str(config.log_path()),
    ]


def test_anything_else_is_refused(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """`os.startfile` runs a file's association — for an .exe, that means running it."""
    from src import app as app_module

    opened: list[str] = []
    monkeypatch.setattr(app_module.os, "startfile", lambda p: opened.append(str(p)), raising=False)

    app_module.NudlApp._open(tmp_path / "payload.exe")
    app_module.NudlApp._open(tmp_path / "script.bat")

    assert opened == []


# ---------------------------------------------------------------------------------------
# Patterns matched against the whole URL get the same ReDoS refusal as key patterns
# ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("pattern", ["^(a+)+$", "(x+x+)+y", "(.*a){15}"])
def test_a_catastrophic_exception_or_rawrule_cannot_hang_the_engine(pattern: str) -> None:
    """exceptions and rawRules run against the full URL, so they were the open door."""
    rules = {
        "global_tracker_keys": ["utm_source"],
        "providers": [
            {
                "name": "x",
                "urlPattern": r"(^|\.)x\.com$",
                "exceptions": [pattern],
                "rawRules": [{"pattern": pattern, "replacement": ""}],
            }
        ],
        "redirect_wrappers": [],
        "referral": [],
    }
    url = "https://x.com/" + "a" * 3000 + "!?utm_source=1"
    start = time.perf_counter()
    result = clean.clean(url, rules=rules)
    assert time.perf_counter() - start < 1.0, f"{pattern!r} hung the engine"
    assert result == "https://x.com/" + "a" * 3000 + "!", "a refused pattern changed behaviour"


def test_the_validator_says_why_such_a_pattern_does_nothing() -> None:
    from src import validate

    findings = validate.check_rules(
        {
            "providers": [
                {
                    "name": "x",
                    "urlPattern": r"(^|\.)x\.com$",
                    "exceptions": ["(a+)+"],
                    "rawRules": [{"pattern": "(a+)+", "replacement": ""}],
                }
            ]
        }
    )
    refused = [f for f in findings if "nested quantifier" in f.message]
    assert [f.where for f in refused] == [
        "providers[0] (x).exceptions[0]",
        "providers[0] (x).rawRules[0]",
    ]
