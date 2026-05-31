# SpOdy GUI

Desktop frontend for the [spody](../) propagator, written in
[PySide6](https://doc.qt.io/qtforpython-6/). Patran/Nastran-style: the GUI
**never links C code directly** — it edits TOML inputs on disk and
invokes the `spody` executable as a subprocess, streaming its terminal
output into an embedded pane.

```
┌──────────────────────────┬──────────────────────────────┐
│ TOML editor (highlight)  │ Terminal output (live)       │
│ [Validate] [Run] [Stop]  │ status: idle / running 12s   │
└──────────────────────────┴──────────────────────────────┘
```

## What it does today (Phase 0)

- Open / edit / save TOML input files with syntax highlighting.
- Launch `spody validate`, `spody propagate`, or `spody batch` as a
  subprocess against the current file.
- Stream the subprocess's stdout/stderr live into a read-only terminal
  pane (no ANSI escapes — `spody` emits plain text).
- Stop the run from the UI (`Ctrl+.` or **Run > Stop**), with a
  graceful terminate-then-kill timeout.
- Remember the path to `spody.exe`, the harmonics file, the ephemeris
  file, and a default output directory across sessions
  (**Settings > Paths**).
- File menu with **Open Recent** (last 8 files), unsaved-changes
  prompt on close/new/open, status bar with elapsed time and exit
  code.

Visualisation (3D Moon + trajectory, batch overlay, event markers) is
**not in this phase**. The Cesium-or-VTK frontend lands separately once
the shell is solid.

## Dev setup

Requirements: Python ≥ 3.9. PySide6 brings its own Qt — no system Qt
install needed.

```powershell
cd python
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .[dev]
python -m spody_gui
```

The first run will show "spody binary not set" on **Run > Validate** —
open **Settings > Paths** and point at `..\build\Release\spody.exe`
(or wherever your build lives).

## Distribution (planned)

The end-user workflow is download-and-run: a single archive containing
`spody-gui.exe` + `spody.exe` + data files, with **no Python install
required**. The bundling is done with
[PyInstaller](https://pyinstaller.org/) — see [`build_exe.ps1`](build_exe.ps1).

```powershell
cd python
.venv\Scripts\Activate.ps1
pip install -e .[dev]   # brings in pyinstaller
.\build_exe.ps1         # writes dist\spody-gui.exe
```

The output `dist/spody-gui.exe` is self-contained (Python interpreter +
PySide6 + Qt all embedded). Ship it alongside the `spody.exe` binary
and the user is set.

## Output binary readers (`spody_io`)

Sibling package to `spody_gui`, no Qt dependency -- pure NumPy. Use
from scripts, notebooks, or the GUI:

```python
from spody_io import read_trajectory, read_accelerations, read_events
import numpy as np

traj = read_trajectory("output/run.bin")     # SPDYOUT_  -> ndarray (N, 7 fields)
acc  = read_accelerations("output/run_acc.bin")  # SPDYACC_  -> per-force breakdown
ev   = read_events("output/run_evt.bin")     # SPDYEVT_  -> impact / eclipse triggers

# columns by name -- t, x, y, z, vx, vy, vz on trajectories
r = np.sqrt(traj["x"]**2 + traj["y"]**2 + traj["z"]**2)
```

Header on every file is fixed at 24 bytes (8-byte ASCII magic + four
little-endian uint32). The reader validates the magic and the record
size encoded in the header, so an ABI change in spody-core
(`ForceBreakdown` / `EventRecord` size drift) is detected loudly
instead of silently misread.

## Layout

```
spody_io/                # binary readers (NumPy only)
  __init__.py            # re-exports of read_*
  headers.py             # magic constants + 24-byte preamble parser
  traj.py                # read_trajectory(path) -> structured ndarray
  accel.py               # read_accelerations(path)
  events.py              # read_events(path) + EVENT_KIND_* constants

spody_gui/               # PySide6 desktop app (depends on spody_io)
  __main__.py            # `python -m spody_gui`
  main.py                # QApplication entry
  main_window.py         # MainWindow: layout, menus, status bar, wiring
  editor.py              # TomlEditor + TomlHighlighter
  completer.py           # context-aware TOML autocomplete
  schema.py              # sections / keys / enum values / snippet templates
  terminal.py            # TerminalView (read-only output pane)
  runner.py              # SpodyRunner (QProcess wrapper)
  settings.py            # SettingsStore (QSettings) + SettingsDialog
```
