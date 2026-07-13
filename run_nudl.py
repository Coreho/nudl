"""Frozen-build entry point.

PyInstaller freezes a *script*, not a package, so it needs a top-level module that can
import `src` normally. Running from source, prefer `pythonw -m src.app`.
"""

from src.app import main

if __name__ == "__main__":
    main()
