# Build the unsigned --onedir distribution for the CP0.5 install smoke test.
#
# --onedir, NOT --onefile: a onefile build unpacks itself into %TEMP% and runs from
# there, which is textbook malware behaviour and draws far more AV heuristics than it
# is worth for an unsigned clipboard tool.
#
# Usage:  .\build.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

Write-Host "Building the nudl icon..." -ForegroundColor Cyan
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

$zip = Join-Path $root "dist\nudl-0.1.0-win64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path (Join-Path $root "dist\nudl\*") -DestinationPath $zip

$size = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host ""
Write-Host "Built:  $exe" -ForegroundColor Green
Write-Host "Ship:   $zip  ($size MB)" -ForegroundColor Green
Write-Host ""
Write-Host "CP0.5: hand the zip to someone else. Have them unzip it anywhere and run"
Write-Host "nudl.exe on their own Windows 11 box. The question is not whether it cleans"
Write-Host "links -- it is whether SmartScreen/Defender lets it start at all."
