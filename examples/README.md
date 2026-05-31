# Examples

TOML inputs that drive `spody`. Each subdirectory is one scenario; this
top-level guide documents the input schema so you can write your own
without copying-and-tweaking from an existing one.

## Scenarios in this directory

| Directory       | Mode    | Purpose                                              |
|-----------------|---------|------------------------------------------------------|
| [`lro_6day/`](lro_6day/)     | propagate | NASA LRO 6-day reference -- the validation scenario   |
| [`batch_demo/`](batch_demo/) | batch     | Smoke test: 3-case mass + SRP sweep over 1 hour       |

---

## TOML input schema

A SpOdy input is a TOML file with the eight base sections below, plus an
optional `[batch]` section that turns it into a multi-case input.

All file paths are resolved **relative to the TOML file's directory**.

### `[simulation]` -- the run as a whole

| Key            | Type    | Notes                                                  |
|----------------|---------|--------------------------------------------------------|
| `name`         | string  | human-readable identifier (used in logs)               |
| `et_start_s`   | float   | start epoch as **ET seconds past J2000 TDB**           |
| `duration_s`   | float   | propagation length, must be > 0                        |

```toml
[simulation]
name        = "lro_6day"
et_start_s  = 3.065472661824111e+08   # 2009-09-18 12:00 UTC TDB
duration_s  = 5.184e+05               # 6 days
```

### `[spacecraft]` and optional `[spacecraft.srp]`

| Key                    | Type   | Notes                                          |
|------------------------|--------|------------------------------------------------|
| `spacecraft.mass_kg`   | float  | dry mass, must be > 0                          |
| `spacecraft.srp.area_m2` | float | SRP cross-section [m²]; A/m derived as `area_m2 / mass_kg` |
| `spacecraft.srp.am_srp`  | float | A/m directly [m²/kg]; alternative to `area_m2` |
| `spacecraft.srp.Cr`    | float  | reflectivity coefficient (1 = absorb, 2 = mirror) |

The `[spacecraft.srp]` table is **required** when `force_model.srp = true`
and **optional** otherwise. SRP only depends on the area-to-mass ratio, so
supply **exactly one** of `area_m2` or `am_srp` (giving both, or neither, is
an error). An `am_srp` value is stored internally as the equivalent area
(`am_srp * mass_kg`).

> **Batch note.** `am_srp` is a single-input convenience for `[spacecraft]`;
> it is **not** a valid `[batch.columns]` target — only `area_m2` is. If you
> batch `mass_kg`, the effective A/m varies across cases (area stays fixed).
> A proper A/m-native batch workflow (sample over A/m, no nominal mass) will
> land with the future debris propagation mode.

```toml
[spacecraft]
mass_kg = 1916.0

  [spacecraft.srp]
  area_m2 = 20.0    # or, equivalently: am_srp = 0.010438
  Cr      = 1.3
```

### `[initial_state]`

Cartesian state at `et_start_s`, in the inertial frame of the central body
(ICRF-aligned for v0).

| Key            | Type        | Notes                              |
|----------------|-------------|------------------------------------|
| `frame`        | string      | `"central_inertial"` (only option) |
| `position_km`  | float[3]    | r, must satisfy `\|r\| > 1e-3` km   |
| `velocity_kms` | float[3]    | v, must satisfy `\|v\| > 1e-12` km/s |

```toml
[initial_state]
frame        = "central_inertial"
position_km  = [ 1622.030,  512.085, -529.343]
velocity_kms = [    0.649,   -0.519,    1.440]
```

### `[force_model]`

Which perturbations are active. v0 supports the Moon as central body.

| Key                | Type           | Notes                                              |
|--------------------|----------------|----------------------------------------------------|
| `central_body`     | string         | `"Moon"` (only option in v0)                       |
| `harmonics_file`   | string (path)  | spherical-harmonics coefficient file (GRGM1200B)   |
| `harmonics_degree` | int            | truncation degree, >= 2 and <= file maximum        |
| `third_bodies`     | string[]       | list of NAIF names (see below)                     |
| `srp`              | bool           | enable cannonball SRP (requires `[spacecraft.srp]`)|

Known `third_bodies` names: `Sun`, `Mercury`, `Venus`, `Earth`, `Moon`,
`Mars`, `Jupiter`, `Saturn`, `Uranus`, `Neptune`. The central body cannot
appear in the list.

```toml
[force_model]
central_body     = "Moon"
harmonics_file   = "../../external/spody-core/raw_data/GRGM1200B/gggrx_1200b_sha.tab"
harmonics_degree = 80
third_bodies     = ["Earth", "Sun"]
srp              = false
```

### `[ephemeris]`

| Key   | Type          | Notes                                                |
|-------|---------------|------------------------------------------------------|
| `file`| string (path) | DE440 binary in `.spody` format (see raw_data/README) |

```toml
[ephemeris]
file = "../../external/spody-core/raw_data/DE440/de440.spody"
```

### `[integrator]`

v0 supports only RKDP45 (adaptive Dormand-Prince 5(4)).

| Key        | Type   | Notes                                            |
|------------|--------|--------------------------------------------------|
| `type`     | string | `"rkdp45"` (only option)                         |
| `rel_tol`  | float  | relative tolerance, > 0                          |
| `h_init_s` | float  | initial step, in `[h_min, h_max]`                |
| `h_min_s`  | float  | min step, > 0                                    |
| `h_max_s`  | float  | max step, > `h_min_s`                            |

```toml
[integrator]
type     = "rkdp45"
rel_tol  = 1.0e-11
h_init_s = 60.0
h_min_s  = 1.0e-5
h_max_s  = 2700.0
```

### `[output]`

What to write and how often.

| Key                  | Type           | Notes                                                |
|----------------------|----------------|------------------------------------------------------|
| `mode`               | string         | `"fixed"` (uniform grid) or `"step"` (one per integrator step) |
| `interval_s`         | float          | sampling cadence; required when `mode = "fixed"`     |
| `csv_file`           | string (path)  | optional; **presence enables CSV trajectory**        |
| `bin_file`           | string (path)  | optional; **presence enables binary trajectory**     |
| `log_file`           | string (path)  | optional; **presence enables stdout/stderr tee**     |
| `accelerations_file` | string (path)  | optional; **presence enables per-force breakdown** (binary) |
| `events_log`         | string (path)  | optional; **presence enables event-trigger log** (binary)   |

Omitting all `*_file` keys is allowed -- the propagation runs and
prints only the final state on stdout. Useful for benchmarking or
sanity-checking the config.

`log_file` is timestamped at run-time: a TOML value of `run.log` becomes
`run_2026-05-19T143022Z.log` so each invocation has a unique file.

The **accelerations file** stores a `ForceBreakdown` struct per
output sample (see `spody-core/include/spody_forcemodels.h`): total
acceleration plus the per-force decomposition (2-body, spherical
harmonics, third-body total and per-body, SRP, drag) and the eclipse
fraction. Cadence matches the trajectory: per accepted step in `step`
mode, per grid point in `fixed` mode (one extra RHS evaluation per
grid sample -- typical overhead ~3% at 1-minute cadence on LRO).

The **events log** stores `EventRecord` entries (see
`spody-core/include/spody_events.h`): `t`, `kind`, `naif_id`,
`radius_km`, `distance_km`, and the state `(r, v)` at the trigger. The
file holds the triggers of every configured event (see [`[events]`](#events)
below); writing it is enabled by setting `events_log`. Localisation
uses cubic Hermite + Brent root-finding, precision sub-millisecond on a
30 s step.

```toml
[output]
mode               = "fixed"
interval_s         = 60.0
csv_file           = "output/lro_6day.csv"
bin_file           = "output/lro_6day.bin"
# log_file           = "output/lro_6day.log"
# accelerations_file = "output/lro_6day_acc.bin"
# events_log         = "output/lro_6day_events.bin"
```

### `[events]`

Optional. Configures the orbital events checked after every accepted
step. **IMPACT is always on and needs no configuration** -- the runtime
checks the satellite against the central body and every third body, and
stops the propagation at the first impact. The `[events]` section only
adds the opt-in **eclipse** detection.

| Key                 | Type  | Notes                                                       |
|---------------------|-------|-------------------------------------------------------------|
| `eclipse_threshold` | float | enables eclipse events; sun-lit fraction in `[0, 1]` whose crossing fires the event |

The occulting body is the central body. The eclipse fraction is the
Montenbruck & Gill sun-lit fraction (`1.0` = full sun, `0.0` = full
umbra). The event fires on **every** crossing of the threshold -- both
entering and leaving shadow -- and only logs (it does not stop the run).
Typical thresholds:

- `1.0` -> any loss of sunlight (penumbra entry / exit)
- `0.5` -> middle of the penumbra
- `0.0` -> full-umbra entry / exit

```toml
[events]
eclipse_threshold = 0.5
```

Each eclipse trigger writes an `EventRecord` with `kind = 1`,
`naif_id` = the occulter, and `distance_km` repurposed to hold the
eclipse fraction at the trigger (which equals `eclipse_threshold` up to
the root-finder tolerance). Requires `events_log` to be set in
`[output]` for the triggers to be recorded.

---

## Batch mode

Add an optional `[batch]` section and the same file is read by
`spody batch` as a multi-case input. The top-level sections become the
**base scenario**; each row of `cases_file` is one **case** that
overrides specific numeric fields.

### `[batch]`

| Key            | Type           | Notes                                                       |
|----------------|----------------|-------------------------------------------------------------|
| `name`         | string         | batch identifier, used in output file names                 |
| `output_dir`   | string (path)  | must exist; `batch/` is auto-created inside it              |
| `thread_number`| int            | 1 today (parallel batch reserved for a future OpenMP build) |
| `cases_file`   | string (path)  | `.csv` (today) or `.spody` (reserved)                       |

```toml
[batch]
name          = "mass_srp_sweep"
output_dir    = "output"
thread_number = 1
cases_file    = "cases.csv"
```

### `[batch.columns]`

Maps each numeric column of `cases_file` to a field of the base config.
Required if `[batch]` is present. Two forms per column:

- **plain string** -> *override*: the cell value replaces the base
  value (`out = cell`).
- **inline table** `{ target = "...", mode = "delta" }` -> *delta*: the
  cell value is added to the base value (`out = base + cell`,
  additive). `mode = "override"` is also accepted and is the default,
  so the string form and the table form with `mode = "override"` are
  equivalent.

```toml
[batch.columns]
mass_kg = "spacecraft.mass_kg"                                      # override
Cr      = "spacecraft.srp.Cr"                                       # override
dx      = { target = "initial_state.position_km[0]", mode = "delta" }  # base + cell
```

Delta columns are meant for perturbations around a nominal scenario
(e.g. dispersing the initial state). Because a delta is an offset, its
cell may be negative; **delta cells are not range-checked** (only the
finiteness guard applies). Override cells keep their normal per-field
validation (see [Per-case validation](#per-case-validation)).

**Targetable paths** (numeric, per-case):

- `simulation.et_start_s` / `simulation.duration_s`
- `spacecraft.mass_kg`
- `spacecraft.srp.area_m2` / `spacecraft.srp.Cr`
- `initial_state.position_km[0..2]`
- `initial_state.velocity_kms[0..2]`
- `force_model.srp` (0 or 1)
- `integrator.rel_tol`, `h_init_s`, `h_min_s`, `h_max_s`
- `output.interval_s`

**Not overridable** (these belong to the shared part loaded once):
`force_model.central_body`, `force_model.harmonics_file`,
`force_model.harmonics_degree`, `ephemeris.file`. The parser rejects
mappings that target these.

### Cases file (CSV)

Comma-separated, optional comments with `#`, header row, then one row
per case. An optional column named `id` provides explicit case names; if
absent, ids are auto-generated as 1-based zero-padded indices.

```csv
# Mass + SRP sweep
id, mass_kg,  Cr
A,  1916.0,   1.3
B,  1916.0,   1.5
C,  2500.0,   1.3
```

Output for each case is written as
`<output_dir>/batch/<batch.name>_<id>.<csv|bin>`.

### Per-case validation

The validator checks every **override** CSV cell against a per-field
rule (delta cells are exempt -- see [`[batch.columns]`](#batchcolumns)):

| Path                                | Rule        |
|-------------------------------------|-------------|
| `*.mass_kg`, `*.area_m2`, `duration_s`, tolerances, step bounds, interval | must be `> 0` |
| `*.Cr`                              | must be `>= 0` |
| `force_model.srp`                   | must be `0` or `1` |
| `*.position_km[i]`, `*.velocity_kms[i]`, `et_start_s` | any finite double |

Every cell (override or delta) must be a finite number. Errors are
reported with the case id, the dotted path, and the value:

```
error: input.toml: batch case 'B': spacecraft.mass_kg must be > 0 (got -100)
```

---

## Running

From the repo root:

```sh
# Validate the schema without running anything.
./build/spody validate examples/lro_6day/input.toml

# Single propagation.
./build/spody propagate examples/lro_6day/input.toml

# Redirect output (CSV / binary / log) to a custom directory.
./build/spody propagate examples/lro_6day/input.toml --out /tmp/lro_run

# Multi-case run (requires [batch] in the TOML).
./build/spody batch examples/batch_demo/input.toml
```

On Windows the binary is `build\Release\spody.exe`.

---

## Tips for writing good TOML inputs

- **Comment liberally.** TOML supports `#` line comments; the parser
  ignores them. Future-you will thank you for noting *why* you chose
  `harmonics_degree = 80` instead of `200`.
- **Use scientific notation for large numbers.** `5.184e+05` is more
  readable than `518400.0`.
- **Pin file paths relative to the TOML.** Avoid absolute paths so the
  TOML is portable across machines. The parser resolves them against
  the TOML's directory.
- **Validate before running long simulations.**
  `spody validate input.toml` is fast and catches schema mistakes,
  bad ranges, missing files, and (for batch) bad CSV values *before*
  loading the gigabyte-scale ephemeris and harmonics files.
- **Test single first, then batch.** A scenario that works with
  `spody propagate` is a good base for batch: add `[batch]` +
  `[batch.columns]` + a CSV and you're done. Comment out `[batch]` to
  switch back.
- **Keep `[batch.columns]` paths consistent with the CSV header**:
  the parser rejects orphan mappings and unmapped columns.
- **Use `id` in the CSV** when your cases have semantically meaningful
  names (debris fragment ids, NORAD ids, scenario tags). Otherwise let
  the parser auto-number.
