"""The rules validator — see exactly what a rule would do before it goes live.

A user editing `rules.json` by hand has no feedback loop. A typo'd regex, a pattern one
character too broad, or a rule aimed at a key that is deliberately absent all fail the
same quiet way: either nudl stops cleaning something it used to clean, or it starts
breaking links. Neither announces itself. This module is that missing feedback loop, and
it is deliberately a separate tool the user invokes — nothing here runs inside the app.

Two properties make it safe to point at a live install:

1. **It never performs a network call.** Nothing is fetched, resolved or reported. Every
   answer comes from the rule file on disk and the local engine, exactly like `clean.py`.
2. **It never writes to the active `rules.json`.** REPL edits mutate an in-memory copy,
   and `save` refuses any path that resolves to a live rules file or its backup. Losing a
   user's hand-written rules to the tool that was supposed to check them would be a worse
   failure than any rule it could ever catch.

Every verdict comes from the real engine — `clean.clean_result` run against a synthetic
rule set, never a second implementation of the matching logic. A validator that models
the pipeline instead of running it eventually disagrees with it, and at that point it is
worse than nothing: it lies to the user about their own rules with total confidence.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from src import clean, config

__all__ = [
    "Finding",
    "PatternTest",
    "Session",
    "check_pattern",
    "check_rules",
    "main",
    "summarise_check",
]

#: The URL every command falls back to when the user has not named one. It carries one
#: obvious tracker, one referral tag and two functional params, so a pattern that is too
#: broad gives itself away on the very first `test`.
SAMPLE_URL = "https://www.amazon.com/dp/B08X?utm_source=google&tag=aff-20&v=123"

#: Ordinary functional keys — the ones that decide WHICH page you land on. None of these
#: is a tracker anywhere, so a pattern matching one is either a mistake or a decision the
#: user should have to look at twice. A pattern matching ALL of them is `.*` wearing a
#: hat: it would strip every parameter on every link, which is nudl breaking the web.
FUNCTIONAL_KEYS = (
    "id",
    "q",
    "page",
    "v",
    "t",
    "search",
    "lang",
    "sort",
    "limit",
    "offset",
    "name",
    "title",
    "url",
    "code",
    "type",
)

#: Why the engine declined to change a URL, in words a user can act on. "Nothing happened"
#: with no reason given is the message that sends someone hunting through their rules for
#: a bug that was never there.
_NOOP_REASONS = {
    "not_a_url": "that is not an absolute http(s) URL, so nudl leaves it alone",
    "signed_url": ("the URL carries a signed-request key, so nudl refuses to touch any part of it"),
    "exception": "an exception in these rules covers this URL, so nudl leaves it alone",
    "identical": "the pattern matched nothing on this URL",
    "error": "the engine failed on this URL and returned it untouched (check the log)",
}


@dataclass(frozen=True)
class Finding:
    """One thing wrong with one rule, and where to find it."""

    #: "error" — this breaks links or silently disables a rule. "warning" — confirm it.
    level: str
    #: Where in the file, e.g. "global_tracker_keys[3]" or "providers[2].rules[0]".
    where: str
    message: str

    def describe(self) -> str:
        where = f"{self.where}: " if self.where else ""
        return f"{self.level:<7} {where}{self.message}"


@dataclass(frozen=True)
class PatternTest:
    """What the engine actually did to one URL with one pattern in play."""

    # pytest imports this module in its own test file; without this it would try to
    # collect a dataclass whose name it happens to like the look of.
    __test__ = False

    pattern: str
    url: str
    before: str
    after: str
    removed: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    reason_noop: str | None = None

    def describe(self) -> str:
        lines = [
            f"Pattern:  {self.pattern}  ({_pattern_kind(self.pattern)})",
            f"Before:   {self.before}",
            f"After:    {self.after}",
            f"Removed:  {', '.join(self.removed) or 'nothing'}",
            f"Kept:     {', '.join(self.kept) or 'nothing'}",
        ]
        if not self.removed and self.reason_noop:
            lines.append(f"Why not:  {_NOOP_REASONS.get(self.reason_noop, self.reason_noop)}")
        lines.extend(finding.describe() for finding in self.findings)
        return "\n".join(lines)


@dataclass
class Session:
    """One REPL's worth of state.

    `rules` is a private copy: `add` and `remove` are experiments, not edits. `reserved`
    is the list of paths `save` must refuse, which is what keeps an experiment from
    landing on top of the file nudl actually reads.
    """

    rules: dict[str, Any]
    url: str = SAMPLE_URL
    reserved: tuple[Path, ...] = ()


# ---------------------------------------------------------------------------------------
# Checking one pattern
# ---------------------------------------------------------------------------------------


def _pattern_kind(pattern: str) -> str:
    """Name the pattern the way `clean._key_matcher` reads it.

    A user who thinks their glob is a regex has already written the wrong rule; saying
    which one the engine sees is the cheapest way to catch that.
    """
    if pattern.startswith("^"):
        return "anchored regex"
    if pattern.endswith("*"):
        return "prefix glob"
    return "exact key"


def check_pattern(pattern: str, where: str = "") -> list[Finding]:
    """Everything that can be said about one tracker pattern without a URL to try it on."""
    if not pattern.strip():
        return [
            Finding(
                "error",
                where,
                "is empty, so it matches nothing — the rule is there but does no work",
            )
        ]

    try:
        matcher = clean._key_matcher(pattern)
    except re.error as exc:
        # Stop here: a pattern that will not compile has no match behaviour to check. The
        # engine skips it silently, so the rule the user thinks they wrote does not exist.
        return [
            Finding(
                "error",
                where,
                f"{pattern!r} is not a valid pattern ({exc}) — the engine skips it, so this "
                "rule would silently do nothing at all",
            )
        ]

    findings: list[Finding] = []

    signed = sorted(key for key in clean.SIGNED_EXACT_KEYS if matcher.fullmatch(key))
    # One representative key per prefix: matching `x-amz-signature` is enough to prove the
    # pattern reaches into the signed-parameter namespace.
    signed += [
        f"{prefix}signature"
        for prefix in clean.SIGNED_KEY_PREFIXES
        if matcher.fullmatch(f"{prefix}signature")
    ]
    if signed:
        findings.append(
            Finding(
                "error",
                where,
                f"{pattern!r} matches the signed-request key(s) {', '.join(signed)} — a signed "
                "URL is load-bearing in its entirety, and stripping any part of one turns "
                "every such link into a 403",
            )
        )

    for key, reason in clean.NEVER_STRIP.items():
        if matcher.fullmatch(key):
            findings.append(
                Finding(
                    "warning",
                    where,
                    f"{pattern!r} matches {key!r}, which is deliberately absent: {reason}",
                )
            )

    hits = [key for key in FUNCTIONAL_KEYS if matcher.fullmatch(key)]
    if len(hits) == len(FUNCTIONAL_KEYS):
        findings.append(
            Finding(
                "error",
                where,
                f"{pattern!r} matches every ordinary functional key — this is a '.*' rule in "
                "disguise and would strip every parameter on every link",
            )
        )
    elif hits:
        findings.append(
            Finding(
                "warning",
                where,
                f"{pattern!r} also matches the functional key(s) {', '.join(hits)}, which are "
                "not trackers — stripping one changes which page the link opens",
            )
        )

    # Only meaningful for an exact key: an anchored regex or a glob already says what it
    # matches, and `.fullmatch` above has answered that question properly.
    if not pattern.startswith("^") and not pattern.endswith("*"):
        low = pattern.lower()
        inside = [key for key in clean.NEVER_STRIP if low != key and low in key]
        if inside:
            findings.append(
                Finding(
                    "warning",
                    where,
                    f"{pattern!r} is a substring of the deliberately absent key(s) "
                    f"{', '.join(inside)} — harmless as an exact key, but it would start "
                    "stripping them the moment somebody widens it into a regex",
                )
            )

    return findings


# ---------------------------------------------------------------------------------------
# Checking a whole rule set
# ---------------------------------------------------------------------------------------


def check_rules(rules: dict[str, Any]) -> list[Finding]:
    """Every finding in a whole rule set, in the order a reader would meet them.

    This is a file a human hand-edited, so every field may be the wrong type. Nothing here
    may raise: a validator that crashes on a malformed file tells the user strictly less
    than the engine's own fallback already did.
    """
    if not isinstance(rules, dict):
        return [Finding("error", "", "this is not a rule set — the top level is not an object")]

    findings: list[Finding] = []
    schema_error = clean._schema_error(rules)
    if schema_error is not None:
        findings.append(Finding("error", "schema_version", schema_error))

    findings += _check_patterns(rules, "global_tracker_keys")
    findings += _check_patterns(rules, "referral")
    findings += _check_providers(rules)
    findings += _check_wrappers(rules)
    return findings


def _as_list(value: object, where: str) -> tuple[list[Any], list[Finding]]:
    """A list field, or the finding that says why there is nothing to check."""
    if value is None:
        return [], []  # absent is legitimate: a user may trim the file to what they use
    if not isinstance(value, list):
        return [], [Finding("error", where, f"is a {type(value).__name__}, not a list")]
    return value, []


def _check_patterns(container: dict[str, Any], key: str, where: str | None = None) -> list[Finding]:
    """One list of tracker patterns, wherever it lives."""
    where = where or key
    values, findings = _as_list(container.get(key), where)
    for index, value in enumerate(values):
        spot = f"{where}[{index}]"
        if not isinstance(value, str):
            findings.append(
                Finding("error", spot, f"is a {type(value).__name__}, not a pattern string")
            )
            continue
        findings += check_pattern(value, spot)
    return findings


def _check_regexes(container: dict[str, Any], key: str, where: str) -> list[Finding]:
    """Exception patterns are plain regexes matched against the whole URL, not key globs."""
    values, findings = _as_list(container.get(key), where)
    for index, value in enumerate(values):
        spot = f"{where}[{index}]"
        if not isinstance(value, str):
            findings.append(
                Finding("error", spot, f"is a {type(value).__name__}, not a regex string")
            )
            continue
        try:
            re.compile(value)
        except re.error as exc:
            findings.append(
                Finding(
                    "error",
                    spot,
                    f"{value!r} does not compile ({exc}) — the engine ignores it, so this "
                    "exception protects nothing",
                )
            )
    return findings


def _check_url_pattern(pattern: object, where: str) -> list[Finding]:
    """A provider's `urlPattern` is the quietest thing in the file to get wrong.

    Missing or uncompilable, and the engine skips the provider: nudl looks healthy and
    cleans nothing on that site. Too broad, and the provider's rules fire everywhere, so a
    site-specific rule starts stripping params on unrelated links.

    The second case is what `example.invalid` is for. RFC 2606 reserves `.invalid`, so no
    real provider can live there — a pattern that matches it is not scoped to a host at
    all. (Checking that a pattern matches the provider's *own* domain is not possible from
    this data: `steam`'s pattern covers steamcommunity.com and steampowered.com, neither
    of which is derivable from the name.)
    """
    if not isinstance(pattern, str) or not pattern:
        return [
            Finding(
                "error",
                where,
                "is missing, so the engine skips this provider entirely and none of its rules "
                "will ever apply",
            )
        ]
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return [
            Finding(
                "error",
                where,
                f"{pattern!r} does not compile ({exc}) — the engine skips this provider, so "
                "none of its rules will ever apply",
            )
        ]
    if compiled.search("example.invalid"):
        return [
            Finding(
                "error",
                where,
                f"{pattern!r} matches example.invalid, a host it cannot possibly mean — it is "
                "not scoped to a site, so this provider's rules would apply everywhere",
            )
        ]
    return []


def _check_raw_rules(provider: dict[str, Any], where: str) -> list[Finding]:
    """rawRules do path surgery with `re.sub`, so a bad pattern here rewrites URLs."""
    values, findings = _as_list(provider.get("rawRules"), where)
    for index, rule in enumerate(values):
        spot = f"{where}[{index}]"
        if not isinstance(rule, dict):
            findings.append(Finding("error", spot, f"is a {type(rule).__name__}, not an object"))
            continue

        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            findings.append(
                Finding("error", spot, "has no pattern, so the engine skips it and it does nothing")
            )
        else:
            try:
                re.compile(pattern)
            except re.error as exc:
                findings.append(
                    Finding(
                        "error",
                        spot,
                        f"{pattern!r} does not compile ({exc}) — the engine skips it and it does "
                        "nothing",
                    )
                )

        if "replacement" not in rule:
            findings.append(
                Finding(
                    "warning",
                    spot,
                    "has no replacement, so it deletes whatever it matches — write "
                    '"replacement": "" if that is what you meant',
                )
            )
    return findings


def _check_providers(rules: dict[str, Any]) -> list[Finding]:
    providers, findings = _as_list(rules.get("providers"), "providers")
    for index, provider in enumerate(providers):
        spot = f"providers[{index}]"
        if not isinstance(provider, dict):
            findings.append(
                Finding("error", spot, f"is a {type(provider).__name__}, not an object")
            )
            continue

        name = provider.get("name")
        # The name is what a user searches the file for; the index alone sends them counting.
        label = f"{spot} ({name})" if isinstance(name, str) and name else spot
        findings += _check_url_pattern(provider.get("urlPattern"), f"{label}.urlPattern")
        findings += _check_patterns(provider, "rules", f"{label}.rules")
        findings += _check_patterns(provider, "referral", f"{label}.referral")
        findings += _check_regexes(provider, "exceptions", f"{label}.exceptions")
        findings += _check_raw_rules(provider, f"{label}.rawRules")
    return findings


def _check_wrappers(rules: dict[str, Any]) -> list[Finding]:
    wrappers, findings = _as_list(rules.get("redirect_wrappers"), "redirect_wrappers")
    for index, wrapper in enumerate(wrappers):
        spot = f"redirect_wrappers[{index}]"
        if not isinstance(wrapper, dict):
            findings.append(Finding("error", spot, f"is a {type(wrapper).__name__}, not an object"))
            continue

        host = wrapper.get("host")
        if not isinstance(host, str) or not host.strip():
            findings.append(
                Finding(
                    "error",
                    spot,
                    "has no host — the wrapper allowlist is matched by host, so this entry can "
                    "never fire",
                )
            )

        param = wrapper.get("param")
        if not isinstance(param, str) or not param.strip():
            findings.append(
                Finding(
                    "error",
                    spot,
                    "has no param, so there is no query key to read the real destination out of",
                )
            )

        path = wrapper.get("path")
        if not isinstance(path, str) or not path.startswith("/"):
            findings.append(
                Finding(
                    "error",
                    spot,
                    f"has path {path!r}; it must start with '/'. The path scope is what stops a "
                    "search-for-a-URL being 'unwrapped' into its own query",
                )
            )
    return findings


# ---------------------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------------------


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def summarise_check(path: Path, load: clean.RulesLoad, findings: list[Finding]) -> str:
    """The `--check` report: which file is live, what is in it, and what is wrong with it.

    The header is as important as the findings. A user whose custom file was refused needs
    to see that the counts below belong to the bundled set, not to the file they just
    edited — otherwise "27 global keys" reads as confirmation that their edit took.
    """
    summary = clean.summarize(load.rules)
    source = load.source
    if load.path is not None and load.source != "custom":
        source = f"{source} ({load.path})"
    if load.error is not None:
        source = f"{source} — {path.name} {load.error}"

    lines = [
        f"Checked:  {path}",
        f"Source:   {source}",
        f"Schema:   {summary.schema_version} (this nudl understands {clean.SCHEMA_VERSION})",
        f"Contents: {summary.describe()}",
        f"          {summary.provider_keys} provider patterns, {summary.referral} referral keys",
        f"Updated:  {summary.last_updated or 'not stated'}",
        "",
    ]
    lines.extend(finding.describe() for finding in findings)
    if findings:
        lines.append("")

    errors = sum(1 for finding in findings if finding.level == "error")
    warnings = len(findings) - errors
    if errors:
        lines.append(
            f"{_plural(errors, 'error')}, {_plural(warnings, 'warning')} — every error either "
            "breaks links or silently disables a rule. Fix those before relying on this file."
        )
    elif warnings:
        lines.append(
            f"No errors, {_plural(warnings, 'warning')} — nothing is broken, but each warning "
            "is a deliberate choice worth confirming."
        )
    else:
        lines.append("No problems found.")
    return "\n".join(lines)


def _check_findings(path: Path, load: clean.RulesLoad) -> list[Finding]:
    """What `--check` reports on.

    A file that is simply not there is the normal state — the custom rules path is opt-in
    and nudl ships its own copy — so that is not a finding, and we check the bundled set
    that is genuinely live instead. A file that EXISTS and was refused is a different
    thing entirely: the user wrote it, nudl is ignoring it, and that is precisely what
    they ran `--check` to discover.
    """
    if load.error is not None and path.exists():
        return [Finding("error", path.name, load.error)]
    return check_rules(load.rules)


# ---------------------------------------------------------------------------------------
# --test
# ---------------------------------------------------------------------------------------


def _query_keys(url: str) -> list[str]:
    try:
        return [key for key, _ in clean._raw_pairs(urlsplit(url).query)]
    except ValueError:  # a netloc the parser refuses, e.g. a bad IPv6 literal
        return []


def test_pattern(pattern: str, url: str, rules: dict[str, Any] | None = None) -> PatternTest:
    """What nudl would do to `url` if `pattern` were on the tracker list.

    The answer comes from `clean.clean_result` on a synthetic rule set rather than from a
    second copy of the matching logic here — that is the whole point. Anything this
    reports, the engine really does.

    `rules` is the rule set the pattern is added TO. The default — the pattern and nothing
    else — is what makes the answer unambiguous: every key listed as removed was removed
    by THIS pattern, not by a provider rule the user forgot was there. Pass a real rule
    set to watch the pattern work alongside the rest of the file.
    """
    synthetic: dict[str, Any] = copy.deepcopy(rules) if rules else {}
    existing = synthetic.get("global_tracker_keys")
    synthetic["global_tracker_keys"] = [
        *(existing if isinstance(existing, list) else []),
        pattern,
    ]

    result = clean.clean_result(url, rules=synthetic)
    return PatternTest(
        pattern=pattern,
        url=url,
        before=result.original,
        after=result.result,
        removed=list(result.params_removed),
        kept=_query_keys(result.result),
        findings=check_pattern(pattern),
        reason_noop=result.reason_noop,
    )


# Belt and braces: pytest only collects from test files, but this name is a magnet.
test_pattern.__test__ = False


# ---------------------------------------------------------------------------------------
# The REPL
# ---------------------------------------------------------------------------------------

HELP = """Commands:
  test <pattern> [--url <url>]                  what this pattern removes from a URL
  add <pattern>                                 add to global_tracker_keys (in memory)
  remove <pattern>                              remove from global_tracker_keys (in memory)
  list                                          show the rules currently loaded
  provider <name>                               show one provider's rules
  raw <pattern> <replacement> [--url <url>]     test a rawRules substitution
  wrapper <host> <path> <param> [--url <url>]   test a redirect wrapper
  except <domain> [--url <url>]                 test whether a domain is excepted
  save <path>                                   write these rules to a NEW file
  help                                          this list
  quit                                          leave; nothing is saved

Quote a pattern that contains regex metacharacters:  test "^utm_.*$"
--url is remembered, so later commands reuse the last URL you named."""


def _unquote(token: str) -> str:
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    return token


def _split(line: str) -> list[str]:
    """Split a command line, keeping both quoted patterns and Windows paths intact.

    `shlex.split` defaults to POSIX mode, where a backslash escapes the next character —
    `save C:\\rules\\mine.json` would arrive as `C:rulesmine.json`, a silent write to the
    wrong place on the one platform nudl ships on. Non-POSIX mode keeps backslashes, at
    the price of leaving the quotes attached to the token, so we take those off here.
    """
    return [_unquote(token) for token in shlex.split(line, posix=False)]


def _take_url(session: Session, args: list[str]) -> list[str]:
    """Pull `--url X` out of `args`, remembering it for the rest of the session."""
    rest: list[str] = []
    index = 0
    while index < len(args):
        if args[index] == "--url" and index + 1 < len(args):
            session.url = args[index + 1]
            index += 2
            continue
        rest.append(args[index])
        index += 1
    return rest


def _cmd_help(session: Session, args: list[str]) -> None:
    print(HELP)


def _cmd_test(session: Session, args: list[str]) -> None:
    rest = _take_url(session, args)
    if len(rest) != 1:
        print("usage: test <pattern> [--url <url>]")
        return
    print(test_pattern(rest[0], session.url).describe())


def _cmd_add(session: Session, args: list[str]) -> None:
    if len(args) != 1:
        print("usage: add <pattern>")
        return
    pattern = args[0]
    keys = session.rules.get("global_tracker_keys")
    if not isinstance(keys, list):
        keys = []
        session.rules["global_tracker_keys"] = keys
    keys.append(pattern)
    print(f"added {pattern!r} to global_tracker_keys — in memory only, nothing on disk changed")
    for finding in check_pattern(pattern):
        print(finding.describe())


def _cmd_remove(session: Session, args: list[str]) -> None:
    if len(args) != 1:
        print("usage: remove <pattern>")
        return
    keys = session.rules.get("global_tracker_keys")
    if not isinstance(keys, list) or args[0] not in keys:
        print(f"{args[0]!r} is not in global_tracker_keys")
        return
    keys.remove(args[0])
    print(f"removed {args[0]!r} from global_tracker_keys — in memory only, the file is untouched")


def _cmd_list(session: Session, args: list[str]) -> None:
    rules = session.rules
    summary = clean.summarize(rules)
    print(f"{summary.describe()}  (schema {summary.schema_version})")

    def joined(key: str) -> str:
        return ", ".join(str(value) for value in clean._as_list(rules.get(key))) or "none"

    print(f"\nglobal_tracker_keys: {joined('global_tracker_keys')}")
    print(f"referral: {joined('referral')}")

    print("\nredirect_wrappers:")
    for wrapper in clean._as_list(rules.get("redirect_wrappers")):
        if isinstance(wrapper, dict):
            print(f"  {wrapper.get('host')}{wrapper.get('path')}?{wrapper.get('param')}=<url>")

    print("\nproviders:")
    for provider in clean._as_list(rules.get("providers")):
        if not isinstance(provider, dict):
            continue
        # Printed bare, not with !r: `repr()` doubles every backslash, so a pattern the
        # user is about to compare against their own file would come back looking like a
        # different pattern.
        keys = _plural(len(clean._as_list(provider.get("rules"))), "key")
        print(f"  {provider.get('name')}: {keys}, urlPattern {provider.get('urlPattern')}")


def _cmd_provider(session: Session, args: list[str]) -> None:
    if len(args) != 1:
        print("usage: provider <name>")
        return
    wanted = args[0].lower()
    names = []
    for provider in clean._as_list(session.rules.get("providers")):
        if not isinstance(provider, dict):
            continue
        name = str(provider.get("name", ""))
        names.append(name)
        if name.lower() == wanted:
            print(json.dumps(provider, indent=2, ensure_ascii=False))
            for finding in check_rules({"providers": [provider]}):
                print(finding.describe())
            return
    print(f"no provider named {args[0]!r}. Known: {', '.join(sorted(n for n in names if n))}")


def _cmd_raw(session: Session, args: list[str]) -> None:
    rest = _take_url(session, args)
    if len(rest) != 2:
        print("usage: raw <pattern> <replacement> [--url <url>]")
        return
    pattern, replacement = rest
    url = session.url
    try:
        candidate = re.sub(pattern, replacement, url)
    except re.error as exc:
        print(f"{pattern!r} does not compile ({exc}) — the engine would skip this rawRule")
        return

    print(f"Before: {url}")
    print(f"After:  {candidate}")
    # The engine's two gates, from `clean._apply_raw_rules`: a rawRule may only ever
    # shorten a URL, and what comes out must still be a URL. Anything else is discarded,
    # so a rule that fails one of them is a rule that does nothing.
    if len(candidate) > len(url):
        print(
            "Rejected: the result is longer than the original. A rawRule may only shorten a "
            "URL, so the engine would discard this edit."
        )
    elif not clean._is_absolute_http(candidate):
        print(
            "Rejected: the result is not an absolute http(s) URL, so the engine would discard "
            "this edit."
        )
    elif candidate == url:
        print("Accepted, but it changed nothing on this URL.")
    else:
        print("Accepted: the engine would apply this.")


def _wrapper_miss(parts: Any, host: str, path: str, param: str) -> str:
    """Why `_wrapper_target` said no. Same primitives, so this cannot drift from it."""
    url_host = clean._strip_www(clean._hostname(parts))
    if not url_host:
        return "this URL has no host"
    if clean._strip_www(host.lower()) != url_host:
        return f"the wrapper host is {host!r}, but this URL's host is {url_host!r}"
    if not parts.path.startswith(path):
        return f"the wrapper path is {path!r}, but this URL's path is {parts.path!r}"
    for key, raw in clean._raw_pairs(parts.query):
        if key.lower() == param.lower():
            value = unquote(raw.split("=", 1)[1]) if "=" in raw else ""
            return (
                f"{param}={value!r} is not an absolute http(s) URL — following it could send "
                "the user somewhere that is not a link at all"
            )
    return f"this URL has no {param!r} parameter"


def _cmd_wrapper(session: Session, args: list[str]) -> None:
    rest = _take_url(session, args)
    if len(rest) != 3:
        print("usage: wrapper <host> <path> <param> [--url <url>]")
        return
    host, path, param = rest
    synthetic = {"redirect_wrappers": [{"host": host, "path": path, "param": param}]}
    parts = urlsplit(session.url)

    print(f"URL:    {session.url}")
    target = clean._wrapper_target(parts, synthetic)
    if target is not None:
        print(f"Target: {target}")
        print("The engine would unwrap this, then clean the target.")
        return
    print("Target: none — the engine would not unwrap this")
    print(f"Why:    {_wrapper_miss(parts, host, path, param)}")


def _cmd_except(session: Session, args: list[str]) -> None:
    rest = _take_url(session, args)
    if len(rest) != 1:
        print("usage: except <domain> [--url <url>]")
        return
    domain = rest[0]
    host = clean._hostname(urlsplit(session.url))
    print(f"URL:  {session.url}")
    print(f"Host: {host or 'none'}")

    if clean._host_covers(host, domain):
        print(f"{domain!r} covers this host, so nudl would return the URL untouched.")
    elif clean._is_excepted(session.url, host, session.rules, [domain]):
        # The engine checks provider exceptions in the same call, so a True that the
        # domain did not earn has to be attributed honestly.
        print(
            f"{domain!r} does not cover this host, but a provider exception in these rules "
            "matches — nudl would return the URL untouched."
        )
    else:
        print(f"{domain!r} does not cover this host; nudl would clean this URL normally.")


def _absolute(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return Path(os.path.abspath(str(path)))


def _same_path(left: Path, right: Path) -> bool:
    """Same file even when spelled differently.

    The refusal below is a hard guarantee, so it must not be defeated by a relative path,
    a `..`, or a different case on a case-insensitive filesystem.
    """
    return os.path.normcase(str(_absolute(left))) == os.path.normcase(str(_absolute(right)))


def _cmd_save(session: Session, args: list[str]) -> None:
    if len(args) != 1:
        print("usage: save <path>")
        return
    target = Path(args[0]).expanduser()

    for reserved in session.reserved:
        if _same_path(target, reserved):
            print(
                f"refused: {target} is a live rules file (or its backup). The validator never "
                "writes to the rules nudl actually reads — save somewhere else, look at it, "
                "then move it into place yourself."
            )
            return

    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as fh:
        json.dump(session.rules, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {target}")


_COMMANDS = {
    "help": _cmd_help,
    "test": _cmd_test,
    "add": _cmd_add,
    "remove": _cmd_remove,
    "list": _cmd_list,
    "provider": _cmd_provider,
    "raw": _cmd_raw,
    "wrapper": _cmd_wrapper,
    "except": _cmd_except,
    "save": _cmd_save,
}


def run_command(session: Session, line: str) -> bool:
    """Run one REPL line. Returns False only when the user asked to leave.

    Every failure is caught and printed. A validator that dies on an unbalanced quote
    sends the user back to editing rules.json blind, which is the entire problem it exists
    to solve.
    """
    try:
        args = _split(line)
    except ValueError as exc:
        print(f"error: {exc}")
        return True
    if not args:
        return True

    name = args[0].lower()
    if name in ("quit", "exit"):
        return False

    handler = _COMMANDS.get(name)
    if handler is None:
        print(f"unknown command {args[0]!r}")
        print(HELP)
        return True

    try:
        handler(session, args[1:])
    except Exception as exc:  # noqa: BLE001 — one typo must not end the session
        print(f"error: {type(exc).__name__}: {exc}")
    return True


def repl(session: Session) -> int:
    print("nudl rules validator — nothing you do here touches the live rules file.")
    print(f"URL: {session.url}")
    print("'help' for commands, 'quit' to leave.")
    while True:
        try:
            line = input("nudl-validator> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not run_command(session, line):
            return 0


# ---------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------


def _reserved_paths(path: Path) -> tuple[Path, ...]:
    """Every path `save` must refuse.

    Both the file this session read and the file nudl reads, plus their backups. The two
    are usually the same, but `--rules somewhere/else.json` must not turn into a licence
    to write over the real one.
    """
    return (
        path,
        Path(f"{path}.bak"),
        config.rules_path(),
        config.rules_backup_path(),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.validate",
        description=(
            "Check and experiment with nudl's rules. Never touches the network and never "
            "writes to the rules file nudl reads."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the active rules file, print a report, exit 1 if anything is wrong",
    )
    parser.add_argument(
        "--test",
        metavar="PATTERN",
        help="show what adding PATTERN would do to --url, then exit",
    )
    parser.add_argument(
        "--url",
        metavar="URL",
        help=f"the URL to test against (default: {SAMPLE_URL})",
    )
    parser.add_argument(
        "--rules",
        metavar="PATH",
        help="work on PATH instead of the active rules file",
    )
    args = parser.parse_args(argv)

    path = Path(args.rules).expanduser() if args.rules else config.rules_path()
    # Read-only, and no backup fallback on purpose: `--check` is asked about ONE file, and
    # quietly reporting on a different one is how a user comes away sure their edit landed.
    load = clean.load_rules_verbose(path)

    if args.check:
        findings = _check_findings(path, load)
        print(summarise_check(path, load, findings))
        return 1 if any(finding.level == "error" for finding in findings) else 0

    if args.test is not None:
        print(test_pattern(args.test, args.url or SAMPLE_URL).describe())
        return 0

    if load.error is not None:
        print(f"note: {path.name} {load.error} — loaded the {load.source} rules instead.")
    return repl(
        Session(
            rules=copy.deepcopy(load.rules),
            url=args.url or SAMPLE_URL,
            reserved=_reserved_paths(path),
        )
    )


if __name__ == "__main__":
    sys.exit(main())
