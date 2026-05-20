# SpOdy

**Simultaneous Propagation of Orbital DYnamics**

[![CI](https://github.com/ValeEng/spody/actions/workflows/ci.yml/badge.svg)](https://github.com/ValeEng/spody/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Status: alpha](https://img.shields.io/badge/Status-alpha-orange.svg)](#status)

SpOdy is a high-performance orbital dynamics propagator built as a thin
application layer on top of [**spody-core**](https://github.com/ValeEng/spody-core),
the underlying C library. The long-term aim is to make precision astrodynamics
accessible without ceremony: a single, small CLI driver plus an optional
graphical front-end, both fed by a plain-text input file.

---

## Status

**Alpha — functional, rough edges.** Single-scenario propagation, schema
validation, and multi-case batch runs all work end-to-end against the
[LRO 6-day reference](examples/lro_6day/) and a
[batch smoke test](examples/batch_demo/). The Python GUI, events, and
parallel batch (OpenMP) are still on the roadmap below. The library
underneath (`spody-core`) is validated against SPICE LRO POD ephemerides
with sub-km position drift over the 6-day window.

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
        [Python GUI / web frontend]   <- planned, file-based, no C bindings
```

The split is deliberate:

- **spody-core** is a clean C99 library, fully reusable on its own.
- **spody** is the executable that turns it into a complete tool: input
  parsing, simulation orchestration, output formatting.
- The future Python GUI (under `python/`) will follow the **Patran/Nastran**
  pattern — it generates the TOML, invokes the binary, and parses the output
  files. It will not link C code directly. The same binary therefore serves
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
SpOdy app  : 0.1.0
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
- [x] CSV + binary output writers
- [x] `spody batch` — multi-case run from a base TOML + CSV matrix of
      per-case overrides, sequential
- [x] Tee log output (`output.log_file` mirrors stdout/stderr to a
      timestamped file)
- [x] Per-force acceleration breakdown (`output.accelerations_file`,
      binary `ForceBreakdown` records; ~3% overhead at 1-minute cadence
      on LRO)
- [x] Event detection (`output.events_log`): always-on multi-body
      IMPACT (stop) + opt-in ECLIPSE (`[events].eclipse_threshold`,
      recurring), sub-millisecond Hermite + Brent localisation
- [x] Examples: [`lro_6day/`](examples/lro_6day/),
      [`batch_demo/`](examples/batch_demo/)

**Pending**

- [ ] Atmospheric drag model in spody-core (placeholder today)
- [ ] More central bodies (Earth, Mars) in addition to the Moon
- [ ] More event kinds: altitude crossings, apsides
- [ ] Parallel batch via OpenMP (`thread_number > 1`)
- [ ] Binary `.spody` variant of `cases_file` (CSV-only today)
- [ ] Additional examples: ISS LEO with drag, GEO with SRP
- [ ] Python GUI prototype under `python/` — TOML editor + result viewer
- [ ] Web frontend wrapping the same binary

---

## Repository layout

```
spody/
├── .github/workflows/        # CI (build matrix linux / macos / windows + smoke test)
├── external/
│   ├── spody-core/           # submodule, the C library
│   └── tomlc99/              # vendored TOML parser (cktan/tomlc99, MIT)
├── src/
│   ├── main.c                # CLI entry point + subcommand dispatch
│   ├── app_diagnostics.{c,h} # SpodyError + tee log mirror
│   ├── toml_input.{c,h}      # TOML parser, validator, batch matrix loader
│   ├── sim_setup.{c,h}       # InputConfig -> SimulationShared + SimulationWorker
│   └── sim_run.{c,h}         # propagation loop, CSV / binary writers
├── examples/                 # input TOML examples (schema guide in examples/README.md)
│   ├── lro_6day/             # reference: NASA LRO 6-day propagation
│   └── batch_demo/           # smoke test: 3-case mass + SRP sweep
├── tests/                    # end-to-end CLI tests (planned)
├── python/                   # GUI prototype (planned, file-based IO)
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
systems, notably **GMAT** (NASA, Apache 2.0). Validation work has been done
against external references including SPICE LRO POD products and side-by-side
benchmarks vs **Tudat** (TU Delft) and **Orekit** (CS Group / ESA).
