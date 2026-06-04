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

| Key            | Type    | Default | Range  | Description |
|----------------|---------|---------|--------|-------------|
| `name`         | string  | &mdash; | &ndash; | Human-readable scenario name. Used as the prefix for batch case output names. |
| `et_start_s`   | float   | &mdash; | &ndash; | Start epoch as TDB seconds past the J2000 epoch (2000-01-01 12:00:00 TT). Negative values are valid for pre-J2000 epochs. |
| `duration_s`   | float   | &mdash; | `> 0`  | Propagation duration in seconds. Positive only (forward-time propagation). |

The `et_start_s` value is the same scale the planetary ephemeris
uses internally. Converting a calendar date to ET seconds past
J2000 is the user's responsibility; the engine does not perform
any UTC/TAI/TDB conversions. The DE440 wizard data covers
1950 &ndash; 2050 by default; choose the *Full pack* coverage
profile in the wizard if you need to start outside that window.

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
Required.

| Key             | Type            | Default | Range | Description |
|-----------------|-----------------|---------|-------|-------------|
| `frame`         | string          | &mdash; | `central_inertial` | Reference frame. Only `central_inertial` is supported in this release (the central body's J2000-aligned inertial frame; see chapter 10). |
| `position_km`   | array of 3 floats | &mdash; | &ndash; | `[x, y, z]` position in km in the chosen frame. |
| `velocity_kms`  | array of 3 floats | &mdash; | &ndash; | `[vx, vy, vz]` velocity in km/s, same frame as `position_km`. |

The initial state must be self-consistent: an `|r|` smaller than
the central body's mean radius will trigger an IMPACT event at
the first step. A `|v|` greater than the local escape velocity
turns the simulation into a hyperbolic flyby, which the engine
handles but is rarely what the user intended; double-check the
magnitudes against your scenario.

## `[force_model]`

Forces the propagator integrates against. Required.

| Key                  | Type            | Default | Range | Description |
|----------------------|-----------------|---------|-------|-------------|
| `central_body`       | string          | &mdash; | `Moon` | Central body of the propagation. Only `Moon` is supported in this release. |
| `harmonics_file`     | string (path)   | &mdash; | &ndash; | Path to a spherical-harmonic gravity coefficients file (`gggrx_1200b_sha.tab` for the recommended GRGM1200B model). Relative paths resolve against the TOML's directory. |
| `harmonics_degree`   | int             | &mdash; | `[2, 1200]` | Truncation degree of the harmonic gravity expansion. Higher = more accurate but more expensive. See *Choosing a harmonics degree* below for guidance. |
| `third_bodies`       | array of strings | `[]`   | one of `Sun`, `Mercury`, `Venus`, `Earth`, `Moon`, `Mars`, `Jupiter`, `Saturn`, `Uranus`, `Neptune` (excluding the central body) | Perturbing bodies whose point-mass gravity is added at every step. |
| `srp`                | bool            | `false` | &ndash; | Enable cannonball SRP. When `true` a `[spacecraft.srp]` block must be present (in Spacecraft mode) or `am_srp` must be set in `[debris]` (in Debris mode). |

### Choosing a harmonics degree

A rough guide for runs with the Moon as the central body, based on
empirical scaling of the GRGM1200B model and the cost of the
spherical-harmonic evaluation (which scales as O(N&sup2;)):

| N    | Use case                                                        |
|------|-----------------------------------------------------------------|
| 30 &ndash; 50 | quick sanity propagation, low-fidelity orbit averaging |
| 80   | reasonable default for LRO-class missions; sub-km vs SPICE LRO POD over 6 days |
| 150  | sweet spot for low-lunar orbits; recovers ~95% of N=200's residual reduction at half the cost |
| 200  | high-fidelity floor; beyond ~200 the GRGM1200B coefficients become weakly observed and adding terms can slightly *increase* mean drift |

Higher N values are accepted (up to the model's nominal 1200) but
do not visibly improve accuracy for the example scenarios shipped
with SpOdy.

## `[ephemeris]`

Path to the planetary ephemeris binary. Required.

| Key   | Type          | Default | Range | Description |
|-------|---------------|---------|-------|-------------|
| `file` | string (path) | &mdash; | &ndash; | Path to a `.spody` ephemeris file. Use `de440.spody` produced by the setup wizard (chapter 3). Relative paths resolve against the TOML's directory. |

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

All output paths are resolved relative to the TOML's directory, so
a TOML at `examples/foo/input.toml` with `bin_file = "output/foo.bin"`
writes the binary into `examples/foo/output/foo.bin`. You are
responsible for creating the destination directory; the engine
does not create it for you.

## `[events]` (optional)

Opt-in event detection. Present only when the *Enable [events]*
checkbox is ticked.

| Key                  | Type  | Default | Range    | Description |
|----------------------|-------|---------|----------|-------------|
| `eclipse_threshold`  | float | &mdash; | `[0, 1]` | Sunlight-fraction crossing that fires an eclipse event. `0` = enter umbra (start of total eclipse); `1` = full sunlight (end of any eclipse); `0.5` = penumbra midpoint. |

Impact events are always detected regardless of this section: any
trajectory that crosses a central-body or third-body surface (mean
radius) produces an IMPACT record. The `[events]` section only
controls the opt-in eclipse detection.

## `[batch]` (optional)

Multi-case sweep section. Present only when the *Enable [batch]*
checkbox is ticked. Covered in detail in chapter 7.

| Key            | Type          | Default | Range  | Description |
|----------------|---------------|---------|--------|-------------|
| `name`         | string        | &mdash; | &ndash; | Batch run name. Used as the prefix for per-case output names. |
| `output_dir`   | string (path) | &mdash; | &ndash; | Existing directory under which a `batch/` subfolder is auto-created. |
| `thread_number` | int          | `1`    | `[1, N_cpu]` | Worker thread count. `1` = sequential. Capped at the host's logical-CPU count by the form. Parallel execution requires the OpenMP-enabled engine build. |
| `cases_file`   | string (path) | &mdash; | &ndash; | CSV file describing the parameter sweep. First non-comment line is the header. |
| `columns`      | inline table  | empty  | &ndash; | Mapping from CSV columns to target paths (see chapter 7). Programmatic; the form's column-mapping table is the friendly editor. |
