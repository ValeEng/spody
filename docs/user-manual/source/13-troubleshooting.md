# Troubleshooting

This chapter is a catalogue of the issues you are most likely to
hit, grouped by the part of the workflow they belong to, with the
recommended fix for each. If you encounter something not listed
here please file a report with the steps that reproduce it; the
catalogue grows with experience.

## Setup wizard

### "Download failed" on a DE440 chunk

The JPL FTP-over-HTTPS server occasionally returns transient
errors during high-traffic periods. The standard fix is to:

1. Wait a minute.
2. Click **Download** on the affected card again.

If the failure is persistent, check the URL printed in the error
dialog against the canonical pattern
`https://ssd.jpl.nasa.gov/ftp/eph/planets/ascii/de440/ascpXXXXX.440`
and adjust it in the card's URL field if the server has moved.

### "Download failed" on a GRGM1200B file

The PDS Geosciences Node at Washington University hosts the
GRGM1200B coefficient files. If the canonical URL
(`https://pds-geosciences.wustl.edu/grail/grail-l-lgrs-5-rdr-v1/grail_1001/shadr/`)
returns a 404, the file has been relocated. Open the PDS
Geosciences GRAIL landing page in a browser, navigate to the
`shadr/` subfolder, find the current location of
`gggrx_1200b_sha.tab` and `gggrx_1200b_sha.lbl`, and paste the new
URL into the card.

### "Conversion failed (exit 1)"

The automatic conversion runs `spody.exe convert ephemeris` under
the hood. The two common failures:

- **Missing files**: at least one ASCII chunk was not actually
  downloaded successfully. Hit **Refresh** at the bottom of the
  wizard and verify every required card is green. Re-download any
  that are not.
- **`spody.exe` is not configured**: the wizard delegates the
  conversion to the engine, and if the engine path is empty or
  invalid the conversion cannot run. Open **Settings &rsaquo;
  Paths**, set the path to `spody.exe` (it lives next to
  `spody-gui.exe` in the bundle), and reopen the wizard.

### Wizard reopens at every launch

This means the **hard run-guard** decides one or more required
files are not in place. Common causes:

- The data folder is on a removable drive that is not currently
  mounted.
- A virus scanner quarantined the downloaded files (some
  enterprise scanners are aggressive about large binary
  downloads from FTP-derived URLs).
- The bundle was moved to a different folder after the wizard
  was completed, and the registered data folder no longer points
  at a writable location.

Open the wizard and inspect the status of every card to identify
the missing file; restore the file or change the data folder via
the **Change&hellip;** button.

## TOML form

### "Form has invalid values" placeholder in the preview

A field's value cannot be coerced to its declared type &mdash;
typically a non-numeric character in a float or integer field.
Look for fields with the red border and fix the offending value.
The TOML preview restores automatically once the form is again
in a valid state.

### **Validate** badge shows `(spody binary not set)`

The engine path is not configured. Open **Settings &rsaquo;
Paths** and point at `spody.exe`.

### **Validate** badge shows `(data not ready)`

The hard run-guard intercepted the Validate call because one or
more required data files are missing from the data folder. The
button next to the badge invites you to open the wizard; do so
and complete the downloads.

### **Validate** badge shows `(form has invalid values)`

The form contains a value that cannot be serialised. This is
distinct from "out of range": the type conversion itself failed
(e.g. typing letters in a numeric field). Fix the indicated
field; the badge clears on the next edit.

### **Validate** badge stays red after a fix

The previous red badge persists until you press **Validate**
again. There is no auto-revalidation on edit; only a successful
explicit Validate turns the badge green.

### Floats render in scientific notation after a load

This is intentional. The form reads the float, then re-emits it
through Python's `repr()` to ensure a round-trippable
representation; the result may look like `1e-5` even if you
typed `0.00001`. The two are equal. No information is lost.

> Earlier versions of the form attached a `QDoubleValidator` that
> aggressively normalised user input (`1e-5` &rArr; `1e-05`,
> `0.00001` &rArr; `1e-05`). The current version intentionally
> *does not* validate floats with a Qt validator and keeps the
> typed text verbatim until the next Load.

## Run mode

### "spody binary not set"

The engine path is empty. Open **Settings &rsaquo; Paths**.

### "Cannot run: data not ready"

Same as the validate counterpart: the run-guard intercepted the
launch because data files are missing. The dialog offers to open
the wizard; accept the offer and complete the downloads.

### Run starts but stops within seconds

Open the terminal pane on the right; the engine printed a one-line
diagnostic before exit. Common cases:

- **`error: harmonics_file: ENOENT`** &mdash; the
  `[force_model].harmonics_file` path inside the TOML does not
  resolve. Remember the path is relative to the TOML's directory.
- **`error: ephemeris.file: ENOENT`** &mdash; same for the
  ephemeris path.
- **`error: integrator step h dropped below h_min_s`** &mdash;
  the adaptive controller could not maintain `rel_tol` and gave
  up. Try a smaller `rel_tol` (so the controller is happier with
  larger steps) or a smaller `h_min_s`.

### Run is much slower than expected

Two common amplifiers:

- A **harmonics degree** above 200 in a long propagation;
  cost scales as O(N&sup2;), so going from N=80 to N=200 makes
  the engine 6&times; slower without proportional accuracy gain.
- A **tight `rel_tol`** (smaller than 1e-13) below the engine's
  resolution; the controller spends all its time rejecting steps.

Tune both first; engine-side performance is rarely the actual
bottleneck.

### Terminal pane stops updating mid-run

A long-running step (heavy harmonics evaluation, especially with
event detection enabled) can keep the engine inside one numeric
routine without producing console output for several seconds.
The status bar still increments the elapsed-time counter, which
is your hint that the process is alive even when the terminal is
quiet. Worry only if the elapsed-time counter freezes too.

## Analysis tab

### File tree shows no files in the working directory

The recursive `.bin` scan goes three folders deep. A binary buried
deeper does not appear; either move the file up or use the
**+ Add external file&hellip;** button to register it explicitly.

### "Unknown file" on a `.bin` you know is good

The kind detection reads the first 8 bytes of the file and
matches them against the four supported magics (`SPDYOUT_`,
`SPDYACC_`, `SPDYEVT_`, `SPDYEVTB`). A `.bin` produced by an
external tool that does not write a SpOdy magic will be flagged;
convert the file into one of the supported formats first.

### Plot button is missing

There is no plot button. Click a leaf in the plot tree to render
it; the dispatch is on the click itself.

### Sun-arrow row is missing

The Sun-arrow row appears only when the **active plot is 3D**.
The currently-selected leaf in the plot tree is 2D (which covers
every plot except `3D orbit + Moon` and `Impact 3D on Moon`);
pick a 3D plot and the row reappears.

### Impact-view says "No input.toml found" / "Could not locate ephemeris"

The four impact lat/lon views (equirect, Mollweide, density
heatmap, 3D) read the run-folder `input.toml` snapshot for
`et_start_s` and `[ephemeris].file` &mdash; both are required to
project ICRF impacts onto the Moon's body-fixed PA frame. The
snapshot is normally written by the engine at every invocation
(chapter 7). It can be missing when:

- the events file you loaded came from a *pre-run-folder* run
  (an older spody build, before the layout refactor);
- the events file was hand-copied out of its original run folder
  to another location.

In both cases, copy the matching `input.toml` next to the
`<batch>_events.bin` and reload the file. The ephemeris path
inside that TOML must still resolve from somewhere reachable; the
resolver tries the snapshot directory, then two ancestor levels
up (where the original TOML usually lived), then the current
working directory.

### Moon texture not showing in the impact map / 3D scene

The Moon texture is an **optional** wizard asset and is not
downloaded by default (chapter 3). Open the Setup wizard and
press **Download** on the *Moon texture* card. The impact map
falls back to a plain background and the 3D scene to a flat-grey
sphere when the texture is absent; no error is raised.

### "Diff needs exactly 2 files (currently selected: 1)"

You clicked a diff leaf with only the loaded file in the
selection. **Ctrl-click** another file in the file tree to add it
to the selection, then re-click the diff leaf.

### "Diff requires overlapping time windows"

The two selected files were propagated over disjoint time spans.
This is fundamental: there is no way to compare two trajectories
that do not coexist in time. Re-run one of them so the time
windows overlap.

### "Tile cannot mix single-file and diff plots"

The tile dispatcher requires the selection to be uniform: either
all single-file specs (rendered against the loaded file) or all
diff specs (rendered against the two-file selection). Clear the
selection (Ctrl-click to unselect) and rebuild it.

### 3D viewer is unresponsive

The first VTK render after a fresh launch can take a few seconds
while OpenGL is initialised. Subsequent renders are fast. If the
viewer remains unresponsive after ten seconds, your graphics
adapter may have an unusual OpenGL driver; switch to a 2D plot
to confirm the rest of the application is healthy.

### Moon sphere is flat grey instead of textured

The 3D scene falls back to the flat-grey sphere when no Moon
texture is configured. Open **Settings &rsaquo; Paths &rsaquo;
Moon texture (3D view)** and point at an equirectangular Moon
image (JPEG or PNG). The texture is reloaded on the next plot
dispatch; no application restart is needed.

## Settings

### Persistent paths are not remembered across launches

Settings are stored in the Windows registry under
`HKEY_CURRENT_USER\Software\SpOdy\SpOdy`. If you are running as a
guest or restricted user, registry writes may be blocked by
policy. Run the application as your normal interactive user.

### Resetting the application to a clean state

Two steps:

1. Delete the `data/` folder next to `spody-gui.exe`.
2. Delete the registry key
   `HKEY_CURRENT_USER\Software\SpOdy\SpOdy` via `regedit`.

The next launch behaves exactly as a fresh install: the wizard
pops, no recent files are remembered, no paths are configured.
