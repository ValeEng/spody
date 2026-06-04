# The main window

Once the wizard has done its job you can take stock of the
application proper. This chapter is a tour of the main window: the
two top-level modes (**Run** and **Analysis**), the menus, the
status bar, and how the pieces work together over the course of a
typical session. Detailed coverage of the form, the schema, the
plot catalogue, and the diff workflow waits for the chapters that
follow.

## Layout overview

The window divides into four regions:

1. The **menu bar** along the top, with five menus: **File**,
   **Edit**, **Run**, **Settings**, **Help**.
2. The **mode tab strip** immediately below it, with two tabs:
   **Run** and **Analysis**. The window switches between completely
   different layouts depending on which tab is active; the menus
   stay shared, although a few entries are no-ops while the
   irrelevant tab is up.
3. The **active workspace**, which fills the bulk of the window
   and changes shape with the mode (see sections 4.2 and 4.3).
4. The **status bar** along the bottom, with the path of the
   currently loaded TOML on the left and the run state
   (`idle`, `running 12.3s`, `OK (47.1s)`, `exit 1 (3.2s)`) on the
   right.

The window remembers its last size and position across launches
through Qt's standard mechanism, persisted alongside the other
settings in the registry.

## The Run tab

The Run tab is where you describe a scenario, validate it, and
launch the engine. Its workspace is a horizontal splitter you can
drag to redistribute width:

| Pane (left)               | Pane (right)                           |
|---------------------------|----------------------------------------|
| TOML form (chapter 5)     | Terminal view streaming spody's output |
| Live TOML preview below   | Engine status (idle / running)         |

On the left, an automatically-generated form lets you fill in every
field the TOML schema accepts &mdash; one widget per scalar key,
collapsible sections for optional blocks (`[spacecraft.srp]`,
`[events]`, `[batch]`), and a live TOML preview pane underneath
showing exactly what will be written to disk when you press
**Generate**.

On the right, a read-only terminal view displays the merged stdout
and stderr streams of `spody.exe` while it runs. Output appears
line by line as the engine produces it, exactly as you would see it
in a console; no ANSI colour codes are interpreted, because the
engine does not produce any.

At the very top of the form sits a row of action buttons:

- **Load&hellip;**: open a `.toml` file and pre-fill the form with
  its values.
- **Generate**: serialise the current form state to disk as
  canonical TOML. The first time, you are prompted for a filename;
  subsequent presses overwrite the same file.
- **Validate**: write a temporary TOML next to the current one and
  invoke `spody.exe validate` synchronously, displaying the result
  as a coloured badge under the row (`✓ valid` in green, or
  `✗ <reason>` in red with the full error in the tooltip).
- **RUN** (green): generate, then launch `spody.exe propagate` or
  `spody.exe batch` (the form auto-detects which subcommand applies
  from the presence of a `[batch]` section).

Detailed coverage of the form and the schema appears in chapters 5
and 6.

## The Analysis tab

The Analysis tab is where you inspect simulation outputs. Its
workspace is a horizontal splitter with a richer left column:

| Pane (left, vertical splitter) | Pane (right)                       |
|--------------------------------|------------------------------------|
| File tree (auto-scanned)       | Sun-arrow row (only when 3D plot)  |
| **Add external** button        | Stacked 2D / 3D canvas             |
| **Overlay selected** button    | Info label                         |
| **(splitter)**                 |                                    |
| Plot tree (grouped by topic)   |                                    |
| **Tile selected** button       |                                    |

The left column is split vertically: file selection on top, plot
selection at the bottom, with a draggable bar between them. The
right column is dedicated to the canvas, with the optional Sun-arrow
row appearing only when a 3D plot is active.

The **working directory** at the top of the tab is the folder the
file tree scans for `.bin` outputs (recursively, up to three levels
deep). It auto-fills when you load a TOML in the Run tab, pointing
at the TOML's parent so the analysis results land naturally next to
their input. You can also change it manually via the
**Change&hellip;** button.

Click a single file in the file tree to load it. The application
detects its type from the 8-byte magic in the header
(`SPDYOUT_` trajectory, `SPDYACC_` accelerations, `SPDYEVT_`
events) and rebuilds the plot tree with the catalogue applicable to
that type. Click a leaf in the plot tree to render it immediately
into the canvas. Re-click the same leaf to re-plot.

Detailed coverage of the analysis layout, including overlay and
diff workflows, appears in chapters 8 through 11.

## The menus

The menu bar is identical in both modes. Entries that do not apply
to the current mode are still listed but are no-ops until you
switch.

### File

- **New** &mdash; reset the Run-tab form to a blank scenario.
- **Open&hellip;** &mdash; open a `.toml` file (also achievable via the
  form's **Load&hellip;** button).
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
- **Stop** (<kbd>Ctrl</kbd>+<kbd>.</kbd>) &mdash; terminate the
  current engine run. The signal sequence is graceful first, hard
  kill after two seconds.

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

1. Launch `spody-gui.exe`. The window opens on the Run tab with an
   empty form (or with whichever file you last saved, if Open
   Recent is non-empty and you click one).
2. Either build a scenario from scratch by typing into the form, or
   open one with **File &rsaquo; Open&hellip;** or the **Load&hellip;**
   button at the top of the form.
3. Click **Validate** to check the schema. The badge under the
   button turns green; if it does not, fix the indicated field and
   try again.
4. Click **RUN**. The Run tab's right pane streams the engine's
   output for the duration of the propagation; the status bar
   ticks `running 12s`, `running 13s`, &hellip;
5. When the run finishes, the status bar shows `OK` in green. You
   are now ready to inspect the results: switch to the **Analysis**
   tab. Its working directory has auto-pointed at your TOML's
   folder, so the freshly produced `.bin` files are already listed
   in the file tree.
6. Click a `.bin` file to load it; click a plot leaf to render it;
   use **Ctrl/Shift-click** for multi-selection if you want to
   overlay or tile several plots.

That is the loop. Everything else &mdash; batches, diffs,
3D scenes, frame conventions &mdash; is variations on it.
