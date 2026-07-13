# nudl

**nudl** (*"naked URL"*, pronounced *noodle*) strips the tracking junk off any link you copy —
in Slack, Discord, a terminal, a doc, your browser — and hands back **the same real link,
trimmed**.

![nudl removing three trackers from a copied Amazon link](docs/demo.gif)

Note what *survives*: `psc=1` is a real parameter the link needs, so nudl leaves it alone. It
strips only what it recognises as tracking, and nothing else.

```
https://l.facebook.com/l.php?u=https%3A%2F%2Fsite.com%2Fx&h=AT1
                        ->  https://site.com/x
```

Redirect wrappers get unwrapped too — the output is the destination they were hiding.

It is **not** a URL shortener. No alias, no server, no redirect — the output *is* the real
destination, just shorter. The link can't rot, because it was never replaced.

**Two ways to use it:** press a hotkey (`Ctrl+Alt+V`), or turn on automatic mode and every link
is cleaned the moment you copy it, in any app.

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

## Install

Windows 11 (Windows 10 best-effort). No Python needed — everything is bundled.

**Download the [latest release](https://github.com/Coreho/nudl/releases/latest)**, unzip it
anywhere, and run `nudl.exe`.

It's an unsigned build, so SmartScreen will show *"Windows protected your PC."* Click
**More info → Run anyway**. See [Is it safe?](#is-it-safe) — I'd rather tell you up front than
have you find out.

Or with [Scoop](https://scoop.sh):

```
scoop install https://raw.githubusercontent.com/Coreho/nudl/master/scoop/nudl.json
```

From source (Python 3.11+):

```
git clone https://github.com/Coreho/nudl.git
cd nudl
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\pythonw -m src.app
```

## Use

On first launch nudl asks how it should work: **Hotkey** (copy a link, press `Ctrl+Alt+V`) or
**Automatic** (links are cleaned as you copy them). Switch any time from the tray menu. The
tray icon is always visible on purpose — a tool that reads your clipboard and hides itself is
exactly what you should be suspicious of.

**Undo:** click *Undo* on the toast, or press the hotkey again within three seconds.

## What it removes

**[The full list is here.](docs/RULES.md)** Every parameter nudl touches, and — just as
importantly — the ones it deliberately won't.

The short version: `utm_*`, `fbclid`, `gclid`, `msclkid` and the other cross-site click IDs
everywhere; site-specific junk from Amazon, LinkedIn, TikTok, Reddit, eBay, Etsy, AliExpress,
Booking, Airbnb, Substack, Twitch, Steam and more; and the redirect wrappers
(`l.facebook.com/l.php`, `google.com/url`, …) get unwrapped to the real destination.

**What it won't remove** is the interesting half. Medium's `?sk=` looks like a tracker but is
a *friend link* — strip it and you paywall the article you were sharing. eBay's `hash=`
identifies the item. Instagram's `img_index=` picks which photo. Amazon's `keywords=` **is**
the search. Each of those has a test guarding it, because breaking a link the user needed
always outranks removing a tracker.

The rule set is one readable file, [`src/rules.json`](src/rules.json). Edit it freely; if you
break it, nudl falls back to the bundled defaults rather than failing.

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
- SmartScreen will warn about the unsigned build. That means Windows doesn't recognise the
  publisher — not that it found anything. Code signing is on the roadmap.

### The VirusTotal score, stated plainly

**[1 of ~70 engines flags the download](https://www.virustotal.com/gui/file/96d640071927403ae992e2647d106238621f23e526b5b554df1006028b3c4261)** — `nudl-0.1.0-win64.zip`, SHA-256 `96d64007…4261`. That is the exact file on the releases page; verify it yourself with `Get-FileHash`.

I'd rather you hear that from me than find it yourself.

That one detection is a machine-learning heuristic, and what it reacts to is **PyInstaller**, not nudl. Bundling a Python interpreter into a self-extracting executable looks structurally like a packer, and packers are what malware uses to hide — so aggressive engines flag *tools built this way* regardless of what they do. It's a known false-positive pattern, not a finding about this code.

- **Microsoft Defender scans it clean**, with current signatures and real-time protection on. Defender is what actually decides whether nudl runs on your machine.
- **Every major engine reads clean.**
- For completeness: the [bootloader `nudl.exe` scanned on its own](https://www.virustotal.com/gui/file/27a5c30a9c6a6b220bcb2f25bfc8f867ce5b108f9f8b8c882f7db83cfecc61ea) draws 3 flags (ArcticWolf, SecureAge). That file is a stub — it contains none of nudl's logic and cannot even run without the `_internal` folder beside it. The zip above is the honest scan, and it's the one you download.

Not satisfied? That's a completely reasonable place to land. Run it from source instead — no packed binary at all, and every line is readable.

## Development

```
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest      # 153 tests
.venv\Scripts\python -m ruff check .
```

The test suite is the specification. `src/tests/test_clean.py` holds 76 hand-vetted before/after
URLs, 23 of them "looks like tracking but isn't" tripwires — the cases where a careless cleaner
breaks a working link. It's the standing gate: if it isn't green, the build doesn't ship.

## License

MIT.
