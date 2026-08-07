# Build the unsigned --onedir distribution.
#
# --onedir, NOT --onefile: a onefile build unpacks itself into %TEMP% and runs from
# there, which is textbook malware behaviour and draws far more AV heuristics than it
# is worth for an unsigned clipboard tool.
#
# Usage:  .\build.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

# PyInstaller resolves several things against the working directory — the .spec it
# writes, and module lookup via --paths. Run from $root so that `.\build.ps1` invoked
# from anywhere builds the same thing rather than something subtly different.
Push-Location $root
try {

# Anything holding a DLL inside dist\ makes PyInstaller fail with a bare "Access is
# denied", which is a miserable thing to debug. The usual culprit is not nudl itself:
# VS Code's Python extension ships pet.exe ("Python Environment Tools"), which crawls
# the disk looking for Python environments, finds dist\nudl\_internal (it has a
# python*.dll in it, so it looks exactly like one), loads VCRUNTIME140.dll out of it,
# and locks the file. Name the offender rather than let it look like a mystery.
$dist = Join-Path $root "dist"
if (Test-Path $dist) {
    $lockers = @()
    Get-Process | ForEach-Object {
        $proc = $_
        try {
            if ($proc.Modules | Where-Object { $_.FileName -like "$dist*" }) {
                $lockers += "$($proc.ProcessName) (pid $($proc.Id))"
            }
        } catch { }
    }
    if ($lockers) {
        throw "dist\ is locked by: $($lockers -join ', ').  Stop them and re-run. (pet.exe is VS Code's Python extension; killing it is harmless — it restarts.)"
    }
}

# One source of truth for the version. Hardcoding it here as well as in pyproject.toml
# and the Scoop manifest is how you ship a v0.1.1 zip named v0.1.0.
$versionMatch = Select-String -Path (Join-Path $root "pyproject.toml") -Pattern '^version = "(.+)"'
if (-not $versionMatch) { throw "no 'version = ""...""' line in pyproject.toml — cannot name the release" }
$version = $versionMatch.Matches[0].Groups[1].Value
Write-Host "Building nudl $version" -ForegroundColor Cyan

Write-Host "Rendering the icon..." -ForegroundColor Cyan
$buildDir = Join-Path $root "build"
New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
# ONE icon file, at src\nudl.ico. PyInstaller embeds it as the exe resource AND ships it
# as data, because tray.py now LOADS it at runtime rather than drawing with Pillow —
# which is what keeps Pillow's 12.8 MB of codecs out of the download. Re-rendering here
# instead of trusting the committed copy is what stops it drifting from make_icon.py.
$icoPath = Join-Path $root "src\nudl.ico"
& $python (Join-Path $root "tools\make_icon.py") $icoPath
# An exit-code check, because PyInstaller is handed $icoPath regardless: a render that
# failed would leave it picking up a STALE icon from a previous build, silently.
if ($LASTEXITCODE -ne 0) { throw "icon render failed (exit $LASTEXITCODE)" }
if (-not (Test-Path $icoPath)) { throw "icon render wrote no file at $icoPath" }

Write-Host "Freezing with PyInstaller (--onedir)..." -ForegroundColor Cyan
# Paths must be absolute: PyInstaller resolves --add-data relative to --specpath,
# not to the working directory.
#
# The --exclude-module list is load-bearing, not tidiness. PyInstaller resolves imports
# statically, so `urllib.parse` (all clean.py wants) dragged in urllib.request ->
# http.client -> ssl, and `random` dragged in _hashlib: between them, 7.4 MB of OpenSSL
# in a tool whose headline promise is that it makes no network calls. Nothing loads any
# of it at runtime — verified against the live process — and scanners fingerprint the
# bundled OpenSSL by version, so it was reported as vulnerable while never executing.
# Dropping _hashlib leaves hashlib on its builtin sha2/md5 backends, which is all that
# random.seed(str) and tempfile ever needed.
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name nudl `
    --icon $icoPath `
    --add-data "$(Join-Path $root 'src\rules.json');src" `
    --add-data "$icoPath;src" `
    --exclude-module ssl `
    --exclude-module _ssl `
    --exclude-module urllib.request `
    --exclude-module http `
    --exclude-module _hashlib `
    --paths $root `
    --distpath (Join-Path $root "dist") `
    --workpath (Join-Path $root "build\pyinstaller") `
    --specpath (Join-Path $root "build") `
    (Join-Path $root "run_nudl.py")

# PyInstaller is a native command, so a failure does NOT trip $ErrorActionPreference.
# Without this check the script sails on and zips up whatever half-built wreckage is
# lying in dist/ — which is exactly how you ship a 3.9 MB archive that cannot start.
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE). Is nudl.exe still running?" }

$exe = Join-Path $root "dist\nudl\nudl.exe"
if (-not (Test-Path $exe)) { throw "build failed: no exe at $exe" }

# nudl.exe on its own is only a bootloader stub — it exists even when the build is
# broken. The real payload is _internal\, so validate THAT.
$internal = Join-Path $root "dist\nudl\_internal"
# NOT $python — that is the venv interpreter, and clobbering it here would leave a
# FileInfo object sitting in the variable every later `& $python` call depends on.
$pythonDll = Get-ChildItem $internal -Filter "python*.dll" -ErrorAction SilentlyContinue
if (-not $pythonDll) { throw "build is incomplete: no python*.dll in _internal\" }

$distMb = (Get-ChildItem (Join-Path $root "dist\nudl") -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
# The floor exists to catch a half-built wreckage (the original bug shipped a 3.9 MB
# archive that could not start), NOT to police the size. It moved from 20 to 12 when
# dropping Pillow and OpenSSL took a healthy build from 44 MB to roughly 24.
if ($distMb -lt 12) { throw ("build is incomplete: dist is only {0:N1} MB (expected 12+)" -f $distMb) }
Write-Host ("  payload: {0:N1} MB, python DLL present" -f $distMb) -ForegroundColor Green

$zip = Join-Path $root "dist\nudl-$version-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $root "dist\nudl\*") -DestinationPath $zip

# The hashes people verify the download against.
$zipHash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
$exeHash = (Get-FileHash $exe -Algorithm SHA256).Hash.ToLower()
$zipHash | Out-File -Encoding ascii -NoNewline "$zip.sha256"

$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host ""
Write-Host "Built:  $exe" -ForegroundColor Green
Write-Host "Ship:   $zip  ($size MB)" -ForegroundColor Green
Write-Host ""
Write-Host "zip SHA-256: $zipHash" -ForegroundColor Yellow
Write-Host "exe SHA-256: $exeHash" -ForegroundColor Yellow
Write-Host ""
Write-Host "The zip hash must be pinned in scoop/nudl.json, and the zip re-scanned on"
Write-Host "VirusTotal -- a scan of the previous build describes a file nobody can download."
}
finally {
    Pop-Location
}
