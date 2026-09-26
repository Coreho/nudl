# Releasing nudl

Every step below publishes something, and each points at the one before it: the Scoop
manifest points at the GitHub release, winget points at the same zip, the README tells
people to `pipx install`. Do them in order.

## 0. Before you build

- `src/__init__.py`: bump `__version__`. It is the only place the version lives —
  `pyproject.toml` and `build.ps1` both read it.
- `src/rules.json`: bump `last_updated` if the rules changed, then
  `python docs/make_rules_doc.py`.
- README: the date line and *What's new*.
- `python -m pytest` green (don't copy anything while it runs — the integration tests share
  your clipboard), `python -m ruff check .` clean.

## 1. Build

```
.\build.ps1
```

This writes `dist\nudl-X.Y.Z-win64.zip` and its `.sha256`, and refuses to zip a build whose
`nudl.exe` can't clean a link. **That zip is the release.** Rebuilding changes the hash, and
every manifest below pins it.

## 2. Get the zip known-clean *before* anyone else scans it

v0.1.1 reached winget and was pulled back out 36 minutes later (winget-pkgs #402572, reverted
by #413395): Microsoft's manual validation hit `Trojan:Win32/SuspExecRep.A!cl`, then
`Program:Win32/Vigram.A`, on the PyInstaller zip. Four `@wingetbot run` retries didn't clear
it. The identical zip was resubmitted as #424671 once Defender's verdict had aged out, and
merged on 2026-09-25. The `!cl` suffix is a cloud verdict, which a local scan does not see.

So, for every new zip:

1. Scan it locally (this catches the easy cases):
   ```
   & "$env:ProgramData\Microsoft\Windows Defender\Platform\*\MpCmdRun.exe" -Scan -ScanType 3 -File dist\nudl-X.Y.Z-win64.zip
   ```
2. Submit it to Microsoft as a developer, **before** the winget PR:
   <https://www.microsoft.com/wdsi/filesubmission> → *Software developer* → *Incorrectly
   detected as malware/malicious*. Give the GitHub release URL. Wait for the verdict.
3. Optionally upload it to VirusTotal and update the README's score if it changed.

Signing the exes (SignPath Foundation, or Azure's signing service) is what fixes this for
good; until then, step 2 is the workaround.

## 3. Manifests

- `scoop/nudl.json`: `version`, `url`, `hash` (lowercase).
- `winget/*.yaml`: `PackageVersion` in all three, `InstallerUrl`, `InstallerSha256`
  (uppercase), `ReleaseNotesUrl`. Then `winget validate --manifest winget`.

Commit.

## 4. GitHub release

Push the branch, then release **from that commit** so the assets exist before master's
Scoop manifest points at them:

```
git push origin <branch>
gh release create vX.Y.Z dist\nudl-X.Y.Z-win64.zip dist\nudl-X.Y.Z-win64.zip.sha256 `
    --target <branch> --title "nudl X.Y.Z" --notes-file <notes.md>
```

Scoop's `autoupdate` reads the `.sha256` asset, so upload it every time.

## 5. PyPI

```
python -m build --outdir dist\pypi
python -m twine check dist\pypi\*
python -m twine upload dist\pypi\*
```

Needs a PyPI API token. The first upload creates the `nudl` project — after that, consider
a project-scoped token and GitHub trusted publishing.

## 6. master

Merge the branch into master and push. The README and Scoop manifest are now live, and both
point at things that exist.

## 7. winget

Copy `winget/*.yaml` into a fork of microsoft/winget-pkgs at
`manifests/c/Coreho/nudl/X.Y.Z/`, and open a PR titled
`New version: Coreho.nudl version X.Y.Z`. From then on, when a release changes only the
version, URL and hash, `wingetcreate update Coreho.nudl --version X.Y.Z --urls <zip url>
--submit` does the same thing in one command.

Existing users get it with `winget upgrade`; nothing pushes it to them, and nudl never
checks for updates itself.

## Once

- GitHub → Settings → General → Social preview: upload `docs/social-preview.png`
  (`python docs/make_social_preview.py` regenerates it). GitHub has no API for this.
