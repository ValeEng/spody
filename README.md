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

**Alpha — early scaffolding.** The CLI dispatch (`propagate`, `validate`, `info`)
compiles and runs, but the simulation handlers are still stubs. The TOML input
schema, the actual propagation driver, and the Python GUI all live on the
roadmap below. The library underneath (`spody-core`) is functional and
validated against an external 6-day LRO reference (322 µm position drift vs the
reference simulator over 6 days, sub-km drift vs SPICE-reconstructed LRO POD,
roughly 8× faster than Tudat and 2.5× faster than Orekit on the same setup).

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

## CLI usage (current)

```
spody <command> [options]

Commands:
  propagate  <input.toml> [--out <dir>]   run a simulation         (stub)
  validate   <input.toml>                 check input file, no run (stub)
  info                                    print version + capabilities
```

Both `propagate` and `validate` are placeholders; only `info` is wired today.
The TOML schema is being designed -- see the roadmap.

---

## Roadmap

Ordered roughly by what unlocks the most for users:

- [ ] TOML input schema and parser (`tomlc99` drop-in)
- [ ] `spody validate` — fully parse + sanity-check input without running
- [ ] `spody propagate` — single-spacecraft propagation end-to-end
- [ ] CSV + binary output writers; output schema documented
- [ ] Examples (`examples/lro_6day/`, ISS LEO, GEO with SRP, …)
- [ ] Python GUI prototype under `python/` — TOML editor + result viewer
- [ ] Multi-spacecraft / constellation propagation in a single run
- [ ] Web frontend wrapping the same binary

---

## Repository layout

```
spody/
├── .github/workflows/        # CI (build matrix linux / macos / windows + smoke test)
├── external/
│   └── spody-core/           # submodule, the C library
├── src/
│   └── main.c                # CLI entry point + subcommand dispatch
├── examples/                 # input TOML examples (planned)
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

SpOdy is developed independently. A few high-level patterns are inspired by
established mission-analysis systems, notably **GMAT** (NASA, Apache 2.0).
Validation work has been done against external references including SPICE LRO
POD products and side-by-side benchmarks vs **Tudat** (TU Delft) and
**Orekit** (CS Group / ESA).
