# PyInstaller spec — two programs, one shared _internal\. Run it through build.ps1.
#
#   nudl.exe   the command line. Console subsystem, because a windowed exe has no stdin
#              or stdout to pipe through: `Get-Clipboard | nudl` would print nothing.
#   nudlw.exe  the tray app. Windowed, or a console window would flash up at every logon.
#
# The same split `pip install nudl` makes with its `nudl` and `nudlw` scripts, so one
# command means one thing however nudl was installed. Until 0.2.0, nudl.exe WAS the tray
# app; cli.py hands a double-click on it over to nudlw.exe so old shortcuts keep working.
#
# A spec file rather than build.ps1's old command-line flags because PyInstaller can only
# build two executables into one folder from a spec. Everything the flags used to say is
# said here instead, and the reasons stay with them in build.ps1.

from pathlib import Path

ROOT = Path(SPECPATH)  # noqa: F821 — injected by PyInstaller
ICON = str(ROOT / "src" / "nudl.ico")

# Load-bearing, not tidiness: see the long comment in build.ps1. Without these, a tool
# whose headline promise is "no network calls" ships 7.4 MB of OpenSSL.
EXCLUDES = ["ssl", "_ssl", "urllib.request", "http", "_hashlib"]
DATAS = [(str(ROOT / "src" / "rules.json"), "src"), (ICON, "src")]


def analyse(script: str):
    return Analysis(  # noqa: F821
        [str(ROOT / script)],
        pathex=[str(ROOT)],
        datas=DATAS,
        excludes=EXCLUDES,
    )


def executable(analysis, name: str, console: bool):
    return EXE(  # noqa: F821
        PYZ(analysis.pure),  # noqa: F821
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=name,
        console=console,
        icon=[ICON],
        # Never UPX. A UPX-packed exe is exactly what the AV heuristics that already
        # dislike PyInstaller are looking for, and nudl ships unsigned.
        upx=False,
    )


tray = analyse("run_nudlw.py")
cli = analyse("run_nudl.py")

COLLECT(  # noqa: F821
    executable(tray, "nudlw", console=False),
    tray.binaries,
    tray.datas,
    executable(cli, "nudl", console=True),
    cli.binaries,
    cli.datas,
    upx=False,
    name="nudl",
)
