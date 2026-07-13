# nudl

**nudl** (*"naked URL"*, pronounced *noodle*) strips the tracking junk off any link you copy.

You copy a link anywhere — Slack, Discord, a terminal, a doc, your browser — and nudl hands
back **the same real link, trimmed**:

```
https://www.amazon.com/dp/B08X?tag=aff-20&ref_=nb&psc=1
                        ->  https://www.amazon.com/dp/B08X?psc=1

https://l.facebook.com/l.php?u=https%3A%2F%2Fsite.com%2Fx&h=AT1
                        ->  https://site.com/x
```

It is **not** a URL shortener. There is no alias, no server, and no redirect — the output *is*
the real destination, just shorter. The link can never rot, because it was never replaced.

## nudl never sends your URLs anywhere

The cleaning engine makes **zero network calls**. No account, no telemetry, no cloud, nothing
leaves your machine. This isn't a policy, it's a property of the code — and the test suite
asserts it by replaying the entire URL corpus with the socket layer monkeypatched to explode
on any connection attempt.

There is a local, human-readable audit log at `%AppData%\nudl\clean.log` recording every
change nudl has ever made (`timestamp · original · what was removed · result`), so you can
check its work rather than take our word for it.

Deliberately **not** included: no network un-shortening of `bit.ly` / `t.co` (it would leak
the link, add latency, and can burn one-time URLs), and no "check for rule updates" (it would
mean phoning home).

## The one promise

**nudl never silently breaks a link you needed.** Every design decision below serves that, and
nothing overrides it.

- **When in doubt, do nothing.** A malformed URL, a signed URL, an excepted domain, a result
  identical to the input, or any internal error at all — nudl returns your clipboard exactly
  as it found it.
- **Signed URLs are never touched.** If a link carries `sig`, `signature`, `hmac`, `token`,
  `expires`, `policy`, or any `X-Amz-*` key, nudl leaves it completely alone. Signed URLs
  compute their signature over the whole query, so removing *any* parameter turns the link
  into a 403. The safest thing nudl can do with one is nothing.
- **Only known trackers are stripped — everything else survives.** nudl works from a small,
  readable allowlist of tracker keys. It does not guess. This is why YouTube's `?v=` and `?t=`,
  Spotify's `?si=`, `?page=`, `?q=` and every parameter nobody has thought of yet are safe
  by default.
- **Values are never rewritten.** Only the *key* of each parameter is inspected. A parameter
  whose value merely looks like a tracker (`?redirect=utm_source_page`) is impossible to
  mangle, and `%20` never quietly becomes `+`.
- **Every real change is loud and reversible.** A toast tells you what was removed; one click
  (or the hotkey again within three seconds) restores the exact original bytes. A no-op is
  completely silent — if nudl changed nothing, you'll never know it ran.

## Install

Requires Windows 11 (Windows 10 best-effort) and Python 3.11+.

```
git clone https://github.com/Coreho/nudl.git
cd nudl
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\pythonw -m src.app
```

Packaged builds (pip / winget / Scoop) are on the roadmap.

## Use

On first launch nudl asks how you want it to work:

- **Hotkey** (default) — copy a link, press `Ctrl+Alt+V`, and your clipboard now holds the
  clean version.
- **Automatic** — links are cleaned as you copy them, no keypress needed. This also gives you
  "right-click, Copy link address, it's already clean" in every app, for free.

You can switch any time from the tray menu. The tray icon is always visible on purpose: a tool
that reads your clipboard and hides itself is exactly what you should be suspicious of.

**Undo:** click *Undo* on the toast, or press the hotkey again within three seconds.

## What it removes

The full rule set is a single readable file — [`src/rules.json`](src/rules.json) — about 25
rules. You are meant to read it. You can edit it, and if you break it, nudl falls back to the
bundled defaults rather than failing.

- **Tracking parameters:** `utm_*`, `fbclid`, `gclid`, `dclid`, `gbraid`, `wbraid`, `msclkid`,
  `mc_cid`, `mc_eid`, `igshid`, `_hsenc`, `_hsmi`, `mkt_tok`, and friends.
- **Amazon:** `tag`, `ref_`, `ref`, `pd_rd_*`, `pf_rd_*`, `qid`, `sr`.
- **Redirect wrappers:** `l.facebook.com/l.php`, `google.com/url`, `out.reddit.com`,
  `steamcommunity.com/linkfilter`, `t.umblr.com/redirect`.

Domains you never want touched go in the exception list in `%AppData%\nudl\config.json`.

The rules are hand-written. nudl does **not** bundle ClearURLs' rules data — that data ships
under a separate, unverified license, and its 600-provider long tail is irrelevant for "clean
the link I just copied." A small rule set you can actually read is the point.

## Is it safe?

nudl is a small, unsigned tool that reads your clipboard, so it's fair to be suspicious.

- It uses `RegisterHotKey`, which asks Windows to deliver **one specific chord** and nothing
  else. It does **not** install a low-level keyboard hook — that's the technique keyloggers
  use, and nudl never sees any keystroke other than its own shortcut.
- It's open source. The entire cleaning engine is one file (`src/clean.py`), and the rules are
  one JSON file.
- Windows SmartScreen may still warn about an unsigned build. That's SmartScreen telling you
  it doesn't recognise the publisher, not that it found anything.

<!-- TODO(CP0.5): add the VirusTotal permalink for the released build here. -->

## Development

```
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest      # 98 tests
.venv\Scripts\python -m ruff check .
```

The test suite is the specification. `src/tests/test_clean.py` is a corpus of 50 hand-vetted
before/after URLs, roughly a quarter of which are "looks like tracking but isn't" tripwires —
the cases where a careless cleaner would break a working link. It is the standing gate: if it
is not green, the build does not ship.

## License

MIT.
