# Diff and validation workflow

The diff plots (chapter 9, section *Diff (pick 2 files)*) compare
two trajectories sample by sample. This chapter explains how to use
them in the two most common scenarios: regression between SpOdy
runs, and validation against an external reference such as a SPICE
ephemeris. It also documents the time-grid alignment rules and
how to read the resulting numbers.

## Two scenarios, same dispatcher

The same six diff plots apply to two distinct workflows:

1. **SpOdy vs SpOdy regression.** You ran a scenario twice with
   slightly different settings (different harmonics degree, with
   and without SRP, on different machines, or before and after a
   code change). The diff tells you how much the two runs
   disagree.
2. **SpOdy vs reference validation.** You have a reference
   trajectory in `SPDYOUT_` format produced by another tool
   &mdash; SPICE, a previously-validated engine, or any other
   source &mdash; and you want to know how close SpOdy gets.

The interaction is identical in both cases: load one file by
clicking it (call this A), Ctrl-click the second (call this B) in
the file tree to multi-select, then click a diff leaf in the plot
tree.

## A is the loaded file, B is the other

The two-file selection has a small but consequential rule: the
**currently-loaded file is treated as A**, and the other selected
file is **B**. Concretely:

- The title and info label show `A = <basename>`, `B = <basename>`
  so you can always read off which is which.
- The RIC frame is built from A's state. Out-of-plane errors are
  measured against A's instantaneous orbital plane, not B's.
- Time-grid alignment (next section) is done **B onto A's grid**,
  so the rendered samples are at A's times.

Swap which is which by clicking the other file in the tree
(making it the loaded one); the previous loaded file remains in
the selection so the second Ctrl-click is not needed.

## Time-grid alignment

The diff dispatcher checks the two arrays' time columns before
subtracting. Two paths:

### Fast path: aligned grids

If A and B share the same time grid sample-by-sample (every pair
of times matches within 1 ms), both arrays pass through unchanged
and the diff is computed directly. This is the case when both
runs were produced by SpOdy at the same `output.mode = fixed`
with the same `output.interval_s`, or are otherwise known to be on
the same fixed grid.

The plot title shows `A = …    B = …` without further annotation.

### Slow path: cubic-Hermite interpolation

If the grids do not match, B is interpolated onto A's grid
restricted to the **overlapping time window**:

- **Position** uses cubic Hermite interpolation, with B's velocity
  vector as the analytical derivative at each sample. This gives
  C&sup1; continuity at sample points and is exact for any
  polynomial position trajectory up to cubic order.
- **Velocity** uses per-component linear interpolation.

The plot title appends `(B interpolated)` and the info label
records the parameters in the form
`B interpolated onto A's grid (46053 -> 8640 samples,
cubic Hermite on r, linear on v)`.

When the two time windows have no overlap at all (one run started
after the other ended), the dispatcher reports `no overlap` cleanly
and refuses to render anything.

### Why cubic Hermite

For trajectory data sampled at coarse intervals relative to the
orbital period, linear interpolation introduces a curvature error
of order `(v &Delta;t)^2 / R`. For LRO at 11 s sampling that is
about 170 m of interpolation error per sample &mdash; much bigger
than the m-level diff signal we want to measure.

Cubic Hermite interpolation with `v` as the analytical derivative
catches the position curvature exactly to cubic order, reducing
the interpolation error to negligible levels for any practical
sampling cadence. We use linear interpolation for velocity
because velocity is smoother than position (one fewer derivative
of the underlying acceleration field), and linear interpolation
of v is adequate at the precision the diff plot needs.

## Reading the magnitudes

Concrete numbers for two of the validation scenarios shipped with
SpOdy: a Moon-centred Apollo-era propagation and an Earth-centred
GNSS broadcast comparison.

### LRO 6-day, N=80 harmonics, vs SPICE LRO reference

| Quantity        | Value        |
|-----------------|--------------|
| Duration        | 6 days       |
| Sample count    | ~8000 (spody) vs ~46000 (SPICE) |
| `|Δr|` at t=0   | 0 m exact (shared IC) |
| `|Δr|` at t=6d  | ~110 m       |
| `|Δr|` max      | ~1.2 km at mid-window |
| `|Δr|` mean     | ~250 m       |
| `|Δv|` max      | ~0.8 mm/s    |

The growth pattern of `|Δr|` is **not monotone**: it accumulates,
peaks somewhere in the middle of the propagation, and recovers
toward the end. This is characteristic of the gravity-harmonics
mismatch between SpOdy's degree-80 expansion and SPICE's
reconstructed ephemeris, which uses the full GRGM1200B set.

### RIC interpretation for the same diff

On the same example, the RIC decomposition shows the in-track
component growing roughly linearly to ~1 km mid-window, while the
radial and cross-track components stay below 200 m. This is the
canonical signature of a small **mean-motion drift** induced by
truncating the harmonics expansion at degree 80 &mdash; the two
propagations have slightly different effective orbital period
because they capture slightly different fractions of the
non-spherical potential.

Raising `harmonics_degree` to 150 reduces the in-track drift
proportionally; raising it beyond 200 yields diminishing returns
because the GRGM1200B coefficients become weakly observed at high
degrees and adding terms can slightly increase the residual.

### GLONASS R03 7-day, N=70 harmonics, vs IGS broadcast

The Earth-centred counterpart is the GLONASS slot 03 broadcast-
nav comparison shipped in `examples/glonass_r03_validation/`. SpOdy
propagates from the first 2024-01-21 broadcast TOC for 167.5 hours
and is diffed against a reference binary built from 7 consecutive
daily RINEX-NAV files (`spody convert glonass <day1.rnx> &hellip;
<day7.rnx> &hellip;`; chapter 12). With `srp = false`,
`harmonics_degree = 70`, and the Moon + Sun as third bodies:

| Day | `|Δr|` RMS | `|Δr|` max |
|-----|------------|------------|
| 1   | 176 m      | 385 m      |
| 2   | 367 m      | 869 m      |
| 3   | 577 m      | 1.4 km     |
| 4   | 803 m      | 2.1 km     |
| 5   | 1026 m     | 2.7 km     |
| 6   | 1232 m     | 3.2 km     |
| 7   | 1425 m     | 3.7 km     |

The RMS grows roughly linearly at ~200 m / day. The signature is
a near-secular in-track residual, the canonical fingerprint of
the un-modelled in-track perturbation forces at GNSS altitude
(box-wing SRP, antenna thrust, empirical ECOM-style
accelerations). Adding a cannonball SRP with guessed Cr / area
makes things worse on this particular spacecraft because the
in-track sign happens to coincide with the residual; closing the
budget to sub-km over a week needs either the proper IGS BOX-WING
SRP model (Rodriguez-Solano 2014) or empirical ECOM5
accelerations, both of which sit outside SpOdy's current
force-model menu.

Independent of the SRP question, the example is the canonical
sanity check that the Earth-orientation pipeline (IAU 2006 +
IERS EOP) is wired correctly: day-1 RMS regresses against the
broadcast at the 177 m level set by the broadcast nav's own OD
precision, which is the floor reachable with a pure force-model
propagation (no per-arc empirical fits).

### GPS PRN 11 7-day, N=70 harmonics, vs IGS Final SP3

The GPS counterpart in `examples/gps_g11_validation/` is built on a
**cleaner two-format reference scheme** &mdash; broadcast nav for the
initial state, IGS Final SP3 for the per-sample truth. The
broadcast bootstrap gives `(r, v)` at broadcast-OD precision
(`~few m / few cm/s`) via the new `spody convert gps` Kepler-with-
corrections propagator (IS-GPS-200 + Remondi 2004; chapter 12),
replacing the previous 5-point Lagrange forward derivative on SP3
positions that gave the SP3 secant rather than the true Keplerian
tangent (`|v0| ~3.57 km/s` vs the correct `~3.87 km/s`, a 7-8%
artefact that swamped the residual at `t = 0`).

With `srp = false`, `harmonics_degree = 70`, Moon + Sun third
bodies, and the cm-precision IGS Final SP3 as ground truth:

| Day | `|Δr|` RMS | `|Δr|` max |
|-----|------------|------------|
| 1   | 46 m       | 91 m       |
| 2   | 128 m      | 316 m      |
| 3   | 212 m      | 552 m      |
| 4   | 300 m      | 800 m      |
| 5   | 390 m      | 1.1 km     |
| 6   | 484 m      | 1.3 km     |
| 7   | 581 m      | 1.6 km     |

`|Δr|` at `t = 0` is **2.3 m** &mdash; the broadcast-vs-SP3-OD
floor, NOT a force-model error. The linear &sim;80 m / day growth
is the same in-track signature as GLONASS, but with a 3-4&times;
smaller day-1 baseline because both endpoints of the diff
(the IC and the truth) are clean.

### Multi-reference comparison: GPS vs GLONASS broadcast OD

The two GNSS examples allow a useful cross-check: diff the same
propagation against **both** the broadcast nav reference AND a
multi-GNSS SP3 reference (e.g. the CODE MGEX `COD0MGXFIN_*.SP3`
files, which include G + R + E + C). Three numbers fall out:

* `prop vs broadcast` &mdash; the legacy reference, easy to build
  but only ~few m of truth precision;
* `prop vs SP3` &mdash; the cm-level truth, but introduces a
  multi-format alignment chore (see *Time-grid alignment* above);
* `broadcast vs SP3` &mdash; the truth-floor of the broadcast
  itself, exposed for free as a side effect of the comparison.

For 2024-01-21:

| Constellation | prop vs brdc (d1) | prop vs SP3 (d1) | broadcast-OD floor |
|---------------|-------------------|------------------|--------------------|
| GPS G11       | 47 m              | 46 m             | ~2 m               |
| GLONASS R03   | 176 m             | 317 m            | ~258 m             |

GPS broadcast is &sim;100&times; tighter than GLONASS broadcast,
which is why a GPS example built on a broadcast-only reference
already measures the propagator's force-model error cleanly.
For GLONASS the SP3 reference is needed to push past the 258 m
broadcast-OD floor &mdash; or the force-model residual must be
recovered indirectly by composing `prop_vs_brdc &approx;
sqrt(prop_vs_SP3^2 - brdc_vs_SP3^2)`.

## Drag validation and ballistic calibration

Validating a drag-enabled propagation (chapter 6,
`[spacecraft.drag]`) is structurally different from the gravity
and GNSS comparisons above, and deserves its own method. This
section documents the process, the calibration method, the
numbers measured with it, and the problems you will meet on the
way &mdash; so you can reproduce the analysis on your own
spacecraft.

### Why drag cannot be validated "as is"

The drag acceleration is

```
a_drag = -1/2 · rho(model) · v_rel^2 · (Cd·A/m) · v_rel_hat
```

and the density `rho` and the ballistic coefficient `Cd·A/m`
enter **only as a product**. A +30% density bias with a correct
ballistic coefficient is indistinguishable &mdash; at the
trajectory level &mdash; from a correct density with a +30%
ballistic error. Both factors are genuinely uncertain:

* **The density model.** NRLMSISE-00 is an empirical climatology
  fitted to pre-2000 data. Against modern orbit-derived density
  references it runs **20&ndash;40% hot at 400&ndash;500 km
  around solar maximum** (the thermosphere has cooled since the
  model's fit epoch). This is a property of the model, not an
  implementation defect.
* **The ballistic coefficient of record.** Free-molecular `Cd`
  is not a plate constant: it depends on attitude, surface
  temperature and gas composition. Operational centres re-estimate
  it continuously &mdash; the NASA/JSC ISS ephemerides changed
  their own published `DRAG_AREA × DRAG_COEFF` by 10% between two
  files six days apart.

Any single drag-on vs truth comparison therefore measures the
*sum* of the two effects. The method below separates them.

### Process: choosing reference data

Two public data families make clean drag benchmarks:

1. **NASA/JSC ISS ephemerides** (CCSDS OEM, EME2000, published
   ~weekly with an archive of dated snapshots). Each file carries
   mass, drag area and Cd in its `COMMENT` block plus a trajectory
   event summary. Pick a **pair of consecutive files** bracketing
   a 3&ndash;7 day gap with **no manoeuvre in the event summary**:
   the older file's first state is a tracking-fresh initial
   condition, the newer file's first day is a tracking-fresh
   truth. Never validate against the *forecast* portion of a
   single file &mdash; that measures the publisher's space-weather
   forecast, not your propagator (see *Problems* below).
2. **ESA Swarm Level-2 precise orbits** (`SP3ACOM` reduced-dynamic
   POD: SP3 format, ITRF, GPS time, cm-level truth), with the
   companion `DNSAPOD` product giving the POD-derived thermospheric
   density along the same orbit. The density product enables a
   **density-space comparison that needs no mass or area at all**.

In both cases prefer a geomagnetically quiet window (daily Ap
below ~15 in the CelesTrak table) so the drag signal is
EUV-driven and the model's storm response stays out of the
budget.

### Method: the single-scale ballistic fit

Introduce one free factor `k` that scales the product:
`a_drag(k) = k · a_drag(nominal)`. Because drag is a small
perturbation, the in-track displacement it accumulates is linear
in `k` to first order, so **two propagations are enough**: with
`I_off(t)` and `I_on(t)` the in-track residuals of the drag-off
and drag-on (k = 1) runs against the truth,

```
I(k, t) ≈ I_off(t) + k · [I_on(t) − I_off(t)]
```

and the least-squares scale over the scored epochs is

```
k* = Σ_t [ −I_off(t) · ΔI(t) ] / Σ_t [ ΔI(t)² ],   ΔI = I_on − I_off
```

Then **verify the linearity by actually re-running** the
propagation with `Cd` multiplied by `k*`: if the chain is healthy
the in-track residual collapses by two orders of magnitude. For an
honest accuracy claim, fit `k*` on a **calibration arc** (the
first 1&ndash;2 days) and quote the residual growth on the
remaining **hold-out days**, which the fit has never seen.

The fitted `k*` is not a fudge: it is exactly the ballistic
re-estimation every operational OD performs, and it cleanly splits
the error budget into a *constant* part (density-model bias ×
ballistic uncertainty, absorbed by `k*`) and a *time-varying* part
(day-to-day thermospheric variability the climatology cannot
capture, visible in the hold-out drift).

### Results (July 2024 window, Ap 2&ndash;10, F10.7a ≈ 205)

ISS, 5-day gap between the 2024-07-03 and 2024-07-09 JSC
ephemerides, truth = first (pre-creation) day of the newer file:

| Run                              | In-track @ 5 d      |
|----------------------------------|---------------------|
| Drag off                         | −146 km             |
| Drag on, BC of record (k = 1)    | +53 km              |
| Drag on, fitted k* = 0.725       | 973 m RMS, 1.8 km max |
| JSC's own 5-day forecast (yardstick) | 1.3 km RMS, 2.1 km max |

The fitted run lands in the same class as the publisher's own
forecast &mdash; which is produced with internal tracking and
attitude timelines. Radial and cross-track stay at tens of metres
throughout.

Swarm-A, 5.5 days against the cm-level `SP3ACOM` POD, `k*` fitted
on the first two days only (Cd 2.6 → 3.29 at the nominal
area/mass), per-day in-track RMS:

| Day | In-track RMS | Arc         |
|-----|--------------|-------------|
| 0   | 51 m         | calibration |
| 1   | 37 m         | calibration |
| 2   | 120 m        | hold-out    |
| 3   | 81 m         | hold-out    |
| 4   | 453 m        | hold-out    |
| 5   | 997 m        | hold-out    |

The scale factor is **stable to 0.6%** between the 2-day
calibration fit and a full-window fit, and the drag-off drift is
−108 km over the same window: with one calibrated number the chain
holds roughly **200 m/day in-track** against a −20 km/day
signal. Radial ≤ 25 m and cross-track ≤ 17 m RMS everywhere.

The independent density-space comparison (NRLMSISE-00 evaluated
along the Swarm orbit at the `DNSAPOD` epochs, same space-weather
inputs the engine uses) gives the model bias directly: **median
+38%** at 470&ndash;500 km over the window &mdash; and rising from
+28% to +51% day by day, which is exactly the time-varying part
that surfaces as the hold-out drift above. The three numbers close
on each other: `k* × BC_nominal × 1.38` reproduces the physical
`Cd·A` expected for the spacecraft geometry.

### Problems and solutions

* **Forecast references flatter or damn you unpredictably.** A
  drag validation first attempted against the forecast portion of
  a single ISS ephemeris "failed" by +7.7 km in two days &mdash;
  entirely explained by an F10.7 spike and a geomagnetic storm
  that post-dated the file's creation. *Solution: anchor both ends
  of the arc on tracking-fresh states from an archive of dated
  files, in a historical window where all space weather is
  observed (`OBS` rows in `SW-All.csv`), never predicted.*
* **The density/BC degeneracy.** No trajectory comparison can
  separate them. *Solution: calibrate the product with `k*` and,
  when a POD-derived density product exists (Swarm), measure the
  density bias independently in density space; the ballistic part
  is whatever remains.*
* **The bias is not constant.** A single `k*` removes the mean
  bias but the residual climatology error (tens of percent over
  days) sets the hold-out floor &mdash; roughly 1 km at 5 days in
  a quiet window at ~480 km. *Solution: a time-varying node table.
  SpOdy applies the calibration natively through
  `force_model.density_scale` (constant `k*`) or
  `density_scale_file` (piecewise-linear `k(t)` nodes, chapter 6),
  so the fitted factor goes where it belongs instead of
  misdeclaring the physical `Cd`; and the whole fit described in
  this section &mdash; window partition, drag on/off arc pairs,
  in-track least squares &mdash; is automated by the
  `spody calibrate` subcommand (chapter 12), which emits the node
  file directly. The remaining ceiling &mdash;
  sub-daily density structure no multiplicative correction can
  capture &mdash; would need storm-time indices (JB2008-class
  models) or assimilative corrections.*
* **Manoeuvres poison the arc silently.** A reboost inside the
  gap turns the whole comparison into noise. *Solution: read the
  event summary in the OEM comments before choosing the pair; for
  spacecraft without event metadata, a kink in the fitted-run
  in-track residual is the diagnostic.*

The same calibration logic applies verbatim to solar radiation
pressure: `Cr·A/m` is one more product of an uncertain optical
coefficient and an effective area, and operational ODs estimate a
`Cr` scale exactly like a ballistic factor. At drag altitudes
(400&ndash;500 km) SRP is only ~5% of drag and its calibration
error is second-order; above ~800 km the roles reverse and SRP
becomes the term worth calibrating (see the GNSS sections above,
where un-modelled SRP dominates the 7-day budget).

## Combining diff plots through the tile dashboard

A useful pattern for the validation workflow is to tile the four
diff plots into a 2&times;2 dashboard for a single overview shot:

1. Click A (your run) in the file tree to load it.
2. Ctrl-click B (your reference) so both are selected.
3. In the plot tree, Ctrl-click each of `|Δr| (log y)`,
   `|Δv| (log y)`, `Δx, Δy, Δz per component`, and `RIC frame`.
4. The `▦ Tile selected (4)` button at the bottom of the plot
   tree lights up. Click it.

The canvas splits into a 2&times;2 grid showing all four diffs at
once. The shared subtitle reports A, B, and the
`(B interpolated)` annotation if applicable.

## Validating against an external reference

The recommended workflow when you have an external reference
trajectory in `SPDYOUT_` format:

1. Place the reference file in the same folder as your TOML (or
   in any folder of your choice).
2. Open the TOML in the Run tab and run the propagation.
3. Switch to the Analysis tab. The working directory is already
   pointed at your TOML's parent.
4. Click your reference file in the file tree to load it.
5. Ctrl-click the SpOdy output file you just produced.
6. Click `RIC frame` in the plot tree.

If your reference file is not yet in `SPDYOUT_` format (e.g. you
have it as a CSV of (t, r, v) rows, or as a SPICE BSP kernel),
you need to convert it first &mdash; SpOdy's reader path accepts
only the binary format. The format is documented in chapter 12,
section *Output binary formats*; producing a converter for it is
typically a 30-line numpy script.

## Validating against another SpOdy run

The simplest regression scenario: run the same TOML twice and
diff. The two runs will be byte-identical unless something has
changed between them (the engine is deterministic for a given
input). A non-zero diff therefore unambiguously points at the
change between the two runs &mdash; a different harmonics degree, a
recompiled engine, a different machine.

This is also the cheapest way to check that a code change you
just made does not silently affect the physics: run before, run
after, diff. The diff plot showing flat zero across all four
panels is the convincing assertion that nothing changed.
