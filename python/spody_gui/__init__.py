"""SpOdy desktop GUI.

A PySide6 frontend that edits TOML input files, launches the spody
executable as a subprocess, and streams its terminal output into an
embedded read-only pane. No C bindings -- the GUI talks to the
propagator entirely through the file-based interface (TOML in, binary
or CSV out).
"""

__version__ = "0.1.0"
