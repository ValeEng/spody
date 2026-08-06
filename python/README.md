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

The window has a top-level **Run / Analysis / Re-run** tab switch,
and a global working-directory bar shared across them.

**Run mode** -- shell around the `spody` binary:

- Edit TOML inputs via a structured form (one widget per field, no
  hand-written TOML). Per-section groups, XOR object switch
  (`[spacecraft]` vs `[debris]`), conditional sub-sections
  (`[spacecraft.srp]`, `[spacecraft.drag]`, `[events]`, `[batch]`),
  per-field tooltips + range validation with red-border on
  out-of-range values. The `dynamics_model` combo reflows the whole
  form between the `high_fidelity` and `cr3bp` section sets.
- Live TOML preview pane below the form -- canonical output that
  reflects every keystroke without writing to disk.
- **Load / Save / Save As** plus a per-tab TOML combo populated by a
  recursive scan of the working dir. Saving on top of a run snapshot
  (or any TOML sitting next to `.bin` output) diverts to a
  `<stem>.wip.toml` sidecar, so the on-disk record of a past run is
  never silently rewritten; launching a run from a WIP unlinks it
  and reloads the starting file.
- CSV-aware `[batch.columns]` mapping table: reads the cases CSV
  header, lets you assign each column to a spody target with a
  per-row dropdown (filtered by the current object schema), and
  shows the first 10 data rows verbatim under the mapping so you
  can sanity-check the assignment. With `cases_frame` set to `ric`
  or `lvlh` a *Rotated preview* appears alongside, showing the rows
  as they will reach the engine.
- **From CR3BP...** popup next to the `[initial_state]` frame combo:
  maps a CR3BP catalog state (dimensional or nondimensional,
  barycentric or primary-centered) into the central-body ICRF state
  at `et_start_s`, for seeding high-fidelity runs from periodic-orbit
  catalog points.
- **Calibrate...** button next to `density_scale_file`: runs
  `spody calibrate`, streams the fit report into the Run-tab console
  and auto-fills the emitted node-file path.
- Launch `spody validate`, `spody propagate`, or `spody batch` as a
  subprocess; stream stdout/stderr live into a read-only terminal
  pane. **Stop** button / `Ctrl+.` with graceful terminate-then-kill.
- File menu with **Open Recent** (last 8 files), unsaved-changes
  prompt on close/new/open, status bar with elapsed time + exit code.
- **First-launch setup wizard** (see [Setup wizard](#setup-wizard) below)
  with a hard run-guard: spody never launches when the required
  data files are missing.

**Analysis mode** -- inspect the spody output binaries. Split into
**Plot / Table / Info** sub-tabs over a file tree grouped by run
folder (recursive scan of the working dir).

- Pick any `.bin` file produced by spody; the magic header is
  auto-detected (`SPDYOUT_` trajectory / `SPDYACC_` accelerations /
  `SPDYEVT_` per-run events / `SPDYEVTB` batch-aggregated events)
  and a kind-specific set of views becomes available. Adding a view
  is one function plus one `PlotSpec` entry in the right
  `analysis/plots_*.py`; adding a file kind is one `registry.py`
  entry.
- **Plot** renders into an embedded **matplotlib** canvas with the
  standard zoom / pan / save toolbar:
  * trajectory: `|r|(t)`, `|v|(t)`, position / velocity components,
    XY / XZ / YZ projections, osculating Keplerian elements,
    eccentricity vs argument-of-periapsis phase plot;
  * accelerations: total magnitude, per-force breakdown on a log
    y-axis (which force dominates when), eclipse fraction;
  * events: timelines, time-to-impact histogram, survival timeline,
    impact lat/lon in equirectangular + Mollweide, density heatmap;
  * altitude bands: time per band, occupancy Gantt, batch population
    over time, per-case heatmap -- vectorised and cached, with
    display budgets that keep ten-million-record logs interactive
    (stated in the plot title when they engage);
  * CR3BP: Jacobi-constant conservation, per-primary osculating
    elements;
  * diff: two files overlaid, with RIC decomposition.
- A **Plot-frame selector** (ICRF / body-fixed) re-projects state
  and Keplerian-angle plots into the central body's body-fixed basis
  on the fly.
- **Table** shows the same records as a grid (selection is
  copy-to-clipboard friendly); **Info** a
  per-kind key/value summary (t-range, |r|/|v| ranges, initial and
  final state, osculating Kepler at t0/tf, per-force RMS, time in
  shadow, event counts, impact rate and survivors, and diff-aware
  |&Delta;r| / |&Delta;v| / RIC rows when a Diff plot is active).
- A **CSV export box** (radio list of export types + one Export
  button, each greying out by data availability) covers figure
  lines, altitude-band per-element `time / entries`, and impact
  `lat/lon + time of flight`.

**3D viewer** -- a [`spoviz`](spoviz/) scene embedded via
`spoviz.qt.SceneWidget`, switched in automatically when a 3D view is
selected. Built-in mouse controls (left-drag rotate, scroll zoom,
middle pan, `r` reset), plus:

- textured central body with its real attitude over time (lunar
  libration / Earth rotation), body-fixed + ICRF frame triads, and
  ephemeris-driven third-body markers that rotate with their own
  body-fixed attitude;
- a playback timeline with a UTC readout tracking the epoch;
- `→ Overlay selected (3D)` on the file list -- pick N trajectories
  with Ctrl/Shift, get a coloured overlay with a legend;
- **Ctrl+left-click** picks an overlaid trajectory: the polyline is
  highlighted, the matching file selected in the tree;
- an optional equirectangular **star-map background**, re-projected
  from galactic to ICRF on the fly (Scene options dialog, persisted);
- camera pan / zoom preserved across re-renders of the same file.

**Body textures (3D view).** Central bodies render grey until a
texture is configured; the Setup wizard has rows for the Moon and
Earth maps, and **Settings > Paths** holds the resolved locations.
Equirectangular JPEG / PNG are uv-mapped automatically on the next
plot.

## Setup wizard

Spody needs external assets: the JPL DE440 planetary ephemeris, a
harmonic-gravity model per central body (GRGM1200B for the Moon,
EIGEN-6C4 for the Earth, the latter auto-converted from ICGEM
`.gfc` to the engine's `.tab`), the IERS Earth-orientation series
plus the IAU 2006/2000A_R06 tables for the Earth's inertial-to-ITRS
rotation, CelesTrak space weather for drag, and the body textures.
The wizard downloads them into the **data dir** (portable: defaults
to a `data/` folder next to the executable, so the whole bundle is
move-as-one) and converts the DE440 ASCII chunks into `de440.spody`
automatically by shelling out to `spody convert ephemeris`.

The EOP series goes stale by design, so at startup the app issues a
HEAD request against the source and re-downloads when the remote
copy is newer.

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
ev   = read_events("output/run_evt.bin")     # SPDYEVT_ or SPDYEVTB

# columns by name -- t, x, y, z, vx, vy, vz on trajectories
r = np.sqrt(traj["x"]**2 + traj["y"]**2 + traj["z"]**2)

# `read_events` handles both event formats and returns the matching
# dtype; the batch-aggregated one (SPDYEVTB, written by `spody batch`)
# carries an extra 0-based `case_idx` per record.
if "case_idx" in ev.dtype.names:
    first_case = ev[ev["case_idx"] == 0]
```

Header on every file is fixed at 24 bytes (8-byte ASCII magic + four
little-endian uint32). The reader validates the magic and the record
size encoded in the header, so an ABI change in spody-core
(`ForceBreakdown` / `EventRecord` size drift) is detected loudly
instead of silently misread.

## Pure-Python engine mirrors (`spopy`)

Sibling package with no Qt and no C: NumPy re-implementations of
spody-core's read-side functions, used by the GUI for plotting and by
scripts that need the same numbers without spawning the binary.
Ephemeris (DE440 position / velocity / full state via the analytic
Chebyshev derivative), ICRF&lt;-&gt;Moon Principal Axes and
ICRF&lt;-&gt;ITRS rotations, Kepler solve and
Keplerian&harr;Cartesian, CR3BP synodic&harr;primary-inertial, and
`spopy.time` -- a zero-ULP twin of `spody_time.c` (leap seconds,
`deltet`, ET&harr;UTC) that is the single Python-side owner of the
time-scale chain.

```python
from spopy import Ephemeris, et_to_utc, icrf_to_moon_pa

eph = Ephemeris("data/DE440/de440.spody")
r_moon = eph.position(399, 301, et_s)     # Earth -> Moon, km
```

> The leap-second table lives in exactly two files in the whole
> project: `spody_time.c` and `spopy/time.py`. They must stay
> bit-for-bit equivalent -- change one, change the other.

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
  events.py              # read_events(path), SPDYEVT_ + SPDYEVTB, EVENT_KIND_*

spopy/                   # pure-Python mirrors of the engine's read side
  ephemeris.py           # DE440 reader: position / velocity / state
  rotations.py           # ICRF <-> Moon Principal Axes
  eop.py                 # IERS EOP reader
  earth_orientation.py   # ICRF <-> ITRS (IAU 2006/2000A_R06)
  kepler.py              # Kepler solve + Keplerian <-> Cartesian
  cr3bp.py               # synodic <-> primary-inertial
  time.py                # zero-ULP twin of spody_time.c (leap seconds, deltet)

spoviz/                  # 3D astrodynamics visualization library (VTK + numpy)
  README.md              # full API reference + usage examples
  scene.py               # Scene3D: Qt-free scene engine (layered renderers,
                         #  bodies, trajectories, animation, sun light, skybox)
  decoration.py          # ephemeris-driven third bodies / sunlight / BF triads
  bodies.py              # NAIF ids, display colours, marker sizing knobs
  textures.py            # equirectangular texture fixups (cached on disk)
  widgets.py             # opt-in in-scene UI: PlaybackBar + OptionsPanel (no Qt)
  qt.py                  # SceneWidget: the ONLY Qt module (QVTK host widget)

spody_gui/               # PySide6 desktop app (depends on spody_io + spopy + spoviz)
  __main__.py            # `python -m spody_gui`
  main.py                # QApplication entry
  main_window.py         # MainWindow: tabs, menus, status, wiring
  toml_form.py           # TomlForm: composes the form/ mixins over a QWidget
  form/                  # the Run-tab form, split by concern
    catalog.py           #   declarative field tables (the schema, one place)
    sections.py          #   section builders + widget construction
    widgets.py           #   the custom field widgets
    visibility.py        #   XOR groups, conditional sections, dynamic batch table
    handlers.py          #   bottom-bar actions (RUN / Validate / Save As)
    roundtrip.py         #   TOML <-> form, incl. rotating-frame CSV rotation
    cr3bp_convert.py     #   "From CR3BP..." catalog-state converter popup
  toml_io.py             # tomli reader + canonical TOML emitter
  frames.py              # RIC / LVLH bases + cases-CSV rotation
  central_bodies.py      # GUI-side central-body specs (radii, textures)
  constants.py           # the single reader of spody_const.h
  paths.py               # data dir resolution (portable, frozen-aware)
  assets.py              # required-files registry + coverage profile
  setup_wizard.py        # download + auto-convert wizard + run-guard
  settings.py            # SettingsStore (QSettings) + SettingsDialog
  runner.py              # SpodyRunner (QProcess wrapper)
  terminal.py            # TerminalView (read-only output pane)
  rerun_panel.py         # Re-run tab
  analysis_panel.py      # Analysis tab: file tree + Plot/Table/Info dispatch
  analysis/              # the Analysis tab's engine
    registry.py          #   file kind -> reader + PlotSpec list
    spec.py              #   PlotSpec
    context.py           #   what a plot function receives
    derived.py           #   per-file derivations, cached and shared
    plots_traj.py        #   trajectory views
    plots_accel.py       #   acceleration-breakdown views
    plots_events.py      #   event + batch-event views
    plots_cr3bp.py       #   Jacobi + per-primary elements
    plots_diff.py        #   two-file diff views
    altitude_bands.py    #   occupancy analysis behind the band views
    info.py              #   Info-tab summaries
    table_model.py       #   Table-tab model
    scene3d.py           #   3D view assembly on top of spoviz
    overlays.py          #   lifts single-file plots into N-file overlays
  plot_options.py        # per-plot option dialogs
  scene_options.py       # 3D scene options (star map, primary selector, ...)
  animation_bar.py       # playback transport for the 3D scene
  vtk_canvas.py          # compat shim: VtkCanvas = spoviz.qt.SceneWidget
  about_dialog.py        # Help > About
  astronomy.py           # low-precision Sun direction (analytic)
```
