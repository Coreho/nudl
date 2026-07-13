"""The nudl cleaning engine — pure, offline, fail-safe.

`clean(url) -> str` is the entire product; the tray, hotkey, clipboard watcher and
toast are only delivery around it. See contracts/clean-contract.md.

Three rules govern every line of this module:

1. **When in doubt, do nothing.** Malformed input, a signed URL, an excepted domain,
   an identical result, or any exception at all → return the input untouched.
2. **Allowlist, not blocklist.** Only KEYS on the curated tracker list are dropped.
   Every unknown param survives, which is what preserves YouTube's `v`/`t`, `page`,
   `q`, and everything else nobody has thought of yet.
3. **Never touch a value.** Matching parses the key; the surviving `key=value` pairs
   are copied through byte-for-byte from the original query string. A pair whose
   *value* merely looks like a tracker (`?redirect=utm_source_page`) is therefore
   impossible to mangle, and `%20` never silently becomes `+`.

Nothing here performs I/O beyond reading the bundled rules file once.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

__all__ = ["CleanResult", "clean", "clean_result", "load_rules"]

BUNDLED_RULES_PATH = Path(__file__).with_name("rules.json")

#: How many nested redirect wrappers to unwrap before giving up (SPEC-CLAUDE.md §5.1).
MAX_UNWRAP_DEPTH = 3

#: Query keys that mean "this URL is cryptographically signed — any edit returns 403".
#: Presence of ANY of these makes the whole URL untouchable. Deliberately broad: a
#: false positive here costs a missed clean, a false negative costs a broken link.
SIGNED_EXACT_KEYS = frozenset(
    {"sig", "signature", "hmac", "token", "expires", "policy", "signedheaders"}
)
SIGNED_KEY_PREFIXES = ("x-amz-", "x-goog-", "x-ms-")


@dataclass(frozen=True)
class CleanResult:
    """The outcome of one cleaning operation (data-model.md §1)."""

    original: str
    result: str
    params_removed: list[str] = field(default_factory=list)
    reason_noop: str | None = None

    @property
    def changed(self) -> bool:
        return self.original != self.result


# ---------------------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------------------


def load_rules(path: str | Path | None = None) -> dict[str, Any]:
    """Load a rule set, falling back to the bundled defaults if anything is wrong (FR-013).

    A user who corrupts their own rules.json gets a working nudl on defaults, not a
    crash and not a nudl that silently stops cleaning.
    """
    if path is not None:
        with contextlib.suppress(OSError, ValueError):  # fall through to bundled defaults
            with open(path, encoding="utf-8") as fh:
                rules = json.load(fh)
            if _rules_are_sane(rules):
                return rules
    return _bundled_rules()


def _rules_are_sane(rules: object) -> bool:
    return isinstance(rules, dict) and any(
        isinstance(rules.get(k), list)
        for k in ("global_tracker_keys", "redirect_wrappers", "providers")
    )


@lru_cache(maxsize=1)
def _bundled_rules() -> dict[str, Any]:
    with open(BUNDLED_RULES_PATH, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------------------
# Key matching
# ---------------------------------------------------------------------------------------


@lru_cache(maxsize=2048)
def _key_matcher(pattern: str) -> re.Pattern[str]:
    """Compile one rule pattern into a case-insensitive full-key matcher.

    `^utm_.*$` -> anchored regex, `pd_rd_*` -> prefix glob, `fbclid` -> exact key.
    """
    if pattern.startswith("^"):
        body = pattern
    elif pattern.endswith("*"):
        body = re.escape(pattern[:-1]) + ".*"
    else:
        body = re.escape(pattern)
    return re.compile(body, re.IGNORECASE)


def _matches_any(key: str, patterns: tuple[str, ...]) -> bool:
    return any(_key_matcher(p).fullmatch(key) for p in patterns)


# ---------------------------------------------------------------------------------------
# Host matching
# ---------------------------------------------------------------------------------------


def _hostname(parts: Any) -> str:
    try:
        return (parts.hostname or "").lower()
    except ValueError:  # malformed netloc, e.g. a bad IPv6 literal
        return ""


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _host_covers(host: str, domain: str) -> bool:
    """True if `host` is `domain` or a subdomain of it. Never a bare substring match."""
    domain = domain.lower().lstrip(".")
    return host == domain or host.endswith("." + domain)


def _providers_for(host: str, rules: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for provider in rules.get("providers", []):
        pattern = provider.get("urlPattern")
        if not pattern:
            continue
        try:
            if re.search(pattern, host, re.IGNORECASE):
                out.append(provider)
        except re.error:
            continue  # a broken pattern disables that provider, nothing else
    return out


# ---------------------------------------------------------------------------------------
# The query string: parsed for matching, copied byte-for-byte for output
# ---------------------------------------------------------------------------------------


def _raw_pairs(query: str) -> list[tuple[str, str]]:
    """Split a query into (decoded key, ORIGINAL raw 'k=v' text) pairs.

    The raw text is what gets re-emitted, so encoding, blank values and order all
    survive exactly as the user copied them.
    """
    if not query:
        return []
    pairs = []
    for raw in query.split("&"):
        raw_key = raw.split("=", 1)[0]
        pairs.append((unquote(raw_key), raw))
    return pairs


def _is_signed(query: str) -> bool:
    for key, _ in _raw_pairs(query):
        low = key.lower()
        if low in SIGNED_EXACT_KEYS or low.startswith(SIGNED_KEY_PREFIXES):
            return True
    return False


def _tracker_patterns(host: str, rules: dict[str, Any], strip_referral: bool) -> tuple[str, ...]:
    patterns: list[str] = list(rules.get("global_tracker_keys", []))
    for provider in _providers_for(host, rules):
        patterns.extend(provider.get("rules", []))
        if strip_referral:
            patterns.extend(provider.get("referral", []))
    if strip_referral:
        patterns.extend(rules.get("referral", []))
    return tuple(patterns)


# ---------------------------------------------------------------------------------------
# Redirect unwrapping — local, bounded, allowlist-only
# ---------------------------------------------------------------------------------------


def _wrapper_target(parts: Any, rules: dict[str, Any]) -> str | None:
    """The real destination hiding inside a known wrapper, or None.

    Only fires when the HOST is on the wrapper allowlist AND the path matches AND the
    named param decodes to an absolute http(s) URL. An arbitrary site's `?u=` is never
    followed, and `google.com/search?q=https://...` is not a wrapper — the /url path
    scope is what keeps a search-for-a-URL from being "unwrapped" into its own query.
    """
    host = _strip_www(_hostname(parts))
    if not host:
        return None

    for wrapper in rules.get("redirect_wrappers", []):
        if _strip_www(str(wrapper.get("host", "")).lower()) != host:
            continue
        if not parts.path.startswith(wrapper.get("path", "/")):
            continue
        param = wrapper.get("param")
        for key, raw in _raw_pairs(parts.query):
            if key != param:
                continue
            # unquote(), never unquote_plus(): a '+' inside an encoded URL is a literal
            # plus, not a space.
            candidate = unquote(raw.split("=", 1)[1]) if "=" in raw else ""
            if _is_absolute_http(candidate):
                return candidate
    return None


def _is_absolute_http(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


# ---------------------------------------------------------------------------------------
# rawRules — conservative, domain-scoped path surgery
# ---------------------------------------------------------------------------------------


def _apply_raw_rules(url: str, host: str, rules: dict[str, Any]) -> str:
    for provider in _providers_for(host, rules):
        for rule in provider.get("rawRules", []):
            pattern, replacement = rule.get("pattern"), rule.get("replacement", "")
            if not pattern:
                continue
            try:
                candidate = re.sub(pattern, replacement, url)
            except re.error:
                continue
            # A rawRule may only ever SHORTEN a URL. If it grew or mangled it into
            # something that no longer parses, discard the edit.
            if len(candidate) <= len(url) and _is_absolute_http(candidate):
                url = candidate
    return url


# ---------------------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------------------


def clean_result(
    url: str,
    *,
    rules: dict[str, Any] | None = None,
    exceptions: list[str] | None = None,
    strip_referral: bool = False,
) -> CleanResult:
    """Clean `url`, reporting what happened (for the toast and the audit log)."""
    try:
        return _clean_result(url, rules or _bundled_rules(), exceptions or [], strip_referral)
    except Exception:  # noqa: BLE001 — the fail-safe. No input may ever raise.
        return CleanResult(original=url, result=url, reason_noop="error")


def clean(
    url: str,
    *,
    rules: dict[str, Any] | None = None,
    exceptions: list[str] | None = None,
    strip_referral: bool = False,
) -> str:
    """Return `url` with tracking junk removed — or `url` itself, untouched.

    Pure and offline: same input, same output, zero network, zero global state.
    """
    return clean_result(
        url, rules=rules, exceptions=exceptions, strip_referral=strip_referral
    ).result


def _clean_result(
    url: str,
    rules: dict[str, Any],
    exceptions: list[str],
    strip_referral: bool,
) -> CleanResult:
    current = url
    seen = {url}
    removed: list[str] = []

    # Unwrap wrappers, re-running the guards on each new target. The loop runs one more
    # time than MAX_UNWRAP_DEPTH so that the last unwrapped URL is still guarded and
    # stripped — but that final pass does not itself unwrap.
    for depth in range(MAX_UNWRAP_DEPTH + 1):
        parts = urlsplit(current)

        if parts.scheme not in ("http", "https") or not parts.netloc:
            return CleanResult(original=url, result=url, reason_noop="not_a_url")

        # Signed URLs are load-bearing in their entirety: removing ANY param invalidates
        # the signature. This guard deliberately runs before unwrapping, so a wrapper
        # that carries its own ?token= (out.reddit.com does) is left alone rather than
        # half-rewritten.
        if _is_signed(parts.query):
            return CleanResult(original=url, result=current, reason_noop="signed_url")

        host = _hostname(parts)
        if _is_excepted(current, host, rules, exceptions):
            return CleanResult(original=url, result=current, reason_noop="exception")

        if depth == MAX_UNWRAP_DEPTH:
            break  # depth cap: guard and strip what we have, but unwrap no further
        target = _wrapper_target(parts, rules)
        if target is None or target in seen:  # not a wrapper, or a cycle
            break
        seen.add(target)
        current = target

    # Strip tracker keys. Kept pairs are re-emitted verbatim.
    patterns = _tracker_patterns(host, rules, strip_referral)
    kept: list[str] = []
    for key, raw in _raw_pairs(parts.query):
        if key and _matches_any(key, patterns):
            removed.append(key)
        else:
            kept.append(raw)

    current = urlunsplit((parts.scheme, parts.netloc, parts.path, "&".join(kept), parts.fragment))
    current = _apply_raw_rules(current, host, rules)

    if current == url:
        return CleanResult(original=url, result=url, reason_noop="identical")
    return CleanResult(original=url, result=current, params_removed=removed)


def _is_excepted(url: str, host: str, rules: dict[str, Any], exceptions: list[str]) -> bool:
    """User exception domains (FR-011) and provider exception patterns."""
    for domain in exceptions:
        if domain and _host_covers(host, str(domain)):
            return True
    for provider in _providers_for(host, rules):
        for pattern in provider.get("exceptions", []):
            try:
                if re.search(pattern, url, re.IGNORECASE):
                    return True
            except re.error:
                continue
    return False
