# Validation and benchmarks

This chapter collects the benchmarks that test **SpOdy as a whole**:
a complete scenario (initial state, force model, integrator, frames,
time scales, output) propagated and compared with an orbit that was
measured, not computed by SpOdy. It is also the place where
comparisons with other software and with further real-data campaigns
will be added.

Checks that concern **one feature** stay next to that feature, where
the reader who turns the feature on will find them; section 15.8 is an
index of them.

In plain words, the results so far:

- around the Moon (LRO, 6 days, against the NASA reconstruction) the
  propagation stays within 1.4 km, and within 0.9 km once the gravity
  field is expanded far enough (degree 150);
- around the Earth at GNSS altitude (GPS and GLONASS, 7 days, against
  IGS / CODE precise orbits) the error grows almost linearly along the
  orbit, 0.1&ndash;0.4 km per day, because the cannonball radiation
  pressure is switched off in these scenarios and the satellites' real
  radiation pressure is not a cannonball;
- in low Earth orbit with drag, after one density-scale factor per day
  is fitted (`spody calibrate`), the along-track residual of the
  International Space Station is 8&ndash;41 m per day.

## Truth, cross-checks and what is measured

Three kinds of comparison appear in this manual, and they are not
equivalent:

| Kind | What the reference is | Can it say SpOdy is right? |
|---|---|---|
| **Truth** | an orbit reconstructed from tracking data by an independent team: SPICE kernels of NASA/NAIF and of the mission, IGS / CODE precise orbits (SP3), operator ephemerides (CCSDS OEM) | yes, within the accuracy of the reconstruction |
| **Cross-check** | the same model computed by another program (Orekit, GMAT, Tudat) | it says the model is implemented as the other program implements it; it cannot say the model is right |
| **Internal check** | an exact result (a closed-form case, a known identity) or an independent re-implementation | it says the code computes what its documentation says |

Only the first kind is a benchmark in the sense of this chapter. The
second and third kind are listed in section 15.8.

### How the numbers are computed

Every number in this chapter is computed in the same way, so the
tables can be compared with each other:

- **A** is the SpOdy propagation, **B** the reference. B is evaluated
  at A's epochs, matched by **absolute** time: `ET = et_start_s + t`
  for A, `ET = t0 + t` for B, with `t0` the time anchor printed in the
  conversion log of the reference (chapter 12, *Conversion log*).
- When B carries velocities and is dense (the LRO SPICE reference,
  11 s), it is interpolated with the cubic Hermite of the diff plots
  (chapter 11). When B is an SP3 file (positions only), it is
  interpolated with a 10-point Lagrange polynomial on positions, the
  usual practice for SP3 orbits. A broadcast reference is used only at
  its own epochs (every 30 min for GLONASS, every 2 h for GPS), never
  interpolated across its gaps.
- `|Δr|` is the distance between A and B [m]. **R, I, C** are its
  radial, in-track and cross-track components in the RIC axes of A
  (chapter 10).
- **RMS to day d** is `sqrt(mean(|Δr|²))` over every epoch from `t = 0`
  to the end of day *d*: a cumulative number, the one the GNSS tables
  have always quoted. **max in day d** is the largest `|Δr|` inside day
  *d* only.

The measurements of sections 15.2&ndash;15.6 were made on 2026-11-07
with spody `9e183e4` and spody-core `d796a3d` (Release build, MSVC,
Windows 10), with the data files the setup wizard installs; the GPS
and GLONASS sections were measured again on 2026-11-08, after their
examples' initial states were regenerated, with the same engine
(the propagation is bit-identical).

## LRO around the Moon, against SPICE

**Scenario.** `examples/lro_6day/`: the Lunar Reconnaissance Orbiter
from 2009-09-18 12:00 UTC for 6 days, in its near-circular polar
mapping orbit about 50 km above the surface (|r| = 1781 km at the
start). GRGM1200B gravity (Goossens et al. 2020), Earth and
Sun as point masses (DE440, Park et al. 2021), no radiation pressure,
`rkdp45` at `rel_tol = 1e-11`, output every 60 s.

**Reference.** `spice_reference.bin`, the LRO orbit reconstructed by the
mission's navigation team (SPICE kernel of the PDS / NAIF archive),
46053 states 11.1 s apart; the initial state of the scenario is its
first state, so `|Δr| = 0` at `t = 0`.

| harmonics degree | `|Δr|` max | `|Δr|` at 6 d (R / I / C) | RMS to day 6 | cost |
|---|---|---|---|---|
| 80 (the example) | 1131 m at 2.1 d | 191 m (30 / &minus;153 / 110) | 357 m | 3.1 s |
| 150 | 932 m at 6.0 d | 875 m (&minus;16 / &minus;873 / 63) | 485 m | 11.8 s |
| 300 | 899 m at 6.0 d | 847 m (&minus;18 / &minus;844 / 59) | 456 m | 52.0 s |

The largest velocity difference at degree 80 is 0.76 m/s, which is what
a 1.1 km in-track offset implies on this orbit (the offset is about
0.7 s of motion; in that time the velocity turns by n&middot;0.7 s).

**How to read it.**

- **The degree must be about 150 here.** Between degree 80 and 150 the
  trajectory changes by up to 1.39 km in 6 days; between 150 and 300 by
  60 m at most (28.6 m at the end). The small final error at degree 80
  is a cancellation, not accuracy: in the middle of the arc the error
  is 1.1 km.
- **What is left at degree 150 and above is a linear in-track drift**,
  about 145 m per day, with the radial and cross-track components
  bounded below 30 m and 65 m. A linear in-track drift is an error of
  the mean motion: a semi-major-axis error of about 0.8 m in the
  initial state, or a missing along-track force. Radiation pressure
  (solar and lunar albedo/infrared), absent in this scenario, is the
  largest force left out. The comparison cannot tell these two causes
  apart; an orbit fit would.

## GPS G11 around the Earth, against IGS final orbits

**Scenario.** `examples/gps_g11_validation/`: GPS satellite G11 from
2024-01-21 00:00 GPS time for 167.75 h. EIGEN-6C4 gravity (Förste et
al. 2014) to degree 70, Moon and Sun as point masses, **no radiation
pressure**, `rkdp45` at `rel_tol = 1e-12`, output every 900 s. The
initial state comes from the broadcast ephemeris of the first day
(`spody convert gps`).

**Reference.** IGS final orbits (`IGS0OPSFIN`, SP3, 15 min), converted
with `spody convert sp3`. Broadcast and precise orbit agree to 2.1 m
RMS (2.5 m at most) over the whole week: that is the floor any
broadcast-started comparison inherits at `t = 0`.

| day | RMS to day d | max in day d |
|---|---|---|
| 1 | 210 m | 410 m |
| 2 | 378 m | 810 m |
| 3 | 543 m | 1.20 km |
| 4 | 704 m | 1.58 km |
| 5 | 862 m | 1.95 km |
| 6 | 1.02 km | 2.31 km |
| 7 | 1.16 km | 2.67 km |

At `t = 0` the error is 2.3 m, the broadcast error of the initial
state; at the end it is 2.28 km, of which 2.25 km in-track.

**The initial state was regenerated on 2026-11-08.** Until then the
example's `input.toml` carried a state produced by the converter of
June 2026, before the Earth angular velocity was corrected for polar
motion (CHANGELOG, 2026-10). That state differed from the one the
converter writes today by 11.8 m in position and 2.8 mm/s in velocity,
and gave 51 m RMS over day 1 and 581 m over the week: about half the
numbers above, from an initial state that was *less* faithful to the
broadcast. The better result was luck in the sign of the velocity
error, not a better model.

**How to read it.** A velocity difference of 2.8 mm/s, well inside
what a broadcast ephemeris can guarantee, halves or doubles the
seven-day result. The error is almost entirely in-track and grows
linearly: the signature of a force missing along the orbit, here the
solar radiation pressure, combined with the initial-state error, which
moves the mean motion. This benchmark therefore measures the initial
state and the missing radiation pressure, not the gravity, frame or
time chain, whose errors are orders of magnitude smaller (the radial
component stays below 0.46 km, the cross-track one below 7 m). Turning the
cannonball SRP on does not close the gap: a GPS satellite is a box
with wings, and the radiation-pressure models that fit GNSS orbits are
either box-wing models (Rodriguez-Solano et al. 2012) or the empirical
ECOM models estimated with the orbit (Beutler et al. 1994; Springer et
al. 1999; Arnold et al. 2015), neither of which SpOdy implements.

## GLONASS R03 around the Earth, against CODE precise orbits

**Scenario.** `examples/glonass_r03_validation/`: GLONASS satellite R03
from the first broadcast epoch of 2024-01-21 (00:15 UTC) for 167.5 h,
same force model and integrator as the GPS case, output every 1800 s
(one record per broadcast epoch).

**References.** The CODE multi-GNSS final orbits (`COD0MGXFIN`, SP3,
5 min), and the broadcast states of the seven days (`spody convert
glonass`). Broadcast and precise orbit agree to 2.5 m RMS (3.7 m at
most).

| day | RMS to day d | max in day d |
|---|---|---|
| 1 | 130 m | 290 m |
| 2 | 243 m | 581 m |
| 3 | 354 m | 871 m |
| 4 | 467 m | 1.16 km |
| 5 | 578 m | 1.45 km |
| 6 | 689 m | 1.73 km |
| 7 | 804 m | 2.02 km |

The radial component stays below 0.84 km, the cross-track one below
5 m. Against the broadcast states the numbers are the same within 1 m,
which is what a 2.5 m broadcast floor allows.

As for GPS, the initial state was regenerated on 2026-11-08. The state
the example carried until then was 24.0 m and 4.0 mm/s away from the
one the converter writes today, and its `et_start_s` was 0.48 ms
earlier than the broadcast epoch it was taken from (the label shift of
the 2026-07 TDB correction); it gave 176 m RMS over day 1 and 1.43 km
over the week. Here the old state was the worse one: the same few mm/s
moved the result the other way.

**How to read it.** The same as GPS: a linear in-track drift from the
initial state and the missing radiation pressure. The two GNSS cases
together show that a seven-day GNSS benchmark started from a broadcast
state is decided by millimetres per second of initial velocity; to
measure the force model itself the initial state has to be fitted to
the precise orbit first.

## ISS with drag: the bundled calibration

**Scenario.** `examples/iss_drag_calibration/`: the International
Space Station from 2024-07-03 12:00 UTC for 15 days, EIGEN-6C4 to
degree 70, Moon and Sun, NRLMSISE-00 drag (Picone et al. 2002) with the
mass, drag area and Cd of the operator file (473 009 kg, 1442.8 m²,
2.70), observed space weather.

**Reference.** The NASA/JSC public ISS ephemeris of 2024-07-03 (CCSDS
OEM, 4 min), converted with `spody convert oem`. It is the operator's
**prediction** at that date, not a reconstruction: what is fitted here
mixes the density-model error with the difference between JSC's
space-weather forecast and the space weather that then occurred.

**Result** of `spody calibrate` with 24 h windows (each window starts
from the reference state, chapter 12):

| quantity | value |
|---|---|
| fitted density scale k, window by window | 0.79, 0.69, 0.68, 0.74, 0.72, 0.64, 0.67, 0.64, 0.57, 0.59, 0.59, 0.52, 0.54, 0.52, 0.52 (1-σ of the fit 0.0001&ndash;0.0006) |
| in-track RMS per window, k = 1 | 2.48&ndash;2.98 km |
| in-track RMS per window, fitted k | 7.7&ndash;40.7 m |
| pooled k (one constant over 15 days) | 0.601 |
| cost | 15 windows, 13 s |

**How to read it.** With the model's own density (k = 1) the station
drifts 2.5&ndash;3 km a day along the orbit away from its predicted
position; every fitted k is below 1, so the model gives more drag than
the prediction assumed. One factor per day removes
99 % of it. The factor drifts from 0.79 to 0.52 over the fortnight,
which is the part of the error a single constant cannot follow, and
the reason `density_scale_file` takes a table rather than one number.

## Drag: earlier measurements

The following measurements were made in July 2026 with SpOdy
v0.3.0-beta, when the drag model was introduced (chapter 11 describes
the method). The data are not bundled and the runs have not been
repeated since; the force model changed afterwards only in parts that
are small next to drag at these altitudes (general relativity, tides,
radiation pressure from the Earth, all off by default).

**ISS, two tracking-fresh states 5 days apart** (the 2024-07-03 and
2024-07-09 JSC ephemerides; truth = the first day of the newer file;
Ap 2&ndash;10, 81-day F10.7 &asymp; 205):

| run | in-track at 5 d |
|---|---|
| drag off | &minus;146 km |
| drag on, ballistic coefficient of the file (k = 1) | +53 km |
| drag on, fitted k* = 0.725 | 973 m RMS, 1.8 km max |
| JSC's own 5-day prediction, for comparison | 1.3 km RMS, 2.1 km max |

**Swarm-A, 5.5 days, against the ESA precise orbit** (`SP3ACOM`,
reduced-dynamic, cm level), k* fitted on the first two days only;
in-track RMS per day: 51, 37 m (fit), then 120, 81, 453, 997 m
(hold-out, never seen by the fit); radial &le; 25 m and cross-track
&le; 17 m RMS throughout. The density product of the same mission
(`DNSAPOD`, densities derived from the precise orbit; van den IJssel et
al. 2020) puts NRLMSISE-00 at +38 % (median) over the window,
rising from +28 % to +51 % day by day, the drift seen in the hold-out
days.

## Determinism and regression

These properties hold for every run and are checked on every change
of the engine:

- **Same input, same bytes.** A run repeated with the same input and
  the same build writes byte-identical binaries (chapter 11, *Validating
  against another SpOdy run*).
- **Threads do not change results.** Every batch case and every Monte
  Carlo output is bit-identical for 1 and 8 threads (chapter 7,
  *Threading*; chapter 14, *Random numbers and reproducibility*); only
  the row order of an aggregated events file depends on the thread
  count.
- **New physics off = old results.** Every force or option added after
  a release reproduces the earlier results bit for bit when it is off
  (the regression set of 75 output files of the bundled scenarios).
- **Twins agree.** The Python routines the GUI uses to convert times,
  frames and elements (`spopy`) agree with the engine to the last bit
  for time conversions and to the documented tolerance elsewhere
  (chapter 10).

## Index of feature-level checks

| feature | check | where |
|---|---|---|
| body shapes (`body_shape`) | shadow contacts vs GMAT and Orekit; geodetic altitude vs SPICE `recgeo` | chapter 6, *Body shapes* |
| harmonics adaptive degree | bit-identical to the fixed degree, speed-up 2.3&ndash;3.7&times; | chapter 6, *Letting the degree follow the orbit* |
| solid tides | acceleration vs an independent implementation; effect vs Orekit and Tudat | chapter 6, *Solid-body tides* |
| general relativity | acceleration vs the formula at 40 digits; effect vs Orekit and Tudat | chapter 6, *General relativity* |
| Earth radiation pressure | exact uniform-sphere case; independent 20 000-ray quadrature | chapter 6, *Earth radiation pressure* |
| `dop853`, discontinuity stops | cost at equal error vs `rkdp45` (GPS, LAGEOS-2, ISS) | chapter 6, *Choosing the integrator*, *Discontinuity stops* |
| `time_scale = "tt"` | vs Orekit and GMAT (TT) and Tudat (TDB), two-body LEO and GPS | chapter 6, *Integration time scale* |
| eclipse events | contact times vs Orekit, GMAT, Tudat | chapter 6, `eclipse_threshold` |
| event refinement | trigger time vs integrator accuracy | chapter 6, `[[events.altitude_crossing]]` |
| Monte Carlo statistics | vs an independent recomputation, a `spody batch` rerun, the linear theory of each process-noise model | chapter 14 |
| UTC &harr; ET in the form | bit-identical to SPICE `str2et` / `et2utc` | chapter 6, `[simulation]` |

## Comparisons with other software

*Reserved.* Whole-scenario comparisons of SpOdy with Orekit, GMAT and
Tudat on the same initial states and force models will be reported
here, each with: the scenario, the versions of the programs, the force
model of each side term by term, the integrator settings, and the
difference in R, I, C over the arc. Until then the cross-checks of
single forces listed in section 15.8 are the only comparisons with
other software.

## Further real-data benchmarks

*Reserved.* Planned additions follow the format of sections
15.2&ndash;15.6: LAGEOS-2 (laser ranging orbits), Galileo (precise
orbits), GRACE-FO and Sentinel-3 (precise orbits with drag), and LRO
over several windows of different years.
