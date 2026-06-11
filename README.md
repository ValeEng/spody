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

**Beta — fully end-to-end, narrow scope.** The whole pipeline runs
out of the box from a `git clone --recursive` to a published
PyInstaller bundle: single-scenario propagation, multi-case batch
(sequential + OpenMP parallel), event detection (always-on multi-
body IMPACT with sub-millisecond Hermite + Brent localisation,
opt-in ECLIPSE), TOML schema validation, per-force acceleration
breakdown, run-folder layout with TOML snapshot, and a PySide6
desktop frontend that covers Setup wizard, TOML editor with
syntax-aware autocompletion, embedded runner, and a full Analysis
tab (Plot / Table split, batch-event impact maps in 2D
equirectangular + Mollweide projections + density heatmap, 3D
Moon-textured impact scene with PA / ICRF triads, diff-RIC plots).
Releases ship signed-sha256 bundles for Windows / Linux x86_64 /
macOS arm64 plus a 14-chapter user manual PDF.

The library underneath (`spody-core`) is validated against SPICE
LRO POD ephemerides with sub-km position drift over the 6-day
window; the Python-side `spopy` package re-implements the
read-side helpers (DE440 reader, ICRF&lt;-&gt;Moon Principal Axes
rotations) bit-identically (104/104 checks at atol 1e-9 km/rad,
&sim;1 ULP IEEE 754).

The narrow scope is what keeps the "beta" label: no atmospheric
drag yet, Moon-only central body, no in-app cases-CSV generator,
and the release Win bundle pins Python 3.9 to dodge a known
apiset/PyInstaller interaction on some end-user Win10 builds. See
[`CHANGELOG.md`](CHANGELOG.md) for what landed when.

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
SpOdy app  : 0.1.1-beta
spody-core : 1.0.0  (git <sha>, built <timestamp>)
```

---

## CLI usage

```
spody <command> [options]

Commands:
  propagate  <input.toml> [--out <dir>]   run a single simulation
  batch      <input.toml>                 run a multi-case batch
  validate   <input.toml>                 check input file (no run)
  info                                    print version + capabilities
```

All commands are functional. For the full input file schema (TOML), see
[`examples/README.md`](examples/README.md). For working scenarios you can
copy from, see [`examples/lro_6day/`](examples/lro_6day/) and
[`examples/batch_demo/`](examples/batch_demo/).

---

## Roadmap

Ordered roughly by what unlocks the most for users.

**Done**

- [x] TOML input schema and parser (`tomlc99` drop-in)
- [x] `spody validate` — fully parse + sanity-check input without running
- [x] `spody propagate` — single-spacecraft propagation end-to-end
- [x] CSV + binary output writers, run-folder layout
      (`<output_dir>/<UTC-ISO8601>/`) with TOML snapshot copied in
- [x] `spody batch` — multi-case run from a base TOML + CSV matrix of
      per-case overrides, sequential + OpenMP parallel
      (`thread_number > 1`)
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
      - Setup wizard for asset downloads (DE440, GRGM1200B, Moon
        texture) with category + central-body filtering
      - TOML editor with syntax highlighting + context-aware
        autocompletion + snippet templates
      - Form-based TOML builder with live preview of cases CSV +
        rotated-frame preview + range validators per field
      - UTC&lt;-&gt;ET converter bit-identical to SPICE `str2et` /
        `et2utc` (`deltet` algorithm + IERS leap seconds)
      - Duration unit combo (`s | min | h | days`)
      - Embedded terminal pane streaming `spody`'s stdout/stderr live
      - **Analysis tab**: Plot / Table split, file tree grouped by
        run folder, batch-event impact views (time-to-impact
        histogram, survival timeline, equirect + Mollweide lat/lon
        maps, density heatmap, 3D Moon-textured scene with PA / ICRF
        frame triads), diff-RIC trajectory plots
      - Settings dialog for persisted asset paths
- [x] **`spopy` Python package**: pure-NumPy DE440 reader + ICRF&lt;-&gt;
      Moon PA rotations, bit-identical to spody-core
- [x] **Release pipeline**: tag-triggered GitHub Actions workflow
      builds PyInstaller bundles for Win64 / Linux x86_64 / macOS
      arm64, computes sha256 sidecars, and drafts a GitHub release
      with the bundled 14-chapter user manual PDF
- [x] Examples: [`lro_6day/`](examples/lro_6day/),
      [`batch_demo/`](examples/batch_demo/),
      [`debris_demo/`](examples/debris_demo/),
      [`debris_ric_demo/`](examples/debris_ric_demo/),
      [`debris_impact_demo/`](examples/debris_impact_demo/)

**Pending**

- [ ] Atmospheric drag model in spody-core (placeholder today)
- [ ] More central bodies (Earth, Mars) in addition to the Moon
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
│   ├── app_io.{c,h}          # filesystem / timestamp / path helpers, run-folder layout
│   ├── toml_input.{c,h}      # TOML parser, validator, batch matrix loader
│   ├── sim_setup.{c,h}       # InputConfig -> SimulationShared + SimulationWorker
│   └── sim_run.{c,h}         # propagation loop, CSV / binary writers, SPDYEVTB
├── examples/                 # input TOML examples (schema guide in examples/README.md)
│   ├── lro_6day/             # reference: NASA LRO 6-day propagation
│   ├── batch_demo/           # smoke test: 3-case mass + SRP sweep
│   ├── debris_demo/          # debris-mode A/m sweep
│   ├── debris_ric_demo/      # RIC-frame batch input, GUI rotates to ICRF
│   └── debris_impact_demo/   # 10-case batch with guaranteed impacts (impact-view dataset)
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
