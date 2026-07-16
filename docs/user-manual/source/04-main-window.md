# The main window

Once the wizard has done its job you can take stock of the
application proper. This chapter is a tour of the main window: the
two top-level modes (**Run** and **Analysis**), the menus, the
status bar, and how the pieces work together over the course of a
typical session. Detailed coverage of the form, the schema, the
plot catalogue, and the diff workflow waits for the chapters that
follow.

## Layout overview

The window divides into five regions:

1. The **menu bar** along the top, with four menus: **File**,
   **Run**, **Settings**, **Help**.
2. The **global top bar** immediately below the menus, hosting the
   shared **Working dir** field and a **Browse&hellip;** button.
   Both the Run tab's TOML picker and the Analysis tab's bin tree
   read from this one path, so picking a folder here drives every
   downstream view.
3. The **mode tab strip** below the top bar, with three tabs:
   **Run**, **Analysis**, **Re-run**. The window switches between
   completely different layouts depending on which tab is active;
   the menus and the top bar stay shared.
4. The **active workspace**, which fills the bulk of the window
   and changes shape with the mode (see sections 4.2 and 4.3).
5. The **status bar** along the bottom, with the path of the
   currently loaded TOML on the left and the run state
   (`idle`, `running 12.3s`, `OK (47.1s)`, `exit 1 (3.2s)`) on the
   right.

The full working-dir path may exceed the field's width; hover the
field to see it in the tooltip.

The window remembers its last size and position across launches
through Qt's standard mechanism, persisted alongside the other
settings in the registry.

## The Run tab

The Run tab is where you describe a scenario, validate it, and
launch the engine. Above the workspace sits a **TOML picker row**;
the workspace itself is a horizontal splitter you can drag to
redistribute width:

| Top row (full Run-tab width)                                     |
|------------------------------------------------------------------|
| TOML combo + **Load TOML&hellip;** + **Save** + **Save As&hellip;** |

| Pane (left)               | Pane (right)                           |
|---------------------------|----------------------------------------|
| TOML form (chapter 5)     | Terminal view streaming spody's output |
| Live TOML preview below   | Engine status (idle / running)         |

The **TOML combo** lists every `.toml` file under the shared
working dir, scanned fully recursively. Snapshots inside
`output/<ts>/` are included as load targets, identifiable by the
`<ts>_` filename prefix; draft files produced by Save (see
chapter 5) carry a trailing `(draft)` tag. Entries display
compactly as `<parent>/<file>`; the full relative path lives in
the item tooltip. Picking an entry loads it into the form (the
form's standard unsaved-edits prompt runs first if needed).

- **Load TOML&hellip;** opens a file dialog &mdash; useful when the
  file lives outside the current working dir (loading it auto-
  adopts its scenario folder as the new working dir if so).
- **Save** writes the current form state to the TOML the form is
  pointing at. If that file lives next to output bins (a
  snapshot, or a source with associated runs), Save diverts to a
  `<stem>.wip.toml` sidecar instead; see chapter 5.
- **Save As&hellip;** always pops the file dialog; the WIP divert
  does NOT fire here (you've explicitly chosen the path).

On the left of the splitter, an automatically-generated form lets
you fill in every field the TOML schema accepts &mdash; one
widget per scalar key, collapsible sections for optional blocks
(`[spacecraft.srp]`, `[events]`, `[batch]`, `[cr3bp]`), and a
live TOML preview pane underneath showing exactly what will be
written to disk on Save.

On the right, a read-only terminal view displays the merged stdout
and stderr streams of `spody.exe` while it runs. Output appears
line by line as the engine produces it, exactly as you would see it
in a console; no ANSI colour codes are interpreted, because the
engine does not produce any.

Three action buttons sit at the top of the form column itself
(below the TOML picker row, above the section groups):

- **Validate**: write a temporary TOML next to the current one and
  invoke `spody.exe validate` synchronously, displaying the result
  as a coloured badge under the row (`✓ valid` in green, or
  `✗ <reason>` in red with the full error in the tooltip).
- **RUN** (green): launch `spody.exe propagate` or `spody.exe
  batch` (the form auto-detects which subcommand applies from the
  presence of a `[batch]` section). When the loaded TOML has
  unsaved edits, Save runs first &mdash; including the WIP
  divert when applicable. While a run is in flight the button is
  disabled.
- **Stop** (red): kill the engine process currently running &mdash;
  enabled only while one is. Same action as **Run &gt; Stop**
  (<kbd>Ctrl</kbd>+<kbd>.</kbd>). A killed run terminates
  immediately; the run folder keeps whatever output records were
  already written (a truncated binary), and no notes stamping or
  WIP cleanup happens &mdash; those are reserved for runs that
  finish with exit 0.

Detailed coverage of the form and the schema appears in chapters 5
and 6.

## The Analysis tab

The Analysis tab is where you inspect simulation outputs. Its
workspace is a horizontal splitter with a richer left column:

| Pane (left, vertical splitter) | Pane (right)                       |
|--------------------------------|------------------------------------|
| File tree (auto-scanned)       | Animation bar (only when 3D plot)  |
| **Add external** + **Refresh** | Stacked 2D / 3D canvas             |
| **Overlay selected** button    | Info label                         |
| **(splitter)**                 |                                    |
| Plot tree (grouped by topic)   |                                    |
| **Tile selected** button       |                                    |

The left column is split vertically: file selection on top, plot
selection at the bottom, with a draggable bar between them. The
right column is dedicated to the canvas, with the optional
animation bar appearing only when a 3D plot is active.

The file tree scans the **shared working dir** (set in the global
top bar, see section 4.1) for `.bin` outputs &mdash; fully
recursive, so every bin under the root surfaces no matter how deep
the run-folder layout. Build / VCS / venv folders
(`__pycache__`, `.git`, `.venv`, `venv`, `build`, `dist`,
`node_modules`) are pruned; `output/` is intentionally included.
A small **Refresh** button next to **Add external** re-scans
manually when you drop new bins in by hand.

Click a single file in the file tree to load it. The application
detects its type from the 8-byte magic in the header
(`SPDYOUT_` trajectory, `SPDYACC_` accelerations, `SPDYEVT_` /
`SPDYEVTB` events) and rebuilds the plot tree with the catalogue
applicable to that type. Click a leaf in the plot tree to render
it immediately into the canvas. Re-click the same leaf to re-plot.

Detailed coverage of the analysis layout, including overlay and
diff workflows, appears in chapters 8 through 11.

## The menus

The menu bar is identical in both modes. Entries that do not apply
to the current mode are still listed but are no-ops until you
switch.

### File

- **New** &mdash; reset the Run-tab form to a blank scenario.
- **Open&hellip;** &mdash; open a `.toml` file (also achievable via the
  Run tab's **Load TOML&hellip;** button or by picking an entry
  from the TOML combo).
- **Open Recent &rsaquo;** &mdash; jump to one of the last eight files
  you have opened. **Clear list** at the bottom of the submenu
  empties the history.
- **Save** / **Save As&hellip;** &mdash; write the current form state to
  the current path, or to a chosen path.
- **Quit**.

### Run

- **Validate**, **Propagate**, **Batch** &mdash; the same three engine
  subcommands available through the buttons on the form, exposed
  through keyboard shortcuts (<kbd>Ctrl</kbd>+<kbd>T</kbd>,
  <kbd>Ctrl</kbd>+<kbd>R</kbd>, <kbd>Ctrl</kbd>+<kbd>B</kbd>
  respectively).
- **Stop** (<kbd>Ctrl</kbd>+<kbd>.</kbd>) &mdash; kill the current
  engine run; same action as the red **Stop** button on the form.
  On Windows the process is killed outright (a console engine
  cannot receive the graceful-close request); on Linux/macOS it
  gets a SIGTERM with a two-second grace window first.

### Settings

- **Paths&hellip;** &mdash; the persistent paths dialog: where to find
  `spody.exe`, the data dir, the Moon texture used by the 3D viewer.
- **Setup wizard&hellip;** &mdash; reopen the wizard (chapter 3) at
  any time, even when everything is already green.
- **About** &mdash; version and build info for both halves of the
  application.

### Help

- **User manual** &mdash; opens this document.
- **Visit project page** &mdash; opens the SpOdy project landing
  page in your default browser, if a network is available.

## The status bar

The status bar at the very bottom of the window shows two
permanently visible items:

- on the **left**, the absolute path of the TOML the Run tab is
  currently editing (or the literal string `(unsaved)` if you have
  never saved). An asterisk after the path (`<path>*`) indicates
  unsaved edits in the form;
- on the **right**, the engine's current state:
  - `idle` &mdash; no run in progress;
  - `running 14s` &mdash; a run is in flight; the elapsed-time
    counter updates every second;
  - `OK (47.1s)` (green) or `exit 1 (3.2s)` (red) &mdash; the result
    of the last completed run.

The status bar is purely informational; nothing in it is clickable.

## A typical session

To put the layout in motion: a routine session of SpOdy looks like
this.

1. Launch `spody-gui.exe`. The window opens with an empty form.
   Browse to the project folder you want to work in via the
   global top bar's **Browse&hellip;**, or use **File &rsaquo;
   Open&hellip;** (the working dir adopts the file's scenario
   folder automatically).
2. Pick a TOML from the Run-tab combo (it now lists every
   scenario under your working dir), or build a scenario from
   scratch by typing into the form.
3. Click **Validate** to check the schema. The badge under the
   button turns green; if it does not, fix the indicated field
   and try again.
4. Click **RUN**. The Run tab's right pane streams the engine's
   output for the duration of the propagation; the status bar
   ticks `running 12s`, `running 13s`, &hellip;
5. When the run finishes, the status bar shows `OK` in green. The
   Analysis tab's file tree (sharing the same working dir) has
   already been refreshed with the freshly produced
   `output/<ts>/<ts>_*.bin` files; the Run-tab combo has also
   picked up the new `<ts>_input.toml` snapshot.
6. Click a `.bin` file to load it; click a plot leaf to render
   it; use **Ctrl/Shift-click** for multi-selection if you want
   to overlay or tile several plots.

That is the loop. Everything else &mdash; batches, diffs,
3D scenes, frame conventions &mdash; is variations on it.
