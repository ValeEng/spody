# Diff and validation workflow

The diff plots (chapter 9, section *Diff (pick 2 files)*) compare
two trajectories sample by sample. This chapter explains how to use
them in the two most common scenarios: regression between SpOdy
runs, and validation against an external reference such as a SPICE
ephemeris. It also documents the time-grid alignment rules and
how to read the resulting numbers.

## Two scenarios, same dispatcher

The same eight diff plots apply to two distinct workflows:

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

### Position-only references (SP3)

`spody convert sp3` writes zero velocities, because an SP3 file
carries positions only. A B file whose velocity columns are all
exactly zero takes neither path above: Hermite would use v = 0 as the
slope, and the velocity diff would be A's velocity itself. Instead,
on every A epoch inside B's span, the dispatcher fits a Lagrange
polynomial through the 10 B positions centred on that epoch (the
standard way to read a precise orbit, reviewed by Schenewerk 2003;
the same polynomial as spody-core's `spody_interp_lagrange`,
mirrored bit for bit by `spopy.interp_lagrange`) and takes its
derivative as B's velocity. On B's own epochs the position is the B
sample itself. An epoch that two consecutive SP3 day files both carry
(the midnight between them) is used once. This path is taken on
matching grids too, so that the velocity diff is meaningful; the info
label reads `B has positions only: 10-node Lagrange onto A's grid
(… -> … samples, v = derivative)`.

### Both files must share `t = 0`

The diff matches the two files on their time column `t`, the seconds
since each file's own start: the trajectory binaries carry no
absolute epoch. A propagation starts at its `et_start_s`; a converted
reference starts at the time anchor its conversion log prints
(chapter 12, *Conversion log*). The two must be the same instant, or
every sample is compared with the reference at the wrong time. The
bundled GPS example starts at the anchor of its broadcast and SP3
references (both 2024-01-21 00:00 GPS time), so either reference
diffs directly. The GLONASS example starts at the first broadcast
epoch, 00:15 UTC, while its CODE SP3 reference starts at 00:00 GPS
time, 918 s earlier: in the Analysis tab, diff it against the
broadcast reference. Chapter 15 compares it with the SP3 in absolute
time, outside the GUI.

### Why cubic Hermite

For trajectory data sampled at coarse intervals relative to the
orbital period, linear interpolation cuts the chord of the orbit:
in the middle of an interval the error is the sagitta
`(v &Delta;t)^2 / (8 R)`. For LRO sampled every 11 s (v &asymp;
1.65 km/s, R &asymp; 1790 km) that is about 23 m, and 1.9 km for the
same orbit sampled every 100 s &mdash; far above the metre-level
signal a diff is meant to measure.

Cubic Hermite interpolation with `v` as the analytical derivative
catches the position curvature exactly to cubic order, reducing
the interpolation error to negligible levels for any practical
sampling cadence. We use linear interpolation for velocity
because velocity is smoother than position (one fewer derivative
of the underlying acceleration field), and linear interpolation
of v is adequate at the precision the diff plot needs.

## Reading the magnitudes

The numbers the bundled validation scenarios give (LRO against
SPICE, GPS G11 against IGS final orbits, GLONASS R03 against CODE
precise orbits, the ISS and Swarm drag calibrations) are benchmarks
of the whole software and are collected, with the method used to
compute them, in chapter 15. What follows is how to read any diff.

- **Look at RIC first.** An error that grows linearly **in-track**
  while radial and cross-track stay bounded is a mean-motion error:
  the two orbits have slightly different periods, because of a
  semi-major-axis difference in the initial state or a force acting
  along the orbit (drag, radiation pressure, a truncated field). A
  growing **cross-track** error points at the orbit plane (a missing
  force normal to it, a frame error); a **radial** error that grows
  on average means the two orbits sit at different heights.
- **Look at the whole arc, not the last point.** An error can grow,
  peak and come back: on the LRO example at degree 80 the error is
  1.1 km on day 2 and 0.19 km at the end (chapter 15). The maximum
  and the RMS over the arc are the honest summaries; the final value
  alone can be a cancellation.
- **Know the floor of the reference.** A reference is only as good
  as its own reconstruction: a GNSS broadcast ephemeris is good to a
  few metres, an IGS precise orbit to centimetres. Differences below
  the floor carry no information.

### Two references for one propagation

A GNSS propagation can be compared with two references at once:
the broadcast ephemeris (`spody convert gps` / `glonass`), easy to
obtain but good only to a few metres, and a precise orbit (`spody
convert sp3`), good to centimetres. Comparing the two references
with each other gives the floor of the broadcast for free. On the
bundled examples of 2024-01-21 the broadcast agrees with the precise
orbit to 2.1 m RMS for GPS G11 and 2.5 m RMS for GLONASS R03, so
for both the propagation error, hundreds of metres after a day, is
read equally well against either reference (chapter 15).

SP3 files carry positions only; the diff plots read them with a
Lagrange polynomial (*Position-only references* above). Up to release
0.4.1 they used the cubic Hermite on them too, with the zero
velocities as slopes, which is wrong whenever the two grids differ.

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

* **The density model.** NRLMSISE-00 (Picone et al. 2002) is an
  empirical climatology fitted to data collected before 2000.
  Empirical models of this kind carry density errors of tens of per
  cent that change with solar and geomagnetic activity (Emmert 2015);
  against the Swarm densities of 2024 it was measured **+28 to +51 %
  at 470&ndash;500 km** (chapter 15). This is a property of the
  model, not an implementation defect.
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
   density along the same orbit (van den IJssel et al. 2020). The density product enables a
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

### Results

The results of this method on the ISS (NASA/JSC ephemerides) and on
Swarm-A (ESA precise orbit and densities), and of `spody calibrate`
on the bundled ISS example, are in chapter 15 (*ISS with drag* and
*Drag: earlier measurements*). In short: one fitted factor brings a
5-day ISS prediction to the class of the operator's own forecast,
and a factor fitted on two days holds Swarm-A to about 200 m/day
in-track afterwards.

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
  capture &mdash; would need models driven by newer solar and
  geomagnetic indices (JB2008, Bowman et al. 2008) or assimilative
  corrections; in a Monte Carlo it is represented by the density
  process noise of chapter 14.*
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
