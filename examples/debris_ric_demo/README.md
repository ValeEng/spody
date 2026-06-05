# Debris RIC demo — sensor-frame batch input

Seven-case batch that exercises the GUI's RIC -> ICRF rotation
pipeline: the source CSV (`cases_ric.csv`) describes a small debris
cloud expressed in the radial / in-track / cross-track frame of a
reference satellite, and the GUI rotates the state columns to ICRF
at Generate-TOML before `spody.exe` reads the resolved file.

## Scenario

| Aspect             | Setting                                                |
|--------------------|--------------------------------------------------------|
| Object schema      | `[debris]` (no `mass_kg`; A/m as primary parameter)    |
| Reference orbit    | LRO 6-day state at 2009-09-18 12:00 UTC (TDB)          |
| Central body       | Moon                                                   |
| Harmonics          | GRGM1200B truncated to degree `N = 20`                 |
| Third bodies       | Earth + Sun                                            |
| SRP                | enabled (cannonball)                                   |
| Duration           | 1 hour                                                 |
| Output cadence     | every 60 s (61 records per case)                       |
| Cases              | 7 — `ref` plus 6 offset fragments (see `cases_ric.csv`)|

## How the workflow splits across GUI and engine

The `spody` propagator (`spody.exe`) **only accepts ICRF central-
inertial state**. The RIC pipeline lives entirely in the GUI:

1. **GUI side** (Python, [`python/spody_gui/frames.py`](../../python/spody_gui/frames.py)).
   When the `[batch]` form's `cases_frame` combo is set to `ric`, on
   every Generate-TOML the GUI:
   - reads the 6 state-vector columns of `cases_ric.csv`
     (identified from `[batch.columns]` — any column whose target is
     `initial_state.position_km[i]` or `velocity_kms[i]`);
   - rotates each row's `[dr_x, dr_y, dr_z]` and `[dv_x, dv_y, dv_z]`
     using `R = ric_basis(r_ref, v_ref)` where the reference orbit
     comes from `[initial_state]`;
   - writes the rotated copy to `cases_ric_wrt_icrf.csv` next to the
     source. Pure change of basis — **no** addition of the reference
     state.
   - rewrites `cases_file` in the saved TOML to point at the rotated
     copy, so `spody.exe` finds the right file.

   The `cases_frame` choice and the original source-file path are
   **runtime-only GUI state**: they are NOT persisted to the TOML.
   This keeps the saved TOML identical to what a CLI user would have
   written by hand and avoids polluting the schema with keys that
   only matter inside the GUI.
2. **C side** (`spody.exe`, `mode = "delta"` in `[batch.columns]`).
   For each case spody computes
   ```
   final[i] = initial_state.<vec>[i] + cell
   ```
   adding the rotated offset to the `[initial_state]` base. No
   spody-core changes are needed; `mode = "delta"` already exists
   since the `e1a8826` commit.

## RIC convention

Sensor-frame **snapshot**: the `dv` components are plain vector
projections onto the instantaneous RIC axes (what an onboard sensor
would report). The GUI does NOT add an `omega × r` term — this is
NOT the Hill / Clohessy-Wiltshire rotating-frame convention used
in rendezvous literature. See the docstring of
[`python/spody_gui/frames.py`](../../python/spody_gui/frames.py).

The 7 cases are intentionally simple to make the rotation visible:

| id      | offset                | A/m     | Cr   |
|---------|-----------------------|---------|------|
| `ref`   | zero (= LRO itself)   | 0.020   | 1.3  |
| `lead`  | +1 km in-track        | 0.020   | 1.3  |
| `trail` | −1 km in-track        | 0.020   | 1.3  |
| `high`  | +0.5 km radial        | 0.050   | 1.3  |
| `low`   | −0.5 km radial        | 0.005   | 1.3  |
| `side_p`| +0.5 km cross-track   | 0.020   | 1.5  |
| `side_n`| −0.5 km cross-track   | 0.020   | 1.8  |

## Run (GUI)

The committed TOML points `cases_file` at `cases_ric.csv` (the
RIC-frame source). The GUI's `cases_frame` combo defaults to `icrf`,
so on first open you have to switch it to `ric` to enable the
rotation:

1. `spody-gui` → **File → Open** → `examples/debris_ric_demo/input.toml`.
2. In the `[batch]` group, change **cases_frame** from `icrf` to `ric`.
   The status line under the combo updates and a *Rotated preview*
   table appears showing the first 10 rows that the rotation will
   produce — sanity-check against `cases_ric.csv` before generating.
3. **Generate TOML** (or just **Run / Batch**, which generates first).
   The GUI writes `cases_ric_wrt_icrf.csv` next to the source and
   updates `cases_file` in the saved TOML to point at it.
4. **Run → Batch** (`Ctrl+B`).

Per-case binaries land in
`examples/debris_ric_demo/output/batch/debris_ric_demo_{ref,lead,trail,high,low,side_p,side_n}.bin`.

## Run (CLI only)

`spody batch` does NOT understand RIC. From the CLI you must
pre-rotate the CSV yourself; the simplest path is:

```python
from pathlib import Path
import numpy as np
from spody_gui.frames import rotate_state_csv_ric_to_eci
demo = Path("examples/debris_ric_demo")
rotate_state_csv_ric_to_eci(
    demo / "cases_ric.csv",
    demo / "cases_ric_wrt_icrf.csv",
    r_ref_km =np.array([1622.030233600,  512.084982400, -529.342614300]),
    v_ref_kms=np.array([   0.648832282,   -0.519033001,    1.440002498]),
    pos_columns=("dr_x_km",  "dr_y_km",  "dr_z_km"),
    vel_columns=("dv_x_kms", "dv_y_kms", "dv_z_kms"),
)
```

Then edit `cases_file` in the TOML to point at
`cases_ric_wrt_icrf.csv` and run:

```bash
spody batch examples/debris_ric_demo/input.toml
```

## Files

- [`input.toml`](input.toml) — scenario; `cases_file` is committed
  pointing at the RIC source, and `[batch.columns]` wires the 6
  state columns to `initial_state.*` with `mode = "delta"`.
- [`cases_ric.csv`](cases_ric.csv) — debris cloud in RIC (source of truth).
- `cases_ric_wrt_icrf.csv` — rotated copy, **generated** at Generate-TOML
  (`.gitignored`).
- `output/` — per-case binaries (`.gitignored`).

## Prerequisites

Same data files as [`../lro_6day/`](../lro_6day/) (GRGM1200B
coefficients and DE440 in `.spody` form). See that example's
README for download notes.
