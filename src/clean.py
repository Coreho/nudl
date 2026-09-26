"""The nudl cleaning engine — pure, offline, fail-safe.

`clean(url) -> str` is the entire product; the tray, hotkey, clipboard watcher and
toast are only delivery around it.

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

import ipaddress
import json
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

__all__ = [
    "CleanResult",
    "RulesLoad",
    "RulesSummary",
    "TextCleanResult",
    "clean",
    "clean_result",
    "clean_text",
    "find_links",
    "load_rules",
    "load_rules_verbose",
    "merge_rules",
    "summarize",
]

logger = logging.getLogger(__name__)

BUNDLED_RULES_PATH = Path(__file__).with_name("rules.json")

#: How many nested redirect wrappers to unwrap before giving up.
MAX_UNWRAP_DEPTH = 3

#: Maximum URL length accepted for processing — bounds ReDoS exponent.
MAX_URL_LENGTH = 4096
#: Maximum query key length accepted for regex matching.
MAX_KEY_LENGTH = 256

#: Patterns with nested quantifiers are the classic ReDoS primitive — a group
#: that itself contains a quantifier, `.`, or alternation, followed by another
#: quantifier: `(a+)+`, `(a*)*`, `(a|a)*`, `(x+x+)+y`, `(.*a){n}`, `(.*)*`.
#: Reject at load time so a user-edited rules.json can never freeze the thread.
_NESTED_QUANTIFIER = re.compile(r"\([^)(]*[+*.\d|][^)(]*\)[*{+?]")

#: Query keys that mean "this URL is cryptographically signed — any edit returns 403".
#: Presence of ANY of these makes the whole URL untouchable. Deliberately broad: a
#: false positive here costs a missed clean, a false negative costs a broken link.
SIGNED_EXACT_KEYS = frozenset(
    {"sig", "signature", "hmac", "token", "expires", "policy", "signedheaders"}
)
SIGNED_KEY_PREFIXES = ("x-amz-", "x-goog-", "x-ms-")

#: The rules-file schema this engine understands. A file declaring a HIGHER version is
#: refused rather than half-read: it was written for an engine that knows fields this one
#: would silently ignore, and silently ignoring half a rule set is how nudl ends up
#: looking healthy while cleaning nothing.
SCHEMA_VERSION = "1.0"
_SCHEMA_PARTS = (1, 0)

#: Query keys that LOOK like tracking and are deliberately absent from every list, with
#: the reason. `rules.json` explains these in prose for a human reading the file; this is
#: the same knowledge in a form the validator can check a user's pattern against before
#: they break their own links. `test_clean.py` holds the executable tripwires.
NEVER_STRIP: dict[str, str] = {
    "si": "Spotify share id — removing it has broken shared-playlist and invite flows.",
    "sk": "Medium friend-link token — strip it and you paywall the article you shared.",
    "hash": "eBay item identifier. Only the _trk* params there are tracking.",
    "img_index": "Instagram: which photo in the carousel.",
    "sid": "Booking.com session id.",
    "context": "Reddit: how many parent comments to show.",
    "keywords": "Amazon: /s?keywords=... IS the search query, not a tracker.",
    "check_in": "Airbnb: the actual booking date.",
    "check_out": "Airbnb: the actual booking date.",
}

#: The only sanctioned exceptions to NEVER_STRIP: a key that may be stripped by the named
#: providers, on their own sites, and nowhere else. YouTube's `si` is the whole list. On
#: YouTube it identifies who shared the video and changes nothing about what plays; it is
#: also the tracker people paste most. Spotify's `si`, the one that has broken things,
#: stays protected — and a global `si` rule still trips every guard that exists.
NEVER_STRIP_EXCEPT_ON: dict[str, frozenset[str]] = {"si": frozenset({"youtube"})}


@dataclass(frozen=True)
class CleanResult:
    """The outcome of one cleaning operation."""

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


#: The last line of defence. If even the bundled rules.json cannot be read — a broken
#: install, a corrupted package — nudl must still clean the obvious offenders rather than
#: silently cleaning nothing. Silently doing nothing is the failure mode a user would
#: never notice, and "it quietly stopped working" is worse than "it does less".
EMERGENCY_RULES: dict[str, Any] = {
    "global_tracker_keys": [
        "^utm_.*$",
        "fbclid",
        "gclid",
        "dclid",
        "gbraid",
        "wbraid",
        "msclkid",
        "igshid",
        "mc_eid",
        "mc_cid",
    ],
    "redirect_wrappers": [],
    "referral": [],
    "providers": [],
}

#: `keep` is the user's side of the ledger: exact keys nudl must never strip, whatever any
#: pattern says. The bundled set has none — its deliberate omissions are simply absent.
_RULE_LIST_KEYS = ("global_tracker_keys", "redirect_wrappers", "providers", "referral", "keep")


@dataclass(frozen=True)
class RulesLoad:
    """Which rule set is live, where it came from, and why it is not the one you asked for."""

    rules: dict[str, Any]
    #: "custom" | "backup" | "bundled" | "emergency"
    source: str
    #: The file the rules were read from, when there was one.
    path: Path | None = None
    #: Why the REQUESTED file was refused. None when it was the one used.
    error: str | None = None
    #: The user's own file as written, before it was layered onto the bundled set. The
    #: validator checks THIS — reporting the bundled rules' warnings as if they were in the
    #: user's file would send them looking for lines they never wrote.
    own: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass(frozen=True)
class RulesSummary:
    """What is actually in a rule set — the counts a user can check their own file against."""

    global_keys: int
    providers: int
    provider_keys: int
    wrappers: int
    referral: int
    schema_version: str
    last_updated: str | None
    keep: int = 0

    def describe(self) -> str:
        return (
            f"{self.global_keys} global keys, {self.providers} providers, {self.wrappers} wrappers"
        )


def load_rules(path: str | Path | None = None) -> dict[str, Any]:
    """Load a rule set, falling back to the bundled defaults if anything is wrong.

    A user who corrupts their own rules.json gets a working nudl on defaults — not a
    crash, and not a nudl that silently stops cleaning.
    """
    return load_rules_verbose(path).rules


def load_rules_verbose(
    path: str | Path | None = None, *, backup: str | Path | None = None
) -> RulesLoad:
    """`load_rules`, but it says which file it used and what was wrong with the other one.

    A user's file is LAYERED onto the bundled set, never swapped in for it: what they wrote
    is added, and `keep` switches bundled rules off. It used to replace the bundled set
    outright, which meant that opening Rules… once froze that user's rules forever — every
    tracker added in a later release silently passed them by.

    Read-only, like the rest of this module. It will happily READ a backup somebody else
    wrote, but it never writes one and it never touches the user's file: deciding to
    overwrite something on disk is a policy call, and policy lives in `app.py`.

    Order is the requested file, then its backup, then the bundled defaults. Preferring
    the backup over the bundled set is the point of having one — a user who typos their
    own rules keeps their customisations for this run instead of silently reverting to
    stock and wondering why their links changed.
    """
    error: str | None = None
    bundled = _bundled_rules()
    if path is not None:
        own, error = _read_rules_file(path)
        if own is not None:
            return RulesLoad(merge_rules(bundled, own), "custom", Path(path), own=own)
        if backup is not None:
            restored, _ = _read_rules_file(backup)
            if restored is not None:
                merged = merge_rules(bundled, restored)
                return RulesLoad(merged, "backup", Path(backup), error, own=restored)

    if bundled is EMERGENCY_RULES:
        return RulesLoad(bundled, "emergency", None, error)
    return RulesLoad(bundled, "bundled", BUNDLED_RULES_PATH, error)


def _read_rules_file(path: str | Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read one rule file. Returns (rules, None), or (None, why it was refused).

    The reason is the whole point: "your rules.json was ignored" with no cause is the
    kind of message that sends a user hunting through their file blind.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            rules = json.load(fh)
    except FileNotFoundError:
        return None, "does not exist"
    except OSError as exc:
        return None, f"could not be read ({exc.strerror or exc})"
    except ValueError as exc:
        return None, f"is not valid JSON ({exc})"

    if not _rules_are_sane(rules):
        return None, "is not a rule set (a rule list is missing or the wrong shape)"
    schema_error = _schema_error(rules)
    if schema_error is not None:
        return None, schema_error
    return rules, None


def _schema_error(rules: dict[str, Any]) -> str | None:
    """Refuse a file written for a newer engine. Absent means 1.0, the original shape."""
    declared = rules.get("schema_version", SCHEMA_VERSION)
    if not isinstance(declared, str):
        return f"has a non-string schema_version ({declared!r})"
    parsed = _version_parts(declared)
    if parsed is None:
        return f"has an unreadable schema_version ({declared!r})"
    if parsed > _SCHEMA_PARTS:
        return f"needs schema_version {declared}; this nudl understands {SCHEMA_VERSION}"
    return None


def _version_parts(version: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in version.split("."))
    except ValueError:
        return None


def _rules_are_sane(rules: object) -> bool:
    """Reject a rule set that would blow up later.

    Checking only that *some* key is a list is not enough: `{"global_tracker_keys": [],
    "providers": "oops"}` would pass, then raise deep inside the pipeline on every single
    URL. The fail-safe would swallow it and nudl would quietly clean nothing at all —
    exactly the silent failure this project refuses to have. So every key that IS present
    must be the right shape.
    """
    if not isinstance(rules, dict):
        return False
    for key in _RULE_LIST_KEYS:
        if key in rules and not isinstance(rules[key], list):
            return False
    return any(key in rules for key in _RULE_LIST_KEYS)


@lru_cache(maxsize=1)
def _bundled_rules() -> dict[str, Any]:
    try:
        with open(BUNDLED_RULES_PATH, encoding="utf-8") as fh:
            rules = json.load(fh)
    except (OSError, ValueError):
        logger.error("bundled rules.json is unreadable; falling back to emergency rules")
        return EMERGENCY_RULES
    if not _rules_are_sane(rules):
        logger.error("bundled rules.json is malformed; falling back to emergency rules")
        return EMERGENCY_RULES
    return rules


def merge_rules(base: dict[str, Any], own: dict[str, Any]) -> dict[str, Any]:
    """`own` layered onto `base`: every list is a union, and base's entries come first.

    A provider in `own` with the same name as one in `base` extends it — its rules,
    referral keys, exceptions and rawRules are added, and base's urlPattern stands. A
    provider with a new name is added whole. Nothing in `own` can remove anything from
    `base`; `keep` is how a user switches a bundled rule off, and the engine enforces it at
    the moment of stripping, so it beats even a regex like `^utm_.*$`.

    Entries of the wrong type are dropped here rather than carried into the engine, where
    one dict in a list of patterns would fail every single clean.
    """
    merged = dict(base)
    for key in ("global_tracker_keys", "referral", "keep"):
        merged[key] = _union(_strings(base.get(key)), _strings(own.get(key)))
    merged["redirect_wrappers"] = _union(
        _dicts(base.get("redirect_wrappers")), _dicts(own.get("redirect_wrappers"))
    )

    providers = [dict(p) for p in _dicts(base.get("providers"))]
    by_name = {p.get("name"): p for p in providers if isinstance(p.get("name"), str)}
    for extra in _dicts(own.get("providers")):
        name = extra.get("name")
        target = by_name.get(name) if isinstance(name, str) else None
        if target is None:
            providers.append(dict(extra))
            continue
        for list_key in ("rules", "referral", "exceptions"):
            target[list_key] = _union(_strings(target.get(list_key)), _strings(extra.get(list_key)))
        target["rawRules"] = _union(_dicts(target.get("rawRules")), _dicts(extra.get("rawRules")))
    merged["providers"] = providers
    return merged


def _union(first: list[Any], second: list[Any]) -> list[Any]:
    return first + [item for item in second if item not in first]


def _strings(value: object) -> list[str]:
    return [item for item in _as_list(value) if isinstance(item, str)]


def _dicts(value: object) -> list[dict[str, Any]]:
    return [item for item in _as_list(value) if isinstance(item, dict)]


def summarize(rules: dict[str, Any]) -> RulesSummary:
    """Count what a rule set contains, so a user can confirm their file really loaded."""
    providers = [p for p in _as_list(rules.get("providers")) if isinstance(p, dict)]
    last_updated = rules.get("last_updated")
    return RulesSummary(
        global_keys=len(_as_list(rules.get("global_tracker_keys"))),
        providers=len(providers),
        provider_keys=sum(
            len(_as_list(p.get("rules")))
            + len(_as_list(p.get("referral")))
            + len(_as_list(p.get("rawRules")))
            for p in providers
        ),
        wrappers=len(_as_list(rules.get("redirect_wrappers"))),
        referral=len(_as_list(rules.get("referral"))),
        schema_version=str(rules.get("schema_version", SCHEMA_VERSION)),
        last_updated=last_updated if isinstance(last_updated, str) else None,
        keep=len(_as_list(rules.get("keep"))),
    )


def _as_list(value: object) -> list[Any]:
    """A rule set is user-editable, so any field may be the wrong type. Count nothing."""
    return value if isinstance(value, list) else []


# ---------------------------------------------------------------------------------------
# Key matching
# ---------------------------------------------------------------------------------------


_NEVER = re.compile(r"(?!)")


def _compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile one rule pattern into a case-insensitive full-key matcher. May raise re.error.

    Rejects patterns with nested quantifiers (the classic ReDoS primitive) — a
    user-edited rules.json must never be able to freeze the clipboard thread.
    """
    if pattern.startswith("^"):
        body = pattern
    elif pattern.endswith("*"):
        body = re.escape(pattern[:-1]) + ".*"
    else:
        body = re.escape(pattern)
    compiled = re.compile(body, re.IGNORECASE)
    # Check the ORIGINAL pattern (before our escaping) for nested quantifiers.
    # Patterns we escape ourselves are always safe; the risk is user-supplied regex.
    if pattern.startswith("^") and _NESTED_QUANTIFIER.search(pattern):
        raise re.error(f"pattern {pattern!r} contains a nested quantifier (ReDoS risk)")
    return compiled


@lru_cache(maxsize=2048)
def _key_matcher(pattern: str) -> re.Pattern[str]:
    """Compile one rule pattern into a case-insensitive full-key matcher.

    `^utm_.*$` -> anchored regex, `pd_rd_*` -> prefix glob, `fbclid` -> exact key.
    Returns a never-matching pattern on error so one bad rule never disables all cleaning.
    """
    try:
        return _compile_pattern(pattern)
    except re.error:
        logger.warning("skipping uncompilable tracker pattern %r", pattern)
        return _NEVER


@lru_cache(maxsize=1024)
def url_regex(pattern: str, flags: int = 0) -> re.Pattern[str] | None:
    """A provider `exceptions` or `rawRules` pattern, compiled — or None if refused.

    These run against the whole URL, up to MAX_URL_LENGTH characters, so a nested
    quantifier here freezes the clipboard thread exactly as it would in a key pattern. They
    get the same refusal, and a refused pattern simply does nothing: an exception that
    protects nothing, a rawRule that rewrites nothing. `validate` reports it.
    """
    if _NESTED_QUANTIFIER.search(pattern):
        logger.warning("skipping URL pattern %r: nested quantifier (ReDoS risk)", pattern)
        return None
    try:
        return re.compile(pattern, flags)
    except re.error:
        return None


def _matches_any(key: str, patterns: tuple[str, ...]) -> bool:
    if len(key) > MAX_KEY_LENGTH:
        return False
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
    host = host.lower().rstrip(".")
    domain = domain.lower().strip().lstrip(".").rstrip(".")
    if not domain:
        return False
    return host == domain or host.endswith("." + domain)


def _providers_for(host: str, rules: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for provider in rules.get("providers", []):
        pattern = provider.get("urlPattern")
        if not pattern:
            continue
        try:
            # Anchor the pattern to prevent `amazon.evil.com` from matching the
            # Amazon provider. A pattern not already anchored with ^ or (^|\.) is
            # wrapped to match only on a domain boundary.
            anchored = pattern
            if not (pattern.startswith("^") or pattern.startswith("(^")):
                anchored = r"(?:^|\.)" + pattern
            if re.search(anchored, host, re.IGNORECASE):
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


def _is_ambiguous(raw_pair: str) -> bool:
    """True if we cannot be certain where this pair ends — so we must not drop it.

    Query strings are split on `&`, but some servers (classic PHP, some Java stacks)
    ALSO accept `;` as a separator. That makes `?fbclid=x;id=5` genuinely ambiguous:

      * to a modern parser it is ONE param, `fbclid` = `"x;id=5"`
      * to a `;`-accepting server it is TWO, and `id=5` is functional

    Dropping the whole blob because its key matched a tracker would destroy `id=5` on
    every server of the second kind — nudl breaking a link, which is the one thing it
    must never do. Splitting on `;` instead would invent an `id` param on servers of
    the first kind, which is just a different way of being wrong.

    There is no interpretation that is safe in both worlds, so we take the fail-safe:
    when a pair we were about to strip contains a `;`, leave it alone. The cost is one
    missed tracker. The alternative cost is a broken link.
    """
    return ";" in raw_pair


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
        param = str(wrapper.get("param", "")).lower()
        for key, raw in _raw_pairs(parts.query):
            if key.lower() != param:
                continue
            # unquote(), never unquote_plus(): a '+' inside an encoded URL is a literal
            # plus, not a space.
            candidate = unquote(raw.split("=", 1)[1]) if "=" in raw else ""
            if not _is_absolute_http(candidate):
                continue
            # Reject targets with userinfo (user:pass@host display spoofing),
            # backslashes (parser confusion), or private/link-local IPs.
            target_parts = urlsplit(candidate)
            if "@" in target_parts.netloc or "\\" in candidate:
                continue
            try:
                ip = ipaddress.ip_address(target_parts.hostname or "")
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    continue
            except ValueError:
                pass
            return candidate
    return None


_FORBIDDEN_CHARS = re.compile(r"[\x00-\x20\x7f]")


def _is_absolute_http(url: str) -> bool:
    if _FORBIDDEN_CHARS.search(url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc)


# ---------------------------------------------------------------------------------------
# rawRules — conservative, domain-scoped path surgery
# ---------------------------------------------------------------------------------------


def _apply_raw_rules(url: str, host: str, rules: dict[str, Any]) -> str:
    before = urlsplit(url)
    for provider in _providers_for(host, rules):
        for rule in provider.get("rawRules", []):
            pattern, replacement = rule.get("pattern"), rule.get("replacement", "")
            compiled = url_regex(pattern) if isinstance(pattern, str) and pattern else None
            if compiled is None:
                continue
            try:
                candidate = compiled.sub(str(replacement), url)
            except re.error:
                continue
            # A rawRule may only ever SHORTEN a URL, and must preserve scheme and
            # host — a rule that silently retargets the link to another domain
            # or downgrades https→http is worse than no rule at all.
            after = urlsplit(candidate)
            if (
                len(candidate) <= len(url)
                and after.scheme == before.scheme
                and after.netloc == before.netloc
                and _is_absolute_http(candidate)
            ):
                url = candidate
                before = after
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
        # Returning the URL untouched is the right BEHAVIOUR — a link nudl cannot reason
        # about must come back exactly as it went in. But swallowing the traceback too
        # means a genuine engine bug is indistinguishable from "nothing to clean here",
        # and nudl would go on quietly cleaning nothing while looking perfectly healthy.
        logger.exception("clean() failed; returning the URL untouched")
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
    if len(url) > MAX_URL_LENGTH:
        return CleanResult(original=url, result=url, reason_noop="too_long")

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
            return CleanResult(original=url, result=url, reason_noop="signed_url")

        host = _hostname(parts)
        if _is_excepted(current, host, rules, exceptions):
            return CleanResult(original=url, result=url, reason_noop="exception")

        if depth == MAX_UNWRAP_DEPTH:
            break  # depth cap: guard and strip what we have, but unwrap no further
        target = _wrapper_target(parts, rules)
        if target is None or target in seen:  # not a wrapper, or a cycle
            break
        seen.add(target)
        current = target

    # Strip tracker keys. Kept pairs are re-emitted verbatim.
    patterns = _tracker_patterns(host, rules, strip_referral)
    keep = {k.lower() for k in _strings(rules.get("keep"))}
    kept: list[str] = []
    for key, raw in _raw_pairs(parts.query):
        if (
            key
            and key.lower() not in keep
            and _matches_any(key, patterns)
            and not _is_ambiguous(raw)
        ):
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
            compiled = url_regex(pattern, re.IGNORECASE) if isinstance(pattern, str) else None
            if compiled is not None and compiled.search(url):
                return True
    return False


# ---------------------------------------------------------------------------------------
# Links inside text
# ---------------------------------------------------------------------------------------

#: Don't scan more than this. The hotkey is for a message or a paragraph, not a log file,
#: and a clipboard this size is not something anyone meant to hand over.
MAX_TEXT_LENGTH = 1_000_000

#: A candidate link: http(s)://, then anything up to whitespace, a quote, an angle bracket
#: or a backtick — the characters that end a link in prose, Markdown, HTML and chat.
_LINK_IN_TEXT = re.compile(r"https?://[^\s<>\"'`\x00-\x1f\x7f]+", re.IGNORECASE)

#: Sentence punctuation that trails a link far more often than it belongs to one. `*` is
#: here for Markdown bold: `**https://x?utm_source=a**` must keep its asterisks.
_TRAILING_PUNCTUATION = ".,;:!?*"
_CLOSERS = {")": "(", "]": "[", "}": "{"}


@dataclass(frozen=True)
class TextCleanResult:
    """The outcome of cleaning every link inside a block of text."""

    original: str
    result: str
    #: One entry per link that actually changed, in the order they appear.
    links: list[CleanResult] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.original != self.result

    @property
    def params_removed(self) -> list[str]:
        return [key for link in self.links for key in link.params_removed]


def clean_text(
    text: str,
    *,
    rules: dict[str, Any] | None = None,
    exceptions: list[str] | None = None,
    strip_referral: bool = False,
) -> TextCleanResult:
    """Clean every link inside `text`, and leave every other character exactly as it was.

    Each link goes through the same `clean_result` as a bare one, with every guard intact
    — a signed link, an excepted domain or a link the engine is unsure of comes back
    untouched. Only the span of a link that genuinely changed is replaced, so the text
    around it survives byte for byte: spacing, line endings, Markdown, all of it.
    """
    if len(text) > MAX_TEXT_LENGTH:
        return TextCleanResult(original=text, result=text)

    pieces: list[str] = []
    links: list[CleanResult] = []
    cursor = 0
    for start, end in find_links(text):
        result = clean_result(
            text[start:end], rules=rules, exceptions=exceptions, strip_referral=strip_referral
        )
        if not result.changed:
            continue
        pieces.append(text[cursor:start])
        pieces.append(result.result)
        cursor = end
        links.append(result)
    pieces.append(text[cursor:])
    return TextCleanResult(original=text, result="".join(pieces), links=links)


def find_links(text: str) -> Iterator[tuple[int, int]]:
    """(start, end) of every http(s) link in `text`, sentence punctuation trimmed off."""
    for match in _LINK_IN_TEXT.finditer(text):
        link = _trim_link(match.group())
        if link:
            yield match.start(), match.start() + len(link)


def _trim_link(candidate: str) -> str:
    """Drop what a sentence put after a link, without eating what belongs to it.

    A closing bracket is only trimmed when it has no opener inside the link, so a
    Wikipedia link like `/wiki/Foo_(bar)` keeps its parenthesis while `(see https://x)`
    gives its back to the sentence.
    """
    while candidate:
        last = candidate[-1]
        unbalanced = last in _CLOSERS and candidate.count(last) > candidate.count(_CLOSERS[last])
        if last not in _TRAILING_PUNCTUATION and not unbalanced:
            break
        candidate = candidate[:-1]
    return candidate
