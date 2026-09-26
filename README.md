# nudl

_Last updated: 2026-09-26_

**nudl** (*"naked URL"*, pronounced *noodle*) strips the tracking junk off any link you copy —
in Slack, Discord, a terminal, a doc, your browser — and hands back **the same real link,
trimmed**.

```
winget install Coreho.nudl
```

![nudl removing three trackers from a copied Amazon link](https://raw.githubusercontent.com/Coreho/nudl/master/docs/demo.gif)

Note what *survives*: `psc=1` is a real parameter the link needs, so nudl leaves it alone. It
strips only what it recognises as tracking, and nothing else.

```
https://youtu.be/dQw4w9WgXcQ?si=Ab12Cd34Ef56&t=42
                        ->  https://youtu.be/dQw4w9WgXcQ?t=42

https://l.facebook.com/l.php?u=https%3A%2F%2Fsite.com%2Fx&h=AT1
                        ->  https://site.com/x
```

Redirect wrappers get unwrapped too — the output is the destination they were hiding.

It is **not** a URL shortener. No alias, no server, no redirect — the output *is* the real
destination, just shorter. The link can't rot, because it was never replaced.

Browser extensions and Firefox's *Copy Link Without Site Tracking* only work inside the
browser. nudl works on the clipboard, so it doesn't matter where the link came from.

## Two ways to use it

**On Windows, the tray app (`nudlw`).** Copy a link and press `Ctrl+Alt+V` — or switch on
automatic mode and every link is cleaned the moment you copy it, in any app. It can start
with Windows, so it's simply there.

**Anywhere, the command line (`nudl`).** Windows, macOS and Linux. Let your OS supply the
clipboard and the hotkey:

```bash
Get-Clipboard | nudl | Set-Clipboard      # PowerShell
pbpaste | nudl | pbcopy                   # macOS
xclip -o | nudl | xclip -i                # Linux, X11
wl-paste | nudl | wl-copy                 # Linux, Wayland

nudl "https://example.com/p?fbclid=IwAR123"
```

Bind one of those lines to a key (Raycast, Hammerspoon, your desktop's keyboard settings)
and you have the hotkey on any OS. `nudl --help` has the options: custom rules, excluded
domains, `--verbose` to see what was removed.

## nudl never sends your URLs anywhere

The cleaning engine makes **zero network calls**. No account, no telemetry, nothing leaves your
machine. That's not a policy, it's a property of the code: the test suite replays every URL in
the corpus with the socket layer patched to explode on any connection attempt.

There's a local audit log at `%AppData%\nudl\clean.log` recording every change the tray app
has made (`timestamp · original · what was removed · result`), so you can check its work
rather than trust it. **Tray → View log** opens it.

Deliberately absent: no network un-shortening of `bit.ly` / `t.co` (it would leak the link and
can burn one-time URLs), and no "check for updates" (it would mean phoning home — winget and
Scoop already do that job, when *you* ask them to).

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

Windows 11 (Windows 10 best-effort) for the tray app; the command line runs anywhere Python
3.11+ does.

**[winget](https://learn.microsoft.com/windows/package-manager/)** — the easiest:

```
winget install Coreho.nudl
```

Then run `nudlw` (or press Win+R and type it) to start the tray app; `nudl` is the command
line. `winget upgrade Coreho.nudl` gets you new rules and fixes — nudl never updates itself.

**[Scoop](https://scoop.sh):**

```
scoop install https://raw.githubusercontent.com/Coreho/nudl/master/scoop/nudl.json
```

**pip / pipx** — the command line on any OS (on Windows it brings the tray app too):

```
pipx install nudl
```

**Or download the [latest release](https://github.com/Coreho/nudl/releases/latest)**, unzip it
anywhere, and run `nudlw.exe`. (Double-clicking `nudl.exe` works too — it hands over to the
tray app.)

It's an unsigned build, so SmartScreen will show *"Windows protected your PC."* Click
**More info → Run anyway**. See [Is it safe?](#is-it-safe) — I'd rather tell you up front than
have you find out.

From source (Python 3.11+):

```
git clone https://github.com/Coreho/nudl.git
cd nudl
python -m venv .venv
.venv\Scripts\pip install -e .
.venv\Scripts\nudlw          # the tray app
.venv\Scripts\nudl --help    # the command line
```

## Use

On first launch nudl asks how it should work: **Hotkey** (copy a link, press `Ctrl+Alt+V`) or
**Automatic** (links are cleaned as you copy them) — and whether to **start with Windows**.
Change either any time from the tray menu. Start with Windows is an ordinary entry in Task
Manager's *Startup apps*, so you can see it and switch it off there too. The tray icon is
always visible on purpose — a tool that reads your clipboard and hides itself is exactly what
you should be suspicious of.

**Undo:** click *Undo* on the toast, press the hotkey again within three seconds, or use
**Tray → Undo last clean**.

## What it removes

**[The full list is here.](https://github.com/Coreho/nudl/blob/master/docs/RULES.md)** Every
parameter nudl touches, and — just as importantly — the ones it deliberately won't.

The short version: `utm_*`, `fbclid`, `gclid`, `msclkid` and the other cross-site click IDs
everywhere; the share-button trackers people paste most — YouTube's `si`, Instagram's `igsh`,
Facebook's `mibextid`, LinkedIn's `rcm`, TikTok's `_t`/`_r`; site-specific junk from Amazon,
Reddit, eBay, Etsy, AliExpress, Booking, Airbnb, Substack, Twitch, Steam and more; and the
redirect wrappers (`l.facebook.com/l.php`, `google.com/url`, …) get unwrapped to the real
destination.

**What it won't remove** is the interesting half. Medium's `?sk=` looks like a tracker but is
a *friend link* — strip it and you paywall the article you were sharing. Spotify's `si=` has
broken shared playlists (YouTube's `si` is a pure tracker, so that one goes). eBay's `hash=`
identifies the item. Instagram's `img_index=` picks which photo. Amazon's `keywords=` **is**
the search. Each of those has a test guarding it, because breaking a link the user needed
always outranks removing a tracker.

### Your own rules

The rule set is one readable file, [`src/rules.json`](https://github.com/Coreho/nudl/blob/master/src/rules.json)
— and you can replace it without rebuilding anything. **Tray → Rules…** opens your own copy at
`%AppData%\nudl\rules.json`, seeded from the bundled file so you start with every comment and
every deliberate omission intact. Edit it, then **Tray → Validate rules** re-reads it and says
what loaded: *"rules reloaded: 27 global keys, 17 providers, 9 wrappers"*.

Once that copy exists, **it is what nudl runs on** — so rules added in later releases don't
reach it. Delete it (or rename it) to go back to the bundled set, which is always current.

Break it and nudl tells you, instead of quietly reverting. The last version that loaded cleanly
is kept beside it as `rules.json.bak` and nudl runs on *that* for the session, so your
customisations survive your typo. **nudl never writes over your rules file** — the broken one
stays exactly as you left it, waiting to be fixed. (A file declaring a `schema_version` newer
than this engine understands is refused outright rather than half-read.)

Domains you never want touched go in `exceptions` in `%AppData%\nudl\config.json`. That's a
list *you* write; nudl just compares hostnames against it locally.

### Checking a rule before it goes live

`nudl-validate` (or `python -m src.validate`) shows you exactly what a rule would do — before it
goes anywhere near your links. Local and offline, like everything else here. It comes with the
pip install; the packaged download ships **Tray → Validate rules**, which is the same check
on the whole file.

```
> nudl-validate --test "^utm_.*$" --url "https://www.amazon.com/dp/B08X?utm_source=google&tag=aff-20&v=123"
Pattern:  ^utm_.*$  (anchored regex)
Before:   https://www.amazon.com/dp/B08X?utm_source=google&tag=aff-20&v=123
After:    https://www.amazon.com/dp/B08X?tag=aff-20&v=123
Removed:  utm_source
Kept:     tag, v
```

It knows which parameters are load-bearing, so it argues back:

```
> nudl-validate --test "si" --url "https://open.spotify.com/playlist/abc?si=share123"
Removed:  si
warning 'si' matches 'si', which is deliberately absent: Spotify share id — removing it has
        broken shared-playlist and invite flows.
```

`--check` audits the whole file and exits non-zero if anything is broken. With no arguments you
get an interactive prompt to `test`, `add`, `remove`, `save` and try wrappers and rawRules
against sample URLs. It drives the **real** engine rather than a reimplementation, so what it
shows is what nudl will do — and it never edits your live rules file; `save` refuses that path.

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

**[1 of ~70 engines flags the v0.1.0 download](https://www.virustotal.com/gui/file/96d640071927403ae992e2647d106238621f23e526b5b554df1006028b3c4261)** — `nudl-0.1.0-win64.zip`, SHA-256 `96d64007…4261`. Verify any download yourself with `Get-FileHash`; each release lists its hash.

I'd rather you hear that from me than find it yourself.

That one detection is a machine-learning heuristic, and what it reacts to is **PyInstaller**, not nudl. Bundling a Python interpreter into a self-extracting executable looks structurally like a packer, and packers are what malware uses to hide — so aggressive engines flag *tools built this way* regardless of what they do. It's a known false-positive pattern, not a finding about this code.

- **Microsoft Defender scans it clean**, with current signatures and real-time protection on — the v0.2.0 zip included. Defender is what actually decides whether nudl runs on your machine.
- **Every major engine reads clean.**
- For completeness: the [bootloader `nudl.exe` scanned on its own](https://www.virustotal.com/gui/file/27a5c30a9c6a6b220bcb2f25bfc8f867ce5b108f9f8b8c882f7db83cfecc61ea) draws 3 flags (ArcticWolf, SecureAge). That file is a stub — it contains none of nudl's logic and cannot even run without the `_internal` folder beside it. The zip above is the honest scan, and it's the one you download.

Not satisfied? That's a completely reasonable place to land. Run it from source instead — no packed binary at all, and every line is readable.

## What's new in 0.2.0

- **The command line**, `nudl` — on Windows, macOS and Linux, and on PyPI. The tray app is now
  `nudlw` (double-clicking `nudl.exe` still opens it).
- **Start with Windows**, asked once on first launch and switchable from the tray.
- **The trackers people paste most**: YouTube `si`, Instagram `igsh`, Facebook `mibextid`,
  LinkedIn `rcm`, TikTok `_t`/`_r`.
- **Your own rules**: Tray → Rules… and Validate rules, plus `nudl-validate`.
- **Hardening**: bounded regexes and URL sizes, stricter redirect unwrapping, far more
  secrets masked in the audit log.

## Development

```
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest      # 347 tests
.venv\Scripts\python -m ruff check .
```

The test suite is the specification. `src/tests/test_clean.py` holds 82 hand-vetted before/after
URLs, 24 of them "looks like tracking but isn't" tripwires — the cases where a careless cleaner
breaks a working link. It's the standing gate: if it isn't green, the build doesn't ship.

`src/tests/integration/` drives the real Windows clipboard — a real message pump, a real listener,
no test doubles anywhere. Those tests close nothing and fake nothing, which is why they found two
bugs the mocked tests had been cheerfully passing over. They will skip themselves if nudl is
already running, and they give you your clipboard back when they're done. Don't copy things
while they run: they share the clipboard with you.

`.\build.ps1` builds the Windows download — `nudl.exe` and `nudlw.exe` over one shared runtime
([`nudl.spec`](nudl.spec)) — and smoke-tests the command line before zipping it.
[docs/RELEASING.md](docs/RELEASING.md) is the release checklist: GitHub, winget, Scoop, PyPI.

## License

MIT.
