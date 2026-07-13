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

# One source of truth for the version. Hardcoding it here as well as in pyproject.toml
# and the Scoop manifest is how you ship a v0.1.1 zip named v0.1.0.
$version = (Select-String -Path (Join-Path $root "pyproject.toml") -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
Write-Host "Building nudl $version" -ForegroundColor Cyan

Write-Host "Rendering the icon..." -ForegroundColor Cyan
& $python -c @"
from pathlib import Path
from src.tray import _icon_image
sizes = [16, 24, 32, 48, 64, 128, 256]
_icon_image(256).save(Path('build') / 'nudl.ico', sizes=[(s, s) for s in sizes])
print('  build/nudl.ico')
"@

Write-Host "Freezing with PyInstaller (--onedir)..." -ForegroundColor Cyan
# Paths must be absolute: PyInstaller resolves --add-data relative to --specpath,
# not to the working directory.
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onedir `
    --windowed `
    --name nudl `
    --icon (Join-Path $root "build\nudl.ico") `
    --add-data "$(Join-Path $root 'src\rules.json');src" `
    --paths $root `
    --distpath (Join-Path $root "dist") `
    --workpath (Join-Path $root "build\pyinstaller") `
    --specpath (Join-Path $root "build") `
    (Join-Path $root "run_nudl.py")

$exe = Join-Path $root "dist\nudl\nudl.exe"
if (-not (Test-Path $exe)) { throw "build failed: no exe at $exe" }

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
