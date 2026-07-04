# SpOdy Developer Guide

How to maintain, update and extend SpOdy without breaking its
invariants. This is the document to read before writing code; the
[user manual](user-manual/) covers *using* the program, this one
covers *changing* it.

---

## 1. System map

SpOdy is three cooperating components:

| Component | Language | Where | Role |
|---|---|---|---|
| **spody-core** | C | `external/spody-core` (git submodule of [ValeEng/spody-core](https://github.com/ValeEng/spody-core)) | The physics/numerics library: ephemeris reader, force models, RKDP45 integrator with dense output, events, Earth orientation (IAU 2006/2000A_R06), GNSS/SP3 converters, time-scale helpers. No I/O policy, no TOML — pure engine. |
| **spody** (app layer) | C | `src/` | The `spody.exe` CLI: TOML parsing/validation (`toml_input.c`), worker setup (`sim_setup.c`), run loop + output writers (`sim_run.c`), subcommand dispatch (`main.c`: `propagate`, `batch`, `validate`, `info`, `convert`, `maxhgdegree`). |
| **GUI + Python libs** | Python | `python/` | `spody_gui` (PySide6 desktop app wrapping `spody.exe` as a subprocess), `spopy` (pure-Python mirror of spody-core read-side functions), `spody_io` (binary output readers). |

Data flow of one run:

```
input.toml ──> toml_input.c ──> sim_setup.c ──> integrator (spody-core)
                                                   │
               output/<ts>/<ts>_*.csv/.bin  <── sim_run.c (+ events)
                        │
               spody_gui Analysis tab (spody_io readers + spopy math)
```

Binary wire formats (all little-endian, 8-byte magic + header):
`SPDYOUT_` trajectory, `SPDYACC_` acceleration breakdown, `SPDYEVT_`
per-run events, `SPDYEVTB` batch-aggregated events (extra `case_idx`
column), `SPDYEPET` compiled DE440 ephemeris (`.spody`). Writers live
in `sim_run.c` / the converters; readers in `python/spody_io/`. A new
format field means touching **both** sides plus `detect_kind` in
`spody_gui/analysis/registry.py`.

### GUI package layout

- `spody_gui/main_window.py` — shell; owns the tabs. Entry points it
  imports are only `TomlForm` and `AnalysisPanel`.
- `spody_gui/form/` — the Run-tab form building blocks: `catalog`
  (declarative tables mirroring the engine schema), `widgets` (field
  factories), `sections` (one builder per TOML table), `visibility`
  (XOR groups + batch table), `roundtrip` (dict ↔ widgets),
  `handlers` (bottom bar). `toml_form.py` composes them as mixins
  over `QWidget` and keeps only state + signals.
- `spody_gui/analysis/` — the Analysis-tab machinery: `spec`
  (PlotSpec contract), `context` (PlotContext from the run-folder
  snapshot), `scene3d` (shared VTK decoration), `plots_*` (one module
  per view family, each exporting a `SPECS` list), `overlays`,
  `registry` (per-kind dispatch), `info`, `table_model`.
  `analysis_panel.py` keeps only the widget + file plumbing.
- `spody_gui/constants.py` — the **single reading point** for
  `spody_const.h` (see §4).
- `spopy/` — pure-Python re-implementations of spody-core read-side
  functions (ephemeris, EOP, rotations, Kepler, CR3BP). Function-for-
  function mirrors: **when you change a core function, check for a
  spopy sibling and keep it in lockstep.**

## 2. Repository workflow

- spody-core is developed in its **own clone**, never inside
  `external/spody-core`. The submodule is only re-pointed
  (`git -C external/spody-core checkout <sha>` + commit the pointer)
  after the core change lands on the core repo.
- Commit style: `scope: imperative summary` (`events:`, `gui(form):`,
  `core:`, `docs:`, `chore:`). Every feature push is followed by a
  separate `docs:` commit covering README + CHANGELOG + the relevant
  user-manual chapter.
- Work goes directly on `main` (single-maintainer flow); campaign-
  sized refactors go on a short-lived branch merged when green.

## 3. Building and running

**Engine** (from the repo root, Windows / MSVC):

```
cmake -S . -B build
cmake --build build --config Release        # -> build/Release/spody.exe
```

Useful options: `-DSPODY_FAST_MATH=ON` (faster, breaks bit
reproducibility — leave OFF for regression runs),
`-DSPODY_WHOLE_PROGRAM_OPT=OFF` (faster links while iterating).
The same recipe builds standalone spody-core in its own clone.

**GUI** (from `python/`, with the `.venv` interpreter):

```
python -m spody_gui
```

**Bundle**: `python/spody_gui.spec` via PyInstaller (`build_exe.ps1`).
Spec gotchas that have bitten before: `datas` paths resolve against
the *spec dir* but file-existence checks in spec code must use
absolute paths derived from `__file__` (PyInstaller cd's into the
build dir); one-folder output puts data under `_internal/`; the spec
ships `spody_const.h` under `spody-core/` so `constants.py` finds it
at `sys._MEIPASS/spody-core/spody_const.h`.

## 4. Conventions (how to write code here)

1. **License header.** Every new `.c`, `.h`, `.py`, `.spec` file gets
   the Apache 2.0 + `Copyright 2026 ValeEng` header *at creation*.
2. **C naming.** Functions exposed in a public header are
   `spody_*`; new file-local `static` functions and data take **no
   leading underscore** (older code still has them — follow the rule
   for new/touched code, don't mass-rename). Conversion constants use
   the `X2Y` style (`MAS2RAD`, `KM2AU`), not `_TO_`.
3. **Constants live in one place.** Every numeric constant belongs in
   `spody-core/include/spody_const.h`; calendar/time-scale helpers
   (Meeus Gregorian→JD, the leap-second chain, ET→UTC MJD) belong in
   `spody-core/src/spody_time.c`. Never hardcode either in an
   individual `.c`.
4. **Python reads the same constants.** `spody_gui/constants.py`
   parses `spody_const.h` (dev checkout and bundled install alike)
   and exposes named values; GUI code never hardcodes a physical
   constant. When adding a constant, add its clearly-marked fallback
   there too.
5. **Leap seconds have exactly two copies**: `spody_time.c` (C) and
   `spopy/eop.py::LEAP_TABLE_MJD` (Python; `spody_gui.time_conv`
   derives its calendar boundaries from it). A new IERS Bulletin C
   insertion is one row in each.
6. **Time and units.** ET = TDB seconds past J2000 is the canonical
   internal time everywhere; positions km, velocities km/s, ICRF
   internally; body-fixed frames only at the edges (input, display,
   surface projections).
7. **No micro-helpers.** Operations under ~6 lines used fewer than 3
   times stay inline; helpers are for non-trivial logic.
8. **Comments state constraints**, not narration: why a tolerance,
   which spec section, what invariant — not what the next line does.
9. **Docs cite SPICE** as the validation ground truth.

## 5. Extension recipes

The design goal after the 2026-07 refactor: each recipe below touches
a small, predictable set of files.

**New physical constant** — add the `#define` to `spody_const.h`
(plain number so the Python parser can read it); use it from C;
Python side reads `constants.const("NAME", fallback)`.

**New TOML key or section** (engine feature):
1. `src/toml_input.c`: `parse_*` for the section, config field in
   `toml_input.h`, checks in `spody_validate_input`; add to
   `FIELD_TABLE` if it must be batch-overridable.
2. GUI: row(s) in `form/catalog.py` (tooltip, validator, unit),
   builder additions in `form/sections.py` (+ one call in
   `TomlForm.__init__` for a whole new section), a hook in
   `form/visibility.py` if conditional. The round-trip is generic:
   widgets registered under the dotted key serialize themselves, and
   unknown sections pass through verbatim, so old TOMLs stay loadable.
3. Docs: manual ch. 5 (input reference) + CHANGELOG.

**New analysis view** — write the plot function in the matching
`spody_gui/analysis/plots_*.py` and append a `PlotSpec` to that
module's `SPECS` list. Nothing else: the registry assembles per-kind
lists, the panel dispatches on them. A new *file kind* additionally
needs: magic + reader in `spody_io`, one entry in each dict of
`analysis/registry.py`, and (usually) a new `plots_<kind>.py`.

**New event kind** (use the altitude-crossing commits as the
template — spody-core `d1bb88b`, spody `96b1ad5`..`913fb6d`):
1. spody-core `spody_events.{h,c}`: kind enum + constructor +
   residual function + a case in `spody_event_check_refined`
   (recurring kinds need the sign-tracking + Brent pattern; note that
   recurring kinds only fire with the RK45 dense-output path).
2. `src/toml_input.c`: parse the `[events]` entry; `sim_run.c`
   `build_events`: instantiate per config.
3. GUI: events table labels (`analysis/table_model.py`), any new
   view in `analysis/plots_events.py`, form panel in
   `form/sections.py`.

**New central body**:
1. Engine: entry in the app-side registry `src/central_body.{h,c}`
   (radius, mu, NAIF id, body-fixed rotation callback if any).
2. GUI: one `CentralBodySpec` in `spody_gui/central_bodies.py`
   (+ an orientation provider backed by spopy if the body rotates),
   texture asset in `spody_gui/assets.py` if desired. The form's
   combo auto-tracks the registry.

**New CR3BP primary pair** — `CR3BP_PAIRS` + separation constant in
`spody_const.h`, table in `src/toml_input.c` (`lookup_cr3bp_pair`),
mirror tuple in `form/catalog.py`. The two lists must stay in
lockstep (the engine validates unknown pairs at load time).

**New batch target** — row in `FIELD_TABLE` (`src/toml_input.c`) +
mode-tagged entry in `form/catalog.py::BATCH_TARGETS`.

**New force model** — implement the accel callback in spody-core
`spody_forcemodels.c` following the existing per-force pattern (each
force reads `ForceModelContext`, writes into the breakdown slots);
wire the enable flag through `[force_model]` parsing and the GUI
section builder. Atmospheric drag must go through the per-body
atmosphere callback declared in `spody_atmosphere.h`.

## 6. Verifying changes

- **Engine**: rebuild both repos; run the bundled example scenarios.
  The strongest cheap regression is bit-identity: re-run an example
  whose `output/<ts>/` you already have (same `input.toml`) and
  `cmp` the new `.bin` against the old — refactors and cleanups must
  be byte-identical; physics changes must explain every delta.
  Numerical validation of new physics is done against SPICE-derived
  references.
- **GUI**: `python -m py_compile` sweep over `spody_gui`/`spopy`/
  `spody_io`; import every module in isolation; instantiate
  `TomlForm` offscreen (`QT_QPA_PLATFORM=offscreen`) and round-trip
  an example TOML through `load_from_dict` → `to_dict` (no keys may
  be lost). `AnalysisPanel` needs a real GL context (VTK) — verify
  it by launching the app. Always open the GUI and exercise the
  changed surface before committing GUI work.
- **Bundle**: after touching the spec or data files, build the
  bundle and launch it on a machine (or folder) without the dev
  checkout — that is the only place the `constants.py` fallback and
  `_MEIPASS` paths are actually exercised.

## 7. Invariants that are easy to break

- `simulation.et_start_s` is TDB seconds past J2000; the GUI's UTC
  field converts through the SPICE `deltet` algorithm in
  `time_conv.py` — don't introduce a second conversion path.
- **Two ET→UTC chains coexist by design, with different fidelity.**
  The form's `time_conv` includes the TDB−TT periodic term (SPICE
  `deltet`, amplitude ±1.657 ms) so `et_start_s` is true TDB. The
  engine's `spody_et_to_mjd_utc` (and its bit-identical mirror
  `spopy.eop`) *neglects* TDB−TT when deriving UTC for EOP lookup
  and the ERA argument. Consequence: up to ±1.657 ms of UT1 error →
  ~25 mas of ERA → ~0.8 m at LEO radius, ~3.2 m at GPS radius in the
  Earth-fixed rotation, worst-case phase. This cancels in the
  current validations (propagator and reference converters share the
  same rotation chain) and sits below the broadcast-ephemeris floor,
  but it is NOT below cm-level SP3 truth: porting `deltet` into
  `spody_time.c` is the known fix if that precision class is ever
  targeted — do it as its own validated physics change, never inside
  a refactor.
- Events: recurring kinds (eclipse, altitude crossing) rely on dense
  output; only the RK45 integrator provides it. If another integrator
  is ever exposed, `spody_event_check` must grow a real fallback (it
  currently does not fire recurring kinds).
- The run folder contract: engine creates `output/<ts>/` and
  ts-prefixes every file; the GUI's rerun/analysis features parse
  that layout (`_RUN_FOLDER_RE`) — change it in both places or not
  at all.
- `InputConfig` is flat-copied by `spody_apply_batch_case`; adding a
  heap-owned field to it breaks batch mode. Fixed-size buffers only,
  or teach the copy.
- The four-representation `[initial_state]` cache in the form is
  invalidated by epoch/body/model changes — a new field that affects
  the state conversion must be added to that invalidation list.
- `spopy.Ephemeris` is not thread-safe (per-instance cache): one
  instance per worker thread.
