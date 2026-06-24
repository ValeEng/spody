# The analysis tab

The Analysis tab is where you inspect the binary outputs the engine
produces. Its workspace is organised around two trees on the left
(files and plots) and a **three-tab right pane** &mdash; one tab
for plots, one for the raw record table, one for a per-kind
summary &mdash; that lets you switch between graphical, tabular
and high-level views of the same loaded file without re-reading
from disk. This chapter walks through the layout and the
interaction patterns; the plot catalogue itself lives in chapter 9.

## Layout

The Analysis tab consists of three regions (the working dir lives
in the application-wide top bar above the tabs &mdash; section 4.1
of the main-window chapter &mdash; not in the tab itself):

1. The **left column**, a vertical splitter with two halves:
    - **upper half**: the file tree, with `+ Add external file...`
      + **⟳ Refresh** buttons at the bottom (Refresh re-scans the
      working dir when you've dropped bins in by hand) and
      `→ Overlay selected` below them;
    - **lower half**: the plot tree, with the `▦ Tile selected (N)`
      button at the bottom.
   The splitter bar between the two halves is draggable; pull it
   up to give the plot tree more room when many plots are visible,
   pull it down when you want more file rows.
2. The **right column**, a vertical stack:
    - a **tab bar** at the top with three tabs: **Plot** (the
      canvas), **Table** (a spreadsheet view of the loaded
      file's records), and **Info** (a per-kind key/value
      summary populated from the file + its run snapshot);
    - inside the **Plot** tab: an **animation bar** that appears
      only when the active plot is 3D (chapter 9 covers it),
      followed by the **canvas** (matplotlib for 2D plots, VTK
      for 3D). The matplotlib toolbar carries a **Plot
      options&hellip;** button on its right edge for the 2D plot
      &mdash; today's actions are listed under *Plot options* below;
    - inside the **Table** tab: a `QTableView` over the loaded
      record array, populated whenever you click a file (see
      section *The Table tab* below);
    - inside the **Info** tab: a two-column key/value table
      summarising the loaded binary (see section *The Info tab*
      below);
    - an **info label** at the bottom (shared between the three
      tabs), with the current file or operation summary.
3. The **horizontal splitter** between left and right columns is
   draggable too.

Switching tabs on an already-loaded file is **free**: the loaded
record array is held in memory once, and both tabs render off the
same data. The Plot tab does not re-render on every tab switch
&mdash; it only fires when the active plot is empty (e.g. you
loaded a new file while looking at Table and then switched to
Plot for the first time on that file).

The window-size and splitter positions are not yet persisted
across launches in this release; default sizes apply on every
launch.

## The shared working directory

The Analysis tab does not own its own working dir &mdash; it
consumes the **shared one** from the application top bar (see
chapter 4, section 4.1). The file tree's **In folder** section
auto-populates from a fully recursive scan of that path; build /
VCS / venv folders (`__pycache__`, `.git`, `.venv`, `venv`,
`build`, `dist`, `node_modules`) are pruned, but `output/` is
intentionally included so the per-run snapshots and bins surface.

The working directory updates **automatically** in two situations:

- when you load a TOML (the top bar's auto-adopt logic walks up
  the loaded path's ancestors looking for a scenario root &mdash;
  the closest folder that contains both an `output/` subdir and a
  `.toml` &mdash; and the Analysis tab follows along);
- when a run completes (the file tree rescans the same dir so
  new files appear immediately).

You can also set it manually via the global top bar's
**Browse&hellip;**, or refresh the current scan with the
**⟳ Refresh** button next to **+ Add external file...** &mdash;
useful when you produce files outside of SpOdy.

## The file tree

The file tree has three top-level sections:

1. **In folder (`<working_dir>`)** &mdash; everything the scan
   found, grouped by **per-run folder**. The engine creates one
   `<UTC-ISO8601>/` directory per invocation under `output_dir`
   (see chapter 6), and the file tree mirrors that grouping:
   every run becomes its own collapsible header (`run: 2026-06-
   09T120000Z`), most-recent first. Files that do not sit inside
   any run folder land in a final *loose files* group, collapsed
   by default. File names inside a run folder carry the run's
   timestamp as a prefix (e.g. `2026-06-09T120000Z_lro_state.bin`)
   so they're unambiguous when copied or moved out of context.
2. **External (N)** &mdash; explicit files you added via
   **+ Add external file...**. These remain in the list across
   working-directory changes, so you can keep a reference file
   visible regardless of which run-output folder you are looking
   at.

Each leaf shows the basename of a `.bin` file. Hovering a leaf
shows the absolute path. Adding an *external* file via the button
also loads it immediately (single-click load), which is a small
convenience &mdash; you typically add an external file because you
want to look at it next.

### Selection modes

The file tree supports two interaction modes:

- **Single click** on a leaf &mdash; loads that file. The
  application detects its type from the 8-byte magic in the
  header and rebuilds the plot tree with the catalogue applicable
  to that file's kind:
    - `SPDYOUT_` &mdash; trajectory (state vectors + time)
    - `SPDYACC_` &mdash; per-force accelerations breakdown
    - `SPDYEVT_` &mdash; events log from a single `propagate` run
    - `SPDYEVTB` &mdash; **aggregated batch events** (one file per
      batch invocation, with a `case_idx` column joining each row
      back to a CSV case). Sourced from `cmd_batch`'s
      `events_log` stream. The plot catalogue under this kind has
      a richer set of views (impact lat/lon map, density heatmap,
      3D impact view, &hellip; see chapter 9).
- **Ctrl-click / Shift-click** &mdash; extends the selection (Qt's
  *ExtendedSelection* mode). The currently-loaded file (the one
  whose data feeds the active plot) does not change. Multi-
  selection is the input for the **Overlay** and **Diff** buttons.

## The plot tree

The plot tree below the file splitter is the catalogue of plots
applicable to the currently-loaded file's kind. It is grouped by
topic (for trajectories: *State vectors*, *Orbit shape*, *Orbital
elements*, *Diff (pick 2 files)*) with collapsible folders. Single-
click a leaf to render it into the canvas; re-clicking the same
leaf re-plots.

Three things to know about it:

1. **Click is dispatch** &mdash; there is no "Plot" button to press
   after picking a leaf. The plot fires on the click.
2. **Ctrl-click extends** &mdash; the *Tile selected* button below
   the tree uses the multi-selection (chapter 9, section 9.4).
3. **The animation bar appears only for 3D plots** &mdash; the row
   immediately above the canvas hides itself when the active plot
   is 2D, because there's nothing to animate there. When a 3D
   plot is selected the bar reappears with play / scrub / speed
   controls plus a **Scene&hellip;** button that opens the per-
   scene options dialog (per-body visibility, triads, trail,
   CR3BP primary selector for osculating elements).

The plot tree is rebuilt every time you load a different *kind* of
file (e.g. switching from a trajectory to an accelerations
breakdown). Within the same kind it persists across loads.

## Overlaying multiple files

Selecting multiple files in the file tree (Ctrl/Shift-click) and
pressing **→ Overlay selected** plots them together on a single
axes, with a turbo-coloured palette and a legend listing each file
basename.

The overlay only applies to plots that draw **one line per file**.
A plot that already draws three lines (the per-component
`Position x, y, z`, `Velocity vx, vy, vz`, the per-force
accelerations breakdown) is not overlay-safe; selecting it and
pressing **Overlay** triggers an explanation dialog instead of an
illegible 3N-line plot.

The button works for the **3D orbit + Moon** plot too. In that
case the overlay paints each trajectory as its own polyline in
the same VTK scene, with a viewport legend and **Ctrl+click
picking** enabled on every polyline (more on picking in chapter
9, section 9.7).

A few rules:

- All selected files must be the **same kind** as the currently
  loaded one. Files of a different kind are quietly skipped and
  listed under *Skipped* in the info label.
- The overlay tolerates **different time grids** as long as the
  underlying plot uses each file's own t-axis (e.g. `|r|(t)` plots
  each file's own `t` independently). Time-aligned overlay is not
  available; for sample-aligned comparison use the diff plots.
- The **Overlay** button silently ignores **Diff (pick 2 files)**
  specs &mdash; clicking a diff plot is its own dispatch pathway
  and does not need an overlay button.

## Tiling several plots

The **▦ Tile selected (N)** button below the plot tree is the
dashboard mode. Multi-select N plot leaves with Ctrl/Shift-click
(the counter on the button updates live), then click. The canvas
splits into `ceil(sqrt(N)) × ceil(N/cols)` subplots, one per
selected plot, all rendered against the currently-loaded file.

Two modes are auto-detected from the selection:

- **All single-file plots** &mdash; the subplots draw against the
  loaded file. Useful for at-a-glance overview: select |r|, |v|,
  a, e, i, &Omega; (six leaves) and the result is a 2&times;3
  dashboard of the most relevant orbit quantities.
- **All diff plots** &mdash; the subplots draw against the two
  files selected in the *file tree* (with the same selection rule
  as a single diff click). Read disks once, render four diff
  subplots showing |&Delta;r|, |&Delta;v|, components, and RIC
  decomposition.

Mixed sets (some single, some diff) are refused with a clear
message. **3D plots** are filtered out and the count of skipped
3D plots is reported in the info label. The hard cap is **12
subplots** to keep the result legible; selecting more triggers a
warning and aborts the tile.

## Plot options (Export CSV, &hellip;)

The **Plot options&hellip;** button on the matplotlib toolbar opens
a small non-modal dialog that hosts per-plot actions on the
currently-displayed 2D figure. Today it exposes a single action:

**Export CSV** dumps every `Line2D` on the active figure (single,
overlay and tile modes are all supported) as a `.csv` file. One
section per subplot is written, separated by blank lines and a
comment header carrying the subplot title; lines that share an
x-array collapse to a single `x` column plus one column per series,
others get paired `x_<label>, y_<label>` columns. Scatter / fill /
heatmap layers (impact maps, density plots, the 3D scene) carry
no `Line2D` and are skipped. The dialog shows a *Saving to&hellip;*
status during the write and auto-closes on success; errors stay
visible so the user can adjust the destination.

The button is hidden while the active plot is 3D &mdash; the 3D
canvas uses VTK, not matplotlib, and its own Scene-options dialog
(animation bar) covers the per-scene controls.

## 3D scene options &mdash; starfield background

The Scene options dialog (animation bar on the 3D canvas, see
chapter 9) has a **Show starfield** checkbox that swaps the dark
background for an equirectangular star map. The asset (Solar
System Scope Milky Way 8K, CC BY 4.0) ships through the Setup
wizard alongside the Moon and Earth textures; until it is
downloaded the checkbox stays disabled with an explanatory
tooltip.

On first activation, SpOdy re-projects the wizard image into the
ICRF axes the scene uses (pole = +Z, vernal equinox = +X). The
shipped texture is in galactic coordinates (image centre = Sgr A*),
so the re-projection chains a standard ICRF&rarr;galactic rotation
before the (l, b) lookup &mdash; without it the bulge would land in
the wrong patch of sky and breach the "looks right when overlaid
with the ICRF triad" sanity test. The rotated copy is cached on
disk next to the source as `<stem>_icrf<ext>` so subsequent runs
are instant. Toggle state is persisted in QSettings, so the next
session opens to the same background.

The skybox lives on the FAR (background) renderer with `vtkSkybox.
Sphere` projection. The shader forces depth = 1 (far plane), so
spacecraft trajectories, central body, third-body markers and
triads all render in front of it correctly regardless of zoom
level.

## Picking in the 3D viewer

When a 3D plot is active, **Ctrl+left-click** on any rendered
trajectory polyline picks it: the line is highlighted, the matching
file is selected (highlighted) in the file tree on the left, and
the info label below the canvas reports the picked file's path.
Pointing at the central body (the Moon sphere) does nothing. This
is mostly useful in overlay scenes, where ten lines may cross each
other and you want to know which file produced which.

The pick interaction does not change which file is loaded, only
which one is *highlighted*. Click a file in the tree (or Ctrl-
click anywhere on a polyline) to keep working with it.

## The Table tab

The Table tab is the spreadsheet view of the loaded file's
records. It uses the same underlying numpy structured array the
Plot tab does &mdash; no re-read happens when you switch tabs.

Column layout follows the file's dtype:

- Scalar fields appear as a single column with the field's name
  (`t`, `kind`, `naif_id`, &hellip;).
- **Nested array fields** are flattened into N columns named
  `<field><i>`: e.g. an `EventRecord`'s `y[6]` shows up as `y0`,
  `y1`, &hellip;, `y5`.
- **Padding fields** (names starting with `_`, used to align
  C-side dtypes) are **hidden** so the view stays tidy.
- A handful of fields get **friendlier display names** where the
  on-disk name is misleading: the events files store the
  trigger metric in a `distance_km` slot, but the slot is a
  *jolly* (impact distance for IMPACT, eclipse fraction for
  ECLIPSE, &hellip;), so the table header surfaces it as
  `trigger_value`. The TOML schema and the engine's on-disk
  format are unchanged &mdash; only the column label differs.

Floating-point cells are formatted with **12 significant digits**,
enough to round-trip a typical km-scale state vector through the
clipboard without surprise. Integer cells stay raw. The events
`kind` column gets the symbolic label (`IMPACT`, `ECLIPSE`)
instead of the enum integer.

### Spreadsheet-style selection

The table accepts the conventional spreadsheet click patterns:

- **Click a cell** to select just that cell.
- **Click a column header** to select the whole column.
- **Click a row index** (left edge) to select the whole row.
- **Shift-click** extends the rectangular selection; **Ctrl-click**
  toggles individual cells in or out (non-rectangular sets are
  supported).

**Ctrl+C** copies the current selection to the clipboard as
**TSV** (Tab-Separated Values, one row per line, one column per
tab). Cells outside the selection in a non-rectangular set are
emitted as empty fields so the row alignment is preserved. Paste
straight into Excel / LibreOffice / a Jupyter cell and the
columns line up; pasting into a chat or a code comment also
works because the format is plain UTF-8 text.

This is the path to take whenever you want the *numbers* &mdash;
the Plot tab is for the *picture*.

## The Info tab

The Info tab is a **per-kind summary** of the loaded binary. It
trades off the Plot tab's visual answer and the Table tab's
exhaustive record list for a compact one-screen overview: which
file you're on, what the run setup was, and a handful of key
statistics computed from the data.

The tab refreshes on three triggers:

- when you click a new file in the file tree;
- when you click a plot in the plot tree (so the diff-aware
  rows described at the end of this section appear / disappear
  with the plot selection);
- when you switch into the tab on an already-loaded file.

All blocks are cheap to compute (one numpy pass over the loaded
array), so the refresh is unnoticeable.

### Run summary (always shown)

Every kind starts with a *Run summary* section. The first four
rows always come from the file itself:

- **File** &mdash; the binary's filename.
- **Folder** &mdash; the absolute parent directory.
- **Type** &mdash; one of `trajectory (SPDYOUT_)`, `accelerations
  (SPDYACC_)`, `events log (SPDYEVT_)`, `events log (SPDYEVTB,
  batch-aggregated)`.
- **Records** &mdash; the record count.

The remaining rows come from the **per-run snapshot TOML** the
engine drops next to the bin (`<ts>_input.toml`); they are skipped
when no snapshot sits alongside the loaded file:

- **Central body** &mdash; `Moon` / `Earth` / &hellip;
- **Dynamics model** &mdash; `high_fidelity` or `cr3bp`.
- **CR3BP primaries** + **CR3BP mass ratio &micro;** &mdash;
  only shown in CR3BP runs.
- **ET start [s]** &mdash; the simulation epoch.
- **Planned duration** &mdash; the scheduled `duration_s`,
  rendered in seconds plus a friendly unit (min / h / d) once
  it crosses the obvious thresholds.
- **Ephemeris** &mdash; the resolved DE-series file's basename.
- **Cases file** &mdash; the resolved batch CSV's basename
  (batch runs only).

### Trajectory files (`SPDYOUT_`)

Two sections on top of *Run summary*:

- *Trajectory* &mdash; t range and span (s + d), &Delta;t
  min/avg/max, |r| min/max [km], |v| min/max [km/s];
- *Initial state* and *Final state* &mdash; the (x, y, z) and
  (vx, vy, vz) triplets at t0 and tf.

For high-fidelity runs whose central body is registered (Moon,
Earth) a final *Kepler elements (HF, central body)* section
adds the six classical osculating elements (a, e, i, &Omega;,
&omega;, &nu;) at t0 and tf, computed via `spopy.cartesian_to_
keplerian` with the body's GM. The section is silently omitted
in CR3BP runs (the relevant frame is primary-relative; switch
to the Plot tab's *Orbital elements* group with the Scene-
options dialog's primary selector for a per-primary breakdown).

### Acceleration files (`SPDYACC_`)

- *Accelerations* &mdash; t range, span, &Delta;t stats, plus
  |a_total| min/max/mean/RMS [km/s&sup2;];
- *Per-force RMS [km/s&sup2;]* &mdash; one row each for the
  2-body, harmonics, 3rd-body, SRP, and drag components;
- *Eclipse* &mdash; the minimum `eclipse_fraction` reached over
  the run plus the **time in shadow** integrated trapezoidally
  over the (1 &minus; fraction) signal.

### Events files (`SPDYEVT_` / `SPDYEVTB`)

The top *Events* section is shared between per-run and batch
events: **Total records**, **IMPACT count**, **ECLIPSE count**.
Batch logs add three extra rows up there (**Cases with events**,
**Cases with impact**, and &mdash; when the cases CSV is
resolved next to the snapshot &mdash; **Cases total (CSV)**,
**Survivors (no impact)**, **Impact rate %**).

Two derived sections follow when the underlying counts are
non-zero:

- *Impact timing* (any IMPACT row present) &mdash; first / last
  / median / mean impact time, each formatted as raw seconds
  with the friendly (min / h / d) equivalent in parentheses.
- *Eclipses* (any ECLIPSE row present) &mdash; **Trigger
  records** is the raw count of crossings (entry + exit). The
  engine emits one record per threshold crossing of the signed
  (fraction &minus; threshold) predicate, so successive triggers
  for the same `{case_idx, naif_id}` group alternate
  entry &harr; exit; consecutive pairs are one **complete
  eclipse** with duration `t_exit - t_entry`. The summary
  reports the pair count and the min / avg / max duration.
  Odd tails (the run started or ended inside shadow) are
  silently dropped from the pairing &mdash; expect
  `2 &times; Complete eclipses + odd-tails = Trigger records`
  per group.

### Diff overlay (plot-aware)

When the active plot in the plot tree is one of the *Diff (pick
2 files)* specs &mdash; and a successful diff dispatch has
already cached the aligned pair &mdash; the Info tab appends a
section named after the plot label with:

- the A and B filenames;
- the alignment note ("B interpolated onto A's grid") when the
  two grids didn't match;
- |&Delta;r| **max / mean / RMS / final** [km];
- |&Delta;v| **max / mean / RMS / final** [km/s];
- |&Delta;r| **linear growth** as the least-squares slope in
  km/day;
- **RIC frame (A)** &mdash; |&Delta;| max and RMS broken down
  into radial / in-track / cross-track in A's local frame.

The overlay disappears as soon as you click a non-diff plot or
load a different file.

