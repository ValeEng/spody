# Building a simulation: the TOML form

The Run tab's left pane is a *form* over the TOML schema: one
widget per scalar key, collapsible groups for optional sections,
range checks at every keystroke, and a live preview of the
canonical TOML the form will write to disk. This chapter walks
through how to fill it in. The schema details &mdash; types,
ranges, defaults, what each field means physically &mdash; live in
chapter 6.

## Anatomy of the form

The form is a vertical scroll of section groups, one per top-level
TOML section. From top to bottom in the order spody expects them:

1. `[simulation]`
2. **Object** &mdash; either `[spacecraft]` *or* `[debris]`
   (mutually exclusive; the choice is made through a pair of
   radio buttons at the top of the group)
3. `[initial_state]`
4. `[force_model]`
5. `[ephemeris]`
6. `[integrator]`
7. `[output]`
8. `[events]` *(optional, enabled by checkbox)*
9. `[batch]` *(optional, enabled by checkbox)*

Each section header is the literal TOML name, so the mapping
between the form and the file you produce is one to one. The form
never invents fields and never silently drops fields it does not
understand: anything it loads from a TOML file that it does not
have a widget for is preserved verbatim through a *passthrough*
mechanism and re-emitted on the next save.

## Filling a field

Every field type follows the same pattern: a label on the left, a
widget on the right.

- **Strings** (`name`, `frame`, `central_body`, &hellip;): a plain
  line edit, except for known enumerated values that use a combo
  box (e.g. `frame` accepts only `central_inertial` today).
- **Floats** (`mass_kg`, `et_start_s`, `rel_tol`, &hellip;): a line
  edit, no validator attached (so the text you type stays verbatim
  &mdash; no surprise normalisation of `1.0e-5` into `1e-05`).
  Range checks happen at every keystroke against the field's
  registered validator (see section 5.3).
- **Integers** (`harmonics_degree`, `thread_number`): a line edit
  with a `QIntValidator` enforcing the min/max range as you type.
- **Booleans** (`srp`): a checkbox.
- **Vector-of-three** (`position_km`, `velocity_kms`): three line
  edits side by side.
- **Lists of strings** (`third_bodies`): one checkbox per known
  value (`Sun`, `Mercury`, `Venus`, `Earth`, &hellip;).
- **Paths to user-chosen files** (`output.output_dir`,
  `batch.cases_source_file`, &hellip;): a line edit alongside a
  **Browse...** button that pops a file dialog and writes the
  chosen path back into the edit.
- **Paths to wizard-managed assets** (`force_model.harmonics_file`,
  `force_model.eop_file`, `force_model.iau2006_dir`,
  `force_model.space_weather_file`, `ephemeris.file`): a
  **dropdown** of files the Setup wizard
  has downloaded into the data dir, filtered by category and (for
  harmonics, EOP, IAU 2006) by `central_body`. A **Browse...**
  next to the dropdown adds an out-of-data-dir file as a one-off
  `(custom)` entry, so a TOML pointing at e.g.
  `external/spody-core/raw_data/GRGM1200B/...` still round-trips.
  The dropdown refreshes automatically when the wizard finishes a
  new download or when `central_body` changes. The Earth-only
  rows (`eop_file`, `iau2006_dir`, `space_weather_file`, `drag`)
  appear only when `central_body = "Earth"`; switching back to
  Moon hides them and drops them from the emitted TOML.
- **Epoch (`simulation.et_start_s`)** &mdash; a dual-cell row: the
  ET value on the left, a UTC ISO 8601 cell on the right, two
  arrow buttons between them. **&rarr;** converts ET to UTC,
  **&larr;** converts UTC to ET. Conversion is bit-identical to
  SPICE `str2et` / `et2utc` (see chapter 14 for the underlying
  algorithm). Only `et_start_s` is written to the TOML; the UTC
  cell is purely a typing aid.
- **Duration (`simulation.duration_s`)** &mdash; a line edit plus a
  unit combo (`s | min | h | days`). The combo selects only the
  *display* unit; the TOML always carries
  `simulation.duration_s` in seconds. Typing `3600` with the
  combo on `s` is equivalent to typing `1.0` with the combo on
  `h` &mdash; switching the combo reconverts the visible number
  so the underlying seconds-value stays invariant. On load the
  form auto-picks the largest unit whose factor does not exceed
  the loaded magnitude (a `86400.0` TOML value comes back as
  `1.0 days`, a `0.5` value stays as `0.5 s`).

Hovering the cursor over a label or its widget shows a **tooltip**
with the field's one-line description; range-validated fields also
include the allowed range in the tooltip.

## Validation as you type

A field whose value falls outside the registered range is flagged
**immediately** with a red border and a small warning glyph appended
to its tooltip (`⚠ must be > 0`, `⚠ must be in [2, 1200]`). The
form does not block invalid input &mdash; you can continue editing
&mdash; but the visual cue makes the bad field obvious.

The range checks the form runs are local: they catch values that
are physically impossible (negative masses, zero rel-tol) or
violate a documented bound (harmonics degree above 2200, the
absolute schema ceiling that accommodates the EIGEN-6C4 / EGM2008
Earth coefficient sets). Cross-field consistency (e.g. `h_init_s`
between `h_min_s` and `h_max_s`) is *not* checked by the form
&mdash; that is the engine's job, and **Validate** is the right
button to press once the per-field indicators look clean.

## The XOR object switch

Two TOML sections describe the propagated object, and a propagation
must use exactly one of them. The form models this with a pair of
radio buttons at the top of the **Object** group:

- **Spacecraft** &mdash; the conventional case: a body with a known
  mass and (when SRP is enabled) a cross-sectional area. This is
  the `[spacecraft]` section in the emitted TOML.
- **Debris** &mdash; an inferred body where only the area-to-mass
  ratio matters, because the gravity-driven accelerations are mass-
  independent and SRP acceleration depends only on `A/m`. This is
  the `[debris]` section.

Switching the radio shows the matching widgets and hides the
others. The form never emits both sections; whichever radio is up
at **Generate** time is the one that ends up in the file.

The choice also affects which override targets are available in
the `[batch.columns]` mapping table (chapter 7). For instance,
`spacecraft.srp.area_m2` only appears as a target when the radio
is on Spacecraft, and `debris.am_srp` only when it is on Debris.
The drag parameters follow the same rule
(`spacecraft.drag.area_m2` / `spacecraft.drag.Cd` vs
`debris.am_drag` / `debris.Cd`).

## Optional sub-blocks

Four sections are not always required, and the form gates them
behind a checkbox:

- **`[spacecraft.srp]`** &mdash; the SRP cannonball sub-block of
  `[spacecraft]`. Enable the *Enable [spacecraft.srp]* checkbox to
  expose the sub-form; inside it a second pair of radios chooses
  between `area_m2` (with `A/m = area / mass`) and `am_srp`
  (the ratio specified directly).
- **`[spacecraft.drag]`** &mdash; the atmospheric-drag sub-block,
  identical in shape to SRP: an *Enable [spacecraft.drag]*
  checkbox, an `area_m2` / `am_drag` radio pair and the `Cd`
  coefficient. In Debris mode the equivalent is the *Enable drag
  (am_drag + Cd)* checkbox inside the debris box. Remember to also
  tick the `drag` toggle in `[force_model]` (visible for Earth) so
  the engine actually integrates the force.
- **`[events]`** &mdash; two independent opt-in sub-sections,
  each behind its own checkbox: *Enable eclipse detection*
  exposes the `eclipse_threshold` field, *Enable altitude
  crossings* exposes a body / altitude_km / action / refined
  table with Add / Remove buttons. The body combo per row tracks
  the model's valid bodies (central + checked third bodies in HF,
  the two primaries in CR3BP) and rebuilds automatically when
  any of those change. The two features are independent &mdash;
  CR3BP forbids eclipse but accepts altitude crossings.
- **`[batch]`** &mdash; the multi-case sweep block. Enable the
  *Enable [batch]* checkbox to expose the batch-specific form
  (name, output directory, thread count, cases CSV, column mapping
  table). Batch scenarios are covered in detail in chapter 7.

Disabling any of these checkboxes after filling in fields *hides*
the widgets but does not erase the values: re-enable later and your
typed numbers are still there. At emit time, however, a disabled
block is omitted entirely &mdash; the form behaves as if you had
never filled it. This is the safest behaviour for round-tripping
files that contain sections you do not currently want to touch.

## Conditional UI

A few smaller conditionals are wired into the form to keep the
visible surface relevant:

- **`output.interval_s`** is only shown when `output.mode = "fixed"`.
  In step mode it is meaningless (the integrator decides when to
  emit a record), so the row hides itself and the field is
  stripped from the emitted TOML even if you typed a value
  previously.
- The **`spacecraft.srp.area_m2`** and **`spacecraft.srp.am_srp`**
  fields grey each other out depending on which SRP-parameter radio
  is selected. The parser rejects files that set both, so this
  prevents the most common XOR mistake before it happens.

## Saving: the file gets written

There is one path for writing the TOML to disk: the **Save** /
**Save As&hellip;** buttons in the Run tab's TOML picker row (or
the matching **File &rsaquo; Save / Save As&hellip;** menu items
and their <kbd>Ctrl</kbd>+<kbd>S</kbd> / <kbd>Ctrl</kbd>+<kbd>
Shift</kbd>+<kbd>S</kbd> shortcuts). Both **Save** and the **RUN**
button write through the same canonical emitter so the result
diffs cleanly between runs.

The emitter is **schema-aware**: it knows the canonical order of
sections and keys, formats floats with `repr()` precision, and
emits inline tables for the entries inside `[batch.columns]` when
they use delta mode. Two **Save** clicks on the same form state
produce byte-identical files.

### The WIP draft mechanism

When you click **Save** on a TOML whose folder already contains
`.bin` output files &mdash; either a per-run snapshot inside
`output/<ts>/` or a source TOML whose runs landed beside it &mdash;
the form does NOT overwrite that file. Doing so would corrupt the
on-disk record every existing run depends on (the snapshot
documents what the engine actually ran; rewriting it makes the
snapshot lie about its bins).

Instead, Save diverts to a `<stem>.wip.toml` sidecar next to the
original file. A one-time popup announces the divert ("Saving as
draft &hellip;"); subsequent Save clicks on the same draft
overwrite it silently (the WIP IS the editing target). WIPs are
tagged `(draft)` in the TOML combo so they're easy to spot in
mixed folders.

If you genuinely want to overwrite the original (e.g. to update a
template you've decided is no longer authoritative), use **Save
As&hellip;**: that path always asks for the destination, never
diverts, and writes wherever you point it.

A successful **RUN** launched from a WIP cleans up automatically:

1. The engine snapshots the WIP's content into the new run folder
   as `output/<new-ts>/<new-ts>_input.toml` (the canonical record
   of this run).
2. The GUI unlinks the WIP from disk (no longer needed).
3. The form is reloaded with that per-run snapshot &mdash; the only
   surviving on-disk copy of what actually ran (with the engine's
   final status line stamped into its notes). Runs launched from a
   regular (non-WIP) TOML leave the form on the source file
   instead.

Failed runs leave the WIP alone so you can fix and retry.

## The live TOML preview

Below the form (the lower half of the splitter visible in the Run
tab's left pane) lives a read-only TOML preview. It mirrors what
**Generate** would write to disk, refreshed on every keystroke.

Practically, it gives you three things:

1. a sanity check that the widgets you have just touched produce
   the TOML you expect &mdash; especially useful when learning the
   schema or when you want to see how the form serialises an
   optional sub-block;
2. a copy-friendly view of the canonical output, so you can paste
   the result into another tool, a chat message, or a bug report
   without having to round-trip through disk;
3. a feedback loop for the **passthrough** mechanism: any section
   the form does not directly render but found in the loaded file
   appears at the bottom of the preview, confirming it is being
   carried through to the next save.

If you ever type something the emitter cannot serialise (very rare
&mdash; only `ValueError` from an internal conversion would
trigger it) the preview shows a single-line `# (form has invalid
values: …)` placeholder until the next valid edit.

## Switching the dynamics model

The `simulation.dynamics_model` combo picks between
`high_fidelity` (default; the full force-model propagator the rest
of this manual is built around) and `cr3bp` (the Circular
Restricted 3-Body Problem). Switching the combo reflows the form:

- **`high_fidelity`** shows the **Object** (spacecraft / debris),
  **force_model** and **ephemeris** sections.
- **`cr3bp`** hides those three groups and reveals a single
  **`[cr3bp]`** group with `primary_1` and `primary_2` combos
  (today's only valid pair is Earth + Moon). HF-only output
  toggles (the `accelerations_file` checkbox and the eclipse-event
  entry) are greyed out with a tooltip explaining why.

The frame combo in `[initial_state]` also filters per model:
`central_inertial` for HF, `synodic_rotating` for CR3BP. The
underlying CR3BP schema details &mdash; barycenter offsets,
omega derivation, impact-event auto-wiring &mdash; live in
chapter 6.

## Switching the initial-state flavour

The `[initial_state]` section carries a `kind` combo above the
field block with two choices:

- **`cartesian`** (the default) shows the legacy
  `position_km` + `velocity_kms` vec3 widgets.
- **`keplerian`** shows the six classical orbital elements
  (`semi_major_axis_km`, `eccentricity`, `inclination_deg`,
  `raan_deg`, `arg_periapsis_deg`, `anomaly_deg` with an
  `anomaly_type = "true" | "mean"` combo), plus a
  `reference_body` combo that picks **which** inertial frame the
  elements live in. Under HF the only option is `central`
  (= the central body); under CR3BP the choices become
  `primary_1` and `primary_2` (the dialog defaults to neither, so
  the user picks explicitly which primary anchors the orbit).

Flipping the combo runs a **best-effort live conversion in either
direction** so the values you just typed are not thrown away. The
converter pulls the right &mu; (central body's GM for HF, primary's
GM for CR3BP) plus the synodic-frame geometry (omega + barycenter
offsets) and chains them through the `spopy.kepler` /
`spopy.cr3bp` helpers. Empty / unparseable fields make the
conversion silently bail (the destination block stays at whatever
it was holding); a conversion that succeeds fills every field of
the new block in one shot.

### Lossless swap cache

The four representations &mdash; `(cartesian, central_inertial)`,
`(cartesian, central_body_fixed)`, `(keplerian, central_inertial)`,
`(keplerian, central_body_fixed)` &mdash; are kept in a per-form
cache that snapshots each finalised edit. Whenever you leave a
cart / kep field (focus loss or Enter), the form treats the
visible block as the ground truth and derives the other three
representations from it via spopy. Subsequent toggles of the
`kind` or `frame` combos then write the cached values verbatim
&mdash; no further conversion runs at toggle time, so back-and-
forth flips of either combo round-trip the displayed numbers
bit-for-bit (no compounding ULP drift the way per-toggle
conversions would).

The cache is invalidated wholesale by changes to anything the
underlying conversions depend on: `et_start_s`, `central_body`,
`dynamics_model`, `anomaly_type`, `reference_body`, the ephemeris
file. After such a change the very next toggle falls back to the
old on-the-fly conversion (and seeds the cache from that result,
so the toggle *after* it lands on the lossless path again).

The engine applies the same converter at parse time, so a
hand-written TOML with `kind = "keplerian"` produces a bit-
identical Cartesian state regardless of which side wrote it.
The underlying schema lives in chapter 6, section *kind =
"keplerian"*.

## Validating with the engine

The **Validate** button at the top of the form runs the engine's
own parser against the form's current state, *without writing your
real file*. Internally the form:

1. produces the canonical TOML from `to_dict()` (the same path the
   live preview uses);
2. writes it to a temporary file next to your current saved file
   (or to the OS temp dir if you have not saved yet) so that
   relative paths inside the TOML (`harmonics_file`, `ephemeris
   .file`, `batch.cases_file`) resolve identically to what they
   would at run time;
3. calls `spody.exe validate <tempfile>` and waits up to 30 seconds
   for the result;
4. deletes the temp file and displays the verdict as a coloured
   badge to the right of the button:
    - **`✓ valid`** in green when the engine returns exit code 0;
    - **`✗ <last line of stderr>`** in red otherwise, with the full
      message in the tooltip.

Any edit you make after a green badge clears it back to neutral,
so a stale green never misleads you into thinking an out-of-sync
file passed validation.
