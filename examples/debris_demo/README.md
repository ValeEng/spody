# Debris demo -- A/m sweep

Three-case batch over a short lunar-orbit scenario, varying the
area-to-mass ratio (`debris.am_srp`) and reflectivity (`debris.Cr`). The
input uses the **debris** object schema, where A/m is the primary
parameter and mass is irrelevant (and absent from the TOML).

## What it does

| Aspect             | Setting                                                  |
|--------------------|----------------------------------------------------------|
| Object schema      | `[debris]` (no `mass_kg`; A/m as primary parameter)      |
| Central body       | Moon                                                     |
| Harmonics          | GRGM1200B truncated to degree `N = 20`                   |
| Third bodies       | Earth + Sun                                              |
| SRP                | enabled (cannonball)                                     |
| Duration           | 1 hour                                                   |
| Output cadence     | every 60 s (61 records per case)                         |
| Cases              | 3 -- vary `am_srp` and `Cr` (see [`cases.csv`](cases.csv)) |

The initial state is borrowed from the LRO 6-day scenario; this demo is
about exercising the debris-mode batch path, not producing scientifically
interesting trajectories.

## Prerequisites

Same data files as
[`../lro_6day/`](../lro_6day/) (GRGM1200B coefficients and DE440 in
`.spody` form). See the LRO example README for download notes.

## Run

From the repo root:

```bash
spody batch examples/debris_demo/input.toml
```

Per-case binaries land in
`examples/debris_demo/output/<UTC-ISO8601>/debris_am_sweep_{low,mid,high}_state_icrf.bin`.

## Notes

- Targets in `[batch.columns]` must use the `debris.*` prefix; a
  `spacecraft.*` target here is rejected at parse with a clear message.
- `force_model.srp = true` is recommended (it's the point of varying
  A/m) but not enforced -- a debris fragment can also be propagated
  under gravity only, in which case `am_srp` / `Cr` go unused.
