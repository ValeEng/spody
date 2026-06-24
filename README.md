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

**Beta — fully end-to-end, growing scope.** The whole pipeline
runs out of the box from a `git clone --recursive` to a published
PyInstaller bundle: single-scenario propagation, multi-case batch
(sequential + OpenMP parallel), two **central bodies** (Moon and
Earth, the latter with IAU 2006/2000A_R06 + IERS EOP for the
inertial-to-ITRS rotation), two **dynamics models** selected per
TOML (`high_fidelity`, the full force-model integrator, and
`cr3bp`, the synodic-rotating Circular Restricted 3-Body Problem),
**two flavours of initial state** (Cartesian or Keplerian
elements referenced to the central body / one of the CR3BP
primaries), event detection (always-on multi-body IMPACT with
sub-millisecond Hermite + Brent localisation, opt-in ECLIPSE),
TOML schema validation, per-force acceleration breakdown, run-
folder layout with timestamp-prefixed snapshot + outputs, and a
PySide6 desktop frontend covering Setup wizard, TOML editor with
syntax-aware autocompletion, embedded runner, and a full Analysis
tab (Plot / Table / Info split, per-plot Export CSV, per-kind
key/value summary panel with diff-aware |&Delta;r| / |&Delta;v|
/ RIC rows, optional equirectangular star-map background on the
3D scene, batch-event impact maps in 2D equirectangular +
Mollweide projections + density heatmap, 3D body-textured impact
scene with body-fixed + ICRF triads, diff-RIC plots, Jacobi-
constant conservation for CR3BP). Releases ship signed-sha256 bundles for Windows / Linux
x86_64 / macOS arm64 plus a 14-chapter user manual PDF.

The library underneath (`spody-core`) is validated against SPICE
LRO POD ephemerides (sub-km position drift over the 6-day window,
Moon central body), GLONASS R03 broadcast vs MGEX SP3 (177 m RMS
on 24h, ~200 m/day linear growth over 7 days, Earth central body),
and a scipy DOP853 differential-corrector closure on an L1 Lyapunov
(30 microns / 1.6e-10 km/s over one synodic period, CR3BP). The
Python-side `spopy` package re-implements the read-side helpers
(DE440 reader, ICRF&lt;-&gt;Moon Principal Axes rotations) bit-
identically (104/104 checks at atol 1e-9 km/rad, &sim;1 ULP IEEE
754).

The remaining narrow scope keeps the "beta" label: no atmospheric
drag yet, no Mars / Sun-Earth central bodies, no in-app cases-CSV
generator, and the release Win bundle pins Python 3.9 to dodge a
known apiset/PyInstaller interaction on some end-user Win10
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
SpOdy app  : 0.2.0-beta
spody-core : 1.2.0  (git <sha>, built <timestamp>)
```

---

## CLI usage

```
spody <command> [options]

Commands:
  propagate  <input.toml> [--out <dir>]   run a single simulation
  batch      <input.toml>                 run a multi-case batch
  validate   <input.toml>                 check input file (no run)
  convert    <kind> <args...>             convert external formats
                                          (sp3 | glonass | gps |
                                          harmonics_icgem)
  info                                    print version + capabilities
```

All commands are functional. For the full input file schema (TOML), see
[`examples/README.md`](examples/README.md). For working scenarios you can
copy from: [`lro_6day/`](examples/lro_6day/) (Moon HF),
[`batch_demo/`](examples/batch_demo/) (batch sweep),
[`gps_g11_validation/`](examples/gps_g11_validation/) (Earth HF
vs SP3), [`cr3bp_em_l4/`](examples/cr3bp_em_l4/) (CR3BP L4
stability).

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
- [x] `spody convert` CLI: `harmonics_icgem` (ICGEM .gfc → engine
      .tab format), `sp3` (IGS SP3 precise orbits → SpOdy
      reference binary, multi-file concat), `glonass` /
      `gps` (RINEX-NAV broadcast → SpOdy reference binary, multi-
      file)
- [x] Aggregated batch events file (SPDYEVTB, single
      `<batch>_events.bin` with `case_idx` per record)
- [x] Tee log output (`output.log_file` mirrors stdout/stderr to a
      timestamped file)
- [x] Per-force acceleration breakdown (`output.accelerations_file`,
      binary `ForceBreakdown` records; ~3% overhead at 1-minute cadence
      on LRO)
- [x] Event detection: always-on multi-body IMPACT (stop) + opt-in
      ECLIPSE (`[events].eclipse_threshold`, recurring), sub-
      millisecond Hermite + Brent localisation
- [x] Two object schemas: `[spacecraft]` (mass + area) and `[debris]`
      (A/m only, mass-irrelevant); mutually exclusive at parse with
      mode-tagged batch targets
- [x] Per-column batch modes: plain `target = "..."` (override) and
      inline `{ target = "...", mode = "delta" }` (additive
      perturbation); empty-string `target = ""` sentinel for metadata
      columns
- [x] Rotating-frame batch input: `cases_frame = "ric"` or `"lvlh"`
      rotated to ICRF at Generate-TOML by the GUI
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
      - Duration unit combo (`s | min | h | days`)
      - Embedded terminal pane streaming `spody`'s stdout/stderr live
      - **Analysis tab**: Plot / Table / **Info** split, file tree
        grouped by run folder (fully recursive scan), per-plot
        **Export CSV** action (every `Line2D` on the active
        figure, one section per subplot; tile and overlay views
        supported), batch-event impact views (time-to-impact
        histogram, survival timeline, equirect + Mollweide
        lat/lon maps, density heatmap, 3D body-textured scene
        with body-fixed + ICRF frame triads), diff-RIC trajectory
        plots, CR3BP Jacobi-constant conservation, per-primary
        osculating orbital elements for CR3BP runs (primary
        selector lives in the Scene options dialog), optional
        **3D star-map background** (Solar System Scope Milky Way
        8K, ICRF-aligned via on-the-fly re-projection; toggle in
        the Scene options dialog, persisted across sessions);
        **camera pan / zoom preserved** across re-renders of the
        same file; **Info tab** with per-kind key/value summary
        (trajectory: t-range, |r|/|v| ranges, initial+final
        state, osculating Kepler at t0/tf; accel: per-force RMS
        + time in shadow; events: counts, impact timing,
        complete-eclipse pairing min/avg/max; batch: impact
        rate, survivors; diff-aware overlay with |&Delta;r| /
        |&Delta;v| / RIC stats when a Diff plot is active)
      - Settings dialog for persisted asset paths
- [x] **`spopy` Python package**: pure-NumPy DE440 reader + ICRF&lt;-&gt;
      Moon PA rotations + Keplerian&harr;Cartesian + CR3BP
      synodic&harr;primary-inertial conversions, bit-identical to
      spody-core for the forward direction
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
      [`cr3bp_em_l4/`](examples/cr3bp_em_l4/) (Earth-Moon L4
      stability smoke test)

**Pending**

- [ ] Atmospheric drag model in spody-core (placeholder today)
- [ ] More central bodies (Mars, Sun-Earth) in addition to Moon
      and Earth
- [ ] More CR3BP primary pairs (today's curated pair is
      Earth-Moon only)
- [ ] More event kinds: altitude crossings, apsides
- [ ] Binary `.spody` variant of `cases_file` (CSV-only today)
- [ ] Engine-side rotating-frame handler so RIC / LVLH cases CSVs no
      longer need the GUI to pre-rotate them
- [ ] PyInstaller runtime hook to drop the Python 3.9 pin in the
      Windows release path
- [ ] Additional examples: ISS LEO with drag, GEO with SRP
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
│   └── cr3bp_em_l4/          # CR3BP Earth-Moon L4 30-day stability smoke test
├── tests/                    # end-to-end CLI tests (stub)
├── python/
│   ├── spody_gui/            # PySide6 frontend (Setup wizard, TOML editor, runner,
│   │                         #  Analysis tab, VTK 3D, UTC<->ET converter, ...)
│   ├── spody_io/             # NumPy readers for the binary outputs (.bin / SPDYEVTB)
│   ├── spopy/                # Pure-Python DE440 + ICRF<->Moon PA rotations
│   ├── pyproject.toml
│   ├── build_bundle.py       # PyInstaller driver (rebuilds manual + packs the dist)
│   └── spody_gui.spec        # PyInstaller spec, one-folder mode
├── docs/
│   ├── user-manual/          # 14-chapter Markdown + build_pdf.py + committed PDF
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
