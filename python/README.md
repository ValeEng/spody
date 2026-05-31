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

## What it does today

The window has a top-level **Run / Analysis** tab switch.

**Run mode** -- shell around the `spody` binary:
- Open / edit / save TOML input files with syntax highlighting and
  context-aware autocompletion (sections, keys, enum values, target
  paths for `[batch.columns]`); snippet templates via Tab on a known
  section keyword or via the **Insert** menu.
- Launch `spody validate`, `spody propagate`, or `spody batch` as a
  subprocess against the current file.
- Stream the subprocess's stdout/stderr live into a read-only terminal
  pane (no ANSI escapes -- `spody` emits plain text).
- Stop the run from the UI (`Ctrl+.` or **Run > Stop**), with a
  graceful terminate-then-kill timeout.
- Remember the path to `spody.exe`, the harmonics file, the ephemeris
  file, and a default output directory across sessions
  (**Settings > Paths**).
- File menu with **Open Recent** (last 8 files), unsaved-changes
  prompt on close/new/open, status bar with elapsed time and exit
  code.

**Analysis mode** -- inspect the spody output binaries:
- Pick any `.bin` file produced by spody; the magic header is
  auto-detected (`SPDYOUT_` trajectory / `SPDYACC_` accelerations /
  `SPDYEVT_` events) and a kind-specific set of 2D plots becomes
  available.
- Plots are rendered into an embedded **matplotlib** canvas with the
  standard zoom / pan / save toolbar:
  * trajectory: `|r|(t)`, `|v|(t)`, position / velocity components,
    XY / XZ / YZ orbit projections;
  * accelerations: total magnitude, per-force breakdown on a log
    y-axis (shows which force dominates when), eclipse fraction
    over time;
  * events: timeline scatter (IMPACT / ECLIPSE markers along the
    time axis).

**3D viewer** -- VTK widget embedded in the Analysis tab, switched in
automatically when a 3D plot is selected. Built-in mouse controls
(left-drag rotate, scroll zoom, middle pan, `r` reset). Today:
- `3D orbit + Moon` per trajectory binary
- `→ Overlay selected (3D)` button on the file list -- pick N
  trajectories with Ctrl/Shift, get a coloured overlay with a legend
- `+ Sun arrow` -- direction to the Sun at the typed epoch (auto-
  filled from the loaded TOML; low-precision analytic ephemeris,
  arcminute-class)
- **Ctrl+left-click** on an overlaid trajectory picks it: the
  polyline is highlighted, the matching file is selected in the tree
  and shown in the info label below the canvas.

**Moon texture (3D view).** The central body sphere is grey by
default; configure **Settings > Paths > Moon texture (3D view)** with
an equirectangular Moon image (JPEG or PNG) and the sphere is uv-
mapped automatically on the next plot. Suggested sources, all public
domain / CC, equirectangular projection:
- NASA SVS CGI Moon Kit -- <https://svs.gsfc.nasa.gov/4720> (2k / 8k
  / 24k variants; 2k JPEG is ~600 KB)
- USGS Astrogeology LRO WAC Mosaic, equirectangular tile
- Solar System Scope -- <https://www.solarsystemscope.com/textures/>
  (CC BY 4.0, 2k / 4k / 8k)

The texture is loaded from disk every time a 3D plot is dispatched
(VTK caches internally), so changing the Settings path takes effect
on the next **Plot** click without restarting the GUI.

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
  analysis_panel.py      # Analysis tab: file picker + plot dispatch + 2D/3D canvases
  vtk_canvas.py          # VtkCanvas: QVTKRenderWindowInteractor + Moon + traj + sun
  astronomy.py           # low-precision Sun direction (analytic, ~arcmin)
```
