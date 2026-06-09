# Glossary

A short list of the technical terms used through the manual,
defined where SpOdy uses them. Cross-references to the chapter
that introduces each term in detail are given in square brackets.

**Accelerations binary.** &mdash; A `SPDYACC_`-format output file
listing, at every record, the contribution of each active force
to the total acceleration. Used by the **Per-force breakdown**
plot. [Chapter 9.]

**Accelerations file.** &mdash; The `output.accelerations_file`
TOML field, naming the on-disk accelerations binary. [Chapter 6.]

**Adaptive step.** &mdash; The mode of the RKDP integrator in
which the step size `h` is chosen at every step to maintain
`rel_tol`. Opposed to fixed-step integration. [Chapter 6.]

**A/m.** &mdash; Area-to-mass ratio, in m&sup2;/kg. The single
parameter SpOdy needs to compute SRP acceleration when used in
debris mode; in spacecraft mode it is derived from `area_m2 /
mass_kg`. [Chapter 6.]

**Argument of periapsis (`ω`).** &mdash; The classical orbital
element measuring the angle from the ascending node to the
periapsis, in the orbital plane. [Chapter 9.]

**Batch.** &mdash; A multi-case parameter sweep, driven by a CSV
file and a column-to-target mapping. [Chapter 7.]

**Cases file.** &mdash; The CSV file describing one case per row,
referenced from `batch.cases_file`. [Chapter 7.]

**Central body.** &mdash; The body the propagation is centred on
(today only the Moon is supported). Defines the inertial frame
the state vector is expressed in. [Chapter 6, chapter 10.]

**Central-inertial frame.** &mdash; The reference frame the engine
propagates in: origin at the central body's centre, axes aligned
with the ICRF (J2000), right-handed. [Chapter 10.]

**Coverage profile.** &mdash; The user's choice of ephemeris time
window in the setup wizard: *Modern era* (1950 &ndash; 2050) or
*Full pack* (1550 &ndash; 2650). [Chapter 3.]

**`Cr`.** &mdash; Coefficient of reflectivity. `1.0` = pure
absorbing, `2.0` = pure mirror, intermediate values for partial
diffuse reflection. SpOdy uses the cannonball SRP model with a
single Cr. [Chapter 6.]

**Cross-track.** &mdash; The third RIC axis: perpendicular to the
orbit plane, positive along the angular-momentum direction.
[Chapter 10.]

**Cubic Hermite.** &mdash; Interpolation scheme used by the diff
dispatcher when the two trajectories are on different time grids.
Uses position and velocity at each sample to produce a piecewise
cubic that matches both at sample points. [Chapter 11.]

**Data dir.** &mdash; The folder the wizard populates with the
external data files. By default a `data/` subfolder next to
`spody-gui.exe`, overridable via the wizard. [Chapter 3.]

**DE440.** &mdash; The JPL planetary ephemeris model SpOdy uses
for third-body positions. Distributed as ASCII chunks; the
wizard converts these into the internal `de440.spody` binary.
[Chapter 3, chapter 6.]

**Delta mode.** &mdash; The batch column mapping mode in which the
CSV cell value is *added* to the TOML's nominal value, as opposed
to *overriding* it. Useful for Monte-Carlo perturbations.
[Chapter 7.]

**Diff plot.** &mdash; A plot in the `Diff (pick 2 files)` folder
of the plot tree. Subtracts trajectory B from trajectory A
sample-by-sample (with cubic Hermite interpolation when the
grids do not match). [Chapter 9, chapter 11.]

**Eccentricity (`e`).** &mdash; The classical orbital element
measuring how non-circular the orbit is. `e = 0` for circular,
`0 < e < 1` for elliptical, `e = 1` for parabolic, `e > 1` for
hyperbolic. [Chapter 9.]

**Eclipse threshold.** &mdash; The sunlight-fraction value at
which an eclipse event fires. Configured in `[events]`.
[Chapter 6.]

**Engine.** &mdash; The C executable `spody.exe` that runs the
actual numerical integration. Distinct from the GUI
(`spody-gui.exe`), which is a Python application that drives the
engine. [Chapter 1.]

**Ephemeris.** &mdash; Time-tabulated planetary positions and
velocities used to compute third-body gravity. SpOdy uses JPL's
DE440 in an internal binary format. [Chapter 3, chapter 6.]

**Event.** &mdash; A discrete occurrence detected by the engine
during a propagation: an IMPACT against a celestial body surface,
or (when enabled) an ECLIPSE entry/exit. [Chapter 6.]

**`et_start_s`.** &mdash; The start epoch of the simulation, in
TDB seconds past J2000. [Chapter 6.]

**Form.** &mdash; The Run tab's left pane: one widget per TOML
field, with range checks and live preview. [Chapter 5.]

**`GRGM1200B`.** &mdash; The recommended lunar spherical-harmonic
gravity coefficient set, derived from the NASA GRAIL mission's
observations. SpOdy reads the PDS-distributed `.tab` file at
runtime. [Chapter 3.]

**Hard run-guard.** &mdash; The runtime check that refuses to
launch the engine if any required data file is missing. It also
blocks the **Validate** button for the same reason. [Chapter 3,
chapter 13.]

**Harmonics degree.** &mdash; The truncation order `N` of the
spherical-harmonic gravity expansion. Higher = more accurate but
O(N&sup2;) more expensive. [Chapter 6.]

**ICRF.** &mdash; International Celestial Reference Frame, the
modern realisation of the J2000 inertial axes. SpOdy's
central-inertial frame is ICRF-aligned. [Chapter 10.]

**In-track.** &mdash; The second RIC axis: perpendicular to the
radial direction, in the orbital plane, in the half-plane
containing the velocity. For circular orbits this aligns with the
velocity direction. [Chapter 10.]

**Inclination (`i`).** &mdash; The classical orbital element
measuring the orbit plane's tilt with respect to the reference
plane (the ICRF XY plane in SpOdy). [Chapter 9.]

**Inline table.** &mdash; The TOML syntax `{ key = value, &hellip; }`,
used in `[batch.columns]` for delta-mode column descriptors.
[Chapter 7.]

**J2000.** &mdash; The standard epoch 2000-01-01 12:00:00 TT, used
as the zero of TDB-seconds time scale. [Chapter 6.]

**`mu`.** &mdash; Gravitational parameter, in km&sup3;/s&sup2;. For
the Moon: `4902.800066`. [Chapter 9.]

**Override mode.** &mdash; The batch column mapping mode in which
the CSV cell value *replaces* the TOML's nominal value. The
default. [Chapter 7.]

**PA (Moon Principal Axes).** &mdash; The Moon's body-fixed
reference frame in which the GRGM1200B harmonics are expressed,
and the same frame the impact lat/lon views project IMPACT events
into. The rotation from ICRF to PA is `Rz(psi) · Rx(theta) ·
Rz(phi)` where `(phi, theta, psi)` are the lunar mantle Euler
angles from DE440. [Chapter 10.]

**Overlay-safe.** &mdash; Property of a plot that draws exactly
one line per file, so an N-file overlay produces N lines (legible)
rather than 3N or more (illegible). [Chapter 9.]

**Plot tree.** &mdash; The lower-half tree in the Analysis tab's
left column, listing the plots applicable to the currently-loaded
file's kind. [Chapter 8.]

**RAAN (`Ω`).** &mdash; Right Ascension of the Ascending Node,
the classical orbital element measuring the angle from the
reference X axis to the orbit's ascending node. [Chapter 9.]

**Radial.** &mdash; The first RIC axis: from the central body to
the spacecraft, positive outward. [Chapter 10.]

**`rel_tol`.** &mdash; The relative error tolerance the integrator
maintains per accepted step. Smaller = more accurate, slower.
[Chapter 6.]

**RIC.** &mdash; Radial / In-track / Cross-track. A local orbit
frame defined sample-by-sample from the state vector. Used by the
RIC diff plot. Equivalent to the RSW frame in Vallado's
nomenclature and the Hill frame in Clohessy-Wiltshire studies.
[Chapter 10.]

**RKDP / RKDP45.** &mdash; Runge-Kutta Dormand-Prince 5(4), the
adaptive integrator SpOdy uses. The 5/4 numbers refer to the order
of the embedded error estimate. [Chapter 6.]

**Run-guard.** &mdash; See *Hard run-guard*.

**SPDYOUT_ / SPDYACC_ / SPDYEVT_ / SPDYEVTB.** &mdash; The 8-byte
magics of SpOdy's four binary output formats: trajectory state
vectors, per-force accelerations, per-run events log, and the
batch-aggregated events log respectively. [Chapter 7, chapter 12.]

**spopy.** &mdash; Pure-Python (numpy-only) re-implementation of
spody-core's read-side helpers (DE440 ephemeris reader, lunar
libration, ICRF&nbsp;&hArr;&nbsp;PA rotations). Bundled under
`python/spopy/` and used by the Analysis tab's impact views to
project IMPACT events onto the lunar surface at interactive speed
without spawning a subprocess. Bit-identical to the C
implementation (validated at landing). [Chapter 10.]

**SPICE.** &mdash; NASA NAIF's toolkit and associated kernels.
SpOdy's planetary ephemeris is derived from SPICE-format DE440
data; the recommended validation reference for any SpOdy run is a
SPICE-derived trajectory of the same epochs. [Chapter 11.]

**SRP.** &mdash; Solar radiation pressure. SpOdy uses the
cannonball model: a single A/m and Cr, with eclipse cuts when
enabled. [Chapter 6.]

**Step mode.** &mdash; The `output.mode = "step"` value: one
output record per accepted RKDP step, irregular sampling. Opposed
to fixed-step output. [Chapter 6.]

**Target.** &mdash; A dotted path inside the TOML schema that a
batch column can override. E.g. `spacecraft.mass_kg`,
`debris.am_srp`, `initial_state.position_km[0]`. [Chapter 7.]

**TDB.** &mdash; Barycentric Dynamical Time, the time scale the
DE440 ephemeris is expressed in. SpOdy's `et_start_s` is in TDB
seconds past J2000. [Chapter 6.]

**Third body.** &mdash; A celestial body whose gravity perturbs
the central-body two-body solution but which is not itself
propagated. Configured in `force_model.third_bodies`. [Chapter 6.]

**Tile dashboard.** &mdash; The multi-subplot rendering mode
triggered by the **▦ Tile selected (N)** button: N plots from the
multi-selection are drawn as a grid in a single matplotlib figure.
[Chapter 8.]

**TOML.** &mdash; The input file format SpOdy reads. A
human-readable, strongly-typed configuration syntax; see
<https://toml.io/> for the specification. [Chapter 6.]

**True anomaly (`ν`).** &mdash; The classical orbital element
measuring the angle from the periapsis to the current position,
in the orbital plane, measured in the direction of motion.
[Chapter 9.]

**Two-body.** &mdash; The Kepler-problem central acceleration
`-mu * r / |r|^3`. Always present; the rest of the force model is
added on top. [Chapter 6.]

**Validate.** &mdash; The action of running `spody.exe validate`
against a TOML to check it parses and passes consistency rules,
without actually propagating. [Chapter 5, chapter 12.]

**Vis-viva.** &mdash; The relation `|v|^2 = mu * (2/|r| - 1/a)`
that ties together speed, distance, and semi-major axis on a
Keplerian orbit. Used by the orbital-elements solver to derive
`a` from the state vector. [Chapter 9.]

**Working directory.** &mdash; The folder the Analysis tab scans
for `.bin` files. Set explicitly via **Change&hellip;** or auto-
filled from the loaded TOML's parent. [Chapter 8.]
