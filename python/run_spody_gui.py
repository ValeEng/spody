"""PyInstaller entry script.

Kept as a standalone module (rather than pointing PyInstaller at the
`__main__.py` inside the package) so the analysis step has a single
top-level script and the bundle name `spody-gui.exe` follows naturally
from this filename.

The actual application logic stays in `spody_gui.main` so `python -m
spody_gui` keeps working from a regular checkout.
"""
from spody_gui.main import main

if __name__ == "__main__":
    raise SystemExit(main())
