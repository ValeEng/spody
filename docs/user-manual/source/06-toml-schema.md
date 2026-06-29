# TOML schema reference

This chapter is the field-by-field reference for the TOML input
files SpOdy reads. Every section, every key, every accepted value
is listed here in the order they appear in the form. For the
high-level workflow consult chapter 5; for batch-specific keys
also see chapter 7.

The conventions used in the tables below:

- **Type** is the TOML type the engine expects, with units where
  applicable.
- **Default** is what the engine assumes when the key is absent.
  An em dash (&mdash;) marks keys that are required.
- **Range** documents the allowed values; an unbounded numeric
  field is shown as `> 0` or similar.

## `[simulation]`

Scenario name and time window. Required.

| Key              | Type    | Default          | Range  | Description |
|------------------|---------|------------------|--------|-------------|
| `name`           | string  | &mdash;          | &ndash; | Human-readable scenario name. Used as the prefix for batch case output names. |
| `dynamics_model` | string  | `"high_fidelity"`| `"high_fidelity"`, `"cr3bp"` | Selects the propagator. `high_fidelity` (default) drives the full force-model integrator the bulk of this manual describes; `cr3bp` switches to the Circular Restricted 3-Body Problem in the synodic rotating frame (see *The `[cr3bp]` section* below). |
| `et_start_s`     | float   | &mdash; (HF only)| &ndash; | Start epoch as TDB seconds past the J2000 epoch (2000-01-01 12:00:00 TT). Negative values are valid for pre-J2000 epochs. Required in `high_fidelity` mode; ignored (and rejected if present) in `cr3bp` mode &mdash; the synodic frame is time-invariant. |
| `duration_s`     | float   | &mdash;          | `> 0`  | Propagation duration in seconds. Positive only (forward-time propagation). |

The `et_start_s` value is the same scale the planetary ephemeris
uses internally. The form provides a UTC&nbsp;&hArr;&nbsp;ET
converter (an ISO 8601 UTC field next to `et_start_s` with two
arrow buttons between them): typing a UTC instant and clicking
**&larr;** fills the ET cell; clicking **&rarr;** does the inverse.
The conversion is bit-identical to SPICE `str2et` &mdash; same
`deltet` algorithm (`K`, `EB`, `M0`, `M1` from the NAIF LSK
kernel) plus the hard-coded IERS Bulletin C leap-seconds table.
The UTC cell itself is never written to the TOML; only `et_start_s`
is serialised, so the engine still sees a single canonical
number. The DE440 wizard data covers 1950 &ndash; 2050 by default;
choose the *Full pack* coverage profile in the wizard if you need
to start outside that window.

`duration_s` is likewise always SI seconds on disk, but the form
ships a unit combo (`s | min | h | days`) next to the field so a
multi-day debris run does not need to be typed as `86400.0` or
`604800.0`. The combo affects only the displayed number; emit and
load round-trip the same float value. Auto-pick on load chooses
the largest unit whose factor is &le; the loaded magnitude.

### Sections required by `dynamics_model`

The schema branches on `simulation.dynamics_model`:

| `dynamics_model` | Required sections | Forbidden sections |
|------------------|-------------------|--------------------|
| `high_fidelity` (default) | `[simulation]`, exactly one of `[spacecraft]` / `[debris]`, `[initial_state]` with `frame = "central_inertial"` or `"central_body_fixed"`, `[force_model]`, `[ephemeris]`, `[integrator]`, `[output]` | `[cr3bp]` |
| `cr3bp` | `[simulation]`, `[cr3bp]`, `[initial_state]` with `frame = "synodic_rotating"`, `[integrator]`, `[output]` | `[spacecraft]`, `[debris]`, `[force_model]`, `[ephemeris]`, `[events]` with `eclipse_threshold`, `[output].accelerations_file` |

The validator rejects mismatches up front (HF without `et_start_s`,
CR3BP with a `[force_model]` block, &hellip;) so a misclassified
TOML never silently runs the wrong dynamics.

## The `[cr3bp]` section

Required when `dynamics_model = "cr3bp"`, forbidden otherwise.

| Key         | Type   | Default | Range            | Description |
|-------------|--------|---------|------------------|-------------|
| `primary_1` | string | &mdash; | `"Earth"`        | Larger primary; sits at synodic x = `-(mu2 / mu_tot) * L`. |
| `primary_2` | string | &mdash; | `"Moon"`         | Smaller primary; sits at synodic x = `+(mu1 / mu_tot) * L`. |

The pair `(primary_1, primary_2)` selects a curated entry in the
engine's `CR3BP_PAIRS` table that fixes `L` (the primary
separation in km, from `spody_const.h` &mdash; today
`EARTH_MOON_DISTANCE_KM = 384400`). The synodic angular velocity
`omega = sqrt((mu1 + mu2) / L^3)` is derived at run start; both
primaries' GM values come from the same central-body registry the
HF propagator uses, so the constants stay consistent across
dynamics models.

State is in **dimensional km / km/s** in the synodic rotating
frame: x along the line from primary 1 to primary 2 (positive
toward primary 2), z along the rotation axis, y completes the
right-handed triad. The frame rotates at `omega` in the inertial
frame; the barycenter is at the synodic origin. Impact events on
both primaries are auto-wired with their standard radii (no
`[events]` block required for that).

## `[spacecraft]` *or* `[debris]`

Mutually exclusive object descriptions. Exactly one of the two
sections must be present in a valid TOML.

### `[spacecraft]`

The conventional case: a body with a known dry mass. Gravity is
mass-independent, but SRP scales as `A/m`, so when SRP is enabled
the engine derives `A/m` from the area and the mass.

| Key       | Type  | Default | Range | Description |
|-----------|-------|---------|-------|-------------|
| `mass_kg` | float | &mdash; | `> 0` | Dry mass in kilograms. |

The optional `[spacecraft.srp]` sub-table is detailed below.

### `[debris]`

The inferred-body case: only the area-to-mass ratio matters. Use
this section when you do not have or do not care about a mass
value, typically for parameter sweeps over a debris cloud.

| Key      | Type  | Default | Range | Description |
|----------|-------|---------|-------|-------------|
| `am_srp` | float | &mdash; | `> 0` | Area-to-mass ratio in m&sup2;/kg, used by SRP. |
| `Cr`     | float | `1.5`   | `>= 0` | Reflectivity coefficient, only consulted when SRP is enabled. `1.0` = pure absorbing, `2.0` = pure mirror. |

In Debris mode, every batch override target that mentions a mass
or area (`spacecraft.mass_kg`, `spacecraft.srp.area_m2`) is
unavailable; only `debris.am_srp` and `debris.Cr` are accepted.

### `[spacecraft.srp]` (optional)

The cannonball solar-radiation-pressure sub-block. Present only
when `[spacecraft]` is the active object and the *Enable
[spacecraft.srp]* checkbox is ticked.

Within this sub-block exactly one of `area_m2` and `am_srp` is
allowed; setting both is a validation error.

| Key       | Type  | Default | Range  | Description |
|-----------|-------|---------|--------|-------------|
| `area_m2` | float | &mdash; | `> 0`  | Cross-sectional area in m&sup2;. The engine derives `A/m = area_m2 / mass_kg`. |
| `am_srp`  | float | &mdash; | `> 0`  | Area-to-mass ratio in m&sup2;/kg, specified directly. Equivalent to `area_m2 / mass_kg`; use this form when you want sweep over `A/m` independently of `mass_kg`. |
| `Cr`      | float | `1.5`   | `>= 0` | Reflectivity coefficient, same convention as in `[debris]`. |

## `[initial_state]`

Initial position and velocity vector of the propagated object.
Required. Two input flavours are supported via the optional `kind`
key &mdash; **Cartesian** (default, the only choice before this
release) and **Keplerian** (six classical elements + a reference
body). The engine converts Keplerian input into the Cartesian state
the integrator consumes; the rest of the pipeline (and the
snapshot TOML on disk) is identical for both.

| Key            | Type            | Default       | Range | Description |
|----------------|-----------------|---------------|-------|-------------|
| `frame`        | string          | &mdash;       | `central_inertial` or `central_body_fixed` (HF), `synodic_rotating` (CR3BP) | Reference frame. Model-exclusive: only the listed values are valid under each `dynamics_model`. `central_inertial` (HF) leaves the parsed `(position, velocity)` in the integrator's working basis; `central_body_fixed` (HF) interprets the values in the central body's body-fixed basis at `et_start_s` (Earth ITRS, Moon PA) and the engine rotates them to ICRF via the same `bf_rotation` callback the force-model uses on every step, before the run begins &mdash; the downstream integrator still sees a `central_inertial` state. `synodic_rotating` (CR3BP) places the elements in the reference primary's local inertial frame; the engine then rotates / translates them into the synodic frame at `t = 0`. The value of `frame` still has to match the model. |
| `kind`         | string          | `"cartesian"` | `"cartesian"`, `"keplerian"` | Which set of keys below is consumed. Omit for the legacy Cartesian path. |

### `kind = "cartesian"` (default)

| Key            | Type              | Default | Description |
|----------------|-------------------|---------|-------------|
| `position_km`  | array of 3 floats | &mdash; | `[x, y, z]` position in km in the chosen frame. |
| `velocity_kms` | array of 3 floats | &mdash; | `[vx, vy, vz]` velocity in km/s, same frame as `position_km`. |

The initial state must be self-consistent: an `|r|` smaller than
the central body's mean radius will trigger an IMPACT event at
the first step. A `|v|` greater than the local escape velocity
turns the simulation into a hyperbolic flyby, which the engine
handles but is rarely what the user intended; double-check the
magnitudes against your scenario.

### `kind = "keplerian"`

Six classical orbital elements + a reference body and an anomaly
selector. The convention for the reference inertial frame matches
the standard aerospace one: `inc = 0` means the orbit lies in the
reference frame's *xy* plane, `raan = 0` puts the ascending node on
the `+x` axis, `arg_periapsis = 0` puts periapsis at the ascending
node.

| Key                  | Type   | Range          | Description |
|----------------------|--------|----------------|-------------|
| `reference_body`     | string | `"central"`, `"primary_1"`, `"primary_2"` | Which body the elements reference. HF: defaults to `"central"`; the explicit value is also accepted, others are rejected. CR3BP: **required**, must be one of the primaries (no implicit default since both are physical). |
| `semi_major_axis_km` | float  | `> 0`          | Semi-major axis in km. |
| `eccentricity`       | float  | `[0, 1)`       | Eccentricity. Hyperbolic / parabolic orbits are not supported via Keplerian input (use the Cartesian path with the equivalent state). |
| `inclination_deg`    | float  | `[0, 180]`     | Inclination, degrees. |
| `raan_deg`           | float  | any            | Right ascension of the ascending node, degrees. |
| `arg_periapsis_deg`  | float  | any            | Argument of periapsis, degrees. |
| `anomaly_deg`        | float  | any            | Anomaly value at `t = 0`, degrees. |
| `anomaly_type`       | string | `"true"`, `"mean"` | What `anomaly_deg` represents. Mean is converted to true via Kepler's equation before the state synthesis. |

**CR3BP caveat.** Keplerian elements describe an instantaneously
osculating Kepler orbit around the chosen primary. The CR3BP system
is *not* a Kepler problem &mdash; the trajectory will not stay
closed; the second primary's gravity perturbs it from the first
step onward. This is exactly the same situation as a satellite
inserted into a Lunar orbit feeling Earth's pull, and is normally
the point of running a CR3BP scenario. The Keplerian input form is
just a convenient way to specify the *initial* state; once the
integration starts it is identical to a Cartesian IC carrying the
same `(r, v)`.

## `[force_model]`

Forces the propagator integrates against. Required.

| Key                  | Type            | Default | Range | Description |
|----------------------|-----------------|---------|-------|-------------|
| `central_body`       | string          | &mdash; | `Moon`, `Earth` | Central body of the propagation. Two bodies are supported in this release. The choice drives the gravity-model coefficient set, the body-fixed rotation provider (lunar PA libration angles from DE440 for Moon, IAU 2006/2010 + IERS EOP for Earth), and the list of valid `third_bodies`. |
| `harmonics_file`     | string (path)   | &mdash; | &ndash; | Path to a spherical-harmonic gravity coefficients file (`gggrx_1200b_sha.tab` for GRGM1200B / Moon; `eigen-6c4.tab` for EIGEN-6C4 / Earth, produced by the wizard from the upstream `.gfc`). In the form this row is a **dropdown of harmonics files the wizard has downloaded**, filtered by `central_body`. A **Browse...** button next to the combo adds an out-of-data-dir file as a one-off `(custom)` entry, so legacy TOMLs pointing at e.g. `external/spody-core/raw_data/...` keep round-tripping. Relative paths resolve against the TOML's directory. |
| `harmonics_degree`   | int             | &mdash; | `[2, 2200]` | Truncation degree of the harmonic gravity expansion. Higher = more accurate but more expensive. The effective upper bound is whatever the chosen `harmonics_file` declares (1200 for GRGM1200B, 2190 for EIGEN-6C4 / EGM2008); the `2200` cap is the absolute schema ceiling. See *Choosing a harmonics degree* below for guidance. |
| `eop_file`           | string (path)   | &mdash; (Earth only) | &ndash; | Path to the IERS Earth-orientation file (`finals2000A.all` from the IERS Rapid Service). Required when `central_body = "Earth"`, omitted otherwise. The form exposes this row as a wizard-populated dropdown that only appears when Earth is selected. |
| `iau2006_dir`        | string (path)   | &mdash; (Earth only) | &ndash; | Path to the directory containing the IAU 2006 X / Y / s+XY/2 conventions tables (`tab5.2a.txt`, `tab5.2b.txt`, `tab5.2d.txt`). Required when `central_body = "Earth"`. Wizard-managed; same conditional form row as `eop_file`. |
| `third_bodies`       | array of strings | `[]`   | one of `Sun`, `Mercury`, `Venus`, `Earth`, `Moon`, `Mars`, `Jupiter`, `Saturn`, `Uranus`, `Neptune` (excluding the central body) | Perturbing bodies whose point-mass gravity is added at every step. |
| `srp`                | bool            | `false` | &ndash; | Enable cannonball SRP. When `true` a `[spacecraft.srp]` block must be present (in Spacecraft mode) or `am_srp` must be set in `[debris]` (in Debris mode). |

### Choosing a harmonics degree

The right degree depends on the central body, the altitude, and the
duration you want to integrate. Two starter tables follow, both
based on empirical scaling against external references; the cost
of the harmonic evaluation itself scales as O(N&sup2;).

**Moon (GRGM1200B):**

| N    | Use case                                                        |
|------|-----------------------------------------------------------------|
| 30 &ndash; 50 | quick sanity propagation, low-fidelity orbit averaging |
| 80   | reasonable default for LRO-class missions; sub-km vs SPICE LRO POD over 6 days |
| 150  | sweet spot for low-lunar orbits; recovers ~95% of N=200's residual reduction at half the cost |
| 200  | high-fidelity floor; beyond ~200 the GRGM1200B coefficients become weakly observed and adding terms can slightly *increase* mean drift |

Higher N values are accepted (up to the model's nominal 1200) but
do not visibly improve accuracy for the example scenarios shipped
with SpOdy.

**Earth (EIGEN-6C4):**

| N    | Use case                                                        |
|------|-----------------------------------------------------------------|
| 30 &ndash; 50 | quick sanity propagation, sub-percent of N=70 cost |
| 70   | standard for GNSS-altitude propagation (GLONASS, GPS at &sim;20-25,000 km); matches IGS reprocessing conventions |
| 120 &ndash; 200 | LEO-altitude work; the EIGEN-6C4 high-degree terms become observable below &sim;1000 km |
| 2190 | full EIGEN-6C4 expansion; only relevant for surface gravity or very-low-LEO long-arc work |

At GNSS altitudes the harmonics contribution is already tiny
compared to the central two-body term, so degree 70 is comfortably
above the noise floor of the rest of the force model (luni-solar
third-body gravity, SRP) for most use cases.

## `[ephemeris]`

Path to the planetary ephemeris binary. Required.

| Key   | Type          | Default | Range | Description |
|-------|---------------|---------|-------|-------------|
| `file` | string (path) | &mdash; | &ndash; | Path to a `.spody` ephemeris file. Use `de440.spody` produced by the setup wizard (chapter 3). In the form this row is a body-agnostic **dropdown of ephemerides the wizard has produced** (DE-series files cover every planet at once, so the list does not depend on `central_body`). A **Browse...** button next to the combo adds an out-of-data-dir file as a `(custom)` entry. Relative paths resolve against the TOML's directory. |

A future release may accept `.bsp` SPICE kernels directly; today
only the internal `.spody` format is supported.

## `[integrator]`

Integration algorithm and tolerances. Required.

| Key         | Type   | Default  | Range  | Description |
|-------------|--------|----------|--------|-------------|
| `type`      | string | &mdash;  | `rkdp45` | Integration scheme. Only Dormand-Prince 5(4) is supported in this release. |
| `rel_tol`   | float  | &mdash;  | `> 0`  | Relative error tolerance per accepted step. `1e-11` is the recommended default for orbital regression work. |
| `h_init_s`  | float  | &mdash;  | `> 0`  | Initial step size in seconds. Normally somewhere between `h_min_s` and `h_max_s`. |
| `h_min_s`   | float  | &mdash;  | `> 0`  | Minimum allowed step size. The integrator gives up and reports failure if it would need to step smaller than this. |
| `h_max_s`   | float  | &mdash;  | `> h_min_s` | Maximum allowed step size. Useful as a guard against the integrator picking very large steps in low-perturbation regions and missing events. |

A typical low-lunar-orbit setup uses `rel_tol = 1e-11`,
`h_init_s = 60`, `h_min_s = 1e-5`, `h_max_s = 2700`. The relatively
large `h_max_s` (45 minutes) is harmless because the adaptive
controller picks smaller steps where the dynamics need them.

## `[output]`

Output stream configuration: which files to write, and at what
cadence. Required.

| Key                    | Type          | Default | Range   | Description |
|------------------------|---------------|---------|---------|-------------|
| `mode`                 | string        | &mdash; | `fixed` or `step` | Output cadence. `fixed` writes records on a uniform grid (`interval_s`); `step` writes one record per accepted RKDP step. |
| `interval_s`           | float         | &mdash; | `> 0`   | Sampling interval in seconds when `mode = "fixed"`. Ignored otherwise. |
| `csv_file`             | string (path) | none    | &ndash; | CSV trajectory output. Empty/absent = no CSV produced. |
| `bin_file`             | string (path) | none    | &ndash; | Binary (`SPDYOUT_`) trajectory output. Recommended for analysis: the GUI's reader path uses this format. |
| `log_file`             | string (path) | none    | &ndash; | Path that the engine tees its stdout/stderr into. |
| `accelerations_file`   | string (path) | none    | &ndash; | Per-force acceleration breakdown (`SPDYACC_` format). Empty = no breakdown produced. |
| `events_log`           | string (path) | none    | &ndash; | Event records (`SPDYEVT_` format) for impacts and (if `[events]` is enabled) eclipses. |

Output paths in the form are not edited directly; the
`[output]` block exposes five **on/off checkboxes** (csv, bin,
accelerations, events, log) plus a single `output_dir` picker.
spody auto-derives every enabled stream's filename as
`<output_dir>/<sim_name>_<subject>_<frame>.<ext>` (e.g. the
state-vector binary is `<sim_name>_state_icrf.bin`, the
accelerations binary is `<sim_name>_acc_icrf.bin`); the
under-the-hood TOML still carries the five `<stream>_file`
strings so the engine sees no schema change.

On every invocation the engine creates a **per-run folder** named
`<output_dir>/<UTC-ISO8601>/` (compact format, e.g.
`2026-06-09T120000Z`) and rewrites every enabled output path so it
lives inside that folder. The TOML used to start the run is also
copied into the run folder as `input.toml`, so a run is fully
self-contained: zip the folder and you have the inputs + outputs
together. The Analysis tab (chapter 8) groups files by these
folders.

## `[events]` (optional)

Opt-in event detection. Two independent sub-sections, each gated by
its own form checkbox:

### `eclipse_threshold` &mdash; eclipse detection (HF only)

| Key                  | Type  | Default | Range    | Description |
|----------------------|-------|---------|----------|-------------|
| `eclipse_threshold`  | float | &mdash; | `[0, 1]` | Sunlight-fraction crossing that fires an eclipse event. `0` = enter umbra (start of total eclipse); `1` = full sunlight (end of any eclipse); `0.5` = penumbra midpoint. |

Rejected under `dynamics_model = "cr3bp"` (no Sun in the model).

### `[[events.altitude_crossing]]` &mdash; altitude triggers

Array of tables; one entry per altitude band the user wants logged.
Fires on *every* sign change of `|r_sat - r_body| - body_radius -
altitude_km`, so the same band logs both the ascending and the
descending crossing of one orbit. Direction is recoverable from the
radial velocity at trigger (`v_trigger · r̂_trigger`).

| Key            | Type   | Default | Range    | Description |
|----------------|--------|---------|----------|-------------|
| `body`         | string | &mdash; | &ndash;  | Body to measure altitude from. HF: the central body or any entry in `force_model.third_bodies`. CR3BP: one of `cr3bp.primary_1` / `cr3bp.primary_2`. |
| `altitude_km`  | float  | &mdash; | `> 0`    | Target altitude above the body's mean radius (km). Use the always-on IMPACT detector for surface contact (`altitude_km = 0` is rejected). |
| `action`       | string | `"log"` | `"log"`, `"stop"`, `"log_and_stop"` | Behaviour on trigger. `log` keeps the propagation going (the natural choice for monitoring several bands); `stop` ends the run silently; `log_and_stop` does both. |
| `refined`      | bool   | `true`  | &mdash;  | When `true` (default), Brent + dense-output localises the trigger sub-microsecond inside the accepted step. When `false`, the trigger lands at the end of the accepted step (step-size precision). Refinement is essentially free in steady state &mdash; Brent only runs at the actual sign-change step &mdash; but the toggle is exposed for catalog-style runs with many bands. |

Example:

```toml
[[events.altitude_crossing]]
body        = "Earth"
altitude_km = 500
action      = "log"

[[events.altitude_crossing]]
body        = "Earth"
altitude_km = 1000

[[events.altitude_crossing]]
body        = "Moon"
altitude_km = 100
action      = "log_and_stop"
```

### Always-on IMPACT detection

Any trajectory that crosses a central-body or third-body surface
(mean radius) produces an IMPACT record regardless of the `[events]`
section. The section only controls the opt-in eclipse and altitude
detection above.

## `[batch]` (optional)

Multi-case sweep section. Present only when the *Enable [batch]*
checkbox is ticked. Covered in detail in chapter 7.

| Key            | Type          | Default | Range  | Description |
|----------------|---------------|---------|--------|-------------|
| `name`         | string        | &mdash; | &ndash; | Batch run name. Used as the prefix for per-case output names. |
| `output_dir`   | string (path) | &mdash; | &ndash; | Existing directory under which the engine auto-creates a per-run folder named with a compact ISO 8601 UTC timestamp (e.g. `2026-06-09T120000Z/`). All per-case binaries, the aggregated events file, and a copy of the input TOML land in that folder, so each batch invocation is self-contained and discoverable. Replaces the older `batch/` subfolder convention. |
| `thread_number` | int          | `1`    | `[1, N_cpu]` | Worker thread count. `1` = sequential. Capped at the host's logical-CPU count by the form. Parallel execution requires the OpenMP-enabled engine build. |
| `cases_file`   | string (path) | &mdash; | &ndash; | CSV file describing the parameter sweep. First non-comment line is the header. |
| `columns`      | inline table  | empty  | &ndash; | Mapping from CSV columns to target paths (see chapter 7). Programmatic; the form's column-mapping table is the friendly editor. |
