# SpOdy Developer Guide

How to maintain, update and extend SpOdy without breaking its
invariants. This is the document to read before writing code; the
[user manual](user-manual/) covers *using* the program, this one
covers *changing* it.

It is written to be followed literally: every recipe lists the files
you will touch, the order to touch them in, how to verify the result,
and which documents to update afterwards. When in doubt, copy the
pattern of the most recent similar change (the CHANGELOG names the
commits).

---

## 0. How to use this guide

| You want to… | Read |
|---|---|
| Understand what the pieces are and how a run flows | §1 |
| Set up a working dev environment from a fresh clone | §2 |
| Know the git / submodule / docs discipline | §3 |
| Write code that fits the house style | §4 |
| Add a feature (TOML key, plot, event, body, force, …) | §5 (find your recipe) |
| Prove your change didn't break anything | §6 |
| Avoid the classic traps | §7 (invariants) + §8 (tooling pitfalls) |

Domain shorthand used everywhere in the code and in this guide
(full definitions in user-manual ch. 14, the glossary):

- **ET** — ephemeris time: TDB seconds past J2000. The one canonical
  time scale inside the engine, the TOML (`et_start_s`) and every
  binary output.
- **HF / CR3BP** — the two dynamics models: `high_fidelity` (full
  force model) and the Circular Restricted 3-Body Problem.
- **EOP / ERA** — IERS Earth-orientation parameters / Earth rotation
  angle; together with the IAU 2006/2000A_R06 series they build the
  ICRF↔ITRF rotation.
- **IC** — initial conditions (`[initial_state]`).
- **RIC** — radial / in-track / cross-track frame, used by the diff
  views and batch deltas.

## 1. The big picture

SpOdy is three cooperating components:

| Component | Language | Where | Role |
|---|---|---|---|
| **spody-core** | C | `external/spody-core` (git submodule of [ValeEng/spody-core](https://github.com/ValeEng/spody-core)) | The physics/numerics library: ephemeris reader, force models, RKDP45 integrator with dense output, events, Earth orientation (IAU 2006/2000A_R06), GNSS/SP3 converters, time-scale helpers (`spody_time.c`). No I/O policy, no TOML — pure engine. |
| **spody** (app layer) | C | `src/` | The `spody.exe` CLI: TOML parsing/validation (`toml_input.c`), worker setup (`sim_setup.c`), run loop + output writers (`sim_run.c`), subcommand dispatch (`main.c`). |
| **GUI + Python libs** | Python | `python/` | `spody_gui` (PySide6 desktop app wrapping `spody.exe` as a subprocess), `spopy` (pure-Python mirror of spody-core read-side functions), `spody_io` (binary output readers). |

The split is deliberate: the GUI never links the engine — it writes a
TOML, spawns `spody.exe`, and reads the binary outputs back
(Patran-style file-based coupling). That means you can develop and
test each side in isolation, and a GUI crash can never corrupt a run.

### 1.1 Anatomy of one run

What actually happens when a user clicks **Run** (or types
`spody propagate input.toml`):

```
input.toml
   │  src/toml_input.c      parse + validate every key, resolve paths
   │                        relative to the TOML, fill InputConfig
   ▼
   src/sim_setup.c          load ephemeris/EOP/harmonics, build the
   │                        ForceModelContext + integrator config
   ▼
   spody-core               RKDP45 loop with dense output; per-step
   │                        force evaluation; event residuals checked
   │                        and refined (Brent) inside accepted steps
   ▼
   src/sim_run.c            creates output/<ts>/ next to the TOML and
                            writes <ts>_-prefixed CSV + binaries
                            (+ events file, + acceleration breakdown)
   ▼
   spody_gui Analysis tab   spody_io readers + spopy math + PlotSpec
                            registry render the views
```

`main.c` dispatches the subcommands: `propagate` (one TOML),
`batch` (base TOML + CSV of per-case overrides, OpenMP-parallel),
`validate` (parse + validate only), `info` (print a binary's header),
`convert` (harmonics_icgem / sp3 / gps / glonass), `maxhgdegree`.

### 1.2 Binary wire formats

All little-endian, 8-byte magic + version/dim header:

| Magic | Contents | Writer | Reader |
|---|---|---|---|
| `SPDYOUT_` | trajectory records `(t, x, y, z, vx, vy, vz)`; `t` is seconds since the run's `et_start_s` | `sim_run.c`, GNSS/SP3 converters | `spody_io/traj.py` |
| `SPDYACC_` | per-force acceleration breakdown | `sim_run.c` | `spody_io/accel.py` |
| `SPDYEVT_` | per-run events | `sim_run.c` | `spody_io/events.py` |
| `SPDYEVTB` | batch-aggregated events (extra `case_idx`) | `sim_run.c` | `spody_io/events.py` |
| `SPDYEPET` | compiled DE440 ephemeris (`.spody`) | offline generator | spody-core + `spopy/ephemeris.py` |

A new format field means touching **both** sides plus `detect_kind`
in `spody_gui/analysis/registry.py`. Never change a record layout in
place — bump the header version and keep the reader
backward-compatible.

### 1.3 GUI package layout

- `spody_gui/main_window.py` — shell; owns the tabs. The only entry
  points it imports are `TomlForm` and `AnalysisPanel`.
- `spody_gui/form/` — the Run-tab form building blocks:
  - `catalog.py` — **declarative tables** mirroring the engine schema:
    field keys, tooltips, units (`UNIT`), validators, batch targets
    (`BATCH_TARGETS`), third-body lists. Most form changes are one
    row here.
  - `widgets.py` — field factories (line edits with validators,
    combos, `AssetCombo`).
  - `sections.py` — one builder method per TOML table
    (`[simulation]`, `[force_model]`, …).
  - `visibility.py` — conditional visibility: XOR groups
    (spacecraft/debris, cartesian/keplerian), HF↔CR3BP reflow,
    batch table.
  - `roundtrip.py` — generic dict ↔ widgets serialization.
  - `handlers.py` — bottom bar (Load/Save/Generate/Run).
  - `toml_form.py` — composes the five mixins over `QWidget`; keeps
    only state, signals and change-tracking.
- `spody_gui/analysis/` — the Analysis-tab machinery:
  - `spec.py` — the `PlotSpec` contract (name, kind, callable,
    requirements).
  - `context.py` — `PlotContext`/`resolve_run_context`: everything a
    plot needs, resolved from the run folder snapshot.
  - `plots_traj.py`, `plots_cr3bp.py`, `plots_diff.py`,
    `plots_accel.py`, `plots_events.py` — one module per view
    family; each exports a `SPECS` list. **A new view = one function
    + one spec entry here.**
  - `registry.py` — assembles `PLOTS` per file kind, owns
    `KIND_LABEL`, `READERS`, `detect_kind`.
  - `scene3d.py` (shared VTK decoration), `overlays.py`, `info.py`,
    `table_model.py` (events table).
  - `analysis_panel.py` (one level up) keeps only the widget + file
    plumbing.
- `spody_gui/constants.py` — the **single reading point** for
  `spody_const.h` (see §4.3/§4.4).
- `spody_gui/runner.py` — spawns `spody.exe` with the scenario root
  as CWD (Windows MAX_PATH defence), streams output to the terminal
  pane.
- `spody_gui/setup_wizard.py` — first-run data download (DE440
  coverage profiles, EOP, textures).
- `spopy/` — pure-Python re-implementations of spody-core read-side
  functions, module-per-C-file: `ephemeris.py`, `eop.py`,
  `earth_orientation.py`, `rotations.py`, `kepler.py`, `cr3bp.py`,
  `time.py` (the zero-ULP twin of `spody_time.c`). **When you change
  a core function, check for a spopy sibling and keep it in
  lockstep** — several are verified bit-identical against the C side.
- `spody_io/` — pure readers for the wire formats above; no Qt, no
  spopy dependency.

## 2. Dev setup from zero

Target platform is Windows + MSVC (Visual Studio 2022+); the engine
also builds with clang/gcc (CI does macOS/Linux smoke builds).

### 2.1 Clone

```
git clone --recurse-submodules https://github.com/ValeEng/spody.git
cd spody
```

Forgot `--recurse-submodules`? Run
`git submodule update --init` — an empty `external/spody-core` is
the #1 cause of "cannot find spody_const.h" configure errors.

### 2.2 Build the engine

```
cmake -S . -B build
cmake --build build --config Release
```

Produces `build/Release/spody.exe`. Check it:

```
build\Release\spody.exe validate examples\gps_g11_validation\input.toml
```

Notes and troubleshooting:

- **cmake not on PATH**: use the one bundled with Visual Studio
  (`<VS>\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe`)
  or a standalone install.
- The MSVC linker printing *"found MSIL .netmodule … /LTCG"* while
  linking executables is **normal** (whole-program optimization),
  not an error.
- Useful configure options: `-DSPODY_FAST_MATH=ON` (faster, breaks
  bit reproducibility — leave OFF for regression work),
  `-DSPODY_WHOLE_PROGRAM_OPT=OFF` (faster links while iterating),
  `-DSPODY_WITH_OPENMP=OFF` (serial batch).
- The same recipe builds standalone spody-core in its own clone
  (see §3.1 for why you need that clone).

### 2.3 Data files

Physics data lives under `data/` and is **not** all in git (sizes).
The GUI's setup wizard (first launch, or *Settings → re-run wizard*)
downloads what is missing. For CLI-only work you need, per feature:

| Data | Path | Needed by |
|---|---|---|
| DE440 compiled ephemeris | `data/DE440/de440.spody` | every HF run (third bodies, librations) |
| Earth gravity field | `data/EIGEN-6C4/eigen-6c4.tab` | Earth central body |
| Moon gravity field | `data/GRGM1200B/…` | Moon central body |
| IERS EOP | `data/eop/finals2000A.all` | Earth rotation (auto-refreshed by the GUI when stale) |
| IAU 2006 series tables | `data/iau2006/` | Earth rotation |
| Textures | `data/Moon/`, … | 3D views only |

Every example TOML references these with relative paths
(`../../data/...`) — run the engine **from the example's folder** so
relative paths and the `output/` folder land next to the TOML (this
is also exactly what the GUI runner does):

```
cd examples\gps_g11_validation
..\..\build\Release\spody.exe propagate input.toml
```

Expected console tail: a `run dir : ./output/<timestamp>Z` line, the
output file list, and `done in <seconds>`.

### 2.4 Python environment

The GUI venv is pinned to **Python 3.9** (a PyInstaller/apiset
crash on some end-user Win10 machines forces the pin — see
CHANGELOG). Consequences for you:

- every new module that uses `X | Y` type annotations **must** start
  with `from __future__ import annotations`;
- no 3.10+ syntax (`match`, parenthesized context managers).

Create the venv in `python/.venv` and install: `PySide6`, `numpy`,
`matplotlib`, `pyerfa`, `vtk` (+ `pyinstaller` if you build bundles).
Then:

```
cd python
python -m spody_gui
```

First launch opens the setup wizard; point it at (or let it
download) the data files above.

### 2.5 Bundled app (PyInstaller)

`python/build_exe.ps1` drives `python/spody_gui.spec`. Spec gotchas
that have bitten before (details in §8): `datas` paths resolve
against the *spec dir* but file-existence checks in spec code must
use absolute paths derived from `SPECPATH`; one-folder output puts
data under `_internal/`; the spec ships `spody_const.h` under
`spody-core/` so `constants.py` finds it at
`sys._MEIPASS/spody-core/spody_const.h`. Never enable `strip` on
Windows.

## 3. Repository workflow

### 3.1 The submodule dance (memorize this)

spody-core is developed in its **own standalone clone**, never by
editing files inside `external/spody-core` (the submodule checkout is
a read-only consumer). A core change lands in six steps:

1. Edit in the standalone spody-core clone.
2. Build + test there (`cmake --build build --config Release`, then
   the core test suite).
3. Commit + push to spody-core `main`.
4. In the spody repo:
   `git -C external/spody-core fetch origin` then
   `git -C external/spody-core checkout <new-sha>`.
5. Rebuild the app (`cmake --build build --config Release`) and
   re-run the regression checks of §6 — the app is the real consumer.
6. `git add external/spody-core` and commit the pointer bump in
   spody (this is the "bump" commit; it may ride along with the
   app-side half of the same feature).

If you skip step 5 you are shipping an untested combination — the
core suite alone does not exercise the TOML/output layer.

### 3.2 Commit style

- `scope: imperative summary` — scopes in use: `core:`, `events:`,
  `input:`, `gui(form):`, `gui(analysis):`, `docs:`, `chore:`,
  `time:`, `batch:`.
- Single-maintainer flow: work goes **directly on `main`** of both
  repos; campaign-sized refactors go on a short-lived branch merged
  when green.
- No AI co-author trailers.

### 3.3 Documentation catch-up (mandatory, after every change)

Every feature/physics push is closed by a separate `docs:` commit.
Walk this checklist and update what applies — the goal is that a
reader of any one document is never lied to:

1. **CHANGELOG.md** — always. Add/extend the `Unreleased` entry;
   physics changes state their measured effect (what moved, by how
   much, what stayed identical).
2. **README.md** — if the feature list, CLI surface, or build story
   changed.
3. **User manual** (`docs/user-manual/source/`) — the chapter(s)
   covering the touched surface: ch. 5 form / ch. 6 TOML schema /
   ch. 7 batch / ch. 8 analysis tab / ch. 9 plot catalog / ch. 12
   CLI, plus ch. 14 glossary for new terms. The HTML/PDF are build
   artifacts — only the `source/*.md` files are versioned.
4. **This guide** — if you added an extension point, changed a
   convention, moved a module, changed the build, or discovered a
   new invariant/pitfall. Treat it exactly like the user manual:
   part of the definition of "done", not an afterthought.

### 3.4 What never gets committed

- Anything with machine-specific absolute paths.
- Local scratch/test material outside the repo's public surface.
- Regenerated manual HTML/PDF (build artifacts; the PDF is refreshed
  at release time).
- Bulky run outputs (`examples/*/output/` is gitignored; so are the
  downloaded IGS/RINEX source files).

## 4. Conventions (how to write code here)

Each rule exists because its violation has already cost debugging
time. Follow them for new/touched code; don't mass-rename old code.

1. **License header.** Every new `.c`, `.h`, `.py`, `.spec` file gets
   the Apache 2.0 + `Copyright 2026 ValeEng` header **at creation**,
   not as a later cleanup pass.
2. **C naming.** Functions exposed in a public header are `spody_*`;
   new file-local `static` functions and data take **no leading
   underscore**. Conversion constants use the `X2Y` style (`MAS2RAD`,
   `KM2AU`), never `_TO_`.
3. **Constants live in one place.** Every numeric constant belongs in
   `spody-core/include/spody_const.h` — as a *plain number literal*,
   because the GUI parses the header textually (§4.4). Calendar /
   time-scale algorithms (Meeus Gregorian→JD, the leap-second chain,
   the SPICE-`deltet` TDB−TT term, ET→UTC MJD) belong in
   `spody-core/src/spody_time.c`. Never hardcode either in an
   individual `.c`. If you catch yourself typing `86400` or
   `2451545` in a source file, stop — the name already exists.
4. **Python reads the same constants.** `spody_gui/constants.py`
   parses `spody_const.h` (dev checkout and bundled install alike)
   and exposes named values; GUI code never hardcodes a physical
   constant — call `constants.const("NAME", fallback)` and add the
   clearly-marked fallback for headerless installs.
5. **Leap seconds have exactly two copies**: `spody_time.c` (C) and
   `spopy/time.py::LEAP_TABLE_MJD` (Python; every other Python
   consumer derives from it). A new IERS Bulletin C insertion is one
   row in each. The same twin relationship covers the whole time
   module: `spopy/time.py` mirrors `spody_time.c` **bit-for-bit**
   (same operation order — the twins are verified zero-ULP). Change
   one, change both, re-verify (§6.3).
6. **Time and units.** ET = TDB seconds past J2000 is the canonical
   internal time everywhere; positions km, velocities km/s, ICRF
   internally; body-fixed frames only at the edges (input, display,
   surface projections).
7. **No micro-helpers.** Operations under ~6 lines used fewer than 3
   times stay inline; helpers are for non-trivial logic. (A codebase
   of one-line wrappers is harder to read than the lines themselves.)
8. **Comments state constraints**, not narration: why a tolerance,
   which spec section, what invariant — not what the next line does.
9. **Docs cite SPICE** as the only validation ground truth.

## 5. Extension recipes

The design goal of the 2026-07 refactor: each recipe touches a small,
predictable set of files. Every recipe ends the same way: run the
matching §6 verification, then walk the §3.3 docs checklist.

### 5.1 New physical constant

1. Add the `#define` to `spody-core/include/spody_const.h` in the
   thematically right block, as a plain number with a source comment
   (which publication/kernel the value comes from).
2. Use it from C.
3. If the GUI needs it: `constants.const("NAME", fallback)` — the
   parser picks it up automatically (it tolerates parenthesized
   values and trailing comments).

### 5.2 New TOML key or section (engine feature)

Files: `src/toml_input.c` / `.h`, `src/sim_setup.c` or `sim_run.c`
(consumer), `python/spody_gui/form/*`.

1. **Parse**: in `toml_input.c`, extend the right `parse_*` function
   (or add one for a new `[section]`), add the field to
   `InputConfig` in `toml_input.h`. Fixed-size buffers only — the
   struct is flat-copied by batch mode (§7).
2. **Validate**: range/consistency checks go in
   `spody_validate_input`, with error messages that name the TOML
   key verbatim (users grep for them).
3. **Batch**: if the key must be overridable per case, add a row to
   `FIELD_TABLE` in `toml_input.c` **and** a mode-tagged entry to
   `BATCH_TARGETS` in `form/catalog.py`.
4. **Consume**: read the config field in `sim_setup.c` / `sim_run.c`
   and wire it into the ForceModelContext / run loop.
5. **GUI form**: one row in `form/catalog.py` (label, tooltip, unit,
   validator); builder additions in `form/sections.py` (+ one call
   in `TomlForm.__init__` for a whole new section); a hook in
   `form/visibility.py` if the field is conditional (model- or
   mode-dependent). The round-trip is generic — widgets registered
   under the dotted key serialize themselves, and unknown sections
   pass through verbatim, so old TOMLs stay loadable.
6. Verify: §6.2 round-trip + a `spody validate` run on an example
   TOML with and without the new key.
7. Document: manual ch. 6 (schema table) + ch. 5 if the form UI is
   visible; CHANGELOG.

### 5.3 New analysis view (plot/table on existing data)

1. Write the plot function in the matching
   `spody_gui/analysis/plots_*.py` — signature and idioms as its
   neighbours (take a `PlotContext`, draw into the provided figure).
2. Append a `PlotSpec` to that module's `SPECS` list.

That's all: the registry assembles per-kind lists, the panel
dispatches on them. Verify by opening the Analysis tab on a run of
the right kind. Document: manual ch. 9 (plot catalog).

### 5.4 New output file kind

1. Engine: new magic + writer (follow `sim_run.c` patterns; 8-byte
   magic, version, dims).
2. `python/spody_io/`: reader module + export in `__init__.py`.
3. `spody_gui/analysis/registry.py`: entries in `KIND_LABEL`,
   `READERS`, `detect_kind`.
4. Usually a new `plots_<kind>.py` with its `SPECS`.
5. Document: manual ch. 8 + ch. 9; CHANGELOG; §1.2 table in this
   guide.

### 5.5 New event kind

Use the altitude-crossing implementation as the template
(spody-core `d1bb88b`, spody `96b1ad5`..`913fb6d`).

1. spody-core `spody_events.{h,c}`: kind enum value + constructor +
   residual function + a case in `spody_event_check_refined`.
   Recurring kinds need the sign-tracking + Brent refinement pattern
   and **only fire on the RK45 dense-output path** (§7).
2. `src/toml_input.c`: parse the `[events]` entry (array-of-tables
   if multiple instances make sense); `sim_run.c` `build_events`:
   instantiate per config.
3. GUI: events table labels in `analysis/table_model.py`; any new
   view in `analysis/plots_events.py`; form panel in
   `form/sections.py` (follow the eclipse/altitude collapsible-panel
   pattern).
4. Verify: a purpose-built scenario TOML that provably triggers the
   event; check count, ET and refinement of every logged row.
5. Document: manual ch. 6 (events schema) + ch. 8; CHANGELOG.

### 5.6 New central body

1. Engine: entry in the app-side registry `src/central_body.{h,c}`
   (radius, mu, NAIF id, gravity-field file wiring, body-fixed
   rotation callback if the body rotates).
2. GUI: one `CentralBodySpec` in `spody_gui/central_bodies.py`
   (+ an orientation provider backed by spopy if the body rotates),
   texture asset in `spody_gui/assets.py` if desired. The form's
   combo and the impact/3D views auto-track the registry.
3. Verify: propagate a simple orbit around the new body; check the
   3D view triads and an impact-event lat/lon if applicable.
4. Document: manual ch. 6 + README feature list; CHANGELOG.

### 5.7 New CR3BP primary pair

1. `spody_const.h`: separation constant (`*_DISTANCE_KM`).
2. `src/toml_input.c`: row in `CR3BP_PAIRS` (used by
   `lookup_cr3bp_pair`; the engine rejects unknown pairs at load).
3. `form/catalog.py`: mirror tuple for the combo.
4. The two lists must stay in lockstep — grep both when touching
   either.

### 5.8 New force model

1. spody-core `spody_forcemodels.c`: implement the acceleration
   callback following the existing per-force pattern — read inputs
   from `ForceModelContext`, write into the per-force breakdown
   slots (so `SPDYACC_` stays complete).
2. Wire the enable flag / parameters through `[force_model]` parsing
   (§5.2 steps 1–4) and the GUI section builder.
3. Atmospheric drag specifically must go through the per-body
   atmosphere callback declared in `spody_atmosphere.h` — never
   hardwire an atmosphere to a body.
4. Verify: per-force breakdown shows the new column with plausible
   magnitude; total acceleration matches an independent SPICE-based
   estimate on a spot check.
5. Document: manual ch. 6; README feature list; CHANGELOG.

## 6. Verifying changes

### 6.1 Engine changes

Rebuild **both** repos (core clone + app), then:

- **Bit-identity regression** (the strongest cheap check, mandatory
  for refactors/cleanups): re-run a bundled example whose
  `output/<ts>/` you already have, with the same `input.toml`, and
  binary-compare the trajectory:

  ```
  cd examples\gps_g11_validation
  ..\..\build\Release\spody.exe propagate input.toml
  fc /b output\<old-ts>\<old-ts>_*.bin output\<new-ts>\<new-ts>_*.bin
  ```

  Refactors must be byte-identical. Physics changes must explain
  every delta *quantitatively* (measure it — a one-line Python/numpy
  diff of the two binaries via `spody_io.read_trajectory` — and put
  the numbers in the CHANGELOG entry).
- Run the other example families your change could plausibly touch
  (`batch_demo`, `cr3bp_em_l4`, `debris_impact_demo`,
  `glonass_r03_validation`).
- Numerical validation of *new physics* is done against
  SPICE-derived references.
- Warning discipline: the build must stay warning-clean at the
  default level; new MSVC C4244/C4267 warnings are treated as bugs
  (explicit casts with a reason, or fix the types).

### 6.2 GUI / Python changes

In order of increasing cost:

1. `python -m py_compile` sweep over `spody_gui`, `spopy`,
   `spody_io` (catches syntax + 3.9 incompatibilities).
2. Import every touched module in isolation (catches circular
   imports and missing `from __future__ import annotations`).
3. Offscreen form round-trip (no display needed):

   ```
   QT_QPA_PLATFORM=offscreen python -c "…instantiate TomlForm,
   load_from_dict(an example TOML), to_dict(), diff the two dicts…"
   ```

   No keys may be lost. (Known benign diffs: list-order
   normalization of `third_bodies`, output filenames re-derived from
   `simulation.name`.)
4. `AnalysisPanel` needs a real GL context (VTK does not render
   offscreen here) — verify 3D views by launching the app.
5. **Always launch the GUI and exercise the changed surface before
   committing GUI work.** Watch the console: it must stay silent.

### 6.3 Time-scale changes

Anything touching `spody_time.c` / `spopy/time.py` must re-verify
the twins: dump `spody_tdb_minus_tt` + `spody_et_to_mjd_utc` from
the C side in hexfloat (`printf("%a")`) over a dense ET sweep
(thousands of epochs spanning 1972→2035, i.e. across every leap
boundary) and compare against `spopy.time` with `float.fromhex` —
equality must be exact (zero-ULP), not "close".

### 6.4 Bundle changes

After touching the spec or data files, build the bundle
(`python/build_exe.ps1`) and launch it on a machine (or at least a
folder) without the dev checkout — that is the only place the
`constants.py` fallback and `sys._MEIPASS` paths are actually
exercised.

## 7. Invariants that are easy to break

Each entry: the rule, and the symptom you'll see if you break it.

- **One ET↔UTC chain, shared C↔Python.** Since the 2026-07 deltet
  port, `spody_et_to_mjd_utc` (engine) and `spopy.time.et_to_mjd_utc`
  (zero-ULP twin) both apply the TDB−TT periodic term (SPICE
  `deltet`, ±1.657 ms) before the leap-second chain, and the GNSS
  converters apply it in the TT→TDB direction — `et_start_s` is true
  TDB everywhere and the GUI's `utc_to_et`/`et_to_utc` agree with
  the engine to <1 µs. Don't introduce a second conversion path, and
  don't "simplify" the deltet term away: before the port the engine
  treated ET≈TT self-consistently, which silently mislabeled every
  stored ET by up to 1.657 ms vs SPICE. *Symptom of breakage:
  sub-second epoch offsets between GUI-displayed UTC and converter
  output, or meter-level Earth-fixed rotation biases at GNSS radius.*
- **Recurring events need dense output.** Eclipse / altitude
  crossings fire from the RK45 dense-output path; other integrators
  don't provide it and `spody_event_check` has no fallback.
  *Symptom: recurring events silently absent from the events file.*
- **The run-folder contract.** The engine creates `output/<ts>/` and
  ts-prefixes every file; the GUI's rerun/analysis features parse
  exactly that layout (`_RUN_FOLDER_RE`). Change it in both places
  or not at all. *Symptom: runs invisible in the Analysis tree.*
- **`InputConfig` is flat-copied** by `spody_apply_batch_case`;
  adding a heap-owned (pointer) field breaks batch mode. Fixed-size
  buffers only, or teach the copy. *Symptom: double-free / shared
  state across batch cases.*
- **The four-representation `[initial_state]` cache** in the form
  (cart/kep × ICRF/BF, kept to make representation swaps lossless)
  is invalidated by epoch/body/model changes — a new field that
  affects the state conversion must be added to that invalidation
  list. *Symptom: stale numbers after a swap.*
- **`spopy.Ephemeris` is not thread-safe** (per-instance record
  cache): one instance per worker thread. *Symptom: garbled
  positions under concurrency.*
- **Wire formats are append-only** (§1.2): readers in the wild parse
  old files. *Symptom: `spody_io` exceptions on historical runs.*

## 8. Tooling pitfalls (Windows-flavoured)

- **Python 3.9 pin** (§2.4): `X | Y` annotations need
  `from __future__ import annotations`; no `match`.
- **BOM**: several sources are UTF-8 *with BOM*; scripts that read
  them must decode `utf-8-sig` (plain `utf-8` leaves `﻿` in the
  first token; `ast.parse` chokes).
- **CRLF**: the repo lives with mixed endings; git prints `LF will
  be replaced by CRLF` warnings — harmless, don't "fix" files
  wholesale (it destroys diffs/blame).
- **MSVC + /GL**: the "MSIL .netmodule … /LTCG" linker message is
  informational.
- **PyInstaller spec**: paths in `datas` are relative to the spec
  dir, but code inside the spec runs from the build dir — always
  build absolute paths from `SPECPATH`. One-folder bundles put data
  under `dist/<app>/_internal/`. Never enable `strip` on Windows
  binaries. The bundle pin to Python 3.9 dodges a known apiset
  loader crash on some Win10 builds.
- **Qt offscreen**: `QT_QPA_PLATFORM=offscreen` is enough for
  `TomlForm` logic tests; VTK views need a real GL context — test
  those in the launched app.
- **Windows MAX_PATH**: the GUI runner uses the scenario root as the
  subprocess CWD so `output/<ts>/<ts>_…` stays short — don't switch
  it to absolute-path invocation without checking nesting depth.
- **EOP freshness**: the GUI HEAD-checks `finals2000A.all` at
  startup and re-downloads when stale; offline dev just uses the
  local file. Don't hand-edit that file — one malformed row shifts
  the fixed-width parser.
- **Stored GNSS `convert` artifacts predating a converter change are
  not regression references** — regenerate them (the commands are in
  each example's TOML header) instead of chasing phantom deltas.
