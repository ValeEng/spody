# Batch propagations

A *batch* is a parameter sweep: the same scenario propagated many
times with one or more fields varied case by case. SpOdy describes
the sweep through a CSV file (one row per case, columns matching
the fields to vary) and a mapping table that tells the engine
which CSV column overrides which TOML field. This chapter walks
through how to set one up.

## Why batches

The typical reasons to run a batch instead of repeated single-case
runs are:

- **Sensitivity analyses** &mdash; sweep over `harmonics_degree`
  to see how much accuracy you actually need; sweep `Cr` to
  understand the SRP coefficient's contribution to long-term drift;
  sweep `mass_kg` to characterise the response of a chosen
  propellant tank load.
- **Monte-Carlo simulation** &mdash; randomly perturb the initial
  state across thousands of cases and compute statistics on the
  final position.
- **Debris-cloud propagation** &mdash; one case per fragment, each
  with its own `A/m` ratio drawn from a debris-population
  distribution.

The engine threads cases across CPU cores when the OpenMP-enabled
build is used and `batch.thread_number > 1` (see section 7.5),
giving near-linear speed-up for the typical case sizes.

## The CSV file

A SpOdy batch cases file is a plain CSV. The conventions are:

- Lines starting with `#` are comments and are skipped.
- The **first non-comment, non-blank line is the header**: a
  comma-separated list of column names. Whitespace around each
  name is trimmed.
- One special header value is reserved: `id`. When present, the
  column's value is used as the per-case name (for the output
  file basenames). When absent, the engine auto-generates names
  like `case_0001`, `case_0002`, &hellip;
- Every other column is a **parameter override** whose name is
  arbitrary; the mapping to a TOML field is decided by the
  `[batch.columns]` section, not by the column name itself.
- All non-`id` cells are parsed as floats.

An example three-case CSV for a mass + Cr sweep:

```
id, mass_kg, Cr
A,  1916.0,  1.3
B,  1916.0,  1.5
C,  2500.0,  1.3
```

Three cases (`A`, `B`, `C`) with two parameters varied.

## The column mapping table

The form's `[batch]` section embeds a table that maps every
non-`id` CSV column to a *target*: a dotted path inside the TOML
schema. When the engine runs case *i*, it reads row *i* of the
CSV, applies each cell to the target the column is mapped to, and
propagates the resulting scenario.

The table updates live whenever you change the **cases_file**
field above it:

- you can browse to a CSV with the **Browse...** button, which
  also re-reads the column list immediately;
- or type the path manually and press **Re-read columns**;
- or load a TOML that already has a `[batch.columns]` block: the
  table populates from the file.

For each row of the table you see three cells:

| CSV column | Target dropdown | Mode dropdown |
|------------|-----------------|---------------|

The **Target** dropdown contains all valid override paths for the
currently-selected object mode (Spacecraft or Debris), pre-filled
with a heuristic when the column name matches the last segment of
a known target (`mass_kg` &rArr; `spacecraft.mass_kg`,
`am_srp` &rArr; `debris.am_srp`). The **Mode** dropdown picks
between the two override semantics described in section 7.4 below.
A column you leave on `(unassigned)` is silently dropped at
**Generate** time &mdash; the engine validates that the table
covers every non-`id` column at run time, so unassigned columns
trigger a clean error.

### Available targets

The list of override paths is the same as the path-style API the
engine accepts for `[batch.columns]`:

- `simulation.et_start_s`, `simulation.duration_s`
- `spacecraft.mass_kg`, `spacecraft.srp.area_m2`,
  `spacecraft.srp.Cr` (only in Spacecraft mode)
- `debris.am_srp`, `debris.Cr` (only in Debris mode)
- `initial_state.position_km[0]`, `[1]`, `[2]`
- `initial_state.velocity_kms[0]`, `[1]`, `[2]`
- `force_model.srp`
- `integrator.rel_tol`, `integrator.h_init_s`,
  `integrator.h_min_s`, `integrator.h_max_s`
- `output.interval_s`

Component access into the vector-of-three fields
(`position_km[0]`) lets you sweep along a single axis without
disturbing the others.

## Override vs delta

The **Mode** dropdown picks between two semantics for the CSV
value:

- **`override`** (default) &mdash; the value in the CSV cell
  *replaces* the field's TOML value. If your TOML has
  `mass_kg = 1916.0` and a row's `mass_kg` column reads `2500.0`,
  the case runs with `mass_kg = 2500.0`.
- **`delta`** &mdash; the cell value is *added* to the field's TOML
  value. The same row with `mass_kg` in delta mode would run with
  `mass_kg = 1916.0 + 2500.0 = 4416.0`. Useful for Monte-Carlo
  perturbations around a nominal case, where the CSV column is
  literally a delta drawn from a distribution.

In delta mode the emitted TOML uses an *inline table* for the
column descriptor:

```toml
[batch.columns]
mass_kg = "spacecraft.mass_kg"                       # override
mass_dm = { target = "spacecraft.mass_kg", mode = "delta" }
```

Mixing both modes across columns is supported. Mixing across
*cases* (i.e. the same column behaving as delta in one row and
override in the next) is not.

## RIC-frame batch input

The propagation engine (`spody.exe`) **only accepts ICRF central-
inertial state**: every cell that ends up overriding
`initial_state.position_km[i]` or `velocity_kms[i]` must be in that
frame. There is no `frame =` knob at the cases-CSV level on the C
side.

The GUI bridges this gap so a user with state measurements in the
**RIC frame of a reference satellite** (the typical "debris cloud
seen from the chaser" use case) does not have to rotate them by
hand. The form has a single cases-CSV path field (always showing
the user-picked source) plus a `cases_frame` combo (`icrf` |
`ric`), and emits three coordinated keys inside `[batch]`:

| TOML key            | Meaning                                                              |
|---------------------|----------------------------------------------------------------------|
| `cases_source_file` | The path the user picked &mdash; the file the form shows.            |
| `cases_frame`       | `"icrf"` (default) or `"ric"`: the frame the source CSV is in.       |
| `cases_file`        | The file `spody.exe` actually reads. See the rule below.             |

The contract between the three keys is:

- **`cases_frame = "icrf"`**: `cases_file` equals
  `cases_source_file` byte for byte. The picked CSV goes straight
  to `spody.exe`.
- **`cases_frame = "ric"`**: at **Generate-TOML** the GUI
  1. reads the column-mapping table to find which CSV columns
     target `initial_state.position_km[i]` /
     `initial_state.velocity_kms[i]`;
  2. computes the rotation
     `R = ric_basis([initial_state].position_km, .velocity_kms)` &mdash;
     the reference orbit comes straight from `[initial_state]`, so
     no extra input is required;
  3. rotates each row's position triplet and velocity triplet by
     `R @ vec` (**pure change of basis** &mdash; the reference
     vector is *not* added to the result);
  4. writes the rotated copy to `<stem>_wrt_icrf.csv` next to the
     source;
  5. emits `cases_file = "<stem>_wrt_icrf.csv"` so `spody.exe` reads
     the rotated copy. `cases_source_file` and `cases_frame` round
     out the triple so the form restores its RIC state on the next
     Load without the user re-picking the source.

`spody.exe` ignores `cases_frame` and `cases_source_file` today &mdash;
its TOML parser only reads the keys it knows about. The two extras
are also a placeholder for a future engine-side RIC handler that
would take the source CSV directly.

### Live rotated preview

When the combo is set to `ric`, a second preview table appears
under the standard cases-CSV preview, showing the first 10 rows
*after* rotation. It refreshes automatically when you change the
source path, the frame combo, or re-read the columns. A **Refresh
preview** button on top of it forces a recompute after edits to
`[initial_state]` or to the column-mapping table.

### Pairing with delta mode

Because the GUI rotates *components without translation*, the
typical RIC workflow pairs each rotated state column with `mode =
"delta"` (see "Override vs delta" above). With the rotated cell
playing the role of an offset and `[initial_state]` playing the
role of the base, the engine computes per case

```
final[i] = initial_state.<vec>[i] + rotated_cell
```

which is the absolute ICRF state to integrate. Override mode is
also accepted (in which case the rotated cell *replaces* the base
value &mdash; useful only if the CSV's row magnitudes are already
on the order of the reference state, which is unusual for sensor-
frame measurements).

### Sensor-frame snapshot convention

The GUI uses a **sensor-frame snapshot** convention for the velocity:
no `omega x r` term is added. The `dv` components are treated as
plain vector projections onto the instantaneous RIC axes &mdash;
exactly what an onboard sensor (radar / lidar / optical relnav)
would report when tracking a relative target. This is **not** the
Hill / Clohessy-Wiltshire rotating-frame convention used in
rendezvous literature; if you have CW residuals from another tool
and want SpOdy to interpret them, transform them before passing
the CSV in.

See `examples/debris_ric_demo/` for a runnable end-to-end example.

## The data preview

Below the mapping table, a small read-only table previews the
first ten data rows of the cases CSV verbatim, with the original
column names (including `id`) as headers. It is purely
informational, but two things make it worth glancing at:

- you can sanity-check that the column-to-target mapping is right
  by comparing each header against the value you see &mdash;
  `mass_kg` values around 1916 should belong to a mass column;
- on long CSVs the row counter `(preview: first 10 of N rows)`
  next to the preview tells you how many total cases the engine
  will see, useful for catching truncated files.

## Threading

The `batch.thread_number` field caps automatically at the host's
logical-CPU count (the form's integer validator enforces this as
you type, with the cap shown next to the field). Setting more
threads than cores never helps and often hurts (oversubscription),
so the form refuses values above the cap rather than letting them
through silently.

A few additional points:

- **Sequential execution** (`thread_number = 1`) works with any
  engine build. If you do not know whether your `spody.exe` was
  built with OpenMP, leave the field at `1` and the batch will
  still run correctly, just one case at a time.
- **Parallel execution** (`thread_number > 1`) requires an OpenMP-
  enabled engine. The standard SpOdy bundle ships with one such
  build. The engine rejects the run with a clean message if the
  build does not support parallel execution.
- **Determinism**: the engine processes cases in a fixed order
  regardless of thread count, so the per-case output is bit-
  identical across thread counts.

## Output layout

When the batch runs, the engine creates a per-run subfolder named
after the UTC instant the run started (ISO 8601 compact, e.g.
`2026-06-05T195819Z`) inside `batch.output_dir`, snapshots the
source TOML inside as `input.toml`, and writes one set of files
per case alongside it:

```
<batch.output_dir>/<UTC-ISO8601>/
  input.toml                                       (snapshot of the source)
  <name>_<case_id>_state_icrf.csv                  (if csv_file is set)
  <name>_<case_id>_state_icrf.bin                  (if bin_file is set)
  <name>_<case_id>_acc_icrf.bin                    (if accelerations_file is set)
  <name>_events.bin                                (if events_log is set; aggregated)
```

The `<name>` part comes from `batch.name`; the `<case_id>` part is
the value of the `id` column or the auto-generated `case_NNNN`
name. Each invocation goes in its own timestamp folder, so the
results of any prior run are never overwritten silently &mdash; the
listing under `<batch.output_dir>/` is the chronological history
of every batch run executed from that scenario.

> The events file is **aggregated**: a single binary covers every
> trigger across the whole batch. Each row carries an extra
> `case_idx` (int32) field at the front so post-processing can join
> with the cases CSV. The file's 8-byte magic is `SPDYEVTB`
> (versus `SPDYEVT_` for the per-run propagate output). The Python
> readers in `python/spody_io/events.py` auto-detect the format
> from the magic and return the matching numpy dtype.

## A complete worked example

The `examples/batch_demo/` scenario shipped with SpOdy is a
realistic, runnable batch. Open `examples/batch_demo/input.toml`
in the Run tab to see the form populated:

- a single `[simulation]` block;
- `[spacecraft]` with a nominal `mass_kg = 1916.0`;
- `[spacecraft.srp]` enabled with a nominal `Cr = 1.3` and
  `area_m2 = 15.0`;
- `[batch]` enabled with `cases_file = "cases.csv"`, `name =
  "mass_srp_sweep"`, and a column mapping that overrides
  `spacecraft.mass_kg` and `spacecraft.srp.Cr`.

The CSV at `examples/batch_demo/cases.csv` lists three cases
(`A`, `B`, `C`) varying mass and reflectivity, exactly as in
section 7.1. Press **RUN** to dispatch all three; the Run tab's
terminal pane streams the case-by-case progress, and the resulting
files land in `examples/batch_demo/output/<UTC-ISO8601>/`. Switch
to the Analysis tab afterwards: the working directory has auto-
pointed at `examples/batch_demo/output/`, the per-run folder
appears as a group in the file tree, all three trajectories are
inside it, and you can `Ctrl`-click them all and press **Overlay
selected** on a single-line plot (such as **Radial distance |r|**)
to compare the cases visually.
