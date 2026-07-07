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

#### 3D orbit + central body

The trajectory rendered as a polyline in a 3D scene with the
central body sphere at the origin. The sphere is textured with the
equirectangular image configured in **Settings &rsaquo; Paths**
for the active central body (Moon texture for `central_body =
"Moon"`, Blue Marble texture for `central_body = "Earth"`); when
the texture is absent the sphere falls back to flat grey.

The scene also draws **two reference-frame triads** from the
central body's centre (introduced in v0.1.2-beta for the Moon and
generalised in v0.1.3-beta to track the active central body):

- **Body-fixed triad** (bright RGB, &sim;2.1 body radii long)
  with billboard labels `X_pa / Y_pa / Z_pa` for the Moon
  (Principal Axes) or `X_itrf / Y_itrf / Z_itrf` for the Earth
  (ITRS). For Earth the triad **rotates in real time** along the
  animated trajectory: at every animation frame the engine's
  `R_ICRF→ITRS` rotation is recomputed (IAU 2006/2000A_R06 +
  the IERS EOP table) and applied to the basis vectors, so the
  triad and the textured continents stay locked to the same
  Earth orientation. For the Moon the same animation step applies
  the libration-driven `R_ICRF→PA` rotation from DE440.
- **ICRF triad** (muted RGB, &sim;1.8 body radii long, opacity
  0.25) with labels `X_icrf / Y_icrf / Z_icrf`, fixed in scene
  coordinates. Useful as a static reference against the rotating
  body-fixed triad.

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

When the **Moon appears as a third body** in an Earth-centred
scene (`third_bodies = ["Moon", ...]`), its marker is rendered as
a textured sphere using the wizard-managed Moon texture (rather
than a flat-grey dot), so the Moon stays recognisable at its true
&sim;384,000 km distance.

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

#### |Δr| distribution

Histogram of the per-sample position-error magnitude across the
shared sample grid. Bin count is `min(60, sqrt(N))` so a 6-day
LRO regression (~9k samples at 1-minute cadence) gets &asymp;60
bins and a short smoke test still produces a readable histogram.

A small box in the bottom-right corner of the figure reports the
**RMS**, **median**, **p95** and **max** of the distribution. The
RMS is `sqrt(mean(|Δr|^2))`, the canonical single-number summary
in orbit-determination and conjunction work; the percentiles are
the standard summary when the underlying error distribution is
**not normal** (which is the usual case for SPICE-vs-SpOdy
`|Δr|`).

#### |Δr| empirical CDF

The cumulative distribution function of the same `|Δr|` samples,
drawn as a `steps-post` line from 0 to 1. The CDF is the natural
tool when the question is **"what fraction of samples is below
this error?"** &mdash; read the curve at the horizontal value of
interest, scale up to the percentile on the y axis. Conversely,
to find the value such that 95% / 99% / 99.9% of samples are
below it, scan the y axis at 0.95 / 0.99 / 0.999.

A box in the bottom-right corner of the figure reports the
**RMS**, **median**, **p95**, **p99**, **p99.9** and **max**.
These (minus the RMS) are the **distribution-free percentile
budgets** &mdash; the right answer regardless of whether the
underlying distribution is normal, log-normal, multi-modal, or
anything else. The standard "`mean &plusmn; 2σ`" 95% interval
only works if the distribution is normal; the empirical p95 read
off the CDF works in every case, which is the point of this plot
for regression work. The RMS sits alongside the percentiles
because OD and conjunction budgets traditionally quote it.

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

The single-run events binary records every detected event
(IMPACT, ECLIPSE entry/exit) along **one** propagation.

#### Events timeline

A horizontal-axis-time scatter plot with one marker per detected
event. The y axis is one **series** per row: IMPACT, ECLIPSE, and
&mdash; for altitude crossings &mdash; **one row per crossed
altitude**, labelled with the altitude itself (e.g. `alt 45 km`)
rather than the raw event-kind code. Altitude rows split ascending
vs descending crossings by marker (&#9650; up / &#9660; down); the
altitude values are clustered straight from the records, so the plot
still works with no `input.toml` in sight. When more than one body is
involved the altitude rows carry the body's NAIF id.

**When to use.** Quick visualisation of when, in the simulation
window, each event happens. For impact-prediction work, the
first IMPACT marker gives the predicted collision time at a
glance; for altitude monitoring, each band's row shows the entry /
exit cadence at a glance.

**Overlay-safe.** No (multi-series plot).

## Batch-events plots (`SPDYEVTB`)

When a batch run with `events_log` enabled finishes, the engine
writes a **single aggregated events file** (`SPDYEVTB` magic,
chapter 7) covering every trigger across every case. Each row
carries an extra `case_idx` (int32) so each event can be joined
back to a row of the cases CSV. The Analysis tab exposes five
views over that file in addition to the generic *Events
timeline* (which still works because it only consumes `t` and
`kind`):

- Time-to-impact histogram
- Survival timeline per case
- Impact lat/lon (equirect)
- Impact lat/lon (Mollweide)
- Impact density heatmap
- Impact 3D on Moon

The four "Impact &hellip;" views project IMPACT rows onto the
lunar surface in the **Moon Principal Axes (PA)** body-fixed
frame (chapter 10): for each event the dispatcher reads
`et = simulation.et_start_s + row.t`, queries the DE440
ephemeris for the lunar libration angles via the bundled
`spopy` package, builds the ICRF&rarr;PA rotation matrix, and
applies it to the `y[0:3]` ICRF state. The Moon's central body
is required (today `spopy`'s libration model is lunar-only;
running with a different `central_body` makes these views show
a friendly "not applicable" message rather than crash).

The four impact views all rely on three pieces of context the
binary itself does not carry: `simulation.et_start_s` (sim time
to ET), `[ephemeris].file` (to query libration), and
`simulation.duration_s` (for survivor counts). They read these
from the **`input.toml` snapshot** the engine drops into the
run folder at every invocation. Opening an events file from
outside any run folder triggers a "snapshot not found" message
in place of the plot.

#### Time-to-impact histogram

Distribution of `t_trigger` across IMPACT rows on a 1D
histogram. Bin count is Sturges' rule capped at 40; the x
axis is in **days**.

**Overlay-safe.** No (single-file aggregate).

#### Survival timeline per case

One horizontal bar per `case_idx`. Bars for cases that impacted
are **red**, ending at the first `t_impact`; bars for survivors
are **green**, extending to `simulation.duration_s`. Cases are
sorted by `t_impact` ascending; survivors follow in `case_idx`
order. The title states the total number of cases, impacted
count, and survivor count.

Reads `duration_s` from the run-folder TOML snapshot; the total
case count comes from the `cases_file` CSV (`#`-prefixed
comment lines and the header row are skipped). When either is
unreachable, only impacted cases are drawn and the title
flags it.

**Overlay-safe.** No.

#### Impact lat/lon (equirect)

Scatter plot of impact positions on the lunar Principal Axes
frame in an equirectangular projection (extent
`[-180, 180]` &times; `[-90, 90]` degrees). Background is the
Moon texture when present (NASA SVS LROC color, chapter 3);
points fall back to a flat-grey background when the texture
is missing.

Marker colour is `time of flight [days]` via the `turbo`
colormap, with a colorbar on the right. The same colour
encoding is reused on the 3D impact view so a fragment can be
recognised across views.

**Overlay-safe.** No.

#### Impact lat/lon (Mollweide)

Same data as the equirect view but on matplotlib's `mollweide`
(equal-area) projection &mdash; the elliptic map that does not
exaggerate the polar areas. Backgrounded by a downsampled
(720 &times; 360) **grayscale** copy of the Moon texture; the
colour scatter on top reads cleanly against the muted
background.

**When to use.** Whenever the impact field is spread over a
wide longitude range &mdash; the equirect projection visually
stretches the high-latitude bands, whereas Mollweide preserves
area.

**Overlay-safe.** No.

#### Impact density heatmap

A 2D histogram of impact (lat, lon) in 2.5&deg; cells (144
&times; 72 bins), rendered as a `pcolormesh` on the Mollweide
projection over the same grayscale Moon background. Empty cells
are transparent (mask) so the surface texture stays visible
where no impacts happened.

The colour scale is the per-cell impact count; the cap is set
to `max(hist)` so a debris cloud with a strong hot spot still
shows graded intensity across the rest. The title declares the
per-cell angular size (e.g. `2.5° x 2.5° bins`).

**When to use.** Bulk debris cloud impact analysis. With a
batch of a few thousand fragments the heatmap reveals clusters
and concentration zones at a glance; with a small batch (a few
dozen impacts) the individual cells stay visible and the view
degrades gracefully.

**Overlay-safe.** No.

#### Impact 3D on Moon

3D scene with the textured Moon at the origin and one **30 km
solid sphere per impact** placed in PA coordinates. Marker
colours follow the same `turbo`-on-time-of-flight encoding as
the 2D maps; no in-scene colorbar (VTK does not offer a
comfortable equivalent), so the 2D views are the colour
legend.

Two **reference-frame triads** are drawn from the centre of
the Moon:

- **PA triad** (bright RGB, ~2.1 Moon radii long) with
  billboard labels `X_pa`, `Y_pa`, `Z_pa`. Since the scene IS
  the body-fixed PA frame, these axes are identity in scene
  coordinates: `X_pa` exits the prime meridian (sub-Earth
  point), `Z_pa` exits the north pole.
- **ICRF triad** (muted RGB, ~1.8 Moon radii long) with labels
  `X_icrf`, `Y_icrf`, `Z_icrf`. These point where the ICRF
  basis vectors land in the scene at `et_start_s`, computed by
  applying `R_icrf_to_pa(et_start)` to `(1,0,0) / (0,1,0) /
  (0,0,1)`. Useful as a sanity check: rotate the scene until
  `X_pa` points at the camera and you have the same view as
  the equirect map centred on the prime meridian.

**Interactions** are the same as **3D orbit + Moon**: left-
drag rotates, scroll zooms, middle-drag pans, <kbd>r</kbd>
resets the camera.

**Overlay-safe.** No (the view is already an N-marker overlay
from a single file).

## Altitude-band plots (events)

When an events file carries `altitude_crossing` triggers on the
central body, an **Altitude bands** folder appears in the plot tree.
The thresholds, sorted ascending, split the altitude axis into bands
(chapter 8, *Info tab &rarr; Altitude bands*); these views draw the
occupancy reconstructed from the crossing records. All are
high-fidelity only (the reconstruction measures altitude from the
central body; the CR3BP synodic frame has no comparable altitude) and
colour the bands with a sequential map (dark = low, bright = high) so
the visual order matches the physical one. The time axis auto-scales
(s / min / h / days) to the analysis window.

#### Time per band

Horizontal bar per band = time spent in it: total time (with the
percentage of the window) for a `propagate` run, or the summed
*object-time* over all cases for a batch. Lowest band at the bottom,
so the axis reads like an altitude axis.

**Overlay-safe.** No.

#### Band occupancy timeline

A single object's occupancy Gantt: one row per band, a coloured bar
for every interval the object spends in that band. Reads as "which
altitude band, when" at a glance. Per-run events file (one object).

**Overlay-safe.** No.

#### Band population over time (batch)

Stacked step-area of how many objects sit in each band over time. The
stack height at any instant is the number of objects still alive (not
yet impacted or stopped); each coloured layer is that band's
instantaneous population, whose time-integral is the Info tab's
*population mean*.

**Overlay-safe.** No (single-file aggregate).

#### Per-case time in band (batch)

Heatmap with one row per case (ascending id) and one column per band,
coloured by the time that case spent in the band. The visual
companion of the *Altitude bands* CSV export &mdash; spot at a glance
which cases live low vs high.

**Overlay-safe.** No (single-file aggregate).
