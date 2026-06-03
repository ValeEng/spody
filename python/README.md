# SpOdy GUI

Desktop frontend for the [spody](../) propagator, written in
[PySide6](https://doc.qt.io/qtforpython-6/). Patran/Nastran-style: the GUI
**never links C code directly** — it edits TOML inputs on disk and
invokes the `spody` executable as a subprocess, streaming its terminal
output into an embedded pane.

```
┌──────────────────────────┬──────────────────────────────┐
│ TOML form  (one widget   │ Terminal output (live)       │
│ per field, live preview, │ status: idle / running 12s   │
│ Generate / Validate /RUN)│                              │
└──────────────────────────┴──────────────────────────────┘
```

## What it does today

The window has a top-level **Run / Analysis** tab switch.

**Run mode** -- shell around the `spody` binary:

- Edit TOML inputs via a structured form (one widget per field, no
  hand-written TOML). Per-section groups, XOR object switch
  (`[spacecraft]` vs `[debris]`), conditional sub-sections
  (`[spacecraft.srp]`, `[events]`, `[batch]`), per-field tooltips +
  range validation with red-border on out-of-range values.
- Live TOML preview pane below the form -- canonical output that
  reflects every keystroke without writing to disk.
- CSV-aware `[batch.columns]` mapping table: reads the cases CSV
  header, lets you assign each column to a spody target with a
  per-row dropdown (filtered by the current object schema), and
  shows the first 10 data rows verbatim under the mapping so you
  can sanity-check the assignment.
- Top-row actions: **Load... / Generate / Validate / RUN** (green).
  Generate writes canonical TOML to disk; Validate runs `spody
  validate` synchronously against a temp file next to the current
  TOML and shows the verdict as a colored badge.
- Launch `spody validate`, `spody propagate`, or `spody batch` as a
  subprocess; stream stdout/stderr live into a read-only terminal
  pane. Stop from the UI (`Ctrl+.`) with graceful terminate-then-kill.
- File menu with **Open Recent** (last 8 files), unsaved-changes
  prompt on close/new/open, status bar with elapsed time + exit code.
- **First-launch setup wizard** (see [Setup wizard](#setup-wizard) below)
  with a hard run-guard: spody never launches when the required
  data files are missing.

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
  * events: timeline scatter (IMPACT / ECLIPSE markers).

**3D viewer** -- VTK widget embedded in the Analysis tab, switched in
automatically when a 3D plot is selected. Built-in mouse controls
(left-drag rotate, scroll zoom, middle pan, `r` reset). Today:

- `3D orbit + Moon` per trajectory binary
- `→ Overlay selected (3D)` button on the file list -- pick N
  trajectories with Ctrl/Shift, get a coloured overlay with a legend
- `+ Sun arrow` -- direction to the Sun at the typed epoch (auto-
  filled from the loaded TOML; low-precision analytic ephemeris)
- **Ctrl+left-click** on an overlaid trajectory picks it: the
  polyline is highlighted, the matching file is selected in the tree
  and shown in the info label below the canvas.

**Moon texture (3D view).** The central body sphere is grey by
default; configure **Settings > Paths > Moon texture (3D view)** with
an equirectangular Moon image (JPEG or PNG) and the sphere is
uv-mapped automatically on the next plot.

## Setup wizard

Spody needs an external planetary ephemeris (JPL DE440) and a lunar
harmonic-gravity model (GRGM1200B). The wizard downloads the raw
files into the **data dir** (portable: defaults to a `data/` folder
next to the executable, so the whole bundle is move-as-one) and
converts the DE440 ASCII chunks into `de440.spody` automatically by
shelling out to `spody convert ephemeris`.

The wizard pops automatically on first launch if any required file
is missing, and from **Settings > Setup wizard...** at any time.

Per-row layout: status icon (✓/⚠/✗) | name (size) | editable URL |
progress bar | Download. Hit **Download all missing** in the footer
to start every pending download in one click. The conversion to
`de440.spody` runs automatically when the raw DE440 inputs are
complete and the derived binary is missing or older than them.

Two **coverage profiles**:

- **Modern era** (default) -- one DE440 ASCII chunk
  (`ascp01950.440`, ~30 MB), covering 1950..2050. Right pick for
  anyone running near-present epochs.
- **Full pack** -- all 11 DE440 ASCII chunks (1550..2650, ~340 MB).
  Needed only for historical / far-future scenarios.

URL fields are editable: if a download fails, paste the corrected URL
in the row and re-try. Working URLs land in
[`spody_gui/assets.py`](spody_gui/assets.py) for the next release.

The same hard run-guard wraps every entry point that launches the
spody binary (Run menu, Validate button); missing data → dialog
explaining what's gone with a one-click jump to the wizard.

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

The first run will pop the setup wizard (data files missing) and the
**Settings > Paths > spody binary** field is empty -- point at
`..\build\Release\spody.exe` (or wherever your build lives) before
hitting Run.

## Distribution

The end-user workflow is download-and-run: a single folder containing
`spody-gui.exe` + `spody.exe` + an empty `data/`, **no Python install
required**. The bundle is produced with
[PyInstaller](https://pyinstaller.org/) via
[`build_bundle.py`](build_bundle.py):

```powershell
cd python
.venv\Scripts\Activate.ps1
pip install -e .[dev]            # brings in pyinstaller
python build_bundle.py
```

Output layout (~500 MB on first build, mostly Qt + VTK):

```
python/dist/spody-gui/
  spody-gui.exe        <- entry point, double-click to launch
  spody.exe            <- C runner (copied from ../build/Release/)
  data/                <- empty; the wizard fills it on first launch
  _internal/           <- bundled Python + PySide6 + VTK + matplotlib
```

Zip `python/dist/spody-gui/` and ship. The end user extracts anywhere
on their disk and double-clicks `spody-gui.exe`. The wizard pops, they
pick a coverage profile and click **Download all missing**, the
conversion fires automatically, and they're ready to run.

Notes:

- **One-folder, not one-file.** The wizard's data dir is resolved as
  `<sys.executable>/data`; `--onefile` would point at PyInstaller's
  per-launch temp extraction dir, wiped on next launch.
- Pass `--spody-exe PATH` to `build_bundle.py` to point at a
  non-default spody.exe location.
- Pass `--clean-only` to wipe `build/` and `dist/` without rebuilding.
- Bundle layout is controlled by [`spody_gui.spec`](spody_gui.spec);
  edit there to ship an icon, a different name, or extra data files.

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
run_spody_gui.py         # PyInstaller entry script (= `python -m spody_gui`)
spody_gui.spec           # PyInstaller spec (one-folder, VTK hooks)
build_bundle.py          # wraps PyInstaller + copies spody.exe + data/

spody_io/                # binary readers (NumPy only)
  __init__.py            # re-exports of read_*
  headers.py             # magic constants + 24-byte preamble parser
  traj.py                # read_trajectory(path) -> structured ndarray
  accel.py               # read_accelerations(path)
  events.py              # read_events(path) + EVENT_KIND_* constants

spody_gui/               # PySide6 desktop app (depends on spody_io)
  __main__.py            # `python -m spody_gui`
  main.py                # QApplication entry
  main_window.py         # MainWindow: tabs, menus, status, wiring
  toml_form.py           # structured form (replaces the old text editor)
  toml_io.py             # tomli reader + canonical TOML emitter
  paths.py               # data dir resolution (portable, frozen-aware)
  assets.py              # required-files registry + coverage profile
  setup_wizard.py        # download + auto-convert wizard + run-guard
  settings.py            # SettingsStore (QSettings) + SettingsDialog
  runner.py              # SpodyRunner (QProcess wrapper)
  terminal.py            # TerminalView (read-only output pane)
  analysis_panel.py      # Analysis tab: file picker + plot dispatch
  vtk_canvas.py          # VtkCanvas: QVTK + Moon + trajectory + Sun arrow
  astronomy.py           # low-precision Sun direction (analytic)
```
