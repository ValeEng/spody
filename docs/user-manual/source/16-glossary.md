# Glossary

A short list of the technical terms used through the manual,
defined where SpOdy uses them, in alphabetical order. The chapter
that introduces each term in detail is given in square brackets.

**A/m.** &mdash; Area-to-mass ratio, in m&sup2;/kg. The parameter
that scales radiation pressure and drag: given directly in debris
mode (`am_srp`, `am_drag`), derived as `area_m2 / mass_kg` in
spacecraft mode. [Chapter 6.]

**Accelerations file.** &mdash; The `SPDYACC_` binary named by
`output.accelerations_file`: at every record, the contribution of
each force to the total acceleration and the eclipse fraction. Read
by the **Per-force breakdown** plot. [Chapters 6, 9, 12.]

**Adaptive step.** &mdash; The integrator chooses the step size `h`
at every step so that the estimated error stays within `rel_tol`.
Both SpOdy integrators are adaptive. [Chapter 6.]

**Altitude crossing.** &mdash; An event fired when the altitude above
a body crosses a value given in `[[events.altitude_crossing]]`, both
going up and going down. [Chapter 6.]

**Argument of periapsis (`ω`).** &mdash; The classical orbital
element measuring the angle from the ascending node to the
periapsis, in the orbital plane. [Chapter 9.]

**Batch.** &mdash; A multi-case parameter sweep, driven by a CSV
file and a column-to-target mapping. [Chapter 7.]

**Bias (Monte Carlo).** &mdash; The mean deviation of the Monte Carlo
cases from the nominal at one epoch: how far the centre of the cloud
has moved. Near zero while the dynamics is linear for the spread
given. [Chapter 14.]

**Body shape.** &mdash; The shape every body has for shadows, impacts
and altitudes, set by the required `force_model.body_shape`: the
`pck00011` spheroid (`"ellipsoid"`, geodetic altitude) or the sphere of
the equatorial radius (`"equatorial_sphere"`). [Chapter 6.]

**Broadcast ephemeris.** &mdash; The orbit a GNSS satellite transmits
in its navigation message (RINEX NAV files), good to a few metres.
Converted by `spody convert gps` / `glonass`. [Chapters 12, 15.]

**Calibration (density).** &mdash; The factor `k` multiplying the
model density in the drag force, fitted against a reference orbit by
`spody calibrate`; a constant (`density_scale`) or a table of nodes
(`density_scale_file`). [Chapters 6, 11, 12.]

**Cases file.** &mdash; The CSV file describing one case per row,
referenced from `batch.cases_file`. [Chapter 7.]

**Central body.** &mdash; The body the propagation is centred on: the
Moon or the Earth in high fidelity. Defines the origin of the
inertial frame and the gravity field. [Chapters 6, 10.]

**`central_body_fixed` / `central_body_fixed_rotating`.** &mdash; The
two body-fixed readings of an initial state: the same axes (Earth
ITRS, Moon PA), with the velocity taken as inertial in the first and
as relative to the rotating body (ECEF-style, &omega;&times;r added)
in the second. [Chapter 6.]

**Central-inertial frame.** &mdash; The reference frame the engine
propagates in: origin at the central body's centre, axes of the ICRF,
right-handed. [Chapter 10.]

**Coverage profile.** &mdash; The user's choice of ephemeris time
window in the setup wizard: *Modern era* (1950 &ndash; 2050) or
*Full pack* (1550 &ndash; 2650). [Chapter 3.]

**`Cr`.** &mdash; Coefficient of reflectivity of the cannonball
radiation-pressure model: `1.0` = pure absorbing, `2.0` = pure
mirror. [Chapter 6.]

**CR3BP.** &mdash; Circular restricted three-body problem: a massless
body moving under two primaries that circle their barycentre at a
fixed distance. `dynamics_model = "cr3bp"`, Earth&ndash;Moon. [Chapters
6, 10.]

**Cross-track.** &mdash; The third RIC axis: perpendicular to the
orbit plane, positive along the angular-momentum direction.
[Chapter 10.]

**Cubic Hermite.** &mdash; Interpolation scheme used by the diff
plots when the two trajectories are on different time grids and the
reference carries velocities. Uses position and velocity at each
sample to produce a piecewise cubic that matches both at sample
points. [Chapter 11.]

**Curvilinear coordinates.** &mdash; Position differences measured
along the nominal orbit instead of along straight RIC axes: radius
difference, arc in the orbital plane, arc out of it. A cloud stretched
along the orbit has no false radial spread in them. [Chapter 14.]

**Data dir.** &mdash; The folder the wizard populates with the
external data files. By default a `data/` subfolder next to
`spody-gui.exe`, overridable via the wizard. [Chapter 3.]

**DE440.** &mdash; The JPL planetary and lunar ephemeris (Park et al.
2021) SpOdy uses for the positions of the Sun, Moon and planets and
for the Moon's orientation. Distributed by JPL as ASCII files; the
wizard converts them into the internal `de440.spody` binary.
[Chapters 3, 6, 10.]

**Delta mode.** &mdash; The batch column mapping mode in which the
CSV cell value is *added* to the TOML's nominal value, as opposed
to *overriding* it. [Chapter 7.]

**Diff plot.** &mdash; A plot in the `Diff (pick 2 files)` folder
of the plot tree. Subtracts trajectory B from trajectory A
sample-by-sample (with cubic Hermite interpolation when the
grids do not match). [Chapters 9, 11.]

**Discontinuity stop.** &mdash; A step boundary placed on a jump of the
force model (the 3-hour space-weather grid of NRLMSISE-00, a shadow
contact), so that no step integrates across it. [Chapter 6.]

**DOP853.** &mdash; Dormand&ndash;Prince 8(5,3): an eighth-order
Runge&ndash;Kutta method with error estimators of order 5 and 3
(Hairer et al. 1993). Longer steps than RKDP45 for the same accuracy.
[Chapter 6.]

**Earth radiation pressure.** &mdash; The push of sunlight reflected by
the Earth (albedo) and of its thermal infrared on the satellite, set
by `force_model.earth_radiation_pressure`. [Chapter 6.]

**Eccentricity (`e`).** &mdash; The classical orbital element
measuring how non-circular the orbit is. `e = 0` for circular,
`0 < e < 1` for elliptical, `e = 1` for parabolic, `e > 1` for
hyperbolic. [Chapter 9.]

**Eclipse fraction.** &mdash; The fraction of the solar disc visible
from the satellite: 1 in sunlight, 0 in umbra, in between in
penumbra. [Chapters 6, 9.]

**Eclipse threshold.** &mdash; The sunlight-fraction value at
which an eclipse event fires. Configured in `[events]`.
[Chapter 6.]

**Engine.** &mdash; The C executable `spody.exe` that runs the
actual numerical integration. Distinct from the GUI
(`spody-gui.exe`), which is a Python application that drives the
engine. [Chapter 1.]

**EOP.** &mdash; Earth orientation parameters: the measured pole
position, UT1 &minus; UTC and celestial-pole offsets the IERS
publishes in `finals2000A.all`, needed to rotate between the ICRF and
the ITRS. [Chapters 3, 10.]

**Ephemeris.** &mdash; Time-tabulated positions and velocities of
bodies. SpOdy uses JPL's DE440 in an internal binary format.
[Chapters 3, 6.]

**ET.** &mdash; Ephemeris time, the SPICE convention: TDB seconds past
2000-01-01 12:00:00 TDB. Every epoch in a SpOdy input is an ET.
[Chapter 6.]

**`et_start_s`.** &mdash; The start epoch of the simulation, as ET.
[Chapter 6.]

**Event.** &mdash; A discrete occurrence detected by the engine
during a propagation: an IMPACT against a body surface, an ECLIPSE
contact or an altitude crossing (when enabled), and the two life
markers of every object. [Chapter 6.]

**Form.** &mdash; The Run tab's left pane: one widget per TOML
field, with range checks and live preview. [Chapter 5.]

**Gauss-Markov process.** &mdash; A random quantity that decays
towards zero with a correlation time `tau_s` and is kicked by white
noise (first-order, Ornstein&ndash;Uhlenbeck). The model of the
Monte Carlo process noise. [Chapter 14.]

**General relativity.** &mdash; In SpOdy, the Schwarzschild
correction to the central body's gravity (IERS 2010 eq. 10.12), set
by `force_model.general_relativity`. [Chapter 6.]

**Geodetic altitude / latitude.** &mdash; Height above a spheroid
along its surface normal, and the angle of that normal from the
equator: the altitude and latitude of a map. What
`body_shape = "ellipsoid"` uses for IMPACT, altitude crossings and
the impact maps. [Chapter 6.]

**GP elements (TLE, OMM).** &mdash; General-perturbation mean elements
of the SGP4 theory, as distributed in two-line element sets and
orbit mean-element messages. Not a state: `spody convert gp` turns
them into one. [Chapter 12.]

**GRGM1200B / EIGEN-6C4.** &mdash; The gravity fields SpOdy uses for
the Moon (from the GRAIL mission, Goossens et al. 2020, degree 1200)
and the Earth (combined satellite and surface data, Förste et al.
2014, degree 2190). [Chapters 3, 6.]

**Hard run-guard.** &mdash; The runtime check that refuses to
launch the engine if any required data file is missing. It also
blocks the **Validate** button for the same reason. [Chapters 3, 13.]

**Harmonics degree.** &mdash; The truncation degree `N` of the
spherical-harmonic gravity expansion. Higher = more accurate but
more expensive, as (N+1)&sup2;. [Chapter 6.]

**ICRF.** &mdash; International Celestial Reference Frame, realised by
extragalactic radio sources; its axes agree with the J2000 mean
equator and equinox to about 0.02&Prime;. SpOdy's central-inertial
frame is ICRF-aligned. [Chapter 10.]

**Impact.** &mdash; The event fired when the trajectory reaches the
surface of the central body or of a third body. Always on; it ends
the run. [Chapter 6.]

**In-track.** &mdash; The second RIC axis: perpendicular to the
radial direction, in the orbital plane, in the half-plane
containing the velocity. For circular orbits this aligns with the
velocity direction. [Chapter 10.]

**Inclination (`i`).** &mdash; The classical orbital element
measuring the orbit plane's tilt with respect to the reference
plane (the ICRF XY plane in SpOdy). [Chapters 9, 10.]

**Inline table.** &mdash; The TOML syntax `{ key = value, &hellip; }`,
used in `[batch.columns]` for delta-mode column descriptors.
[Chapter 7.]

**ITRS.** &mdash; International Terrestrial Reference System, the
Earth-fixed frame, realised by the ITRF. The Earth's body-fixed frame
in SpOdy. [Chapter 10.]

**J2000.** &mdash; The standard epoch 2000-01-01 12:00:00. SpOdy's
ET counts TDB seconds from it. [Chapter 6.]

**Jacobi constant.** &mdash; The integral of motion of the CR3BP,
`C = 2Ω − |v|²` in the rotating frame; its drift measures the
integration error of a CR3BP run. [Chapter 9.]

**Lagrange interpolation.** &mdash; The polynomial through *n* samples
of positions alone, its derivative giving the velocity. The diff
plots read an SP3 reference (positions only) with a 10-node Lagrange
polynomial centred on each epoch. [Chapter 11.]

**Life markers.** &mdash; The `INITIAL_STATE` and `FINAL_STATE`
records every object writes to the events file at its start and at
the end of its run, whatever ended it. [Chapter 6.]

**Love number (`k_nm`).** &mdash; How strongly a body's gravity
field answers a tide: the deformation adds `k` times the tidal
potential, seen from the surface. Earth `k2` &asymp; 0.30 (IERS 2010),
Moon `k2` = 0.024116 (GRAIL). [Chapter 6.]

**LVLH.** &mdash; Local vertical / local horizontal axes: z towards
the central body, y against the orbit normal, x completing the triad
(&asymp; along the velocity). Accepted for batch inputs. [Chapters 7,
10.]

**Monte Carlo.** &mdash; Propagating many copies of a scenario with
the uncertain quantities drawn at random and measuring how the copies
spread; `spody uncertainty montecarlo`. [Chapter 14.]

**`mu` (GM).** &mdash; Gravitational parameter, in km&sup3;/s&sup2;.
For the Moon `4902.8001184575496` (DE440), for the Earth
`398600.4415`; a run with a gravity file uses the GM of the file for
its central term. [Chapter 10.]

**Nominal (case 0).** &mdash; In a Monte Carlo, the scenario
propagated exactly as written; every deviation is measured from it.
[Chapter 14.]

**NRLMSISE-00.** &mdash; The empirical model of the neutral
atmosphere (Picone et al. 2002) that gives SpOdy's drag its density,
driven by the solar flux F10.7 and the geomagnetic index Ap.
[Chapter 6.]

**`orbit_plane`.** &mdash; The initial-state frame of the lunar
frozen-orbit literature: z normal to the Earth's apparent orbit about
the Moon at the start epoch (Ely 2005). [Chapter 6.]

**Overlay-safe.** &mdash; Property of a plot that draws exactly
one line per file, so an N-file overlay produces N lines (legible)
rather than 3N or more (illegible). [Chapter 9.]

**Override mode.** &mdash; The batch column mapping mode in which
the CSV cell value *replaces* the TOML's nominal value. The
default. [Chapter 7.]

**PA (Moon Principal Axes).** &mdash; The Moon's body-fixed frame,
aligned with its axes of inertia, in which GRGM1200B is expressed.
The rotation from ICRF to PA is `Rz(psi) · Rx(theta) · Rz(phi)`,
`(phi, theta, psi)` the lunar libration angles of DE440. [Chapter 10.]

**Plot tree.** &mdash; The lower-half tree in the Analysis tab's
left column, listing the plots applicable to the currently-loaded
file's kind. [Chapter 8.]

**Precise orbit (SP3).** &mdash; An orbit reconstructed after the fact
by an analysis centre (IGS, CODE, ESA) from tracking data, good to
centimetres, distributed in SP3 files. The truth of the GNSS
benchmarks. [Chapters 12, 15.]

**Process noise.** &mdash; In a Monte Carlo, an error that changes
along the trajectory (the density, an unmodelled acceleration),
drawn as a Gauss-Markov process per case. [Chapter 14.]

**RAAN (`Ω`).** &mdash; Right Ascension of the Ascending Node,
the classical orbital element measuring the angle from the
reference X axis to the orbit's ascending node. [Chapter 9.]

**Radial.** &mdash; The first RIC axis: from the central body to
the spacecraft, positive outward. [Chapter 10.]

**`rel_tol`.** &mdash; The relative error tolerance the integrator
maintains per accepted step. Smaller = more accurate, slower.
[Chapter 6.]

**Re-run.** &mdash; The GUI tab that launches a finished batch again
on a chosen subset of its cases. [Chapter 4.]

**RIC.** &mdash; Radial / In-track / Cross-track. A local orbit
frame defined sample-by-sample from the state vector. The same axes
as Vallado's RSW, the RTN of the CCSDS messages and the Hill frame of
the Clohessy-Wiltshire equations. [Chapter 10.]

**RKDP45.** &mdash; Dormand&ndash;Prince 5(4) (Dormand & Prince 1980):
a fifth-order Runge&ndash;Kutta solution with an embedded
fourth-order estimate of its error. SpOdy's reference adaptive
integrator. [Chapter 6.]

**Run-guard.** &mdash; See *Hard run-guard*.

**Seed.** &mdash; The integer that fixes every random number of a
Monte Carlo: same file, same seed, same cases bit for bit.
[Chapter 14.]

**Solid tide.** &mdash; The extra gravity of a central body
deformed by the Moon and the Sun (or, for the Moon, by the Earth
and the Sun), set by `force_model.solid_tides`. Not the direct pull
of those bodies on the satellite, which is the third-body force.
[Chapter 6.]

**SPDYOUT_ / SPDYACC_ / SPDYEVT_ / SPDYEVTB.** &mdash; The 8-byte
magics of SpOdy's trajectory, accelerations, per-run events and
many-case events (batch and Monte Carlo) binaries. [Chapters 7, 12.]

**SPDYUQM_ / SPDYUQC_.** &mdash; The Monte Carlo binaries: moments
(bias and covariance at every epoch) and clouds (every case at the
snapshots). [Chapter 14.]

**SPICE.** &mdash; NASA NAIF's toolkit and its kernels (Acton 1996).
SpOdy follows SPICE's time conventions (ET, `str2et`) and uses
SPICE-format mission reconstructions, such as the LRO orbit, as
truth in its benchmarks. [Chapters 6, 15.]

**spopy.** &mdash; Pure-Python (numpy) re-implementation of
spody-core's read-side helpers (DE440 reader, lunar libration, Earth
orientation, time conversions, Kepler and CR3BP transforms), used by
the GUI so that it never calls the engine for a conversion; the time
conversions agree with the engine to the last bit. [Chapter 10.]

**SRP.** &mdash; Solar radiation pressure. SpOdy uses the
cannonball model: a single A/m and Cr, with the shadow of every
modelled body. [Chapter 6.]

**Step mode.** &mdash; The `output.mode = "step"` value: one
output record per accepted integrator step, irregular sampling.
Opposed to the fixed output grid. [Chapter 6.]

**Synodic frame.** &mdash; The frame of the CR3BP: origin at the
barycentre, x from the first primary to the second, rotating with
them. [Chapter 10.]

**Target.** &mdash; A dotted path inside the TOML schema that a
batch column or a Monte Carlo parameter can change. E.g.
`spacecraft.mass_kg`, `debris.am_srp`,
`initial_state.position_km[0]`. [Chapters 7, 14.]

**TDB / TT.** &mdash; Barycentric Dynamical Time, the time of the
ephemerides (ET = TDB seconds past J2000), and Terrestrial Time, the
time of the geocentric system; they differ by a &plusmn;1.66 ms annual
term. The integrator advances TDB, or TT with `integrator.time_scale =
"tt"` around the Earth. [Chapter 6.]

**TEME.** &mdash; True equator, mean equinox: the frame in which the
SGP4 theory gives its states. Rotated to the ICRF by `spody convert
gp`. [Chapter 12.]

**Third body.** &mdash; A celestial body whose gravity perturbs
the central-body two-body solution but which is not itself
propagated. Configured in `force_model.third_bodies`. [Chapter 6.]

**Tide-free / zero-tide.** &mdash; Where a gravity file keeps the
permanent part of the tide: nowhere (tide-free, add the whole tide)
or inside its coefficients (zero-tide, add only the rest). The value
of `force_model.solid_tides`. [Chapter 6.]

**Tile dashboard.** &mdash; The multi-subplot rendering mode
triggered by the **▦ Tile selected (N)** button: N plots from the
multi-selection are drawn as a grid in a single matplotlib figure.
[Chapter 8.]

**TOML.** &mdash; The input file format SpOdy reads (TOML 2021). A
human-readable, strongly-typed configuration syntax. [Chapter 6.]

**True anomaly (`ν`).** &mdash; The classical orbital element
measuring the angle from the periapsis to the current position,
in the orbital plane, measured in the direction of motion.
[Chapter 9.]

**Two-body.** &mdash; The Kepler-problem central acceleration
`-mu * r / |r|^3`. Always present; the rest of the force model is
added on top. [Chapter 6.]

**Uncertainty file (`.uq.toml`).** &mdash; The small TOML file, with
a `[montecarlo]` table, that names a scenario and says which of its
quantities are uncertain and by how much; *uq* = uncertainty
quantification. [Chapter 14.]

**Validate.** &mdash; The action of running `spody.exe validate`
against a TOML to check it parses and passes consistency rules,
without actually propagating. [Chapters 5, 12.]

**Vis-viva.** &mdash; The relation `|v|^2 = mu * (2/|r| - 1/a)`
that ties together speed, distance, and semi-major axis on a
Keplerian orbit. Used by the orbital-elements solver to derive
`a` from the state vector. [Chapter 9.]

**Wilson interval.** &mdash; The 95 % interval of a probability
estimated as a fraction of cases (k impacts out of N), printed at the
end of a Monte Carlo log. With no impact, the one-sided bound
1 &minus; 0.05^(1/N) &asymp; 3/N is given instead. [Chapter 14.]

**Working directory.** &mdash; The folder the Run and Analysis tabs
scan for TOML and `.bin` files. Set with **Browse&hellip;** in the
top bar, or adopted automatically from a loaded TOML's scenario
folder. [Chapters 4, 8.]
