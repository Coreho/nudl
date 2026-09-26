"""Frozen-build entry point for nudl.exe, the command line.

The counterpart of run_nudlw.py, and for the same reason: PyInstaller freezes a script,
not a package. Running from source, prefer `python -m src.cli`.
"""

import sys

from src.cli import main

if __name__ == "__main__":
    sys.exit(main())
