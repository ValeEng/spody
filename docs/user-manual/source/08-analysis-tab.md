# The analysis tab

The Analysis tab is where you inspect the binary outputs the engine
produces. Its workspace is organised around two trees on the left
(files and plots) and a canvas on the right that switches between
a 2D matplotlib pane and a 3D VTK pane depending on what you ask
to render. This chapter walks through the layout and the
interaction patterns; the plot catalogue itself lives in chapter
9.

## Layout

The Analysis tab consists of four regions:

1. The **working directory** row at the top, with a path field, a
   **Change...** button, and a **Refresh** button that rescans the
   folder.
2. The **left column**, a vertical splitter with two halves:
    - **upper half**: the file tree, with `+ Add external file...`
      and `→ Overlay selected` buttons at the bottom;
    - **lower half**: the plot tree, with the `▦ Tile selected (N)`
      button at the bottom.
   The splitter bar between the two halves is draggable; pull it
   up to give the plot tree more room when many plots are visible,
   pull it down when you want more file rows.
3. The **right column**, a vertical stack:
    - a **Sun-arrow row** at the top that appears only when the
      active plot is 3D (chapter 9 covers what it does);
    - the **canvas** itself (matplotlib for 2D plots, VTK for 3D);
    - an **info label** at the bottom, with the current file or
      operation summary.
4. The **horizontal splitter** between left and right columns is
   draggable too.

The window-size and splitter positions are not yet persisted
across launches in this release; default sizes apply on every
launch.

## The working directory

Almost everything in the Analysis tab is anchored to a *working
directory* &mdash; a folder the application scans recursively (up
to three levels deep) looking for `.bin` files. The file tree's
**In folder** section is auto-populated from this scan.

The working directory updates **automatically** in two situations:

- when you load a TOML in the Run tab (it points at the TOML's
  parent folder, so the spody output ends up listed right next to
  its input);
- when a run completes (the Refresh-after-run mechanism re-scans
  the folder so new files appear immediately).

You can also set it manually with **Change...**, or refresh the
current scan with the **⟳ Refresh** button after, for example,
producing files outside of SpOdy.

## The file tree

The file tree has two top-level sections:

1. **In folder (`<working_dir>`)** &mdash; everything the scan
   found, listed by path relative to the working directory.
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
  to that file's kind (`SPDYOUT_` trajectory, `SPDYACC_`
  accelerations, `SPDYEVT_` events).
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
3. **The Sun-arrow row appears only for 3D plots** &mdash; the row
   immediately above the canvas hides itself when the active plot
   is 2D, because the arrow has no meaning there. When a 3D plot
   is selected the row reappears with its epoch field and **+ Sun
   arrow** button.

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
