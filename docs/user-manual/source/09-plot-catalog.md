# Plot catalogue

This chapter is the reference for every plot SpOdy ships with. The
entries are grouped first by file kind (trajectory, accelerations,
events) and within each by the topic folder the plot tree shows
them under. Each entry documents what the plot displays, the
formulas used, whether the plot is *overlay-safe* (a single line
per file, so an N-file overlay produces N lines rather than 3N),
and whether it is single- or two-file (*diff*).

Notational conventions throughout the chapter:

- `t` &mdash; time elapsed from the start of the propagation, in
  seconds.
- `r, v` &mdash; position and velocity in km and km/s, in the
  central-body inertial frame (chapter 10).
- `|x|` &mdash; Euclidean norm of vector `x`.
- The horizontal axis of any *(t)* plot is the trajectory's own
  time column, in seconds. The toolbar at the top of the canvas
  lets you zoom, pan, and save the current view as PNG.

## Trajectory plots (`SPDYOUT_`)

### State vectors

#### Radial distance |r|

Distance of the spacecraft from the central body's centre, as a
function of time.

**Formula.** `|r|(t) = sqrt(x^2 + y^2 + z^2)`.

**When to use.** Quickest sanity check that the orbit is in the
right altitude band. For LRO, the average altitude is about 50 km
above the Moon's mean radius of 1737.4 km, so `|r|` oscillates
around 1788 km.

**Overlay-safe.** Yes.

#### Speed |v|

Magnitude of the inertial velocity vector.

**Formula.** `|v|(t) = sqrt(vx^2 + vy^2 + vz^2)`.

**When to use.** Companion to `|r|(t)` &mdash; for an elliptical
orbit, `|v|` oscillates inversely to `|r|` per the vis-viva
relation.

**Overlay-safe.** Yes.

#### Position x, y, z

The three Cartesian components of the position vector, on a shared
axes.

**When to use.** When you want to see which component is the
dominant driver of an altitude excursion, or to spot a slow secular
drift on a single axis (typical of certain harmonics-driven
perturbations).

**Overlay-safe.** No (draws three lines per file).

#### Velocity vx, vy, vz

Same as the position components but for the velocity vector.

**Overlay-safe.** No.

### Orbit shape

#### XY projection (and XZ, YZ)

Projection of the orbit onto the named coordinate plane of the
inertial frame, with a green dot at `t = 0` and a red dot at the
final sample.

**When to use.** Visualises the orbit's geometric shape and the
sense of motion (the trajectory line goes from green to red along
the orbit's direction). For a near-polar lunar orbit the XY plot
looks like a tight loop; the XZ and YZ projections show the
inclination directly.

**Overlay-safe.** Yes.

#### 3D orbit + Moon

The trajectory rendered as a polyline in a 3D scene with the
central body sphere at the origin. The sphere is textured with
the equirectangular image configured in **Settings &rsaquo; Paths
&rsaquo; Moon texture** (or rendered grey if no texture is
configured).

**Interactions.**

- Left-drag rotates the camera.
- Scroll zooms in and out.
- Middle-drag pans the camera.
- <kbd>r</kbd> resets the camera to the default oblique view.
- <kbd>Ctrl</kbd>+left-click on a polyline picks it (in overlay
  mode).

The Sun-arrow row above the canvas applies to this plot. Type an
epoch (TDB seconds past J2000) into the field and click
**+ Sun arrow** to add an arrow pointing from the central body
toward the Sun at that epoch. The epoch field auto-fills with the
currently-loaded TOML's `simulation.et_start_s` so it usually
contains a sensible value already.

**Overlay-safe.** Yes (the overlay variant adds N trajectories in
turbo colours plus a legend).

### Orbital elements

The six entries in this folder are the classical Keplerian
elements computed from the state vectors at every sample. The
shared implementation handles the two degenerate cases
gracefully: in a circular orbit (`e ≈ 0`) the argument of
periapsis and the true anomaly are set to 0; in an equatorial
orbit (`i ≈ 0`) the RAAN is set to 0 and its rotation is folded
into the argument of periapsis. The thresholds are tight enough
(`1e-8`) that any realistic propagated orbit is unaffected.

The default central-body gravitational parameter is the Moon's
(`mu = 4902.800066 km^3/s^2`).

#### Semi-major axis a

**Formula.** From the vis-viva relation:
`1/a = 2/|r| - |v|^2 / mu`.

**Units.** km.

**Overlay-safe.** Yes.

#### Eccentricity e

**Formula.** Magnitude of the Laplace-Runge-Lenz vector:
`e_vec = (v × h) / mu - r/|r|`, where `h = r × v` is the
specific angular momentum.

**Units.** Dimensionless.

**Overlay-safe.** Yes.

#### Inclination i

**Formula.** `cos(i) = h_z / |h|`.

**Units.** Degrees.

**Overlay-safe.** Yes.

#### RAAN Ω

**Formula.** With `n = z_hat × h = (-h_y, h_x, 0)` the node line,
`cos(Ω) = n_x / |n|`, quadrant resolved by the sign of `n_y`.

**Units.** Degrees, range `[0, 360)`.

**Overlay-safe.** Yes.

#### Argument of periapsis ω

**Formula.** `cos(ω) = (n · e_vec) / (|n| |e_vec|)`, quadrant
resolved by the sign of `e_vec_z`.

**Units.** Degrees, range `[0, 360)`.

**Overlay-safe.** Yes.

#### True anomaly ν

**Formula.** `cos(ν) = (e_vec · r) / (|e_vec| |r|)`, quadrant
resolved by the sign of `r · v`.

**Units.** Degrees, range `[0, 360)`.

**Note.** For a multi-revolution propagation `ν(t)` shows the
saw-tooth wrap at every orbit. This is the correct shape of the
wrapped angle; if you want a monotone "cumulative" angle, derive
the mean anomaly externally.

**Overlay-safe.** Yes.

### Diff (pick 2 files)

These plots subtract one trajectory from another. Selecting one
of them in the plot tree requires **exactly two** trajectory files
selected in the file tree (sorted top-down = A then B); the dispatch
applies the operation and renders the result.

If the two files share the same time grid sample-by-sample, the
subtraction is direct. If they do not, B is automatically
interpolated onto A's time grid restricted to the overlap window:
position uses cubic Hermite interpolation with B's `v` as the
analytical derivative, velocity uses linear per-component. The
plot title gets a `(B interpolated)` suffix and the info label
states the interpolation parameters so you know the diff is not
direct.

Time-window mismatches with no overlap are reported with a clean
message rather than a numpy traceback.

#### |Δr| (log y)

**Formula.** `|r_A(t) - r_B(t)|` on a logarithmic y axis.

**When to use.** The default error-magnitude plot for orbital
regression. The log scale spans the typical many-orders-of-
magnitude growth from sub-millimetre level (when B is an
interpolation of a finer-grid reference) up to kilometre level
over multi-day propagations.

#### |Δr| (linear y)

Same data on a linear axis. Better for inspecting the *shape* of
the error growth near the end of the propagation, where the log
scale flattens out.

#### |Δv| (log y)

**Formula.** `|v_A(t) - v_B(t)|`.

The velocity error sits typically two to three orders of magnitude
below the position error for well-tuned propagators (the velocity
field is smoother than the position field, and integrator errors
accumulate quadratically in position vs linearly in velocity).

#### |Δv| (linear y)

Same data on a linear axis.

#### Δx, Δy, Δz per component

Per-component position difference: `A.x - B.x`, `A.y - B.y`,
`A.z - B.z` on a shared axes with a legend.

**When to use.** When you want to see which coordinate axis
dominates the error and at what frequency. Modulations periodic
with the orbital period are common; secular drift on one
component points at a specific perturbation mismatch.

#### RIC frame (radial/in-track/cross-track)

The most informative diff plot for orbital regression. The
position-error vector is projected onto the RIC frame of
trajectory A:

- **Radial** axis: `r_hat_A` (positive outward).
- **Cross-track** axis: `c_hat_A = (r_A × v_A) / |r_A × v_A|`
  (orbit normal, positive along `h`).
- **In-track** axis: `i_hat_A = c_hat × r_hat_A` (completes the
  right-handed frame; for circular orbits this aligns with the
  velocity direction).

The three components are plotted on a shared linear y axis with a
legend.

**Interpretation.**

- A growing **in-track** component is the signature of a small
  **energy / timing drift** &mdash; the two propagators have
  slightly different mean motion and one drifts ahead of the other
  along the orbit. Typical for harmonics-degree mismatches.
- A growing **radial** component points at an **altitude error**
  &mdash; one orbit sits slightly higher than the other on
  average.
- A growing **cross-track** component points at an
  **out-of-plane / nodal drift** &mdash; difference in i or RAAN.

For a tuned high-fidelity setup on a near-circular orbit, the
in-track component is the largest by an order of magnitude or
more.

> The "in-track" axis here is the standard RIC convention
> (Vallado's "S" axis, also called the Hill frame). It is **not**
> exactly the LVLH velocity axis, although for circular orbits the
> two coincide. See chapter 10, section 10.4 for the explicit
> relation.

## Accelerations plots (`SPDYACC_`)

The accelerations binary records the per-force accelerations at
every record, alongside the total. These plots break the
contributions down.

#### Total |a_total|

**Formula.** `|a_total| = sqrt(a_x^2 + a_y^2 + a_z^2)` on a
logarithmic y axis.

**When to use.** Quickest sanity check on the order of magnitude
of the total perturbing acceleration. For a low-lunar-orbit at
N=80 harmonics, `|a_total|` is dominated by the central two-body
term (a few `m/s^2` at the surface, dropping with altitude) and
the harmonics contribution (smaller by an order of magnitude).

**Overlay-safe.** Yes.

#### Per-force breakdown (log y)

Each of the active force contributions (two-body, harmonics,
each third body, SRP) is plotted as its own line on a logarithmic
y axis with a legend.

**When to use.** The "who is doing what" plot &mdash; tells you
which force dominates at every part of the orbit. Typical
observations: SRP shows the eclipse cuts (zero during umbra,
modulated by penumbra); harmonics oscillates with the spacecraft's
orbital phase; third-body Earth dominates over third-body Sun for
near-Moon orbits.

**Overlay-safe.** No (draws multiple lines per file).

#### Eclipse fraction

If `[events]` was enabled and the engine recorded the eclipse
sunlight fraction at every step, this plot displays it on a
linear y axis in `[0, 1]`. `1.0` = full sunlight, `0.0` = total
umbra, intermediate values = penumbra.

**Overlay-safe.** Yes.

## Events plots (`SPDYEVT_`)

The events binary records every detected event (IMPACT, ECLIPSE
entry/exit) along the propagation.

#### Events timeline

A horizontal-axis-time scatter plot with one marker per detected
event, colour-coded by event kind. The y axis is purely visual
(separate row per kind for readability).

**When to use.** Quick visualisation of when, in the simulation
window, each event happens. For impact-prediction work, the
first IMPACT marker gives the predicted collision time at a
glance.

**Overlay-safe.** No (multi-kind plot).
