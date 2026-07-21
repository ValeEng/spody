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
| **spody-core** | C | `external/spody-core` (git submodule of [ValeEng/spody-core](https://github.com/ValeEng/spody-core)) | The physics/numerics library: ephemeris reader, force models, RKDP45 integrator with dense output, events, Earth orientation (IAU 2006/2000A_R06), GNSS/SP3 converters, time-scale helpers (`spody_time.c`), NRLMSISE-00 atmosphere (`spody_nrlmsise00.c`, native port). No I/O policy, no TOML — pure engine. |
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
  - `cr3bp_convert.py` — the **From CR3BP...** popup (opened from
    the `[initial_state]` frame row): CR3BP catalog state →
    central-body ICRF at `et_start_s` via the instantaneous
    pulsating-frame transform, in-process on spopy (explicit-inputs
    QDialog, no back-references into the form).
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
  - `scene3d.py` — GUI glue over `spoviz.decoration`: keeps the
    historical `(canvas, ctx, times_s)` signatures, resolves the
    run-folder snapshot / texture assets / `spody_const.h` radii,
    then delegates to the library. Also `overlays.py`, `info.py`,
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
  functions, module-per-C-file: `ephemeris.py` (`position()`,
  `velocity()`, `state()` — the rates are the analytic Chebyshev
  derivative, mirroring `spody_get_ephvelocity`/`spody_get_ephstate`),
  `eop.py`, `earth_orientation.py`, `rotations.py`, `kepler.py`,
  `cr3bp.py`, `time.py` (the zero-ULP twin of `spody_time.c`).
  **When you change a core function, check for a spopy sibling and
  keep it in lockstep** — several are verified bit-identical against
  the C side.
- `spody_io/` — pure readers for the wire formats above; no Qt, no
  spopy dependency.
- `spoviz/` — the 3D astrodynamics visualization library (see §5.12
  for the extension recipe). `scene.py` = `Scene3D`, the **Qt-free**
  scene engine (layered multi-frustum renderers, textured bodies,
  animated trajectories / triads / arrows, sun light, skybox,
  picking, camera) that runs on any `(vtkRenderWindow, interactor)`
  pair, including offscreen; `decoration.py` = ephemeris-driven
  decoration (third bodies, sun illumination, animated body-fixed
  frame) taking explicit callables/tables — no `PlotContext`, no
  QSettings, no Qt; `bodies.py` = NAIF/colour/marker-sizing catalog;
  `textures.py` = equirectangular pixel fixups with on-disk caches;
  `widgets.py` = opt-in in-scene UI chrome (PlaybackBar +
  OptionsPanel on VTK widgets — standalone viewers only, the GUI
  keeps its Qt controls); `qt.py` = `SceneWidget`, the ONLY module
  that imports PySide6. Full API reference + examples in
  `python/spoviz/README.md`.
  Dependency direction: **spoviz never imports spody_gui or spopy**
  (ephemeris objects come in duck-typed); the GUI reaches it through
  the `spody_gui/vtk_canvas.py` shim (`VtkCanvas`) and the
  `analysis/scene3d.py` glue.

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
   **One deliberate exemption:** `spody_nrlmsise00.c`. Its DATA
   constants and coefficient tables are the NRLMSISE-00 model
   *definition* exactly as NRL fit it (`DGTR = 1.74533E-2` is not
   π/180 to double precision *on purpose*); "fixing" them or moving
   them to `spody_const.h` changes the model output and breaks the
   reference-driver equality (§7).
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

## 5. Extension recipes — growing the software without breaking it

This is the heart of the guide. Every recipe below follows the same
skeleton — **Files you touch → Steps → What you can break here →
Verify → Document** — and every recipe assumes you first ran the
safe-change protocol of §5.0. When your change doesn't match any
recipe, assemble it from the closest ones and still follow §5.0.

### 5.0 The safe-change protocol (applies to every change)

Do these in order, literally. The protocol exists because each step
has caught a real bug at least once.

1. **Name the blast radius before typing.** Which layers does the
   change touch: spody-core? `src/`? wire formats? `spopy`? the GUI?
   Each extra layer adds one verification from §6. If the change
   touches a wire format or the time chain, read §7 first.
2. **Take a baseline.** Build clean (`cmake --build build --config
   Release`) and run the bundled example(s) closest to the feature
   *before* changing anything. Keep the produced `output/<ts>/`
   folders — they are your before/after reference. If you skip this
   you will have nothing trustworthy to compare against later.
3. **Change in dependency order**: spody-core first (its own clone,
   §3.1), then `src/`, then Python. Each layer should build/import
   cleanly before you move to the next. Never edit
   `external/spody-core` in place.
4. **Keep every layer honest about its mirror.** If you touched a C
   function, grep `python/spopy/` for a sibling; if you touched a
   constant, remember the GUI parses `spody_const.h`; if you touched
   an output writer, open the matching `spody_io` reader.
5. **Re-run the baseline** and binary-compare (`fc /b`, or a numpy
   diff via `spody_io.read_trajectory`).
   - Pure refactor / cleanup → outputs must be **byte-identical**.
   - Deliberate physics/format change → **measure** every delta and
     write the numbers into the CHANGELOG entry. "Looks fine" is not
     a measurement.
6. **Exercise the GUI surface live** if you touched anything under
   `python/` (§6.2): launch, click through the changed screens,
   console must stay silent.
7. **Walk the docs checklist** (§3.3). A feature isn't done until
   README/CHANGELOG/manual/this guide agree with the code.

Rule of thumb: if you cannot say which §7 invariant your change is
*closest to violating*, you don't understand the change yet — re-read
§7, then start.

### 5.1 New physical constant

**Files:** `spody-core/include/spody_const.h` (+ consumers).

1. Add the `#define` in the thematically right block of
   `spody_const.h`, as a **plain number literal** with a source
   comment (which publication/kernel/datasheet the value comes from):

   ```c
   #define MARS_MU   42828.375816    // GM km^3/s^2 (source: ...)
   ```

   Plain literal matters: the GUI parses the header with a regex that
   accepts numbers (optionally parenthesized, optional exponent,
   trailing comment) — an expression like `(A * B)` is invisible to
   Python and the GUI will silently fall back.
2. Use the name from C. If you typed the raw number anywhere else,
   you did it wrong (§4.3).
3. If the GUI needs the value: `constants.const("MARS_MU", 42828.375816)`
   — the second argument is the clearly-marked fallback used only
   when the header is missing (broken install). Keep fallback == header.

**Break risk:** none if the literal is plain; a silent GUI fallback
mismatch if it isn't. **Verify:** run
`python -c "from spody_gui import constants; print(constants.const('MARS_MU', 0))"`
from `python/` and check it prints the header value, not the
fallback. **Document:** CHANGELOG only (constants are internal).

### 5.2 New TOML key or section (engine feature)

**Files:** `src/toml_input.c`, `src/toml_input.h`,
`src/sim_setup.c` and/or `src/sim_run.c`,
`python/spody_gui/form/catalog.py`, `form/sections.py`,
(`form/visibility.py`), user-manual ch. 6.

Follow the chain in this order — parse, validate, batch, consume,
GUI — because each step is testable on its own:

1. **Struct field**: add the field to `InputConfig` in
   `toml_input.h`. **Fixed-size buffers only** (`char path[...]`,
   `double`, `int`) — the struct is flat-copied by
   `spody_apply_batch_case`, so a heap pointer here breaks batch
   mode (§7). Give it a safe default where `InputConfig` is
   initialized.
2. **Parse**: extend the matching `parse_<section>` function in
   `toml_input.c` (`parse_simulation`, `parse_force_model`,
   `parse_events`, …; for a whole new `[section]` add a new
   `parse_*` and call it from the parse entry point next to its
   siblings). Copy the idiom of the neighbouring keys — the file is
   deliberately repetitive so that patterns can be copied.
3. **Validate**: range/consistency checks go in
   `spody_validate_input`, *not* in the parser. Error messages name
   the TOML key verbatim and, when the value comes from a known set,
   list the accepted values — users grep for these strings.
4. **Batch (only if the key should be overridable per case)**: add a
   row to `FIELD_TABLE` in `toml_input.c`:

   ```c
   { "section.my_key", SPODY_FIELD_DOUBLE,
     offsetof(InputConfig, my_key), 0, SPODY_VAL_POSITIVE },
   ```

   and the mirror entry to `BATCH_TARGETS` in `form/catalog.py`
   (`("section.my_key", None)`; use the `"spacecraft"` /
   `"debris"` tag instead of `None` when the key only exists in one
   object mode). Deliberately excluded from batch: anything that
   would invalidate shared resources across cases (central body,
   harmonics file/degree) — don't add those.
5. **Consume**: read the config field in `sim_setup.c` (setup-time
   resources) or `sim_run.c` (run-loop behaviour) and wire it in.
6. **GUI form**:
   - one row in `form/catalog.py`: tooltip in the tooltip table,
     unit in `UNIT`, validator (reuse `_pos` / `_nonneg` / friends
     or add one that returns `""` when valid, message otherwise);
   - in `form/sections.py`, extend the right section builder with a
     factory call from `form/widgets.py` — `_add_float`, `_add_int`,
     `_add_bool`, `_add_enum`, `_add_path`, `_add_vec3`,
     `_add_duration_seconds`, `_add_asset_combo`,
     `_add_strlist_checks` — registering the widget under the dotted
     key (`"section.my_key"`). For a whole new section: new builder
     method + one call in `TomlForm.__init__`.
   - if the field is conditional (model- or mode-dependent), add the
     hook in `form/visibility.py` next to the HF↔CR3BP /
     spacecraft↔debris logic.
   - you do **not** touch `roundtrip.py`: widgets registered under
     dotted keys serialize themselves, and unknown TOML sections
     pass through verbatim (old TOMLs stay loadable, new TOMLs stay
     loadable by old code that ignores the key).

**Break risk:** heap field in `InputConfig` (batch double-free);
validation in the parser instead of `spody_validate_input` (batch
overrides skip it); catalog row without builder call (key silently
never serialized). **Verify:** `spody validate` on an example TOML
with and without the new key (both must behave as designed); §6.2
offscreen round-trip (the new key must survive load→save); if
batchable, a 2-case `spody batch` overriding the key. **Document:**
manual ch. 6 schema table (+ ch. 5 if the form UI is visible,
+ ch. 7 if batchable); CHANGELOG.

### 5.3 New analysis view (plot/table on existing data)

**Files:** one `spody_gui/analysis/plots_*.py` module. Nothing else.

1. Pick the module by file kind: `plots_traj.py` (trajectories),
   `plots_accel.py` (breakdowns), `plots_events.py`,
   `plots_diff.py` (two-file comparisons), `plots_cr3bp.py`.
2. Write the plot function with the signature its `mode` implies
   (see `analysis/spec.py`, which documents every field):
   - `mode="single"` (default): `def my_view(ax, data): ...` for 2D,
     `def my_view(canvas, data): ...` for 3D;
   - `mode="diff"`: `def my_view(ax, data_a, data_b): ...`;
   - `mode="context"`: `def my_view(ax, data, ctx): ...` where `ctx`
     is a `PlotContext` (run folder, `et_start_s`, central body,
     dynamics model, ephemeris path — everything resolved for you).
   The dispatcher clears/resets/renders the canvas; the function
   only draws its content.
3. Append one `PlotSpec` to the module's `SPECS` list:

   ```python
   PlotSpec(label="My view", dim="2d", fn=my_view,
            category="Diagnostics",          # tree folder ("" = root)
            mode="single",                   # or "diff" / "context"
            overlay_fn=my_view_overlay,      # or None (button disabled)
            models=("high_fidelity",))       # hide where meaningless
   ```

   Field-by-field guidance:
   - `models` gates the view by dynamics model — a body-fixed
     lat/lon map is meaningless in the CR3BP synodic frame, so
     advertise `("high_fidelity",)`; Jacobi-style views advertise
     `("cr3bp",)`.
   - `overlay_fn=None` is correct when overlaying N files would draw
     3N–5N illegible lines; the Overlay button self-disables with an
     explanation.
   - `projection="mollweide"` (or `"aitoff"`/`"hammer"`) for
     geographic ellipse views, 2D only.

**Break risk:** essentially zero for other views (the registry is
additive); the classic mistake is hardcoding a body (radius,
texture, name) instead of reading `ctx.central_body`. **Verify:**
launch the GUI, open a run of the right kind, render the view in
single / tile / overlay modes; check it does *not* appear for
models it doesn't support. **Document:** manual ch. 9 (plot
catalog); CHANGELOG.

**Non-plot analysis (Info-tab rows, export actions).** Not every
analysis is a figure. Two adjacent extension points:

- *Info-tab rows*: add an `info_rows_<kind>` builder in
  `analysis/info.py` returning `(label, value)` pairs (a value of
  `SECTION` is a bold header), then call it from
  `_refresh_info_tab` in `analysis_panel.py`. Keep any non-trivial
  reconstruction in its own module (the altitude-band occupancy lives
  in `analysis/altitude_bands.py`, NOT inside `info.py`) so it is
  unit-testable without Qt and reusable by an export. Formatting goes
  through `fmt_num` / `fmt_duration` so precision stays uniform.
- *A second export action*: `plot_options.py` hosts the export
  buttons. A new one is a `Signal` + a `set_<name>_enabled` method on
  `PlotOptionsDialog`, connected in `_on_open_plot_options` and
  re-synced in `_sync_anim_bar_to_canvas` (fires after every render).
  Gate it on the DATA, not the figure, when the export derives from
  the loaded array rather than the drawn lines — the altitude-band CSV
  is enabled by `_can_export_altitude_bands_csv` (central-body
  `ALT_CROSSING` present), independent of which plot is showing. The
  altitude-band feature is the worked template for both points.

**Scale (millions of rows).** An events log can carry millions of
records, and the Info tab re-runs its analysis on *every* switch to
the tab. Two rules keep that from freezing the GUI:

- *Vectorise the per-record work.* Do the O(N) step in numpy (one
  `lexsort` to group, `np.diff` / `np.bincount` / `cumsum` to
  aggregate), never a Python `for` over the records; leave Python
  loops only over the handful of bands / series. `altitude_bands.py`
  is the worked example (`_reconstruct` + the flat-segment plots). The
  rewrite must stay **bit-identical** to the readable version &mdash;
  guard it with the hand-computed + e2e cross-checks in
  `tests/analysis/` (local-only) before trusting a run.
- *Cache once per file.* Wrap the heavy function in a content-keyed
  memo (see `_cache_key` / `_cached` in `altitude_bands.py`: keyed by
  the array buffer address + size + first/last timestamps + params) so
  the Info tab, the plots and the exports share one computation per
  loaded file and repeat touches are free. Also: pick a readable time
  unit from the plotted span (`_time_axis`) — don't hardcode seconds.

**Verify:** launch the GUI, load the right file kind, read the Info
rows and (for an export) round-trip the CSV back through numpy;
confirm the button greys out on a file that shouldn't offer it; for a
vectorised rewrite, re-run the `tests/analysis/` checks (bit-identity)
and eyeball the timing on a synthetic million-row array.
**Document:** manual ch. 8 (Info tab / exports) + ch. 9 (plots);
CHANGELOG.

### 5.4 New output file kind

**Files:** `src/sim_run.c` (writer), `python/spody_io/` (reader),
`spody_gui/analysis/registry.py`, usually a new
`analysis/plots_<kind>.py`.

1. **Wire format first, on paper**: 8-byte magic (pad to exactly 8,
   e.g. `SPDYXYZ_`), `uint32` version = 1, `uint32` dims, 8 reserved
   bytes, then fixed-size little-endian records. Formats are
   **append-only** (§7): once shipped, a record layout never changes
   in place — future fields mean a version bump handled by the
   reader.
2. **Writer** in `sim_run.c`, following the existing writer
   functions (same header helper, same error paths, ts-prefixed
   filename inside `output/<ts>/`).
3. **Reader** module in `python/spody_io/` (numpy structured dtype
   with an `itemsize` assert, header check, version check), exported
   from `spody_io/__init__.py`.
4. **Registry**: in `analysis/registry.py` add the kind to
   `KIND_LABEL`, `READERS`, and teach `detect_kind` the magic.
5. **Views**: new `plots_<kind>.py` with its `SPECS` (recipe 5.3).

**Break risk:** reader/writer drift (assert record sizes on both
sides); forgetting `detect_kind` (files invisible in the Analysis
tree). **Verify:** run a scenario that writes the new file; `spody
info` prints its header; the Analysis tree lists it under the new
label and the views render. **Document:** §1.2 table in this guide;
manual ch. 8 + ch. 9; CHANGELOG.

### 5.5 New event kind

Use the altitude-crossing implementation as the working template
(spody-core `d1bb88b`, spody `96b1ad5`..`913fb6d` — read those diffs
once before starting; they are the recipe in executable form).

**Files:** spody-core `spody_events.{h,c}`; `src/toml_input.c`,
`src/sim_run.c`; GUI `analysis/table_model.py`,
`analysis/plots_events.py`, `form/sections.py`.

1. **Core enum + descriptor** (`spody_events.h`): add a
   `SPODY_EVENT_KIND_*` value. The enum is deliberately open —
   adding kinds is non-breaking, the wire format discriminates on
   the kind field. Reuse the existing descriptor slots (`naif_id`,
   `radius_km`, `threshold_fraction`, …) and document what each
   means for your kind in the header comment, like the existing
   kinds do.
2. **Predicate**: implement the residual/check and add one `case` to
   the dispatch in `spody_event_check` and (for refined kinds)
   `spody_event_check_refined`. Two families:
   - *one-shot* (impact-like): plain geometric check on the accepted
     state;
   - *recurring* (eclipse/altitude-like): track the residual's sign
     across steps and refine the crossing with Brent on the dense
     output — copy the altitude-crossing pattern wholesale.
     Recurring kinds **only fire on the RK45 dense-output path**
     (§7): if the residual sign-tracks but never refines, you are on
     the wrong path.
3. **Parse**: `[events]` entry in `toml_input.c` — single table for
   a singleton toggle (eclipse-style) or array-of-tables
   (`parse_altitude_crossings`-style) when the user may register
   several instances. Validation messages name the keys and accepted
   `action` values (`log`, `stop`, `log_and_stop`).
4. **Instantiate**: `build_events` in `sim_run.c` constructs the
   events array from the config — add your kind there, including the
   per-event `refined` opt-out if it's recurring.
5. **GUI kind→label maps — there are TWO, update both**:
   `analysis/table_model.py::_EVENT_KIND_LABEL` (Analysis events
   table) **and** `rerun_panel.py::_KIND_LABEL` (Re-run cases table's
   "last event" column). Miss either and that view shows a raw
   `kind=N` int instead of the name (this is exactly how
   `ALT_CROSSING` slipped through the first time). Then any dedicated
   view in `analysis/plots_events.py` (recipe 5.3); form panel in
   `form/sections.py` following the collapsible "Enable …" pattern of
   eclipse/altitude (checkbox + table + Add / Remove, combos
   auto-tracking the model's valid bodies). If the kind is
   *terminal* (LOG_AND_STOP, impact-like), also revisit the Re-run
   survivor/crashed presets (`_sel_survivors` / `_sel_crashed`),
   which classify on `last_kind == EVENT_KIND_IMPACT`.
6. **Test scenario**: write a TOML that *provably* triggers the
   event a known number of times (pick an orbit where you can count
   the crossings by hand). Check count, ET ordering and refinement
   of every logged row, and that `action = "stop"` truncates the run.

**Break risk:** forgetting the refined case (events land on step
boundaries, ~30 s error); parsing an array-of-tables as a single
table; new descriptor fields that the flat `SpodyEvent` copy
doesn't cover; **forgetting one of the two GUI kind-label maps**
(step 5) so a view shows `kind=N`. **Verify:** the purpose-built
scenario + one existing events example (`debris_impact_demo`)
unchanged. **Document:** manual ch. 6 (events schema) + ch. 8
(events table); CHANGELOG.

### 5.6 New central body

**Files:** `src/central_body.{h,c}`; spody-core rotation provider if
the body rotates; GUI `spody_gui/central_bodies.py`
(+ `spody_gui/assets.py` for textures); gravity data under `data/`.

The registry is designed so this is three local edits on the C side
(the header says so, and it's true):

1. One enum value in `SpodyCentralBody` (`central_body.h`).
2. One row in the static registry in `central_body.c`: name, NAIF
   id, `mu` (add the constant to `spody_const.h` first — recipe
   5.1), mean radius, and the `spody_bf_rotation_fn` provider
   (`NULL` is legal while the body has no orientation model: the
   engine then treats it as non-rotating).
3. If the body rotates: implement `spody_bf_rotation_<body>` in
   spody-core following `spody_bf_rotation_earth` /
   `spody_bf_rotation_moon`.
4. Gravity field: ship/convert a harmonics file (`spody convert
   harmonics_icgem` for ICGEM `.gfc` sources) and wire the
   per-body file selection the way Moon/Earth do it.
5. **GUI mirror**: one `CentralBodySpec` in
   `spody_gui/central_bodies.py` (name, `naif_id`, `radius_km`,
   `mu_km3_s2`, `bf_frame_name`, `bf_orientation`). The orientation
   provider is the spopy twin of the C rotation (see
   `_moon_orientation` for the pattern) — if you wrote a C provider,
   write the spopy sibling and keep them in lockstep (§4.5 spirit).
   Texture in `assets.py` if you want a textured 3D body.
6. The form's combo, the validator error text ("known: …") and the
   impact/3D views all auto-track the registries — no further edits.

**Break risk:** C registry and Python `_KNOWN_BODIES` drifting
(different radius/mu between engine and 3D view); a rotation
provider without its spopy twin (3D triads lie). **Verify:**
propagate a simple orbit around the new body; check `spody
validate` rejects a typo'd name listing the new body among the
known ones; check the 3D view triads and, if applicable, an
impact-event lat/lon against hand-computed geometry. **Document:**
manual ch. 6 + README feature list; CHANGELOG; §2.3 data table in
this guide.

### 5.7 New CR3BP primary pair

**Files:** `spody_const.h`, `src/toml_input.c`,
`form/catalog.py`.

1. Separation constant `<PAIR>_DISTANCE_KM` in `spody_const.h`
   (recipe 5.1 rules apply).
2. Row in `CR3BP_PAIRS` in `toml_input.c` (feeds
   `lookup_cr3bp_pair`; unknown pairs are rejected at load with a
   message that lists the known ones — your row updates that
   message for free).
3. Mirror tuple in `CR3BP_PAIRS` in `form/catalog.py` for the combo,
   plus its separation in `CR3BP_L_KM` right below (same constant as
   step 1, read through `constants.py`).
4. The two lists must stay in lockstep — grep both names whenever
   touching either.
5. Free riders — check, don't code: the Keplerian↔Cartesian swap and
   the **From CR3BP...** converter dialog
   (`form/cr3bp_convert.py`, opened from the `[initial_state]`
   frame row) both build their pair lists from `CR3BP_PAIRS` +
   `CR3BP_L_KM` + the central-body registry, so the new pair shows
   up in both automatically — but ONLY if both primaries are
   registered central bodies with `naif_id` + `mu_km3_s2`
   (recipe 5.6) and the ephemeris actually covers the pair
   (`spopy.Ephemeris.position` must resolve both NAIF ids).

**Verify:** a CR3BP run with the new pair (`cr3bp_em_l4` is the
template scenario); the synodic 3D view shows both primaries at the
right separation; a From CR3BP... conversion round-trips a state of
the new pair. **Document:** manual ch. 6; CHANGELOG.

### 5.8 New batch override target

Covered inside recipe 5.2 step 4 — the two tables (`FIELD_TABLE` in
C, `BATCH_TARGETS` in Python) are the whole feature. Remember the
exclusion rule: keys whose change would invalidate resources shared
across batch cases (central body, harmonics file/degree) are
excluded *on purpose* — batch shares one `SimulationShared` across
cases. **Verify:** 2-case CSV overriding the key, check the two
outputs differ in exactly the expected way. **Document:** manual
ch. 7; CHANGELOG.

### 5.9 New force model

**Files:** spody-core `spody_forcemodels.{h,c}`
(+ `spody_atmosphere.{h,c}` for drag-like models);
`src/toml_input.c`, `form/catalog.py` + `form/sections.py`.

1. Implement the acceleration callback in `spody_forcemodels.c`
   following the existing per-force pattern: read inputs from
   `ForceModelContext`, add into the state derivative, **and write
   the per-force contribution into the breakdown slots** — a force
   missing from `SPDYACC_` is invisible to the Analysis tab and to
   future debugging.
2. Add the context fields it needs to `ForceModelContext` (set up in
   `sim_setup.c` from config; remember flat-copy rules if anything
   lands in `InputConfig`).
3. Wire the enable flag / parameters through `[force_model]`
   (recipe 5.2).
4. Atmospheric drag specifically must go through the per-body
   atmosphere callback declared in `spody_atmosphere.h` — the
   atmosphere model is a property of the body, never hardwired into
   the force. The worked example is Earth: the engine ships the
   density model (`spody_nrlmsise00.h`, native re-entrant port) and
   the app binds it in `src/atmosphere_nrlmsise00.c` — geodetic
   conversion via `spody_bf_to_geodetic`, calendar labels via
   `spody_mjd_to_doy`, space-weather inputs via
   `spody_space_weather_msis_inputs`, then `spody_nrlmsise00_gtd7d`
   (the "effective total mass density for drag" variant) with the
   native CGS output converted to kg/m³ (× 1000) at the callback
   boundary. The callback instance is registered on the body's row
   in `central_body.c` (together with `spin_rad_s`); a new
   atmosphere (Mars + MCD) is a new wrapper file + that one
   registry row.
5. Model-calibration knobs follow the density-scale pattern: the
   engine owns a loader + evaluator pair (`MappedDensityScale` in
   `spody_atmosphere.{h,c}`, evaluated via the shared
   `spody_interp_linear` from `spody_interp` — put any new generic
   bracketing/interpolation math there, not in the feature file), a
   `const` pointer slot on `ForceModelContext` where NULL means
   "factor = 1, feature off", and the multiply at exactly one point
   inside the force. The app synthesises the degenerate case (a
   scalar TOML key becomes a single node) so the engine has one
   evaluation path. INVARIANT: the default (NULL slot / factor 1.0
   / key absent) must be bit-identical to the pre-feature engine —
   verify with the §6.1 bit-identity regression, and mind the
   reference trap below.
6. **Validate the physics against SPICE-derived references** on a
   spot check before trusting a full run: per-force magnitude at a
   known state, then a short propagation against an independently
   computed arc.

**Break risk:** missing breakdown slot; force evaluated in the wrong
frame (everything in the RHS is ICRF, body-fixed only via the
context's rotation providers); unvalidated physics shipping because
"the numbers looked plausible". **Verify:** §6.1 including the
breakdown check; bit-identity of runs with the force *disabled*
(a new force must be a strict no-op when off). **Document:** manual
ch. 6; README feature list; CHANGELOG (with the validation numbers).

### 5.10 Touching the time chain or a spopy mirror

Shortest recipe, sharpest edges:

1. Change the C side (`spody_time.c` or the mirrored core function)
   and its Python twin **in the same sitting** — never land one
   without the other.
2. Keep the *operation order* identical between the twins: the
   bit-identity guarantee comes from both sides executing the same
   IEEE-754 operations in the same order against the same libm.
3. Re-verify per §6.3 (dense hexfloat sweep, zero-ULP).
4. If the change alters results (physics): treat outputs as a
   deliberate compat break — measure, update stored example
   references and `et_start_s` values where the epoch semantics
   moved, and write the numbers in the CHANGELOG (the 2026-07 deltet
   entry is the template).

### 5.11 New CLI subcommand or format converter

Canonical examples: `spody convert oem` (converter, spody-core
`spody_oem.{h,c}`) and `spody calibrate` (subcommand,
`src/calibrate.{h,c}`). The split rule decides where the code goes
**before** you write it:

- **Format converters live in spody-core**, one file per format,
  next to `spody_sp3.c` / `spody_gps.c` / `spody_glonass.c` /
  `spody_oem.c`. They read an external text/binary format and emit a
  SpOdy wire format (usually `SPDYOUT_`). They must not depend on
  app-side code (`toml_input`, `sim_setup`, ...).
- **Subcommands that orchestrate propagations live app-side**, one
  `src/<name>.{h,c}` pair plus a thin `cmd_<name>` arg-parsing
  wrapper in `main.c`. `calibrate` is the template: load + validate
  the TOML exactly like `cmd_propagate`, build ONE
  `SimulationShared`, then run as many short-lived
  `SimulationWorker`s as needed off mutated **copies** of the
  `InputConfig` (struct assignment is safe: the copy shares
  read-only heap pointers and is never passed to
  `spody_free_input`).

Checklist, in order:

1. **Converter (if any) first, in the core clone** (§3.1 dance):
   new `include/spody_<fmt>.h` + `src/spody_<fmt>.c`, license
   header, static preamble writer per file (the existing converters
   deliberately keep their own copies), 0-anchored time column (the
   absolute epoch travels in the TOML's `et_start_s` — this is the
   workflow-wide contract). Add the source to the core
   `CMakeLists.txt` **and the header to `spody_core.h`** (forgetting
   the umbrella means the app cannot see the symbol).
2. **Epoch arithmetic**: never build ET through a full-magnitude JD
   (ulp of a modern JD is ~40 µs ≈ 30 cm along a LEO track).
   Compute the date's **midnight JD** (exact half-integer), take
   `(jd0 - JD_J2000) * SECONDSxDAY + seconds-of-day`, then apply
   the timescale chain (`spody_tai_minus_utc`, `TT2TAI_SEC`,
   `spody_tdb_minus_tt`).
3. **Any reusable math** the subcommand needs goes in a shared
   module, never in the feature file — owner's standing rule. The
   split (2026-07): algebra/geometry primitives (`spody_dot3` /
   `spody_cross3` / rotations / geodetic) live in `spody_math`;
   anything that evaluates **tabulated data** (`spody_bracket_index`,
   `spody_interp_linear`, the cubic Hermite dense output, future
   Lagrange/spline for an SPK reader) lives in `spody_interp`.
   Numeric defaults and thresholds go in `spody_const.h`
   (`SPODY_CAL_*` is the pattern), never inline.
4. **App side**: `src/<name>.c` in the app `CMakeLists.txt`,
   `cmd_<name>` + dispatch line + `usage()` entry + the subcommand
   list in `main.c`'s header comment. Outputs follow the run-folder
   convention: `spody_io_make_run_subdir` +
   `spody_io_run_subdir_filepath` + TOML snapshot, so every run is
   self-contained and ts-prefixed.
5. **Verify** with an independent oracle, not self-consistency: the
   OEM converter was cross-checked field-by-field (bitwise states,
   0.0 time axis) against a separate Python parse via
   `spopy.time`; `calibrate` was closed-loop tested (fit → node
   file → propagate → residual shrinks 8.35 km → 0.46 km on 3 ISS
   days). Local scripts under `tests/` (never committed).
6. **GUI hookup, when the subcommand deserves a button** (the
   Calibrate... button is the template — grep `calibrateRequested`):
   - the form owns ONLY the inputs and the busy state: a
     `QPushButton` in the relevant row, a minimal `QDialog` for the
     arguments, a `<name>Requested = Signal(...)` on `TomlForm`,
     and a `set_<name>_busy(bool)` that disables + relabels the
     button (the user must SEE that the click did something);
   - `MainWindow._action_<name>` does the heavy lifting through the
     SHARED `SpodyRunner` (never a second QProcess): same
     save-before-run gating as `_action_run`, banner + streaming
     into the Run-tab console, toolbar Stop free of charge. Pass
     the subcommand tail via `runner.run(..., extra_args=[...])`;
   - results flow back by CAPTURING a report line
     (`_on_calibrate_line` watches for the `nodes :` row while the
     action's flag is armed) — never by re-parsing files the
     engine already named on stdout. The completion pass
     (`_finish_calibrate`) must run on EVERY exit path of
     `_on_run_finished` (including the WIP early-return) and on
     `_on_run_error`, and must stay idempotent;
   - remember §3.3's GUI rule: launch `python -m spody_gui` and get
     the owner's OK before committing.
7. **Docs catch-up** (§3.3): manual ch. 12 section (+ ch. 5 form
   row and ch. 6/11 pointers if the subcommand feeds a TOML key),
   README feature list, CHANGELOG, this guide if the recipe moved.

### 5.12 Touching the 3D scene (spoviz vs spody_gui)

Since the 2026-07 extraction the 3D stack is layered like
CesiumJS-vs-app. Decide WHERE the change goes before writing it:

| layer   | file                              | owns |
|---------|-----------------------------------|------|
| library | `python/spoviz/scene.py`          | `Scene3D`: renderers, actors, the animation engine, sun light, skybox, camera, picking |
| library | `python/spoviz/decoration.py`     | ephemeris-driven garnish: third bodies, sun illumination, animated body-fixed frame, reference triads |
| library | `python/spoviz/bodies.py`         | NAIF ids, display colours, marker sizing / distance-compression knobs |
| library | `python/spoviz/textures.py`       | equirectangular pixel fixups + their on-disk caches |
| library | `python/spoviz/widgets.py`        | opt-in in-scene UI chrome (PlaybackBar, OptionsPanel) — standalone viewers only, never the GUI |
| library | `python/spoviz/qt.py`             | `SceneWidget` — the only PySide6 import in the package |
| app     | `spody_gui/vtk_canvas.py`         | compat shim: `VtkCanvas(SceneWidget)` + `MOON_RADIUS_KM` re-export |
| app     | `spody_gui/analysis/scene3d.py`   | glue: `PlotContext` / run folder / assets / constants → explicit spoviz arguments |

Checklist for a new 3D capability:

1. **New scene primitive** (a new actor kind, marker style, overlay,
   animation behaviour) → a method on `Scene3D` in
   `spoviz/scene.py`. House rules there:
   - positions in km, times in simulation seconds, rotation
     sequences `(N, 3, 3)` with columns = local axes in scene
     coordinates;
   - **no Qt imports, ever** — the module must keep working on an
     offscreen `vtkRenderWindow` with no QApplication in the
     process;
   - lengths/radii that depend on a body are **required
     parameters**, not defaults: spoviz cannot read
     `spody_const.h`, so physical numbers always come from the
     caller (the GUI reads them via `constants.const(...)`).
2. **New ephemeris-driven decoration** → a function in
   `spoviz/decoration.py` that takes `scene` plus explicit inputs
   only: `ephemeris` (duck-typed on `spopy.Ephemeris.position`),
   `orientation_for` / `texture_for` callables,
   `radius_km_by_name` mapping, optional `pump` (the GUI passes
   `QApplication.processEvents`). Then add a same-name wrapper in
   `analysis/scene3d.py` with the historical `(canvas, ctx,
   times_s)` signature that resolves `resolve_run_context`, opens
   the spopy ephemeris (`_run_ephemeris` already does both) and
   feeds `constants.BODY_RADIUS_KM`. Plot modules keep importing
   from `.scene3d` — they never see spoviz directly.
3. **GUI-only behaviour** (which PlotSpec draws what, Scene-options
   toggles, settings persistence) → stays in `spody_gui` (plot
   modules / `analysis_panel.py`), calling the canvas as before.
4. The plot functions receive `VtkCanvas`; every `Scene3D` method is
   reachable on it by delegation (`canvas.add_x(...)` ≡
   `canvas.scene.add_x(...)`, via `SceneWidget.__getattr__`). One
   trap: if a new `Scene3D` method name collides with an existing
   `QWidget` attribute (as `render` does), the QWidget name wins the
   lookup and the delegation is silently bypassed — add an explicit
   override in `spoviz/qt.py` like the existing `render()`.
5. **Verify** both hosts:
   - offscreen, no Qt: build a `vtkRenderWindow` with
     `SetOffScreenRendering(1)` + a plain
     `vtkRenderWindowInteractor`, construct `Scene3D`, drive the new
     API, `render()`, and pixel-check via `vtkWindowToImageFilter`
     (a scene with a central body lights >2 % of the pixels);
   - the launched GUI (§3.3 rule — owner OK before committing).
     QVTK gets **no valid pixel format under
     `QT_QPA_PLATFORM=offscreen`**, so the widget path can only be
     exercised in the real app.
6. **Docs catch-up** (§3.3): CHANGELOG + python/README layout +
   this section if the layering rules moved. The user manual only
   changes when something is user-visible.

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

  **Reference trap:** a stored `output/<ts>/` is only a valid
  bit-identity reference if no *deliberate physics change* shipped
  since it was written (the 2026-07 deltet relabeling invalidated
  every earlier stored run at the ~µm level). If the stored runs
  predate one, regenerate the reference first: stash your changes,
  point the submodule at the pre-change core commit, rebuild, run,
  then restore and compare against *that*. ULP-level noise against
  a stale reference is not your bug — but prove it this way instead
  of assuming it.
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
   `spody_io`, `spoviz` (catches syntax + 3.9 incompatibilities).
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
4. `AnalysisPanel` needs a real GL context (QVTK gets no valid
   pixel format under the offscreen QPA) — verify 3D views by
   launching the app. The Qt-free `spoviz.Scene3D` DOES render
   offscreen (see §5.12 step 5) — use that path for scene-engine
   changes that don't touch the widget.
5. **Always launch the GUI and exercise the changed surface before
   committing GUI work.** Watch the console: it must stay silent.

### 6.3 Time-scale changes

Anything touching `spody_time.c` / `spopy/time.py` must re-verify
the twins: dump `spody_tdb_minus_tt` + `spody_et_to_mjd_utc` from
the C side in hexfloat (`printf("%a")`) over a dense ET sweep
(thousands of epochs spanning 1972→2035, i.e. across every leap
boundary) and compare against `spopy.time` with `float.fromhex` —
equality must be exact (zero-ULP), not "close".

The same hexfloat-sweep discipline applies to the ephemeris twins:
anything touching the query path in `spody_ephemeris.c` /
`spopy/ephemeris.py` (Chebyshev evaluation or its derivative, the
granule/tau arithmetic, the EMRAT split, the per-body cache) must
re-verify `spody_get_ephstate` against `spopy.Ephemeris.state`
zero-ULP over a sweep of ETs spanning the file × a pair mix that
covers the Earth↔Moon fast path, both EMRAT branches, an SSB
shortcut and plain planet slots — all six components. Two extra
edges beyond the time chain: (a) the twins must derive the record
window the same way (nominal `start + i·seconds_per_record`), and
(b) the *operation order* of the EMRAT split must match
(multiply by the rounded reciprocal, never divide — the 2026-07
Earth-branch fix is the cautionary tale). Cross-check physics
against SPICE (`spkezr`, `de440s.bsp`): position and velocity must
agree at roundoff level (~1e-7 km / ~1e-14 km/s), anything worse
means real breakage, not noise.

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
  list. Anything that writes the state widgets *programmatically*
  must end with `_invalidate_ic_cache()` +
  `_seed_ic_cache_from_visible()` so later swaps start from the
  inserted values — `_on_cr3bp_from_clicked` (the From CR3BP...
  insert) is the template. *Symptom: stale numbers after a swap.*
- **`spopy.Ephemeris` is not thread-safe** (per-instance record
  cache): one instance per worker thread. *Symptom: garbled
  positions under concurrency.*
- **NRLMSISE-00 is the model definition, verbatim.** The coefficient
  tables in `spody_nrlmsise00.c` are generated from the official
  Fortran's `BLOCK DATA GTD7BK`, and the DATA constants keep the
  original (low) precision; the port matches the official reference
  driver's 17 cases to the printed 7 digits. Never edit a
  coefficient by hand and never "improve" a constant's precision —
  regenerate from the NRL source or don't touch it. (The model
  itself is fully re-entrant: all state is stack-local.) *Symptom:
  the 17 reference cases drift from the published outputs.*
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
