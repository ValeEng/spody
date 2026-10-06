# TOML schema reference

This chapter is the field-by-field reference for the TOML input
files SpOdy reads. Every section, every key, every accepted value
is listed here in the order they appear in the form. For the
high-level workflow consult chapter 5; for batch-specific keys
also see chapter 7. The Monte Carlo uncertainty file
(`<name>.uq.toml`, table `[montecarlo]`) is a separate file with its
own schema, in chapter 14; a scenario that contains a `[montecarlo]`
table is refused.

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

`duration_s` and `output.interval_s` are likewise always SI seconds
on disk, but the form ships a unit combo (`s | min | h | days`) next
to each field so a multi-day debris run &mdash; or a weekly output
cadence &mdash; does not need to be typed as `86400.0` or
`604800.0`. The combo affects only the displayed number; emit and
load round-trip the same float value. Auto-pick on load chooses
the largest unit whose factor is &le; the loaded magnitude.

### Sections required by `dynamics_model`

The schema branches on `simulation.dynamics_model`:

| `dynamics_model` | Required sections | Forbidden sections |
|------------------|-------------------|--------------------|
| `high_fidelity` (default) | `[simulation]`, exactly one of `[spacecraft]` / `[debris]`, `[initial_state]` with `frame = "central_inertial"`, `"central_body_fixed"`, `"central_body_fixed_rotating"` or `"orbit_plane"` (Moon only), `[force_model]`, `[ephemeris]`, `[integrator]`, `[output]` | `[cr3bp]` |
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

Because the pair names are all the file carries, the numbers a CR3BP
run actually integrates would otherwise leave no trace in the
scenario. They are recorded in two places:

- the form writes a **comment block** under `[cr3bp]` whenever it
  saves a TOML &mdash; `L`, the two GM values, the mass ratio `mu` and
  the synodic `omega` (with its period), re-derived from the pair on
  every save. It is a comment: the engine ignores it and re-resolves
  the values from its own tables, so editing it changes nothing and it
  can never go stale. The per-run snapshot the engine copies into
  `output/<timestamp>/` is a byte-for-byte copy of the input, so the
  block travels with the results;
- `spody propagate`, `spody batch` and `spody validate` print the same
  values in their opening block (manual ch. 12), which puts them in
  the run log of hand-written TOMLs too.

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

The optional `[spacecraft.srp]` and `[spacecraft.drag]` sub-tables
are detailed below.

### `[debris]`

The inferred-body case: only the area-to-mass ratio matters. Use
this section when you do not have or do not care about a mass
value, typically for parameter sweeps over a debris cloud.

| Key       | Type  | Default | Range | Description |
|-----------|-------|---------|-------|-------------|
| `am_srp`  | float | &mdash; | `> 0` | Area-to-mass ratio in m&sup2;/kg, used by SRP. |
| `Cr`      | float | `1.5`   | `>= 0` | Reflectivity coefficient, only consulted when SRP is enabled. `1.0` = pure absorbing, `2.0` = pure mirror. |
| `am_drag` | float | &mdash; (optional) | `> 0` | Drag area-to-mass ratio in m&sup2;/kg. Optional pair with `Cd` (both or neither); may differ from `am_srp` &mdash; the drag cross-section is not the SRP cross-section. Required when `force_model.drag = true`. |
| `Cd`      | float | &mdash; (optional) | `> 0` | Drag coefficient, only consulted when drag is enabled. |

In Debris mode, every batch override target that mentions a mass
or area (`spacecraft.mass_kg`, `spacecraft.srp.area_m2`,
`spacecraft.drag.area_m2`) is unavailable; only the `debris.*`
targets are accepted.

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

### `[spacecraft.drag]` (optional)

The cannonball atmospheric-drag sub-block, same shape as SRP.
Present only when `[spacecraft]` is the active object and the
*Enable [spacecraft.drag]* checkbox is ticked; required whenever
`force_model.drag = true`.

Within this sub-block exactly one of `area_m2` and `am_drag` is
allowed; setting both is a validation error.

| Key       | Type  | Default | Range | Description |
|-----------|-------|---------|-------|-------------|
| `area_m2` | float | &mdash; | `> 0` | Drag cross-section in m&sup2;. The engine derives `A/m = area_m2 / mass_kg`. |
| `am_drag` | float | &mdash; | `> 0` | Drag area-to-mass ratio in m&sup2;/kg, specified directly. |
| `Cd`      | float | &mdash; | `> 0` | Drag coefficient. `~2.2` is the classic value for compact satellites in free-molecular flow; flat/panelled bodies run higher (the ISS ballistic value published by NASA/JSC is `2.40`). |

The density model is NRLMSISE-00 (the engine's own validated port),
evaluated at the geodetic (WGS-84) sub-satellite point with the
observed daily F10.7 of the previous day, the 81-day centered
average, and the 7-element 3-hour Ap history (storm-time mode) from
`space_weather_file`. Air co-rotation with the Earth is included in
the relative velocity. The per-step drag acceleration is written to
the accelerations stream (`SPDYACC_`) like every other force.

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
| `frame`        | string          | &mdash;       | `central_inertial`, `central_body_fixed`, `central_body_fixed_rotating` or `orbit_plane` (HF), `synodic_rotating` (CR3BP) | Reference frame. Model-exclusive: only the listed values are valid under each `dynamics_model`. `central_inertial` (HF) leaves the parsed `(position, velocity)` in the integrator's working basis; `central_body_fixed` (HF) interprets the values in the central body's body-fixed basis at `et_start_s` (Earth ITRS, Moon PA) and the engine rotates them to ICRF via the same `bf_rotation` callback the force-model uses on every step, before the run begins &mdash; the downstream integrator still sees a `central_inertial` state. **Its velocity is the inertial one written on the body-fixed axes** (rotated, no &omega;&times;r). `central_body_fixed_rotating` (HF, Cartesian only) uses the same axes but takes the velocity **measured in the rotating frame**, as ECEF data gives it, and adds &omega;&times;r; see *Two body-fixed frames* below. `orbit_plane` (HF, Moon only) is Ely's OP frame, described below. `synodic_rotating` (CR3BP) places the elements in the reference primary's local inertial frame; the engine then rotates / translates them into the synodic frame at `t = 0`. The value of `frame` still has to match the model. |
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

### Two body-fixed frames: `central_body_fixed` and `central_body_fixed_rotating`

Both place position and velocity on the central body's body-fixed
axes at `et_start_s` (Earth ITRS, Moon PA). They differ in what the
**velocity** means, and mixing them up is not a small error:

| `frame` | the velocity you write is | the engine does | typical source |
|---|---|---|---|
| `central_body_fixed` | the **inertial** velocity, expressed on the body-fixed axes | rotates it: v = R v | the GUI's body-fixed plots and frame flip, which use this convention; Keplerian elements on the body-fixed axes |
| `central_body_fixed_rotating` | the velocity **relative to the rotating body** (ECEF-style) | rotates it and adds the transport term: v = R v + &omega;&times;r | GNSS receivers, SP3 read by hand, other tools' ECEF / ITRF / Moon-fixed states |

The two readings of the same three numbers differ by |&omega;&times;r|:
about 0.49 km/s in LEO, 1.93 km/s at GPS altitude, 4.4 m/s in low
lunar orbit. Entering ECEF data as `central_body_fixed` therefore
starts the run on a completely different orbit, without any error.

&omega; is the angular velocity of the body-fixed frame, read from the
engine's own rotation of that frame over &plusmn;60 s, so it is the true
rotation, not a nominal spin. For the Earth the axis is the CIP, which
polar motion tilts from the ITRS z axis by a few tenths of an
arcsecond; taking z instead puts about 1 mm/s into the inertial
velocity, hundreds of metres along track in a few days (the error of
releases before the fix, see the CHANGELOG). The `spody convert gps |
glonass` converters use the same &omega;, so an ECEF state entered here and
the same state converted give the same ICRF state. For the Moon &omega;
follows the DE440 libration that defines the PA frame
(|&omega;| &asymp; 2.66&times;10<sup>-6</sup> rad/s, its axis within a few
arcminutes of the PA z axis).

`central_body_fixed_rotating` accepts Cartesian input only: Keplerian
elements describe inertial motion, so their body-fixed reading is
`central_body_fixed`. The run's startup summary states which reading
it applied (`initial frame : ...`).

### `frame = "orbit_plane"` &mdash; Ely's OP frame

Available under `high_fidelity` with `central_body = "Moon"`, for
both `kind` values. It is the frame the lunar frozen-orbit
literature states its elements in, and getting it wrong is the
single most common way to "reproduce" a published frozen orbit and
watch it impact instead.

Let `I_p` be the Moon's north pole (the `+Z` of the lunar PA basis)
and `(r_E, v_E)` the Earth's state relative to the Moon at
`et_start_s`. The axes are

- `z_op = (r_E × v_E) / |r_E × v_E|` &mdash; the normal to the
  Earth's *apparent* orbit about the Moon;
- `x_op = (I_p × z_op) / |I_p × z_op|`;
- `y_op = z_op × x_op`.

The frame is built **once**, from the instantaneous state at
`et_start_s`, and is then treated as inertial &mdash; it only ever
labels the initial condition. As with `central_body_fixed`, the
engine rotates the parsed values into the integrator's
`central_inertial` basis before the run starts; the emitted TOML
keeps the frame name, so the user's intent survives save / load.

**Why this matters.** The lunar equator and the Earth's apparent
orbit plane are about 6.8&deg; apart. An inclination measured
against one is therefore *not* the same orbit as the same number
measured against the other: with `a`, `e`, `arg_periapsis` fixed, a
given lunar-equator inclination maps to an OP inclination anywhere
in a &plusmn;6.8&deg; band depending on where the node sits. Frozen
elliptical lunar orbits are stated in the OP frame; entering them
as `central_body_fixed` with the same numbers puts the orbit up to
13&deg; away from the frozen family, which shows up as a secular
growth of `e` that ends in an impact within months.

Because the frame's `x` axis is undefined when the perturber orbits
in the central body's equatorial plane, the engine rejects a
degenerate configuration rather than producing a silently arbitrary
node. The Moon/Earth pair is nowhere near that limit.

## `[force_model]`

Forces the propagator integrates against. Required.

| Key                  | Type            | Default | Range | Description |
|----------------------|-----------------|---------|-------|-------------|
| `central_body`       | string          | &mdash; | `Moon`, `Earth` | Central body of the propagation. Two bodies are supported in this release. The choice drives the gravity-model coefficient set, the body-fixed rotation provider (lunar PA libration angles from DE440 for Moon, IAU 2006/2010 + IERS EOP for Earth), and the list of valid `third_bodies`. |
| `body_shape`         | string          | &mdash; (required) | `"ellipsoid"`, `"equatorial_sphere"` | Shape of every body for the shadow, IMPACT and altitude crossings: the `pck00011` spheroid with geodetic altitude, or the sphere of the equatorial radius of earlier releases. No default: every input states the shape it runs; a TOML without the key is refused, and `"equatorial_sphere"` reproduces what it used to run. See *Body shapes* below. |
| `harmonics_file`     | string (path)   | &mdash; (required unless `harmonics_degree = 0`) | &ndash; | Path to a spherical-harmonic gravity coefficients file (`gggrx_1200b_sha.tab` for GRGM1200B / Moon; `eigen-6c4.tab` for EIGEN-6C4 / Earth, produced by the wizard from the upstream `.gfc`). In the form this row is a **dropdown of harmonics files the wizard has downloaded**, filtered by `central_body`. A **Browse...** button next to the combo adds an out-of-data-dir file as a one-off `(custom)` entry, so legacy TOMLs pointing at e.g. `external/spody-core/raw_data/...` keep round-tripping. Relative paths resolve against the TOML's directory. Optional when `harmonics_degree = 0`, since nothing reads it. |
| `harmonics_degree`   | int             | &mdash; | `0` or `[2, 2200]` | Truncation degree of the harmonic gravity expansion. Higher = more accurate but more expensive. The effective upper bound is whatever the chosen `harmonics_file` declares (1200 for GRGM1200B, 2190 for EIGEN-6C4 / EGM2008); the `2200` cap is the absolute schema ceiling. **`0` switches the gravity field off entirely**: the central body stays a point mass, so together with `third_bodies` the run becomes an ephemeris-driven restricted N-body problem (see *Turning the gravity field off* below). Degree `1` is rejected &mdash; it would only move the origin to the centre of mass, which the central-body convention already assumes. See *Choosing a harmonics degree* below for guidance. |
| `harmonics_adaptive` | bool            | `false` | &ndash; | Let the engine lower the degree per integrator step based on the satellite's distance, using `harmonics_degree` as the ceiling. Off by default; see *Letting the degree follow the orbit* below. Requires `harmonics_degree >= 2` &mdash; at degree `0` there is no expansion to truncate. |
| `solid_tides`        | string          | none    | `"tide_free"`, `"zero_tide"` | Solid-body tide of the central body; the value is the tide system of the gravity file. Absent = no tide. Requires `harmonics_degree >= 2`; `"zero_tide"` is Earth only. See *Solid-body tides* below. |
| `general_relativity` | bool            | `false` | &ndash; | General relativity: the Schwarzschild term of the central body (IERS 2010 eq. 10.12). See *General relativity* below. |
| `eop_file`           | string (path)   | &mdash; (Earth only) | &ndash; | Path to the IERS Earth-orientation file (`finals2000A.all` from the IERS Rapid Service). Required when `central_body = "Earth"`, omitted otherwise. The form exposes this row as a wizard-populated dropdown that only appears when Earth is selected. |
| `iau2006_dir`        | string (path)   | &mdash; (Earth only) | &ndash; | Path to the directory containing the IAU 2006 X / Y / s+XY/2 conventions tables (`tab5.2a.txt`, `tab5.2b.txt`, `tab5.2d.txt`). Required when `central_body = "Earth"`. Wizard-managed; same conditional form row as `eop_file`. |
| `third_bodies`       | array of strings | `[]`   | one of `Sun`, `Mercury`, `Venus`, `Earth`, `Moon`, `Mars`, `Jupiter`, `Saturn`, `Uranus`, `Neptune` (excluding the central body) | Perturbing bodies whose point-mass gravity is added at every step. |
| `srp`                | bool            | `false` | &ndash; | Enable cannonball SRP. When `true` a `[spacecraft.srp]` block must be present (in Spacecraft mode) or `am_srp` must be set in `[debris]` (in Debris mode). The bodies that can eclipse the Sun are the central body plus every entry of `third_bodies` (see *Which bodies cast a shadow* below); there is no separate key. |
| `drag`               | bool            | `false` | &ndash; | Enable atmospheric drag (cannonball, NRLMSISE-00 density in the storm-time 3-hour-Ap mode). Requires a central body with a registered atmosphere model (`Earth` in this release), a `[spacecraft.drag]` block (or the `am_drag`/`Cd` pair in `[debris]`) and `space_weather_file`. The form shows this row only when the central body has an atmosphere. |
| `earth_radiation_pressure` | bool | `false` | &ndash; | Earth albedo + infrared on the spacecraft (Knocke et al. 1988). Earth only; uses the `[spacecraft.srp]` area and `Cr`. The form shows this row only for the Earth. See *Earth radiation pressure* below. |
| `space_weather_file` | string (path)   | &mdash; (drag only) | &ndash; | Path to the CelesTrak combined space-weather CSV (`SW-All.csv`: daily F10.7 + 3-hour Ap, 1957 to a ~45-day prediction tail plus monthly long-range rows). Required when `drag = true`. The run window must start at least 3 days after the table's first row (the Ap history looks back 57 h) and end by the end of the last **daily** row &mdash; about 45 days after the file was downloaded. The monthly long-range rows after it carry no 3-hour Ap and cannot drive NRLMSISE-00. The engine refuses the run otherwise (in a batch, per case) and points at the update URL, `https://celestrak.org/SpaceData/SW-All.csv`. Wizard-managed (*Space weather* card) with the same daily startup freshness probe as `eop_file`. |
| `density_scale`      | float           | `1.0` (drag only) | `> 0` | Constant density calibration factor: the drag force uses `k × rho(NRLMSISE-00)`. Empirical thermosphere models carry a bias of 20&ndash;40% at 400&ndash;500 km around solar maximum (chapter 11, *Drag validation and ballistic calibration*); this key applies the calibrated correction without misdeclaring the physical `Cd`. Mutually exclusive with `density_scale_file`; requires `drag = true`. Batch-targetable as `force_model.density_scale`. |
| `density_scale_file` | string (path)   | &mdash; (drag only) | &ndash; | Path to a time-varying calibration table: plain text, one `mjd,k` pair per line (UTC MJD, strictly ascending; `k > 0`; `#` starts a comment). The factor is linearly interpolated between nodes and **held at the end values outside the node span** (the engine prints a warning when the run window extends past the nodes). A single-node file is equivalent to the constant key. Mutually exclusive with `density_scale`; requires `drag = true`. `spody calibrate` (chapter 12) fits and writes this file automatically from a reference trajectory. |

### Which bodies cast a shadow

When `srp = true`, the Sun is dimmed by **the central body plus every
body listed in `third_bodies`** (the Sun itself is skipped: it cannot
occult itself). The rule is deliberately "whatever you chose to model
also casts a shadow", so there is nothing extra to configure &mdash;
and, symmetrically, a body whose gravity you left out will not shade
the satellite either.

Two consequences worth knowing:

- **In cislunar space this matters a lot.** With `central_body =
  "Moon"` and `"Earth"` among the third bodies, the satellite now goes
  dark when the Earth passes in front of the Sun &mdash; which, seen
  from lunar orbit, is exactly a lunar eclipse. On a 12 h arc through
  the total lunar eclipse of 2025-03-14, an object with
  A/m = 0.02 m&sup2;/kg picks up about 17 m of position change from
  this term alone.
- **Listing a planet has a (tiny) physical price.** Venus among the
  third bodies means that during a transit of Venus the SRP dips by
  about 0.1%. That is real physics, not an artefact; the outer planets
  can never pass in front of the Sun as seen from an inner orbit, so
  they cost nothing but a handful of arithmetic per step.

If two bodies cover the Sun at the same time, their shadows on the
solar disc are combined as a **union**, not a sum: the overlap is
subtracted so it is not counted twice. The engine reports the combined
value in the `eclipse_fraction` column of the accelerations file
(chapter 8). The eclipse *event* (`events.eclipse_threshold`) is a
separate mechanism that watches **one** occulting body at a time, so
during a double eclipse the logged event fraction and the fraction
used by the force are different numbers on purpose.

### Body shapes: `body_shape`

`body_shape` sets the shape of the central body and of every third
body wherever a shape matters: the shadow (SRP force and eclipse
events), IMPACT, and the altitude of `[[events.altitude_crossing]]`.

The key is required (the form starts new scenarios on the ellipsoid,
and loads a TOML without it as `"equatorial_sphere"`, what it ran):

- `"ellipsoid"`: the oblate spheroid of the NAIF kernel
  `pck00011`, equatorial x polar radius, about the body's spin axis
  (IAU pole, taken at mid-run). Earth 6378.1366 x 6356.7519 km, Mars
  3396.19 x 3376.20 km; Mercury and the giant planets too. The Moon,
  Venus and the Sun are spheres in the kernel and stay spheres.
  Altitudes are **geodetic**: the height above the spheroid along its
  normal, as Orekit, GMAT and Tudat report it; the impact maps of the
  Analysis tab use the geodetic latitude too (chapter 9). A circular polar orbit
  therefore does not keep a constant altitude: 500 km over the equator
  is about 521 km over the poles, where the ground is 21 km lower.
- `"equatorial_sphere"`: a sphere of the equatorial radius, altitude =
  distance from the centre minus that radius. This is the model of
  earlier releases and reproduces their runs bit for bit, except
  around Mars, whose radius was the polar one and is now the
  equatorial one like every other body's.

**What the ellipsoid changes.** Where the satellite sees the Earth's
edge at high latitude, the real shadow starts later and ends earlier
than the sphere's: the contacts move by 7.5 s on a polar low orbit, up
to 18 s on the ISS (grazing entries stretch the difference), about 4 s
on GPS and under 0.1 s in GEO. For SRP with A/m = 0.01 m&sup2;/kg this
is about 28 m of along-track position after 7 days on the ISS orbit;
with the Sun near the orbit plane the later entry and the earlier exit
push in opposite directions and cancel, under half a metre on a polar
orbit and a few centimetres on GPS and GEO. Over the poles an IMPACT or
an altitude crossing moves by up to 21 km of height: with the sphere,
an object passing 6 km above the North Pole was declared impacted.
Laser-station photometry of Envisat entering and leaving the shadow
(Adhya, Sibthorpe, Ziebart & Cross, J. Spacecraft Rockets 41(1),
2004) fits an ellipsoidal Earth to 2.6 s rms against 6.4 s for the
equatorial sphere.

**How it is computed.** The spheroid enters the shadow model with the
angular radius of its edge, seen from the satellite, in the plane that
contains the satellite, the body centre and the Sun; stretching the
polar axis by R_eq / R_pol turns the spheroid into a sphere and keeps
tangency, so that edge comes in closed form. Geodetic altitude uses
Bowring's method, the one that already gives the atmosphere model its
altitude. Both are evaluated only where the two spheres (polar and
equatorial) disagree about the answer, so the cost is within the
run-to-run noise (0 to 3 %).

**Checks.** On the same trajectories (ISS, polar LEO, GPS, GEO):
shadow contacts within 1.1 ms of GMAT's `EclipseLocator` (SPICE,
exact ellipsoid) and 0.6 ms of Orekit; geodetic altitudes of every
crossing and impact within 2e-6 mm of SPICE `recgeo` on the same pole
and 0.27 m of Orekit's ITRF ellipsoid (the IAU pole leaves out
nutation). With `"equatorial_sphere"` every event and trajectory file
is identical to the previous release.

The atmosphere, which bends and dims the grazing sunlight, makes the
real shadow longer; it is not modelled (Vokrouhlick&yacute;, Farinella
& Mignard 1993 give the full theory), and the Sun is taken at its
geometric position.

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

### Letting the degree follow the orbit

The tables above ask you to pick one degree for a whole run. That is
the right question for a near-circular orbit, where the satellite
always sits at roughly the same distance. On an eccentric one it is
the wrong question, because the degree you need at closest approach
and the degree you need far out are not the same number, and a single
setting has to be the larger of the two.

How much larger is easy to see. The degree-*n* term of the potential
carries a factor `(R_ref / r)^n`, so it dies off geometrically with
distance. For a lunar orbit with `a = 6541 km` and `e = 0.6`,
`spody maxhgdegree` puts the useful degree at 82 near periapsis and
at 19 near apoapsis. Since the cost of one evaluation goes as
`(N+1)²`, running the whole orbit at 82 does about eight times the
work it needs to in the outer part of the revolution.

`harmonics_adaptive = true` removes that waste. Before every
integrator step the engine picks the degree from

```
N(r) = ln(1/eps) / ln(r / R_ref)
```

capped at your `harmonics_degree`, where `r` is a conservative bound
on the closest the step can get to the body. Only the ratio
`r / R_ref` appears, so there is nothing to calibrate per body or per
gravity model &mdash; the same rule serves the Moon, the Earth and any
body added later.

**It does not trade accuracy for speed.** The threshold is set below
the resolution of double precision, so the terms it drops cannot
change the accumulated acceleration at all. Measured end to end:

| run | fixed degree | adaptive | speed-up | difference |
|-----|-------------:|---------:|---------:|------------|
| Lunar ELFO, 365 days, N = 80 | 32.1 s | 14.0 s | 2.3&times; | none, bit for bit |
| GPS, 7 days, N = 70          | 0.686 s | 0.184 s | 3.7&times; | none, bit for bit |

"None" is literal: 453,447 and 672 output records, identical to the
last bit, with the same number of accepted and rejected integrator
steps. The step controller takes the very same sequence of steps,
which is what tells you the per-step degree change is not perturbing
it &mdash; the degree is held fixed across all stages of a step
precisely so that it cannot.

The gain is largest on eccentric orbits and on runs whose degree is
high relative to their altitude. A near-circular orbit whose degree
is already close to what the altitude needs will see little or
nothing, which is the correct outcome: there was no waste to remove.

Leaving the key out (or setting it to `false`) reproduces earlier
runs bit for bit, so it is safe to add to an existing TOML without
invalidating anything you have already computed.

### Turning the gravity field off

`harmonics_degree = 0` removes the harmonic term altogether: the
central body becomes a point mass and `harmonics_file` may be
omitted. This is not a degenerate setting, it is a deliberate
baseline, useful in three situations.

**Isolating what the gravity field contributes.** Run the same
scenario at degree 0 and at your working degree, and the difference
between the two trajectories *is* the harmonics contribution &mdash;
no algebra, no separate acceleration breakdown.

**A restricted N-body problem on real ephemerides.** With
`third_bodies` populated, degree 0 gives point-mass central body plus
point-mass perturbers pulled from DE440. That is the classical
restricted problem, except the perturbers move on their true
ephemeris rather than the idealised circular orbits assumed by
`dynamics_model = "cr3bp"` (chapter 6, `[simulation]`). It is the
natural bridge between the two dynamics models: same restricted
setup, real geometry.

**A cheap first look.** Degree 0 costs nothing per evaluation, so it
is a fast way to check that an epoch, an initial state and a duration
are sane before paying for a high-degree run.

On the bundled GPS G11 example (7 days, IGS precise orbits as
reference), degree 0 leaves a 115 km residual against a 581 m one at
degree 70 with third bodies &mdash; which is simply the size of the
Earth's oblateness effect over a week at that altitude.

Degree `1` is rejected rather than accepted-and-ignored: it would
only shift the origin to the centre of mass, which the central-body
convention already assumes, so a TOML asking for it has almost
certainly confused degree with something else.

### Solid-body tides

The Moon and the Sun stretch the Earth, and the Earth stretches the
Moon. The deformed body attracts the satellite a little differently
from the rigid one the gravity file describes; `solid_tides` adds
that difference. (The *direct* pull of the Moon and the Sun on the
satellite is a separate thing: it is the third-body force, already in
`third_bodies`.)

```toml
[force_model]
solid_tides = "tide_free"   # or "zero_tide"; leave the key out for no tide
```

**What the value means.** It is not an on/off switch: it states the
*tide system of your gravity file*, because the file decides how much
of the tide is already inside its coefficients.

| value | the gravity file... | the engine adds |
|---|---|---|
| (key absent) | &ndash; | nothing: runs reproduce earlier ones bit for bit |
| `"tide_free"` | holds no tide at all | the whole tide, including its permanent part |
| `"zero_tide"` | already holds the permanent deformation | the tide minus that permanent part (Earth only) |

Any other value, `true` and `false` included, is refused. Read the
value off the file: an ICGEM `.gfc` says it in its header
(`tide_system tide_free` for EIGEN-6C4). GRGM1200B's label does not
say; fitting the NASA LRO orbits over ten arcs showed it is
tide-free (the full tide cuts the cross-track residual in 9 arcs out
of 10), so use `"tide_free"` with it. `"zero_tide"` is refused for
the Moon: there is no permanent-tide convention for it.

**The model** (IERS Conventions 2010, sec. 6.2.1, frequency-independent
step). At every evaluation the positions of the tide-raising bodies,
in the body-fixed frame, give corrections to the normalized
coefficients

```
dC_nm - i dS_nm = k_nm/(2n+1) * sum_j (GM_j/GM) (R/r_j)^(n+1) Pbar_nm(sin phi_j) exp(-i m lambda_j)
```

and the engine adds the acceleration of that small field. The
numbers depend on the central body and come with it:

| | Earth | Moon |
|---|---|---|
| degrees | 2, 3, and 4 through the `k(+)` coupling of a flattened, rotating body | 2 |
| Love numbers | IERS 2010 Table 6.3 (`k20 = 0.30190`, `k21`, `k22` with their anelastic parts, `k3m = 0.093..0.094`) | `k2 = 0.024116` (GRAIL, GRGM1200B label) |
| tide-raising bodies | Moon, Sun | Earth, Sun |

The planets are left out on purpose: Venus at its closest raises
7e-5 of the Moon's tide on the Earth, about a centimetre in a week
on the ISS. A gravity field is required (`harmonics_degree >= 2`):
the corrections are normalized with the file's GM and radius.

**The tide stops at the field's degree.** The tide corrects the
field's coefficients, so it never reaches past them: with
`harmonics_degree = 2` only the degree-2 tide is added, the degree-3
tide needs 3 and the `k(+)` terms need 4 (each is about 0.5 % of the
Earth's tide on the ISS). The setup log says so (`capped by
harmonics_degree`). With `harmonics_adaptive = true` the limit is
`harmonics_degree`, not the degree picked step by step, so the tide
does not switch on and off along the orbit.

**What it is worth** (tide on minus tide off, 7 days):

| orbit | effect | cross-check |
|---|---|---|
| ISS, 400 km | 22.5 m after 1 day, 211 m after 7 | Orekit 21.4 / 203 m |
| GPS | 0.31 / 2.1 m | Orekit 0.33 / 2.4 m |
| LRO, low lunar orbit | 73 / 256 m | Tudat 73.0 / 255.6 m |

Orekit also applies the IERS frequency-dependent corrections and the
pole tide, which SpOdy does not: that is the 3&ndash;10 % gap on the
Earth rows. The cost is 2&ndash;4 % of run time.

### General relativity

Newton's gravity is the first approximation of general relativity.
`general_relativity = true` adds the leading correction for the
central body, its Schwarzschild field (IERS Conventions 2010,
sec. 10.3, eq. 10.12, first line):

```
a = GM/(c^2 r^3) * [ (2(beta+gamma) GM/r - gamma v.v) r + 2(1+gamma) (r.v) v ]
```

with `r`, `v` the satellite's position and velocity relative to the
central body and `beta = gamma = 1` (general relativity). `GM` is the
one the central term uses, i.e. the gravity file's when there is one.
It works the same for the Earth and the Moon; its main effect is a
slow advance of the perigee.

| orbit | 1 day | 7 days | cross-check |
|---|---|---|---|
| ISS | 2.59 m | 18.1 m | Orekit 2.59 / 18.1 m |
| GRACE-FO (490 km) | 2.53 m | 17.9 m | Orekit 2.53 / 17.9 m |
| GPS | 0.33 m | 2.3 m | Orekit 0.33 / 2.3 m |
| GEO | 0.17 m | 1.2 m | Orekit 0.17 / 1.2 m |
| LRO | 0.03 m | 0.19 m | Tudat 0.03 / 0.19 m |

The other two lines of eq. 10.12, Lense&ndash;Thirring (the rotating
Earth dragging the orbital plane) and de Sitter (the Earth's motion
around the Sun), are 1e-11 to 1e-12 of gravity and are not modelled:
about 0.1 m and 1 cm in a week on a low orbit. Leaving the key out,
or `false`, reproduces earlier runs bit for bit; any value that is
not a boolean is refused.

### Earth radiation pressure

The Sun lights the Earth, and the Earth sends part of that energy
back to the satellite: sunlight reflected by clouds, oceans and ice
(**albedo**, only from the lit part of the Earth in view) and heat
(**infrared**, from all of it, day and night).
`earth_radiation_pressure = true` adds the push of both.

**The model.** The part of the Earth the satellite sees is split into
surface elements, each a Lambertian emitter of exitance

```
M = a F cos(zeta_sun)   (albedo, only where the Sun is up)
  + e F / 4             (infrared)
```

with `F` the solar flux at the Earth (the same flux the SRP force
uses) and `zeta_sun` the Sun's angle from the local vertical. An
element of area `dA` at distance `d` gives the satellite an
irradiance `(M/pi) cos(theta) dA / d^2`, where `theta` is the angle
between the element's normal and the direction to the satellite
(NASA TM 104596). Albedo `a` and emissivity `e` follow the latitude
and the season as in Knocke, Ries & Tapley (1988):

```
a = 0.34 + 0.10 cos(w t) P1(sin phi) + 0.29 P2(sin phi)
e = 0.68 - 0.07 cos(w t) P1(sin phi) - 0.18 P2(sin phi)
```

with `t` counted from 1981-12-22 and `w = 2 pi / year`. The original
paper could not be consulted; the coefficients are the ones quoted
by the literature and by Orekit. The acceleration is `Cr (A/m) E / c`,
with the area and `Cr` of `[spacecraft.srp]` (a sphere, like SRP).

**How it is integrated.** `cos(theta) dA / d^2` is the solid angle
under which the satellite sees the element, so the engine sums over
directions seen from the satellite: 6 Gauss-Legendre nodes in the
cosine of the nadir angle times 12 azimuths, 72 rays over the disk.
A uniformly bright sphere comes out exact (`M (R/r)^2`); the real
albedo, with the day/night line in view, to 1e-4..4e-4 (median) and
2e-3 (worst) against a 20 000-ray reference, at every altitude.

**What it is worth** (on minus off, 7 days, cannonball `Cr = 1.3`):

| orbit | A/m | 1 day | 7 days |
|---|---|---|---|
| ISS | 0.0031 m&sup2;/kg | 0.54 m | 4.0 m |
| GRACE-FO | 0.0033 m&sup2;/kg | 0.84 m | 4.5 m |
| GPS | 0.012 m&sup2;/kg | 1.17 m | 8.4 m |
| GEO | 0.010 m&sup2;/kg | 0.76 m | 5.4 m |

On GNSS and GEO orbits this is larger than the solid tide and general
relativity. The cost is about 15 % of run time.

**Not cross-checked against Orekit, on purpose.** Orekit 13.1's
`KnockeRediffusedForceModel` sums over a cap of angle `asin(R/r)`
instead of the visible cap `acos(R/r)` (fixed upstream in 2026,
Orekit issue 852), and weights each element with the geocentric angle
instead of `theta`. Reproducing those two choices on a uniformly
bright sphere gives exactly the ratios Orekit 13.1 shows against
SpOdy (2.8 in LEO, 0.10 on GPS, 0.034 in GEO); the second choice alone
still overestimates by 2.0 / 1.3 / 1.2. The validation is the exact
uniform-sphere result and an independent fine quadrature.

The force needs `central_body = "Earth"` (any other body is refused:
the model is the Earth's) and `[spacecraft.srp]` (or the debris
`am_srp` / `Cr`). Leaving the key out, or `false`, reproduces earlier
runs bit for bit.

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
| `type`      | string | &mdash;  | `rkdp45`, `dop853` | Integration scheme: Dormand-Prince 5(4) or Dormand-Prince 8(5,3). See *Choosing the integrator* below. |
| `rel_tol`   | float  | &mdash;  | `> 0`  | Relative error tolerance per accepted step. `1e-11` is the recommended default for orbital regression work. |
| `h_init_s`  | float  | &mdash;  | `> 0`  | Initial step size in seconds. Normally somewhere between `h_min_s` and `h_max_s`. |
| `h_min_s`   | float  | &mdash;  | `> 0`  | Minimum allowed step size. The integrator gives up and reports failure if it would need to step smaller than this. |
| `h_max_s`   | float  | &mdash;  | `> h_min_s` | Maximum allowed step size. Useful as a guard against the integrator picking very large steps in low-perturbation regions and missing events. |
| `discontinuity_stops` | bool | `true` with `dop853`, `false` with `rkdp45` | &ndash; | Stop the steps on the jumps of the force model: the 3-hour UTC grid of the NRLMSISE-00 space-weather inputs (drag on) and the shadow contacts (SRP on). See *Discontinuity stops* below. |
| `time_scale` | string | `"tdb"` | `"tdb"`, `"tt"` | Time coordinate the integrator advances: TDB for every central body, or TT, the IERS geocentric time, Earth only. A missing key means `"tdb"`; the form and the bundled examples write it anyway, so a scenario says which time it runs. Output files are labelled in ET either way. See *Integration time scale* below. |

A typical low-lunar-orbit setup uses `rel_tol = 1e-11`,
`h_init_s = 60`, `h_min_s = 1e-5`, `h_max_s = 2700`. The relatively
large `h_max_s` (45 minutes) is harmless because the adaptive
controller picks smaller steps where the dynamics need them.

### Choosing the integrator

`rkdp45` (Dormand-Prince 5(4)) is the reference: every validation in
this manual was made with it. `dop853` (Dormand-Prince 8(5,3), order 8; Hairer, N&oslash;rsett,
Wanner, *Solving Ordinary Differential Equations I*, 2nd ed., 1993,
sect. II.10) takes much longer steps for the same accuracy: measured at
equal error, 2.5&times; fewer force evaluations on a GPS orbit, 4.5 to
4.9&times; on LAGEOS-2, 2.6&times; on the ISS with drag; a 200-case ISS
Monte Carlo runs in 6.6 s instead of 24.7 s. `rel_tol` has the same
meaning for both (error per step relative to the step's own change), but
at the same `rel_tol` `dop853` is usually more accurate.

`dop853` relies on the discontinuity stops below, on by default with
it: without them its long steps jump across the space-weather changes
and the penumbra, and it does not reach the centimetre with drag or
SRP. The fixed output grid and the events are interpolated inside each
step with the same quintic Hermite for both integrators; with the long
`dop853` steps this interpolation is less accurate than the
integration itself.

### Discontinuity stops

Some force-model inputs change in jumps, not smoothly. The NRLMSISE-00
density takes its solar and geomagnetic indices from the space-weather
table as daily F10.7 and 3-hour Ap values, and the day of year as an
integer: all of them switch on the 3-hour UTC grid (00, 03, ..., 21 h).
A Runge-Kutta step that straddles such a jump loses its order and its
error estimate does not see it. With `discontinuity_stops = true` and
drag on, the step lands 1 ms before each boundary and a step of at most
2 ms crosses it.

Measured on the ISS (3 days, `rel_tol = 1e-11`): the position error
drops from 2 m to 1.1 mm with `dop853` and from 4 cm to 0.14 mm with
`rkdp45`, for 1.1 % and 0.1 % more force evaluations.

Solar radiation pressure switches on and off at the shadow contacts:
the satellite enters and leaves the penumbra and the umbra of each
occulter (the Earth, the Moon, ...). These instants are not known in
advance; after every step the engine checks whether the step crossed
one, finds it on the step's interpolated trajectory, and redoes the
step from its start so that it stops there. Measured on LAGEOS-2
(7 days, 46 shadow passes): with `dop853` the error at `rel_tol =
1e-12` drops from 0.28 m to 0.15 mm for 18 % more force evaluations;
`rkdp45`, with its shorter steps, moves from 1.9 mm to 1.0 mm.

The default is on for `dop853` and off for `rkdp45`, so earlier
`rkdp45` runs reproduce bit for bit. Without drag and SRP the key has
no effect and costs nothing. In the form the checkbox follows the
scheme whenever you pick one in `type` (and can then be changed); a
loaded TOML keeps the value it carries, or the engine default for its
scheme when it has none. The form always writes the key, so a saved
scenario says how it runs.

### Integration time scale

By default the integrator advances **TDB** (Barycentric Dynamical
Time), the time of the JPL ephemerides and of every ET in SpOdy, for
any central body. It is the choice of SPICE-based tools (Tudat does
the same) and is enough for most work.

Around the Earth the equations of motion are, strictly, those of the
geocentric reference system, whose time coordinate is TT: *"The
independent variable of the satellite equations of motion may be ...
either TT or TCG"* (IERS Conventions 2010, TN36 sec. 10.3). TDB and TT
tick at the same average rate but differ by a periodic term, about
1.657 ms &times; sin(g) over the year, because the Earth runs faster
and deeper in the Sun's potential in January than in July. Advancing
the satellite with TDB therefore moves it along its orbit by
v &middot; &Delta;(TDB &minus; TT): up to 1.5 m in 7 days on a LEO and
0.8 m on GPS around early January and early July, almost nothing
around April and October (the term is periodic: over a year it swings
by about &plusmn;13 m on a LEO).

`time_scale = "tt"` integrates in TT instead (Earth central body only;
any other body, or CR3BP, is refused). Ephemerides, Earth rotation and
space weather still receive ET, converted from TT with the same
TDB&minus;TT term as the rest of SpOdy, and every file keeps its time
column as ET &minus; `et_start_s`, the output grid on the same round ET
epochs: only the physics changes. Measured on a two-body LEO and GPS,
7 days, three seasons: SpOdy with `"tt"` agrees with Orekit to 1 mm
and GMAT, both TT-based, to a few mm; with `"tdb"` it agrees with
Tudat (TDB) to 0.3 mm and differs from the TT tools by exactly
v &middot; &Delta;(TDB &minus; TT). Cost: 1 to 4 % of run time. The
default `"tdb"` reproduces earlier runs bit for bit, written or not: a
TOML without the key runs in TDB, identical to one with
`time_scale = "tdb"`; the form writes the key on every high-fidelity
scenario (`"tdb"` for any body but the Earth).

For a lunar orbiter the analogue of TT is Lunar Coordinate Time (TCL,
IAU 2024 Resolution II), not yet available in SpOdy: lunar runs use
TDB.

## `[output]`

Output stream configuration: which files to write, and at what
cadence. Required.

| Key                    | Type          | Default | Range   | Description |
|------------------------|---------------|---------|---------|-------------|
| `mode`                 | string        | &mdash; | `fixed` or `step` | Output cadence. `fixed` writes records on a uniform grid: record *k* is at *k* &times; `interval_s`, computed as that product rather than by repeated addition, so the epochs stay on their nominal values for any interval; the state there is interpolated between the two integrator steps around it with a quintic Hermite on position, velocity and acceleration, so it carries the integrator's own accuracy; when `duration_s` is not a whole multiple of the interval the final state follows as one more record. `step` writes one record per accepted RKDP step. In both modes a run that ends on an impact (or a stop-class altitude crossing) closes on the trigger state at its own time, off the grid, and writes nothing later than it: time is strictly increasing in every file. |
| `interval_s`           | float         | &mdash; | `> 0`   | Sampling interval in seconds when `mode = "fixed"`. Ignored otherwise. |
| `csv_file`             | string (path) | none    | &ndash; | CSV trajectory output. Empty/absent = no CSV produced. |
| `bin_file`             | string (path) | none    | &ndash; | Binary (`SPDYOUT_`) trajectory output. Recommended for analysis: the GUI's reader path uses this format. |
| `log_file`             | string (path) | none    | &ndash; | **Deprecated and ignored.** Every run now writes its log, the copy of everything printed on the terminal (the app's own lines and every diagnosis of the engine library: a damaged ephemeris, a malformed harmonics row, a missing data file), as `<ts>_<simulation.name>.log` in the run folder; with `--out`, or without `output_dir`, as `<simulation.name>_<ts>.log` there or beside the TOML. A scenario that sets the key runs normally and prints a `spody: warning:` line; remove the key. The form no longer writes it. |
| `accelerations_file`   | string (path) | none    | &ndash; | Per-force acceleration breakdown (`SPDYACC_` format). Empty = no breakdown produced. |
| `events_log`           | string (path) | none    | &ndash; | Event records (`SPDYEVT_` format) for impacts and (if `[events]` is enabled) eclipses. |

A file name that is a Windows device &mdash; `CON`, `PRN`, `AUX`,
`NUL`, `CONIN$`, `CONOUT$`, `COM0`&ndash;`COM9`, `LPT0`&ndash;`LPT9`,
in any case and with any extension, so `nul.bin` and `Con.csv` too
&mdash; is refused at validation, on every platform. Windows opens the
device instead of a file: the run would end normally and write
nothing. Names that only start like one (`console.bin`, `com10.bin`)
are ordinary files. The names the form derives never collide.

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
`2026-06-09T120000Z`; a run started in the same second as another
one in the same `output_dir` gets `2026-06-09T120000Z-1`, then `-2`,
..., so it never writes into the other run's folder) and rewrites
every enabled output path so it
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
| `eclipse_threshold`  | float | &mdash; | `[0, 1]` | Sunlight-fraction crossing that fires an eclipse event. `0` = umbra contact (start and end of the total eclipse); `1` = penumbra contact (first and last sunlight lost); `0.5` = penumbra midpoint. Each value logs both the entry and the exit. |

The two contacts (`0` and `1`) are located on the angular separation
between the Sun and the occulting body, as seen from the satellite,
rather than on the fraction, which is flat on one side of them; any
other value is located on the fraction inside the penumbra. On the
same trajectories the times agree with Orekit (to a microsecond), GMAT
and Tudat (to the millisecond those checks resolve). The Earth is a sphere of its equatorial radius, with no
atmosphere: a real shadow on a low orbit starts and ends up to about
18 s off these times (see *Which bodies cast a shadow*).

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
| `altitude_km`  | float  | &mdash; | `> 0`    | Target altitude above the body's surface (km): geodetic height above the spheroid with `force_model.body_shape = "ellipsoid"`, distance minus the equatorial radius with `"equatorial_sphere"` (see *Body shapes*). CR3BP primaries are spheres of their equatorial radius. Use the always-on IMPACT detector for surface contact (`altitude_km = 0` is rejected). |
| `action`       | string | `"log"` | `"log"`, `"stop"`, `"log_and_stop"` | Behaviour on trigger. `log` keeps the propagation going (the natural choice for monitoring several bands); `stop` ends the run silently; `log_and_stop` does both. |
| `refined`      | bool   | `true`  | &mdash;  | When `true` (default), Brent + dense-output localises the trigger inside the accepted step at the integrator's own accuracy (8 &micro;s at `rel_tol` 1e-9 on a lunar impact, 0.03 &micro;s at 1e-11). When `false`, the trigger lands at the end of the accepted step (step-size precision). Refinement is essentially free in steady state &mdash; Brent only runs at the actual sign-change step &mdash; but the toggle is exposed for catalog-style runs with many bands. |

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

### Life markers (`INITIAL_STATE` / `FINAL_STATE`)

Also unconditional, and also not configurable: whenever an events log
is written, every propagated object contributes one `INITIAL_STATE`
record at `t = 0` and one `FINAL_STATE` record at whatever ended its
run &mdash; the planned duration, an impact, or a stop-class altitude
crossing. They are not triggers: no predicate is evaluated and
nothing can suppress them.

They exist because a log built only from triggers is not a complete
record of what was propagated. An object that stays between two
altitude thresholds for the whole run crosses nothing, so it wrote
nothing, and was indistinguishable from an object that never ran
&mdash; including in its own denominator, which silently inflated
every per-object percentage computed from the file. With the markers,
the `case_idx` values present in a batch log are the complete list of
propagated cases, so post-processing no longer needs `cases.csv` to
know the batch size.

They also carry the object's starting altitude and the true end of
its window, which is what lets the altitude-band analysis measure
those two facts instead of inferring them from the direction of the
first crossing and from the planned duration.

Field layout follows the altitude-crossing convention &mdash;
`radius_km` is the body's radius, `distance_km` the body-centric
distance, so `distance_km - radius_km` is the altitude. One marker is
written per body whose distance needs no ephemeris query: the central
body in high fidelity, both primaries in CR3BP. A `FINAL_STATE` that
never arrives after an `INITIAL_STATE` means that case died mid-run
(integrator or I/O failure); the Info tab reports those as *Objects
never closed*.

**Reading older files.** Logs written before August 2026 carry no
markers and are read exactly as before. New logs are two records per
object per body longer, so an events `.bin` from before that date is
not byte-comparable with a fresh one &mdash; trajectory and
acceleration binaries are unaffected.

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
