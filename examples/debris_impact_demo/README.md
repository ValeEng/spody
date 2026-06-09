# Debris impact demo — batch with guaranteed surface crossings

Ten-case batch crafted to exercise the four batch-event views in the
Analysis tab (timeline, time-to-impact histogram, survival timeline,
impact lat/lon map + 3D). Every case is a fragment dropped on a
prograde polar trajectory whose perilunio sits below the lunar
surface — most cases crash, a few have enough extra energy to skim
the surface and survive the 3 h window.

## Scenario

| Aspect             | Setting                                                |
|--------------------|--------------------------------------------------------|
| Object schema      | `[debris]` (`Cr` + `am_srp`, no mass)                  |
| Base trajectory    | polar elliptic — apolunio 2000 km, perilunio 1700 km   |
| Central body       | Moon                                                   |
| Harmonics          | GRGM1200B truncated to degree `N = 20`                 |
| Third bodies       | Earth + Sun                                            |
| SRP                | enabled (cannonball)                                   |
| Duration           | 3 hours                                                |
| Output cadence     | every 30 s                                             |
| Cases              | 10 — variations on `dv_x` (longitude) + `dv_z` (time/energy) |

The base orbit (zero-delta case `c00`) starts at the apolunio on the
`+Y` axis with velocity along `+Z`, making a prograde polar pass. It
crosses the equator going north, peaks over the north pole, and dives
toward the south pole where the perilunio — and therefore the surface
crossing — sits about 50 minutes in.

Per-case deltas split into two axes:

- **`dv_x_kms`** (cross-track at the apolunio) tilts the orbit plane,
  shifting the longitude at which the fragment hits the southern
  hemisphere. Cases `c01..c04` walk `±0.05`, `±0.10` km/s and produce
  the longitudinal spread you see in the lat/lon map.
- **`dv_z_kms`** (in-track at the apolunio) bumps the orbital energy.
  Negative deltas drop the perilunio further below the surface and
  bring the impact in earlier; positive deltas lift the perilunio
  above the surface, and the fragment survives the run. Cases
  `c05..c09` are the energy variations.

## Expected event mix

After `spody batch input.toml` finishes:

- ~6–7 cases produce one `IMPACT` row in
  `output/<run>/debris_impact_demo_events.bin`, latitudes clustered
  near the south pole, longitudes spread across ~120° of arc.
- ~3–4 cases survive the full 3 h. They appear in the survival
  timeline as green bars reaching the right edge.
- Each case (impacted or not) may also produce 0–2 `ECLIPSE` rows
  depending on the Sun geometry — these populate the timeline view
  but are filtered out of the impact-only plots.

The exact numbers depend on the harmonic-gravity perturbations
(GRGM1200B at N=20 is non-trivial near the surface) and on the date
the SRP shadow geometry resolves to. The numbers above are typical
for the committed `et_start_s`.

## Run

From the GUI:

1. `spody-gui` → **File → Open** → `examples/debris_impact_demo/input.toml`.
2. **Run → Batch** (`Ctrl+B`). Per-case state binaries land in
   `output/<UTC-ISO8601>/`, the aggregated events file is
   `debris_impact_demo_events.bin` in that same folder.
3. Switch to the **Analysis** tab, working dir set to
   `examples/debris_impact_demo/output/`. Click the events file and
   try the five leaves under `events_batch`:
   - **Events timeline** — IMPACT + ECLIPSE stacked.
   - **Time-to-impact histogram** — distribution of `t_trigger`.
   - **Survival timeline per case** — red bars (impacted, ending at
     `t_impact`) vs green bars (survivors, reaching `duration_s`).
   - **Impact lat/lon on Moon** — equirectangular scatter; if the
     NASA SVS LROC texture is downloaded via the Setup wizard, it
     shows up as a photo background.
   - **Impact 3D on Moon** — same impacts as 30-km spheres on the
     textured Moon, colour-keyed by `case_idx`.

From the CLI:

```bash
spody batch examples/debris_impact_demo/input.toml
```

## Files

- [`input.toml`](input.toml) — scenario; `cases_frame = "icrf"` so no
  RIC rotation is involved (the deltas are already in ICRF).
- [`cases_impact.csv`](cases_impact.csv) — 10 detriti, see header.
- `output/` — per-case binaries (`.gitignored`).

## Prerequisites

Same data files as [`../lro_6day/`](../lro_6day/) (DE440 ephemeris
and GRGM1200B harmonics). The Moon texture is optional — used only by
the impact map / 3D view as background — and lives at
`<data_dir>/Moon/lroc_color_poles_2k.tif`. The Setup wizard has a
dedicated row for it.
