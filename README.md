# SpOdy

**Simultaneous Propagation of Orbital DYnamics**

[![CI](https://github.com/ValeEng/spody/actions/workflows/ci.yml/badge.svg)](https://github.com/ValeEng/spody/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Status: beta](https://img.shields.io/badge/Status-beta-yellow.svg)](#status)
[![Release](https://img.shields.io/github/v/release/ValeEng/spody?include_prereleases&sort=semver)](https://github.com/ValeEng/spody/releases)

SpOdy is a high-performance orbital dynamics propagator built as a thin
application layer on top of [**spody-core**](https://github.com/ValeEng/spody-core),
the underlying C library. The long-term aim is to make precision astrodynamics
accessible without ceremony: a single, small CLI driver plus an optional
graphical front-end, both fed by a plain-text input file.

---

## Status

**Beta — fully end-to-end, growing scope.** The whole pipeline runs
out of the box, from a `git clone --recursive` to a published
PyInstaller bundle.

**Propagation**

- Single-scenario `propagate` and multi-case `batch`, the latter
  sequential or OpenMP-parallel.
- Two **central bodies**: the Moon, and the Earth with IAU
  2006/2000A_R06 + IERS EOP driving the inertial-to-ITRS rotation.
- Two **dynamics models**, selected per TOML: `high_fidelity` (the
  full force-model integrator) and `cr3bp` (the synodic-rotating
  Circular Restricted 3-Body Problem).
- Two **initial-state flavours**, Cartesian or Keplerian, the latter
  referenced to the central body or to one of the CR3BP primaries.
  Either can be written in the body-fixed basis at the run epoch
  (ITRS or PA) or, around the Moon, in Ely's orbit-plane frame that
  lunar frozen orbits are defined in; the engine rotates to ICRF at
  sim setup.

**Force model**

- Spherical-harmonic gravity with an optional adaptive truncation
  degree that follows the orbit radius at bit-identical output.
- **Multi-occulter SRP eclipse**: every third body can shade the
  satellite, overlapping shadows combined by inclusion&ndash;exclusion,
  so the Earth darkens a lunar orbiter.
- **Atmospheric drag** around the Earth: native NRLMSISE-00 with
  CelesTrak space weather, plus an engine-side density-scale
  calibration against a reference ephemeris.
- **Event detection**: always-on multi-body IMPACT, opt-in ECLIPSE
  and altitude crossings, all localised to sub-millisecond by
  Hermite + Brent.

**Tooling**

- TOML schema validation, per-force acceleration breakdown, and a
  run-folder layout with timestamp-prefixed snapshot + outputs.
- A PySide6 desktop frontend: Setup wizard, TOML editor with
  syntax-aware autocompletion, embedded runner, and a full Analysis
  tab — Plot / Table / Info split, per-plot Export CSV, per-kind
  key/value summary with diff-aware |&Delta;r| / |&Delta;v| / RIC
  rows, batch-event impact maps (equirectangular + Mollweide +
  density heatmap), a 3D body-textured impact scene with body-fixed
  and ICRF triads, an optional equirectangular star-map background,
  diff-RIC plots, and Jacobi-constant conservation for CR3BP.
- Releases ship signed-sha256 bundles for Windows / Linux x86_64 /
  macOS arm64, plus a 14-chapter user manual PDF.

**Validation.** The library underneath (`spody-core`) is checked
against SPICE LRO POD ephemerides (sub-km position drift over the
6-day window, Moon central body), GLONASS R03 broadcast vs MGEX SP3
(177 m RMS on 24h, ~200 m/day linear growth over 7 days, Earth
central body), and a scipy DOP853 differential-corrector closure on
an L1 Lyapunov (30 microns / 1.6e-10 km/s over one synodic period,
CR3BP). The Python-side `spopy` package re-implements the read-side
helpers (DE440 reader, ICRF&lt;-&gt;Moon Principal Axes rotations)
bit-identically: 104/104 checks at atol 1e-9 km/rad, &sim;1 ULP
IEEE 754.

The remaining narrow scope keeps the "beta" label: no Mars /
Sun-Earth central bodies, drag limited to the Earth (the per-body
atmosphere hook exists, the second model does not), no in-app
cases-CSV generator, and the release Win bundle pins Python 3.9 to
dodge a known apiset/PyInstaller interaction on some end-user Win10
builds. See [`CHANGELOG.md`](CHANGELOG.md) for what landed when.

---

## Architecture

```
        [TOML input file]
                |
                v
        +-------------------+
        |   spody (CLI)     |   <- this repo
        +-------------------+
                |  links statically against
                v
        +-------------------+
        |   spody-core      |   <- submodule, https://github.com/ValeEng/spody-core
        +-------------------+
                |
                v
        [CSV / binary output files]
                |
                v
        [Python GUI (PySide6)]   <- TOML editor + runner + Analysis tab
                                    (Plot / Table tabs, impact views, diff plots)
```

The split is deliberate:

- **spody-core** is a clean C99 library, fully reusable on its own.
- **spody** is the executable that turns it into a complete tool: input
  parsing, simulation orchestration, output formatting.
- The Python GUI (under [`python/`](python/)) follows the **Patran/Nastran**
  pattern — it generates the TOML, invokes the binary, and parses the output
  files. It does not link C code directly. The same binary therefore serves
  desktop, batch HPC, and (eventually) a web backend with no source changes.

---

## Build

```bash
git clone --recursive https://github.com/ValeEng/spody.git
cd spody
cmake -B build
cmake --build build --config Release
```

The `--recursive` flag clones the `spody-core` submodule. If you cloned
without it:

```bash
git submodule update --init --recursive
```

Resulting binary:

- `build/spody` on Linux / macOS
- `build/Release/spody.exe` on Windows (MSVC multi-config)

Smoke test:

```bash
$ ./build/spody info
SpOdy app  : 0.4.1-beta
spody-core : 1.2.0  (git <sha>, built <timestamp>)
```

---

## CLI usage

```
spody <command> [options]

Commands:
  propagate   <input.toml> [--out <dir>]  run a single simulation
  batch       <input.toml>                run a multi-case batch
  validate    <input.toml>                check input file (no run)
  convert     <kind> <args...>            convert external formats
                                          (ephemeris | harmonics_icgem |
                                          sp3 | glonass | gps | oem)
  calibrate   <input.toml> <ref.bin> [--window <hours>]
                                          fit the drag density-scale k(t)
  info                                    print version + capabilities
  maxhgdegree <harmonics_file> <x> <y> <z>
                                          largest useful harmonics degree
```

All commands are functional. For the full input file schema (TOML), see
[`examples/README.md`](examples/README.md). For working scenarios you can
copy from: [`lro_6day/`](examples/lro_6day/) (Moon HF),
[`batch_demo/`](examples/batch_demo/) (batch sweep),
[`gps_g11_validation/`](examples/gps_g11_validation/) (Earth HF
vs SP3), [`iss_drag_calibration/`](examples/iss_drag_calibration/)
(LEO drag + `spody calibrate`),
[`cr3bp_em_l4/`](examples/cr3bp_em_l4/) (CR3BP L4 stability).

---

## Roadmap

Ordered roughly by what unlocks the most for users.

**Done**

- [x] TOML input schema and parser (`tomlc99` drop-in)
- [x] `spody validate` — fully parse + sanity-check input without running
- [x] `spody propagate` — single-spacecraft propagation end-to-end
- [x] CSV + binary output writers, run-folder layout
      (`<output_dir>/<UTC-ISO8601>/`) with TOML snapshot copied in
      and every file inside the run folder prefixed with its
      timestamp (`<ts>_input.toml`, `<ts>_<scenario>_state.bin`,
      etc.) so snapshots and sources never collide
- [x] `spody batch` — multi-case run from a base TOML + CSV matrix of
      per-case overrides, sequential + OpenMP parallel
      (`thread_number > 1`)
- [x] **Two central bodies**: Moon (GRGM1200B, lunar PA libration
      from DE440) and Earth (EIGEN-6C4, IAU 2006/2000A_R06 + IERS
      EOP); chosen via `force_model.central_body = "Moon" | "Earth"`
- [x] **Adaptive harmonics degree** (`force_model.harmonics_adaptive`,
      opt-in): the expansion degree follows the orbit radius instead
      of being fixed for the whole run, since the degree-*n* term
      decays as `(R_ref/r)^n`. `harmonics_degree` becomes a ceiling.
      The threshold sits below double-precision resolution, so the
      output is bit-identical to the fixed-degree run — 2.3&times; on
      a 365-day lunar ELFO at degree 80, 3.7&times; on a 7-day GPS arc
      at degree 70. One rule for every central body: only the ratio
      `r / R_ref` enters, nothing to calibrate per model
- [x] **Two dynamics models**: `high_fidelity` (full force-model
      integrator) and `cr3bp` (Circular Restricted 3-Body Problem
      in synodic rotating frame, today's curated pair is Earth-Moon
      via the `[cr3bp]` section)
- [x] **Two initial-state flavours**: Cartesian (the legacy
      `[initial_state].position_km` + `velocity_kms`) and Keplerian
      (six classical elements + `reference_body` selector;
      converted to Cartesian by the engine, and to the synodic
      frame for CR3BP runs where the reference body is one of the
      two primaries)
- [x] **From CR3BP... converter** in the GUI form: popup next to
      the `[initial_state]` frame combo that maps a CR3BP catalog
      state (dimensional or nondimensional, barycentric or
      primary-centered) into the central-body ICRF state at
      `et_start_s` via the instantaneous pulsating-frame transform
      on the run's ephemeris, and inserts it into the form — seed
      HF runs from periodic-orbit catalog points (halo / NRHO /
      Lyapunov) without leaving the GUI
- [x] `spody convert` CLI: `harmonics_icgem` (ICGEM .gfc → engine
      .tab format), `sp3` (IGS SP3 precise orbits → SpOdy
      reference binary, multi-file concat), `glonass` /
      `gps` (RINEX-NAV broadcast → SpOdy reference binary, multi-
      file), `oem` (CCSDS OEM text → SpOdy reference binary,
      multi-file, overlap-deduplicated)
- [x] `spody calibrate` — engine-side fit of the drag
      density-scale `k(t)` node table against a full-state
      reference (sliding windows, drag on/off arc pairs, in-track
      least squares); emits the `density_scale_file` consumed by
      `[force_model]`. GUI: density-scale form rows + a
      **Calibrate...** button that streams the fit report into the
      Run-tab console and auto-fills the node-file path; bundled
      `examples/iss_drag_calibration/` ISS bench (NASA/JSC OEM +
      converted reference) exercises the whole loop
- [x] One time-scale chain, engine and GUI: ET is true TDB
      end-to-end (IERS leap seconds + SPICE `deltet` TDB−TT term
      in `spody_time.c`, zero-ULP Python twin in `spopy/time.py`)
- [x] Aggregated batch events file (SPDYEVTB, single
      `<batch>_events.bin` with `case_idx` per record)
- [x] Tee log output (`output.log_file` mirrors stdout/stderr to a
      timestamped file)
- [x] Per-force acceleration breakdown (`output.accelerations_file`,
      binary `ForceBreakdown` records; ~3% overhead at 1-minute cadence
      on LRO)
- [x] Event detection: always-on multi-body IMPACT (stop) + opt-in
      ECLIPSE (`[events].eclipse_threshold`, recurring) + opt-in
      ALT_CROSSING (`[[events.altitude_crossing]]`, ascending +
      descending, per-event refinement opt-out), all with sub-
      microsecond Hermite + Brent localisation
- [x] Two object schemas: `[spacecraft]` (mass + area) and `[debris]`
      (A/m only, mass-irrelevant); mutually exclusive at parse with
      mode-tagged batch targets
- [x] **Atmospheric drag (Earth)**: native NRLMSISE-00 port in
      spody-core (validated against the official NRL reference
      driver to the printed 7 digits), cannonball drag with air
      co-rotation, CelesTrak space-weather input (observed daily
      F10.7 + storm-time 3-hour Ap history), WGS-84 geodetic
      sub-satellite point; `force_model.drag` +
      `[spacecraft.drag]` / debris `am_drag`+`Cd`, all batch
      targets
- [x] Per-column batch modes: plain `target = "..."` (override) and
      inline `{ target = "...", mode = "delta" }` (additive
      perturbation); empty-string `target = ""` sentinel for metadata
      columns
- [x] Rotating-frame batch input: `cases_frame = "ric"` or `"lvlh"`,
      rotated at Generate-TOML by the GUI into whichever frame the
      propagator integrates in — ICRF under `high_fidelity`, synodic
      under `cr3bp` (there the local basis is built about the nearer
      primary, not the barycentre)
- [x] **PySide6 desktop frontend** under [`python/`](python/):
      - Setup wizard for asset downloads (DE440, GRGM1200B,
        EIGEN-6C4 with auto ICGEM &rarr; .tab conversion, IERS EOP
        + IAU 2006 tables, Moon and Earth textures), with EOP
        startup-freshness HEAD check
      - **Unified load/save UX**: global working-dir bar shared
        across tabs + per-Run-tab TOML combo (recursively scanned
        from the working dir) + Load / Save / Save As buttons
      - **WIP TOML protection**: saving on top of a snapshot or
        any TOML next to .bin output diverts to a `<stem>.wip.toml`
        sidecar so the on-disk record of each past run stays
        intact; runs launched from a WIP unlink it and auto-load
        the starting file
      - TOML editor with syntax highlighting + context-aware
        autocompletion + snippet templates
      - Form-based TOML builder with live preview of cases CSV +
        rotated-frame preview + range validators per field;
        `dynamics_model` combo reflows the form between HF and
        CR3BP sections
      - UTC&lt;-&gt;ET converter bit-identical to SPICE `str2et` /
        `et2utc` (`deltet` algorithm + IERS leap seconds)
      - Duration unit combo (`s | min | h | days`), shared by
        `simulation.duration_s` and the `output.interval_s`
        fixed-mode sampling interval
      - Embedded terminal pane streaming `spody`'s stdout/stderr live
      - **Analysis tab**: Plot / Table / **Info** split, file tree
        grouped by run folder (fully recursive scan), a **CSV export
        box** (a radio list of export types + one Export button, each
        greying out by data availability: figure lines, altitude-band
        per-element `time / entries`, impact `lat/lon + time of
        flight`), **altitude-band occupancy analysis** for
        `altitude_crossing` events (per-band entries / time /
        dwell / population in the Info tab, four dedicated plots
        &mdash; time-per-band bars, single-object occupancy Gantt,
        batch population-over-time, per-case heatmap &mdash; and
        per-altitude event-timeline rows; vectorised + cached so
        ten-million-event logs don't stall the tab &mdash; one shared
        per-file derivation behind the Info rows and every event plot,
        plus display budgets that keep the marker timeline and the
        population step function at canvas resolution instead of one
        artist per record, stated in the plot title when they engage;
        with a density event-timeline variant for that scale),
        batch-event impact
        views (time-to-impact
        histogram, survival timeline, equirect + Mollweide
        lat/lon maps, density heatmap, 3D body-textured scene
        with body-fixed + ICRF frame triads), **per-force
        acceleration views** naming each third body individually
        (resolved from the run snapshot) plus a **perturbation
        budget** &mdash; every force's share of the summed
        magnitudes, central two-body excluded, on a log axis so
        LEO's four-decade spread stays readable, diff-RIC trajectory
        plots, CR3BP Jacobi-constant conservation, per-primary
        osculating orbital elements for CR3BP runs (primary
        selector lives in the Scene options dialog), optional
        **3D star-map background** (Solar System Scope Milky Way
        8K, ICRF-aligned via on-the-fly re-projection; toggle in
        the Scene options dialog, persisted across sessions),
        **Plot-frame selector** (ICRF / body-fixed) that re-
        projects state-vector and Keplerian-angle plots into the
        central body's BF basis on the fly, **eccentricity vs
        argument-of-periapsis phase plot**, **UTC overlay** at
        the 3D-scene bottom-right tracking the playback epoch,
        rotating 3rd-body textures (continents / mares move with
        the body's actual ITRS / PA attitude), wait-cursor +
        "Working: ..." status across long renders;
        **camera pan / zoom preserved** across re-renders of the
        same file; **Info tab** with per-kind key/value summary
        (trajectory: t-range, |r|/|v| ranges, initial+final
        state, osculating Kepler at t0/tf; accel: per-force RMS
        + time in shadow; events: counts, impact timing,
        complete-eclipse pairing min/avg/max; batch: impact
        rate, survivors; diff-aware overlay with |&Delta;r| /
        |&Delta;v| / RIC stats when a Diff plot is active)
      - Settings dialog for persisted asset paths
- [x] **`spopy` Python package**: pure-NumPy DE440 reader (position,
      velocity and full state via the analytic Chebyshev derivative,
      SPICE-validated) + ICRF&lt;-&gt;Moon PA rotations +
      Keplerian&harr;Cartesian + CR3BP synodic&harr;primary-inertial
      conversions, bit-identical to spody-core for the forward
      direction
- [x] **Release pipeline**: tag-triggered GitHub Actions workflow
      builds PyInstaller bundles for Win64 / Linux x86_64 / macOS
      arm64, computes sha256 sidecars, and drafts a GitHub release
      with the bundled 14-chapter user manual PDF
- [x] Examples: [`lro_6day/`](examples/lro_6day/),
      [`batch_demo/`](examples/batch_demo/),
      [`debris_demo/`](examples/debris_demo/),
      [`debris_ric_demo/`](examples/debris_ric_demo/),
      [`debris_impact_demo/`](examples/debris_impact_demo/),
      [`gps_g11_validation/`](examples/gps_g11_validation/),
      [`glonass_r03_validation/`](examples/glonass_r03_validation/),
      [`iss_drag_calibration/`](examples/iss_drag_calibration/),
      [`cr3bp_em_l4/`](examples/cr3bp_em_l4/) (Earth-Moon L4
      stability smoke test)

**Pending**

- [ ] More central bodies (Mars, Sun-Earth) in addition to Moon
      and Earth (Mars unlocks the second atmosphere model, MCD --
      the drag plumbing is already per-body)
- [ ] More CR3BP primary pairs (today's curated pair is
      Earth-Moon only)
- [ ] More event kinds: apsides (altitude crossings shipped)
- [ ] Binary `.spody` variant of `cases_file` (CSV-only today)
- [ ] Engine-side rotating-frame handler so RIC / LVLH cases CSVs no
      longer need the GUI to pre-rotate them
- [ ] PyInstaller runtime hook to drop the Python 3.9 pin in the
      Windows release path
- [ ] Additional examples: GEO with SRP
- [ ] Conjunction-analysis feature (deep design parked; see internal
      brainstorm)

---

## Repository layout

```
spody/
├── .github/workflows/        # CI (smoke test) + release (tag-triggered 3-OS bundle)
├── external/
│   ├── spody-core/           # submodule, the C library
│   └── tomlc99/              # vendored TOML parser (cktan/tomlc99, MIT)
├── src/
│   ├── main.c                # CLI entry point + subcommand dispatch
│   ├── app_diagnostics.{c,h} # SpodyError + tee log mirror
│   ├── app_io.{c,h}          # filesystem / timestamp / path helpers, run-folder layout (ts-prefixed)
│   ├── toml_input.{c,h}      # TOML parser, validator, batch matrix loader, [cr3bp] schema branch
│   ├── central_body.{c,h}    # app-side central-body registry (Moon, Earth, ...)
│   ├── dynamics_model.{c,h}  # high_fidelity / cr3bp dispatch table
│   ├── atmosphere_nrlmsise00.{c,h} # per-body atmosphere callback + space-weather input
│   ├── calibrate.{c,h}       # density-scale k(t) fit driving `spody calibrate`
│   ├── sim_setup.{c,h}       # InputConfig -> SimulationShared + SimulationWorker (per-model branches)
│   └── sim_run.{c,h}         # propagation loop, CSV / binary writers, SPDYEVTB
├── examples/                 # input TOML examples (schema guide in examples/README.md)
│   ├── lro_6day/             # reference: NASA LRO 6-day propagation (Moon HF)
│   ├── batch_demo/           # smoke test: 3-case mass + SRP sweep
│   ├── debris_demo/          # debris-mode A/m sweep
│   ├── debris_ric_demo/      # RIC-frame batch input, GUI rotates to ICRF
│   ├── debris_impact_demo/   # 10-case batch with guaranteed impacts (impact-view dataset)
│   ├── gps_g11_validation/   # Earth HF vs IGS SP3 (GPS G11, multi-day)
│   ├── glonass_r03_validation/ # Earth HF vs MGEX SP3 (GLONASS R03, 7-day)
│   ├── iss_drag_calibration/ # ISS 15-day drag bench vs NASA/JSC OEM + `spody calibrate`
│   └── cr3bp_em_l4/          # CR3BP Earth-Moon L4 30-day stability smoke test
├── python/
│   ├── spody_gui/            # PySide6 frontend (Setup wizard, TOML editor, runner,
│   │                         #  Analysis tab, UTC<->ET converter, ...)
│   ├── spody_io/             # NumPy readers for the binary outputs (.bin / SPDYEVTB)
│   ├── spopy/                # Pure-Python DE440 + ICRF<->Moon PA rotations
│   ├── spoviz/               # 3D astrodynamics visualization library (VTK + numpy;
│   │                         #  Qt only in spoviz.qt — renders offscreen without it)
│   ├── pyproject.toml
│   ├── build_bundle.py       # PyInstaller driver (rebuilds manual + packs the dist)
│   └── spody_gui.spec        # PyInstaller spec, one-folder mode
├── docs/
│   ├── user-manual/          # 14-chapter Markdown + build_pdf.py + committed PDF
│   ├── developer-guide.md    # system map, conventions, extension recipes
│   └── RELEASES.md           # release protocol notes
├── CHANGELOG.md
├── CMakeLists.txt
├── LICENSE
└── README.md
```

---

## Design philosophy

- **One binary, file-based I/O.** No Python ↔ C bindings, no plugin system.
  The same `spody` executable drives desktop, batch, and web.
- **spody-core is a first-class consumer-friendly library.** Anyone can pull
  the submodule, link the static library, and ignore this app entirely.
- **Performance accessible, not hidden.** spody-core is C99 + CMake with zero
  external dependencies; the API is direct (no virtual dispatch, no opaque
  managers), so SIMD-friendly hot paths stay SIMD-friendly through the whole
  call chain.

---

## License

Apache License 2.0 — see [`LICENSE`](./LICENSE).

---

## Acknowledgements

The core of SpOdy (`spody-core`) is the work of **Valerio (@ValeEng)**. The engineering polish that turns the research codebase into a shippable, production-grade tool was done in pair-programming with **Anthropic's Claude Opus 4.7**.

A few high-level patterns are inspired by established mission-analysis
systems, notably **GMAT** (NASA, Apache 2.0). Validation work uses SPICE
LRO POD ephemerides as the ground-truth reference.
