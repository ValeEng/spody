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
  `ephemeris.file`): a **dropdown** of files the Setup wizard
  has downloaded into the data dir, filtered by category and (for
  harmonics, EOP, IAU 2006) by `central_body`. A **Browse...**
  next to the dropdown adds an out-of-data-dir file as a one-off
  `(custom)` entry, so a TOML pointing at e.g.
  `external/spody-core/raw_data/GRGM1200B/...` still round-trips.
  The dropdown refreshes automatically when the wizard finishes a
  new download or when `central_body` changes. The Earth-only
  rows (`eop_file`, `iau2006_dir`) appear only when `central_body
  = "Earth"`; switching back to Moon hides them and drops them
  from the emitted TOML.
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

## Optional sub-blocks

Three sections are not always required, and the form gates them
behind a checkbox:

- **`[spacecraft.srp]`** &mdash; the SRP cannonball sub-block of
  `[spacecraft]`. Enable the *Enable [spacecraft.srp]* checkbox to
  expose the sub-form; inside it a second pair of radios chooses
  between `area_m2` (with `A/m = area / mass`) and `am_srp`
  (the ratio specified directly).
- **`[events]`** &mdash; opt-in eclipse detection through
  `eclipse_threshold`. The threshold field is gated behind the
  *Enable [events]* checkbox.
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

## Save versus Generate

Two paths produce the same canonical TOML on disk:

- the **File &rsaquo; Save** (and **Save As&hellip;**) menu items;
- the **Generate** button at the top of the form.

The difference is purely conceptual: **Save** reads as "I'm done
editing", **Generate** reads as "give me the TOML so I can do
something with it" (typically the **RUN** button immediately
following). Functionally they go through the same emitter, produce
byte-identical output, and update the same recent-files list.

The emitter is **schema-aware**: it knows the canonical order of
sections and keys, formats floats with `repr()` precision, and
emits inline tables for the entries inside `[batch.columns]` when
they use delta mode. Two **Generate** clicks on the same form
state produce byte-identical files, so the result diffs cleanly
between runs.

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
