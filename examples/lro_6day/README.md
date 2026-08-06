# LRO 6-day reference propagation

Long-form integration of NASA's Lunar Reconnaissance Orbiter for 6 days
starting from `2009-09-18 12:00 UTC` (ET = `3.065472661824111e+08`).
This is the canonical end-to-end scenario for spody-core and the basis
of the validation against SPICE LRO POD.

## Physics

| Component       | Setting                                                  |
|-----------------|----------------------------------------------------------|
| Central body    | Moon (`mu = 4902.8005821478 km^3/s^2`)                   |
| Harmonics model | `GRGM1200B` truncated to degree `N = 80`                 |
| Third bodies    | Earth + Sun (positions from DE440)                       |
| SRP / drag      | disabled                                                 |
| Integrator      | Dormand-Prince 5(4), `rel_tol = 1e-11`, `h_max = 2700 s` |

Initial conditions come from a SPICE LRO POD query at the start epoch,
not from a Kepler-element approximation. See the `[initial_state]`
block in [`input.toml`](./input.toml).

## Prerequisites

The TOML references two data files that are **not** in the repository
(see [`raw_data/README.md`](../../external/spody-core/raw_data/README.md)):

- `external/spody-core/raw_data/GRGM1200B/gggrx_1200b_sha.tab`
  (NASA PGDA -- spherical-harmonic coefficients)
- `external/spody-core/raw_data/DE440/de440.spody`
  (built once from the JPL ASCII chunks with
  `spody convert ephemeris <folder> 440 01950`, which the GUI's
  Setup wizard also runs for you)

Drop them in place before running the example.

## Run

From the repo root:

```sh
# Optional -- parse + sanity check without integrating.
./build/spody validate examples/lro_6day/input.toml

# Propagate. Output lands in examples/lro_6day/output/ (already created
# by the TOML, but you can redirect anywhere via --out).
./build/spody propagate examples/lro_6day/input.toml
```

On Windows the binary is `build\Release\spody.exe`.

Each run creates its own folder `output/<ts>/` and prefixes every
file inside it with that same UTC-ISO8601 timestamp, so runs never
overwrite each other. Outputs (8641 records each):

| File                                       | Format                                              | Size   |
|--------------------------------------------|-----------------------------------------------------|--------|
| `<ts>/<ts>_input.toml`                     | snapshot of the input that produced the run         | ~2 KB   |
| `<ts>/<ts>_lro_6day_state_icrf.csv`        | header + `%.15e` CSV (`t, x, y, z, vx, vy, vz`)     | ~1.4 MB |
| `<ts>/<ts>_lro_6day_state_icrf.bin`        | magic `SPDYOUT_` + 16 B header + 56 B raw records   | ~484 KB |

Indicative wall time on a desktop x86-64 in Release: ~1.5 s. Adaptive
RKDP45 takes a few hundred accepted steps; switching to
`mode = "step"` in `[output]` keeps one record per accepted step
instead of the uniform 60-s grid.

## Expected accuracy vs SPICE LRO POD

At `N = 80` with `rel_tol = 1e-11` the integrator + force model is
already below the published accuracy of the SPICE LRO POD reconstruction
itself. The **final** position drift vs SPICE LRO POD at `t = 6 days` is
on the order of **~110 m** (dominated by the SPICE reconstruction, not
by spody).

To reproduce the comparison, convert an LRO POD ephemeris covering the
window into a SpOdy reference binary and diff it against this run in the
Analysis tab: load both the propagated `*_state_icrf.bin` and the
reference, pick a **Diff** plot, and the Info tab reports
|&Delta;r| / |&Delta;v| plus the RIC decomposition over the whole span.

Raising `harmonics_degree` to `150` reduces the residual only
marginally (the GRGM1200B coefficients become weakly observed beyond
~150) and roughly halves the runtime margin; see the empirical degree
table in
[`raw_data/README.md`](../../external/spody-core/raw_data/README.md).
