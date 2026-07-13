# nudl

**nudl** (*"naked URL"*, pronounced *noodle*) strips the tracking junk off any link you copy —
in Slack, Discord, a terminal, a doc, your browser — and hands back **the same real link,
trimmed**.

```
https://www.amazon.com/dp/B08X?tag=aff-20&ref_=nb&psc=1
                        ->  https://www.amazon.com/dp/B08X?psc=1

https://l.facebook.com/l.php?u=https%3A%2F%2Fsite.com%2Fx&h=AT1
                        ->  https://site.com/x
```

It is **not** a URL shortener. No alias, no server, no redirect — the output *is* the real
destination, just shorter. The link can't rot, because it was never replaced.

## nudl never sends your URLs anywhere

The cleaning engine makes **zero network calls**. No account, no telemetry, nothing leaves your
machine. That's not a policy, it's a property of the code: the test suite replays every URL in
the corpus with the socket layer patched to explode on any connection attempt.

There's a local audit log at `%AppData%\nudl\clean.log` recording every change nudl has made
(`timestamp · original · what was removed · result`), so you can check its work rather than
trust it.

Deliberately absent: no network un-shortening of `bit.ly` / `t.co` (it would leak the link and
can burn one-time URLs), and no "check for rule updates" (it would mean phoning home).

## The one promise

**nudl never silently breaks a link you needed.** Everything below serves that.

- **When in doubt, do nothing.** A malformed URL, a signed URL, a domain *you've* excluded, a
  result identical to the input, or any internal error — nudl leaves your clipboard exactly as
  it found it.
- **Signed URLs are never touched.** Anything carrying `sig`, `signature`, `hmac`, `token`,
  `expires`, `policy`, or an `X-Amz-*` key is left alone. Signed URLs compute their signature
  over the whole query, so removing *any* parameter turns the link into a 403.
- **Only known trackers are stripped.** nudl works from a small allowlist and never guesses,
  which is why YouTube's `?v=` and `?t=`, Spotify's `?si=`, `?page=`, `?q=` and everything
  nobody has thought of yet survive by default.
- **Values are never rewritten.** Only the *key* of each parameter is inspected, so a parameter
  whose value merely looks like a tracker (`?redirect=utm_source_page`) can't be mangled, and
  `%20` never quietly becomes `+`.
- **Every real change is loud and reversible.** A toast says what was removed; one click — or
  the hotkey again within three seconds — restores the exact original bytes. A no-op is silent:
  if nudl changed nothing, you'll never know it ran.

## Install and use

Windows 11 (Windows 10 best-effort), Python 3.11+. Packaged builds (pip / winget / Scoop) are
on the roadmap.

```
git clone https://github.com/Coreho/nudl.git
cd nudl
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\pythonw -m src.app
```

On first launch nudl asks how it should work: **Hotkey** (copy a link, press `Ctrl+Alt+V`) or
**Automatic** (links are cleaned as you copy them). Switch any time from the tray menu. The
tray icon is always visible on purpose — a tool that reads your clipboard and hides itself is
exactly what you should be suspicious of.

**Undo:** click *Undo* on the toast, or press the hotkey again within three seconds.

## What it removes

The whole rule set is one readable file, [`src/rules.json`](src/rules.json) — about 25 rules.
You're meant to read it. Edit it freely; if you break it, nudl falls back to the bundled
defaults rather than failing.

- **Tracking params:** `utm_*`, `fbclid`, `gclid`, `dclid`, `gbraid`, `wbraid`, `msclkid`,
  `mc_cid`, `mc_eid`, `igshid`, `_hsenc`, `mkt_tok`, and friends.
- **Amazon:** `tag`, `ref_`, `ref`, `pd_rd_*`, `pf_rd_*`, `qid`, `sr`.
- **Redirect wrappers:** `l.facebook.com/l.php`, `google.com/url`, `out.reddit.com`,
  `steamcommunity.com/linkfilter`, `t.umblr.com/redirect`.

Domains you never want touched go in `exceptions` in `%AppData%\nudl\config.json`. That's a
list *you* write; nudl just compares hostnames against it locally.

nudl does **not** bundle ClearURLs' rules data — it ships under a separate, unverified license,
and its 600-provider long tail is irrelevant for "clean the link I just copied." A rule set you
can actually read is the point.

## Is it safe?

It's a small unsigned tool that reads your clipboard, so it's fair to be suspicious.

- It uses `RegisterHotKey`, which asks Windows to deliver **one specific chord** and nothing
  else. It does **not** install a low-level keyboard hook — that's the keylogger technique, and
  nudl never sees any keystroke but its own shortcut.
- It's open source, and the entire cleaning engine is one file: `src/clean.py`.
- SmartScreen may still warn about an unsigned build. That means it doesn't recognise the
  publisher, not that it found anything.

<!-- TODO(CP0.5): add the VirusTotal permalink for the released build here. -->

## Development

```
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest      # 98 tests
.venv\Scripts\python -m ruff check .
```

The test suite is the specification. `src/tests/test_clean.py` holds 50 hand-vetted before/after
URLs, a quarter of them "looks like tracking but isn't" tripwires — the cases where a careless
cleaner breaks a working link. It's the standing gate: if it isn't green, the build doesn't ship.

## License

MIT.
