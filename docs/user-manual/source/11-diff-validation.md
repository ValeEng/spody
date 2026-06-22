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
