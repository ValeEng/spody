# Examples

TOML inputs that drive `spody`. Each subdirectory is one scenario; this
top-level guide documents the input schema so you can write your own
without copying-and-tweaking from an existing one.

## Scenarios in this directory

| Directory       | Mode    | Purpose                                              |
|-----------------|---------|------------------------------------------------------|
| [`lro_6day/`](lro_6day/)                         | propagate | NASA LRO 6-day reference -- the Moon validation scenario |
| [`gps_g11_validation/`](gps_g11_validation/)     | propagate | Earth HF vs IGS SP3 precise orbits (GPS G11, 7 days)    |
| [`glonass_r03_validation/`](glonass_r03_validation/) | propagate | Earth HF vs MGEX SP3 (GLONASS R03, 7 days)          |
| [`iss_drag_calibration/`](iss_drag_calibration/) | propagate + calibrate | ISS 15-day drag bench vs the NASA/JSC OEM: `convert oem` -> `spody calibrate` -> propagate with the fitted k(t) |
| [`cr3bp_em_l4/`](cr3bp_em_l4/)                   | propagate | CR3BP Earth-Moon L4 30-day stability smoke test         |
| [`batch_demo/`](batch_demo/)                     | batch     | Smoke test: 3-case mass + SRP sweep over 1 hour         |
| [`debris_demo/`](debris_demo/)                   | batch     | Debris-mode A/m sweep -- 3 cases, 1 hour                |
| [`debris_ric_demo/`](debris_ric_demo/)           | batch     | Rotating-frame batch input, the GUI pre-rotates the CSV |
| [`debris_impact_demo/`](debris_impact_demo/)     | batch     | 10 cases with guaranteed surface crossings (impact views) |

---

## TOML input schema

All file paths are resolved **relative to the TOML file's directory**.

### Which sections apply

`simulation.dynamics_model` selects the propagator, and with it which
sections the parser expects. Everything else is shared.

| Section            | `high_fidelity` (default) | `cr3bp` |
|--------------------|---------------------------|---------|
| `[simulation]`     | required                  | required |
| `[initial_state]`  | required                  | required |
| `[spacecraft]` XOR `[debris]` | required       | not read |
| `[force_model]`    | required                  | not read |
| `[ephemeris]`      | required                  | not read |
| `[cr3bp]`          | not read                  | required |
| `[integrator]`     | required                  | required |
| `[output]`         | required                  | required |
| `[events]`         | optional                  | optional |
| `[batch]`          | optional                  | optional |

CR3BP is autonomous: no ephemeris, no gravity field, no spacecraft
properties -- the trajectory depends only on the two primaries and the
initial state.

### `[simulation]` -- the run as a whole

| Key              | Type    | Notes                                                  |
|------------------|---------|--------------------------------------------------------|
| `name`           | string  | human-readable identifier (used in logs and file names) |
| `dynamics_model` | string  | optional, `"high_fidelity"` (default) or `"cr3bp"`     |
| `et_start_s`     | float   | start epoch as **ET seconds past J2000 TDB**; required under `high_fidelity`, ignored under `cr3bp` |
| `duration_s`     | float   | propagation length, must be > 0                        |

```toml
[simulation]
name        = "lro_6day"
et_start_s  = 3.065472661824111e+08   # 2009-09-18 12:00 UTC TDB
duration_s  = 5.184e+05               # 6 days
```

### Object: `[spacecraft]` or `[debris]` (exactly one, `high_fidelity` only)

The TOML must define the propagated object via **either** `[spacecraft]`
(named vehicle, mass + area known) **or** `[debris]` (A/m-driven fragment,
mass irrelevant). Having both, or neither, is a parse error.

#### `[spacecraft]` + optional `[spacecraft.srp]` / `[spacecraft.drag]`

| Key                      | Type   | Notes                                          |
|--------------------------|--------|------------------------------------------------|
| `spacecraft.mass_kg`     | float  | dry mass, must be > 0                          |
| `spacecraft.srp.area_m2` | float  | SRP cross-section [m²]; A/m derived as `area_m2 / mass_kg` |
| `spacecraft.srp.am_srp`  | float  | A/m directly [m²/kg]; alternative to `area_m2`  |
| `spacecraft.srp.Cr`      | float  | reflectivity coefficient (1 = absorb, 2 = mirror) |
| `spacecraft.drag.area_m2`| float  | drag cross-section [m²]                        |
| `spacecraft.drag.am_drag`| float  | A/m directly [m²/kg]; alternative to `area_m2`  |
| `spacecraft.drag.Cd`     | float  | drag coefficient, must be > 0                  |

The `[spacecraft.srp]` table is **required** when `force_model.srp = true`
and **optional** otherwise; likewise `[spacecraft.drag]` when
`force_model.drag = true`. Both forces only depend on the area-to-mass
ratio, so each table takes **exactly one** of `area_m2` or its `am_*`
twin (both, or neither, is an error). An `am_*` value is stored
internally as the equivalent area (`am * mass_kg`).

> **Batch note.** `am_srp` / `am_drag` here are single-input conveniences
> for `[spacecraft]`; they are **not** valid `[batch.columns]` targets --
> only `area_m2` is. If you batch `mass_kg`, the effective A/m varies
> across cases (area stays fixed). For native A/m sweeps use `[debris]`.

```toml
[spacecraft]
mass_kg = 1916.0

  [spacecraft.srp]
  area_m2 = 20.0    # or, equivalently: am_srp = 0.010438
  Cr      = 1.3

  [spacecraft.drag]
  area_m2 = 20.0    # or, equivalently: am_drag = 0.010438
  Cd      = 2.2
```

#### `[debris]`

For workflows where A/m is the natural primary parameter (debris
fragments, non-cooperative objects). Mass is not part of the schema --
the parser pins it to `1.0` internally so `srp_area_m2` numerically
equals `am_srp`, and the rest of the pipeline runs unchanged.

| Key              | Type  | Notes                                              |
|------------------|-------|----------------------------------------------------|
| `debris.am_srp`  | float | SRP area-to-mass ratio [m²/kg], must be > 0        |
| `debris.Cr`      | float | reflectivity coefficient (only used if SRP is on)  |
| `debris.am_drag` | float | drag area-to-mass ratio [m²/kg]; both-or-neither with `Cd` |
| `debris.Cd`      | float | drag coefficient; both-or-neither with `am_drag`   |

`force_model.srp` is **not** forced to true in debris mode -- you can run
a purely gravitational propagation of a debris fragment and `am_srp` /
`Cr` simply go unused.

```toml
[debris]
am_srp = 0.02       # m²/kg
Cr     = 1.3
```

In batch mode the natural targets are `debris.am_srp` / `debris.Cr` /
`debris.am_drag` / `debris.Cd`; cross-mode targets are rejected at parse
(`spacecraft.*` paths in a `[debris]` file, and vice versa).

```toml
[batch.columns]
am_srp = "debris.am_srp"
Cr     = "debris.Cr"
```

### `[initial_state]`

State at `et_start_s`, in the frame named by `frame`. The engine rotates
whatever you give it into the frame the propagator integrates in
(ICRF-aligned central inertial for `high_fidelity`, synodic for `cr3bp`)
at simulation setup.

| Key     | Type   | Notes                                              |
|---------|--------|----------------------------------------------------|
| `frame` | string | see the frame table below                          |
| `kind`  | string | optional, `"cartesian"` (default) or `"keplerian"` |

| `frame` value          | Meaning                                                        |
|------------------------|----------------------------------------------------------------|
| `"central_inertial"`   | central-body inertial, ICRF-aligned                            |
| `"central_body_fixed"` | central-body-fixed basis at the run epoch (ITRS for Earth, PA for the Moon) |
| `"orbit_plane"`        | Ely's orbit-plane frame -- the basis lunar frozen orbits (ELFO) are published in, which is **not** the lunar equator |
| `"synodic_rotating"`   | CR3BP synodic rotating frame                                   |

#### `kind = "cartesian"` (default)

| Key            | Type        | Notes                              |
|----------------|-------------|------------------------------------|
| `position_km`  | float[3]    | r, must satisfy `\|r\| > 1e-3` km   |
| `velocity_kms` | float[3]    | v, must satisfy `\|v\| > 1e-12` km/s |

```toml
[initial_state]
frame        = "central_inertial"
position_km  = [ 1622.030,  512.085, -529.343]
velocity_kms = [    0.649,   -0.519,    1.440]
```

#### `kind = "keplerian"`

Six classical elements, converted to Cartesian by the engine at parse
time (so every downstream consumer sees a Cartesian state).

| Key                  | Type   | Notes                                             |
|----------------------|--------|---------------------------------------------------|
| `semi_major_axis_km` | float  | a                                                 |
| `eccentricity`       | float  | e                                                 |
| `inclination_deg`    | float  | i                                                 |
| `raan_deg`           | float  | right ascension of the ascending node             |
| `arg_periapsis_deg`  | float  | argument of periapsis                             |
| `anomaly_deg`        | float  | true or mean anomaly, per `anomaly_type`          |
| `anomaly_type`       | string | `"true"` or `"mean"`                              |
| `reference_body`     | string | optional; `"central"` (default, HF) or `"primary_1"` / `"primary_2"` (required under `cr3bp`) |

Under `cr3bp` the elements are read in the chosen primary's local
inertial frame and chained through the synodic transform, so a catalog
orbit around either primary can seed a CR3BP run directly.

```toml
[initial_state]
frame              = "central_inertial"
kind               = "keplerian"
semi_major_axis_km = 1861.0
eccentricity       = 0.0012
inclination_deg    = 89.7
raan_deg           = 45.0
arg_periapsis_deg  = 90.0
anomaly_deg        = 0.0
anomaly_type       = "true"
```

### `[force_model]` (`high_fidelity` only)

Which perturbations are active, and the assets they need.

| Key                  | Type          | Notes                                              |
|----------------------|---------------|----------------------------------------------------|
| `central_body`       | string        | `"Moon"` or `"Earth"`                              |
| `harmonics_file`     | string (path) | spherical-harmonics coefficients (GRGM1200B for the Moon, EIGEN-6C4 for the Earth); required unless `harmonics_degree = 0` |
| `harmonics_degree`   | int           | truncation degree; `0` switches the gravity field off entirely (point mass), otherwise >= 2 and <= file maximum |
| `harmonics_adaptive` | bool          | optional, default `false`; re-picks the degree per step from the orbit radius, with `harmonics_degree` as the ceiling |
| `third_bodies`       | string[]      | list of NAIF names (see below)                     |
| `srp`                | bool          | cannonball SRP (requires an SRP block on the object) |
| `drag`               | bool          | optional, default `false`; NRLMSISE-00 drag, Earth only (requires a drag block + `space_weather_file`) |
| `eop_file`           | string (path) | IERS EOP (`finals2000A.all`); required for `central_body = "Earth"` |
| `iau2006_dir`        | string (path) | IAU 2006/2000A_R06 tables; required for `central_body = "Earth"` |
| `space_weather_file` | string (path) | CelesTrak `SW-All.csv`; required when `drag = true` |
| `density_scale`      | float         | optional constant density multiplier, > 0          |
| `density_scale_file` | string (path) | optional `k(t)` node table from `spody calibrate`; mutually exclusive with `density_scale` |

Known `third_bodies` names: `Sun`, `Mercury`, `Venus`, `Earth`, `Moon`,
`Mars`, `Jupiter`, `Saturn`, `Uranus`, `Neptune`. The central body cannot
appear in the list. Every third body also casts a shadow: overlapping
shadows are combined by inclusion-exclusion, so the Earth darkens a lunar
orbiter.

**Adaptive harmonics.** The degree-*n* term of the potential carries
`(R_ref / r)^n`, so requiring it below a relative threshold gives the
degree directly as `N(r) = ln(1/eps) / ln(r / R_ref)`. Only the ratio
enters -- there is nothing to calibrate per body or per model. The
threshold sits below double-precision resolution, so the output is
bit-identical to the fixed-degree run; the win is wall time on eccentric
orbits (2.3x on a 365-day lunar ELFO at degree 80, 3.7x on a 7-day GPS
arc at degree 70).

```toml
[force_model]
central_body     = "Moon"
harmonics_file   = "../../external/spody-core/raw_data/GRGM1200B/gggrx_1200b_sha.tab"
harmonics_degree = 80
third_bodies     = ["Earth", "Sun"]
srp              = false
```

An Earth run with drag needs the full asset set:

```toml
[force_model]
central_body       = "Earth"
harmonics_file     = "../../data/EIGEN-6C4/eigen-6c4.tab"
harmonics_degree   = 70
eop_file           = "../../data/eop/finals2000A.all"
iau2006_dir        = "../../data/iau2006"
space_weather_file = "../../data/spaceweather/SW-All.csv"
third_bodies       = ["Moon", "Sun"]
srp                = false
drag               = true
```

### `[cr3bp]` (`cr3bp` only)

The two primaries, by name. Their GMs come from the shared body table;
the primary-primary separation `L` is looked up in a curated pair table
(today: Earth-Moon only). Order matters -- `primary_1` is the heavier
body.

| Key         | Type   | Notes                                    |
|-------------|--------|------------------------------------------|
| `primary_1` | string | heavier primary (e.g. `"Earth"`)         |
| `primary_2` | string | lighter primary (e.g. `"Moon"`)          |

```toml
[cr3bp]
primary_1 = "Earth"
primary_2 = "Moon"
```

### `[ephemeris]` (`high_fidelity` only)

| Key   | Type          | Notes                                                |
|-------|---------------|------------------------------------------------------|
| `file`| string (path) | DE440 binary in `.spody` format (see raw_data/README) |

```toml
[ephemeris]
file = "../../external/spody-core/raw_data/DE440/de440.spody"
```

### `[integrator]`

RKDP45 (adaptive Dormand-Prince 5(4)) is the only type today.

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
| `output_dir`         | string (path)  | parent of the per-run timestamp folder               |
| `csv_file`           | string (path)  | optional; **presence enables CSV trajectory**        |
| `bin_file`           | string (path)  | optional; **presence enables binary trajectory**     |
| `log_file`           | string (path)  | optional; **presence enables stdout/stderr tee**     |
| `accelerations_file` | string (path)  | optional; **presence enables per-force breakdown** (binary; `high_fidelity` only) |
| `events_log`         | string (path)  | optional; **presence enables event-trigger log** (binary)   |

Omitting all `*_file` keys is allowed -- the propagation runs and
prints only the final state on stdout. Useful for benchmarking or
sanity-checking the config.

**Run folder.** Each invocation creates
`<output_dir>/<UTC-ISO8601>/` and writes everything inside it,
**prefixed with that same timestamp**: a snapshot of the source TOML as
`<ts>_input.toml`, then `<ts>_<basename>` for each configured stream. So
a `bin_file = "output/lro_6day_state_icrf.bin"` lands as
`output/2026-08-06T101530Z/2026-08-06T101530Z_lro_6day_state_icrf.bin`.
The prefix is what keeps a snapshot from ever colliding with the file it
was copied from.

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
output_dir         = "output"
csv_file           = "output/lro_6day_state_icrf.csv"
bin_file           = "output/lro_6day_state_icrf.bin"
# log_file           = "output/lro_6day.log"
# accelerations_file = "output/lro_6day_acc_icrf.bin"
# events_log         = "output/lro_6day_events.bin"
```

The five stream paths follow the `<sim_name>_<subject>_<frame>`
convention the GUI auto-generates when you tick the corresponding
checkbox in the form -- `_state_icrf` for the trajectory, `_acc_icrf`
for the acceleration breakdown, plain `_events` / `.log` for the rest. A
CLI-only user is free to pick any path; the convention only matters for
the round-trip with the GUI form.

### `[events]`

Optional. Configures the orbital events checked after every accepted
step. **IMPACT is always on and needs no configuration** -- the runtime
checks the satellite against the central body and every third body, and
stops the propagation at the first impact. The section adds the two
opt-in kinds below; an absent `[events]` leaves only IMPACT running.

#### Eclipse

| Key                 | Type  | Notes                                                       |
|---------------------|-------|-------------------------------------------------------------|
| `eclipse_threshold` | float | enables eclipse events; sun-lit fraction in `[0, 1]` whose crossing fires the event |

The eclipse fraction is the Montenbruck & Gill sun-lit fraction (`1.0` =
full sun, `0.0` = full umbra), computed against every occulting body.
The event fires on **every** crossing of the threshold -- both entering
and leaving shadow -- and only logs (it does not stop the run). Typical
thresholds:

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
the root-finder tolerance).

#### Altitude crossings

An array-of-tables: one entry per altitude shell you want monitored.
Both the ascending and the descending crossing fire.

| Key           | Type   | Notes                                                        |
|---------------|--------|--------------------------------------------------------------|
| `body`        | string | the body the altitude is measured above (the central body, or a third body in the list) |
| `altitude_km` | float  | shell altitude above the body's radius, must be > 0          |
| `action`      | string | optional, `"log"` (default), `"stop"`, or `"log_and_stop"`   |
| `refined`     | bool   | optional, default `true`; `false` skips Brent refinement and reports the trigger at step precision |

```toml
[events]
eclipse_threshold = 0.5

  [[events.altitude_crossing]]
  body        = "Moon"
  altitude_km = 100.0

  [[events.altitude_crossing]]
  body        = "Moon"
  altitude_km = 50.0
  action      = "log_and_stop"
```

Both event kinds require `events_log` in `[output]` for the triggers to
be recorded. The Analysis tab's altitude-band occupancy views read
exactly these records.

---

## Batch mode

Add an optional `[batch]` section and the same file is read by
`spody batch` as a multi-case input. The top-level sections become the
**base scenario**; each row of `cases_file` is one **case** that
overrides specific numeric fields. Batch works under both dynamics
models.

### `[batch]`

| Key                 | Type           | Notes                                                       |
|---------------------|----------------|-------------------------------------------------------------|
| `name`              | string         | batch identifier, used in output file names                 |
| `output_dir`        | string (path)  | must exist; a per-run `<UTC-ISO8601>/` subfolder is created inside it |
| `thread_number`     | int            | worker threads; `> 1` runs the cases in parallel (OpenMP)   |
| `cases_file`        | string (path)  | `.csv` (today) or `.spody` (reserved); what `spody.exe` actually reads |
| `cases_frame`       | string         | GUI-only; the frame the *source* CSV is written in          |
| `cases_source_file` | string (path)  | GUI-only; the file you picked before any rotation           |

```toml
[batch]
name          = "mass_srp_sweep"
output_dir    = "output"
thread_number = 4
cases_file    = "cases.csv"
```

`spody.exe` ignores `cases_frame` and `cases_source_file` entirely --
they exist so the GUI form can round-trip its rotating-frame state (see
[Rotating-frame cases](#rotating-frame-cases) below).

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

A column you want carried through the CSV without spody touching
anything (a fragment tag, a NORAD id) maps to the empty-string sentinel
`target = ""`.

```toml
[batch.columns]
mass_kg = "spacecraft.mass_kg"                                      # override
Cr      = "spacecraft.srp.Cr"                                       # override
dx      = { target = "initial_state.position_km[0]", mode = "delta" }  # base + cell
note    = ""                                                        # metadata, ignored
```

Delta columns are meant for perturbations around a nominal scenario
(e.g. dispersing the initial state). Because a delta is an offset, its
cell may be negative; **delta cells are not range-checked** (only the
finiteness guard applies). Override cells keep their normal per-field
validation (see [Per-case validation](#per-case-validation)).

**Targetable paths** (numeric, per-case):

- `simulation.et_start_s` / `simulation.duration_s`
- `spacecraft.mass_kg` *(spacecraft mode only)*
- `spacecraft.srp.area_m2` / `spacecraft.srp.Cr` *(spacecraft mode only)*
- `spacecraft.drag.area_m2` / `spacecraft.drag.Cd` *(spacecraft mode only)*
- `debris.am_srp` / `debris.Cr` *(debris mode only)*
- `debris.am_drag` / `debris.Cd` *(debris mode only)*
- `initial_state.position_km[0..2]`
- `initial_state.velocity_kms[0..2]`
- `force_model.srp` (0 or 1)
- `force_model.drag` (0 or 1)
- `force_model.density_scale`
- `integrator.rel_tol`, `h_init_s`, `h_min_s`, `h_max_s`
- `output.interval_s`

Mode-tagged paths are cross-validated at parse: a `[debris]` file cannot
target `spacecraft.*`, and a `[spacecraft]` file cannot target `debris.*`.

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

Output for each case is written inside the per-run timestamp folder
`spody.exe` creates at launch, every file carrying that timestamp as a
prefix:

```
<output_dir>/<ts>/<ts>_input.toml                          snapshot of the source TOML
<output_dir>/<ts>/<ts>_<batch.name>_<id>_state_icrf.csv    per-case trajectory
<output_dir>/<ts>/<ts>_<batch.name>_<id>_state_icrf.bin
<output_dir>/<ts>/<ts>_<batch.name>_events.bin             batch-wide aggregated events
```

The aggregated events file (magic `SPDYEVTB`) carries a `case_idx` per
record, so one file holds the triggers of the whole batch instead of one
file per case.

### Rotating-frame cases

`spody.exe` only reads case CSVs whose state columns are already in the
frame it integrates in. The GUI can accept them in a local orbital frame
instead and rotate at Generate-TOML:

| `cases_frame` | Meaning                                                          |
|---------------|------------------------------------------------------------------|
| `icrf` / `synodic` | no rotation; the CSV is already in the propagator's frame (the entry is named after the active dynamics model) |
| `ric`         | radial / in-track / cross-track basis of the reference orbit      |
| `lvlh`        | NASA/Goddard LVLH basis of the reference orbit                    |

The rotation targets **whatever frame the propagator integrates in**:
ICRF under `high_fidelity`, the synodic rotating frame under `cr3bp`
(where the local basis is built about the nearer primary, not the
barycentre). The rotated copy is written next to the source as
`<stem>_wrt_icrf.csv` or `<stem>_wrt_synodic.csv`, and `cases_file` is
pointed at it. It is a pure change of basis -- the reference state is
**not** added, which is what `mode = "delta"` is for.

From the CLI there is no rotation step: point `cases_file` at a CSV
already expressed in the propagator's frame.

### Per-case validation

The validator checks every **override** CSV cell against a per-field
rule (delta cells are exempt -- see [`[batch.columns]`](#batchcolumns)):

| Path                                | Rule        |
|-------------------------------------|-------------|
| `*.mass_kg`, `*.area_m2`, `*.am_*`, `*.Cd`, `duration_s`, `density_scale`, tolerances, step bounds, interval | must be `> 0` |
| `*.Cr`                              | must be `>= 0` |
| `force_model.srp`, `force_model.drag` | must be `0` or `1` |
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

# Redirect the run folder to a custom directory.
./build/spody propagate examples/lro_6day/input.toml --out /tmp/lro_run

# Multi-case run (requires [batch] in the TOML; the run folder comes
# from [batch].output_dir).
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
- **Let `spody maxhgdegree` pick the degree.** Given a harmonics file
  and a position it reports the largest degree that still contributes
  above double precision -- a better starting point than guessing,
  and the same rule `harmonics_adaptive` applies per step.
- **Test single first, then batch.** A scenario that works with
  `spody propagate` is a good base for batch: add `[batch]` +
  `[batch.columns]` + a CSV and you're done. Comment out `[batch]` to
  switch back.
- **Keep `[batch.columns]` paths consistent with the CSV header**:
  the parser rejects orphan mappings and unmapped columns.
- **Use `id` in the CSV** when your cases have semantically meaningful
  names (debris fragment ids, NORAD ids, scenario tags). Otherwise let
  the parser auto-number.
