# Batch demo -- mass + SRP sweep

Three-case batch over a short LRO scenario, varying spacecraft mass and
SRP coefficient. Designed to exercise every part of the batch pipeline
end-to-end in a few hundred milliseconds, not to produce scientifically
interesting numbers.

## What it does

| Aspect             | Setting                                                  |
|--------------------|----------------------------------------------------------|
| Central body       | Moon                                                     |
| Harmonics          | GRGM1200B truncated to degree `N = 20`                   |
| Third bodies       | Earth + Sun                                              |
| SRP                | enabled (cannonball)                                     |
| Duration           | 1 hour                                                   |
| Output cadence     | every 60 s (61 records per case)                         |
| Cases              | 3 -- vary `mass_kg` and `Cr` (see [`cases.csv`](cases.csv)) |

The base scenario is the LRO 6-day initial conditions but with a much
smaller `duration_s`, a tiny `harmonics_degree`, and SRP turned on so
the `Cr` override has something to act on.

## Prerequisites

Same data files as
[`../lro_6day/`](../lro_6day/) (GRGM1200B coefficients and DE440 in
`.spody` form). See the LRO example README for download notes.

## Run

From the repo root:

```sh
./build/spody batch examples/batch_demo/input.toml
```

On Windows the binary is `build\Release\spody.exe`.

## What you should see

Three lines of `[i/3] <id>: done in X.XX s` and a final summary.
Output files land in [`output/<UTC-ISO8601>/`](output/) as
`mass_srp_sweep_<id>_state_icrf.{csv,bin}` alongside a snapshot
of the source TOML (`input.toml`) and a timestamped `.log` since
`log_file` is enabled in the input.

The position residual across the three cases at `t = 1 h` is on the
order of centimetres -- consistent with SRP being a tiny perturbation
over an hour. The point of the example is the pipeline, not the
physics.

## How to extend it

- Add a row to [`cases.csv`](cases.csv) to add a case (or remove rows
  to reduce).
- Add a column to the CSV AND a matching entry under `[batch.columns]`
  in [`input.toml`](input.toml) to sweep a new parameter.
- Remove the `[batch]` section to run as a single-scenario `propagate`
  on the base config alone (useful when debugging the base TOML before
  worrying about the sweep).

See [`../README.md`](../README.md) for the full TOML schema reference.
