"""Allow `python -m spody_gui` to launch the app."""
from .main import main

if __name__ == "__main__":
    raise SystemExit(main())
