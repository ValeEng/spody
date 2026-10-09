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
(full definitions in user-manual ch. 15, the glossary):

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
| **spody** (app layer) | C | `src/` | The `spody.exe` CLI: TOML parsing/validation (`toml_input.c`), worker setup (`sim_setup.c`), run loop + output writers (`sim_run.c`), subcommand dispatch (`main.c`), density-scale fit (`calibrate.c`), Monte Carlo uncertainty propagation (`uncertainty.c`). |
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
`validate` (parse + validate only), `info` (app and core versions),
`convert` (ephemeris / harmonics_icgem / sp3 / gps / glonass / oem /
gp), `calibrate` (`calibrate.c`), `uncertainty montecarlo`
(`uncertainty.c`, §1.4), `maxhgdegree`.

### 1.2 Binary wire formats

All little-endian, 8-byte magic + version/dim header:

| Magic | Contents | Writer | Reader |
|---|---|---|---|
| `SPDYOUT_` | trajectory records `(t, x, y, z, vx, vy, vz)`; `t` is seconds since the run's `et_start_s` | `sim_run.c`, GNSS/SP3 converters | `spody_io/traj.py` |
| `SPDYACC_` | per-force acceleration breakdown (v4: `acc_earthradiation` appended, 432 B; v3 adds `acc_relativity`, 408 B; v2 `acc_solidtides`, 384 B; v1 360 B; all still read) | `sim_run.c` | `spody_io/accel.py` |
| `SPDYEVT_` | per-run events | `sim_run.c` | `spody_io/events.py` |
| `SPDYEVTB` | batch-aggregated events (extra `case_idx`; record struct `BatchEventRecord` in `sim_run.h`) | `sim_run.c` (batch, uncertainty) | `spody_io/events.py`, `uncertainty.c` (reads impacts back) |
| `SPDYUQM_` | Monte Carlo moments, one 44-double record per nominal epoch: `t, n, x_n[6], bias[6], C[21]` (lower triangle by rows, OEM order) `, curv_bias[3], curv_C[6]`; reserved1 = N cases | `uncertainty.c` | `spody_io/uq.py` |
| `SPDYUQC_` | Monte Carlo clouds, 8 doubles `(t, case, d[6])` per case at each snapshot, snapshot-major; reserved1 = N, reserved2 = snapshots | `uncertainty.c` | `spody_io/uq.py` |
| `SPDYEPET` | compiled DE440 ephemeris (`.spody`) | offline generator | spody-core + `spopy/ephemeris.py` |

Both events formats carry two record kinds that are **not** triggers:
`INITIAL_STATE` (3) and `FINAL_STATE` (4), the per-object life
markers written unconditionally by `emit_life_markers` in
`sim_run.c`. Adding a kind to `spody_event_kind` is deliberately
non-breaking — both dispatch switches in `spody_events.c` end in
`default: break`, so an unknown kind never fires — but every *reader*
that partitions records by kind must be revisited: the per-kind
digest in `analysis/derived.py`, the label map in
`analysis/table_model.py`, and any count that means "triggers" rather
than "records" (both event-timeline titles subtract the markers).
The rule of thumb: a marker is part of the population, never part of
the statistics.

A new format field means touching **both** sides plus `detect_kind`
in `spody_gui/analysis/registry.py`. Never change a record layout in
place — bump the header version and keep the reader
backward-compatible. (`SPDYUQM_` / `SPDYUQC_` are not in the registry
yet: they arrive with the GUI's Uncertainty tab.)

### 1.3 GUI package layout

- `spody_gui/main_window.py` — shell; owns the tabs and the one
  `SpodyRunner`. The entry points it imports are `TomlForm`,
  `UncertaintyPanel`, `AnalysisPanel` and `RerunPanel`. File > New /
  Save / Save As go through `_menu_*`, which act on the tab shown
  (Run or Uncertainty); the `_action_*` slots stay Run-form only
  because the form's save prompt calls them.
- `spody_gui/uncertainty_panel.py` — the Uncertainty tab: a
  self-contained editor for `<name>.uq.toml` (tomli in,
  `toml_io.format_uq_toml` out) with the Run tab's layout (TOML row,
  path + Draw samples / RUN / Stop, form, live preview, its own
  `TerminalView`). It owns no process: `runRequested(bool)` /
  `stopRequested` / `openResultsRequested(Path)` go to MainWindow,
  which runs `uncertainty montecarlo` on the shared runner with
  `_run_owner = "uq"` (that routes the output to the tab's terminal,
  arms the `run folder:` capture and sends the finish to
  `run_finished`). It validates only what it cannot write (unparsable
  numbers, a lognormal without `scenario_value_is`); everything else
  is the engine's, reached in under a second by Draw samples
  (`--samples-only`). Dispersible targets = `BATCH_TARGETS` minus
  `simulation.*`, `initial_state.*`, `integrator.*`, `output.*` and
  the two on/off switches (`dispersible_targets`), filtered by the
  scenario's object kind.
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
    plot needs, resolved from the run folder snapshot (epoch,
    duration, ephemeris path, central body, dynamics model, cases
    file, altitude crossings, `third_bodies`). **A new snapshot need
    goes into `resolve_run_context`, never into a private re-read of
    `input.toml` in the consumer** — `scene3d.add_third_bodies` did
    exactly that for the third-body list, and the two copies then
    parsed the same file with different fallbacks until the field
    landed here.
  - `plots_traj.py`, `plots_cr3bp.py`, `plots_diff.py`,
    `plots_accel.py`, `plots_events.py`, `plots_uq.py` (Monte Carlo
    moments and clouds) — one module per view family; each exports a `SPECS` list. **A new view = one function
    + one spec entry here.**
  - `registry.py` — assembles `PLOTS` per file kind, owns
    `KIND_LABEL`, `READERS`, `detect_kind`.
  - `derived.py` — per-file derived data, computed once and shared by
    the Info rows and every event plot: the identity-keyed cache
    (`cache_key`/`cached`, also used by `altitude_bands.py`), the
    `EventsDigest` (`events_digest`), the cached body-fixed impact
    projection (`impact_latlon`), the rendering-budget helper
    `decimate_for_display` and the axis-unit helper `time_axis`
    (used by the event *and* acceleration views). **Anything an
    events view derives from the raw array belongs here, not in the
    plot function** — see the scale rules in §5.3.
  - `altitude_bands.py` — the band-occupancy reconstruction behind
    the Info rows, the five band plots and the two band CSVs. One
    cached `_Recon` per file feeds every consumer, so the cumulative
    views, the per-object export and the instant snapshot can never
    disagree on thresholds, object set or window. Prefer adding a
    reader of `_Recon` over adding a second pass on the raw array.
  - `scene3d.py` — GUI glue over `spoviz.decoration`: keeps the
    historical `(canvas, ctx, times_s)` signatures, resolves the
    run-folder snapshot / texture assets / `spody_const.h` radii,
    then delegates to the library. Also `overlays.py`, `info.py`,
    `table_model.py` (events table).
  - `analysis_panel.py` (one level up) keeps only the widget + file
    plumbing.
- `spody_gui/constants.py` — the **single reading point** for
  `spody_const.h` (see §4.3/§4.4). Also holds the name-keyed mirrors
  of the engine's `BODY_TABLE` (`BODY_RADIUS_KM`, `BODY_MU_KM3_S2`)
  and of `CR3BP_PAIRS` (`CR3BP_PAIR_L_KM`) — one definition each,
  re-exported where the form needs them (`catalog.CR3BP_L_KM`).
- `spody_gui/toml_io.py` — `tomli` reader + the canonical, schema-aware
  emitter (`_SECTION_ORDER`, `_KEY_ORDER`). It writes two kinds of
  comment on top of the key/value body: the marker-delimited `notes`
  block at the end of the document (the only one that round-trips back
  into the form), and the **derived-parameter blocks** declared in
  `_SECTION_COMMENTS` — a provider per section name, taking the
  section's own dict and returning `#`-prefixed lines. Use one when a
  section's values are names the engine resolves into numbers that
  would otherwise leave no trace in the file (`[cr3bp]` today: `L`,
  `mu1`, `mu2`, `mu`, `omega`). Rules: derive from `constants` only, so
  the block is recomputed at every save and cannot go stale; return
  `[]` rather than guessing when anything is unresolvable; keep it
  informational — nothing may read it back.
  The same module lists TOMLs for both tabs (`find_toml_files`, which
  prunes `TOML_SCAN_SKIP_DIRS`; `is_uq_toml` splits them) and writes
  uncertainty files (`format_uq_toml`: documented key order, 6x6
  matrices one row per line, the file's top comment block kept via
  `uq_header_comment`).
- `spody_gui/runner.py` — spawns `spody.exe` with the scenario root
  as CWD (Windows MAX_PATH defence), streams output to the terminal
  pane. `subcommand` may be two words (`"uncertainty montecarlo"`),
  split before the TOML path.
- `spody_gui/setup_wizard.py` — first-run data download (DE440
  coverage profiles, EOP, textures).
- `spopy/` — pure-Python re-implementations of spody-core read-side
  functions, module-per-C-file: `ephemeris.py` (`position()`,
  `velocity()`, `state()` — the rates are the analytic Chebyshev
  derivative, mirroring `spody_get_ephvelocity`/`spody_get_ephstate`),
  `eop.py`, `earth_orientation.py`, `rotations.py`, `kepler.py`,
  `cr3bp.py`, `time.py` (the zero-ULP twin of `spody_time.c`).
  `rotations.py` also holds `ric_to_icrf` / `icrf_to_ric`, the
  zero-ULP twins of `spody_getrotmatrix_ric2icrf` / `_icrf2ric`
  (`spody_math.c`).
  **When you change a core function, check for a spopy sibling and
  keep it in lockstep** — several are verified bit-identical against
  the C side.
  **The dependency runs GUI → engine, never the other way.** When the
  GUI needs a computation the core already does (or gains), add its
  1:1 twin to `spopy` (same operations in the same order, the core's
  private constants copied inside the function, a zero-ULP twin
  check) and call that; never keep a GUI-only re-implementation.
  The core's documentation names its spopy twin, not the GUI. Example:
  the batch RIC rotation used `spody_gui.frames.ric_basis`
  (`np.linalg.norm` / `np.cross`, last-bit different from the engine
  on ~20 % of states); it now calls `spopy.rotations.ric_to_icrf`.
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

### 1.4 Anatomy of a Monte Carlo run (`uncertainty.c`)

`spody uncertainty montecarlo <name>.uq.toml` (manual ch. 14) reuses
the single-run machinery; nothing in `sim_run.c` knows about it except
two hooks that exist for it:

- **the state sink** (`SimulationWorker.state_sink` /
  `state_sink_user`, `sim_setup.h`): `emit_trajectory` hands every
  emitted state `(t, y)` to it, so a caller keeps a trajectory in
  memory without a file. NULL for propagate/batch/calibrate.
  The sink is called from the worker's thread: one sink object per
  worker, never shared.
- **`BatchEventSink`**: the run passes one with `case_idx` = the case
  number, so impacts and life markers of every case land in one
  SPDYEVTB file, read back at the end (`report_impacts`).

The flow, in `spody_uncertainty_montecarlo_run` → `run_montecarlo`:

1. `spody_load_uq_input` / `spody_validate_uq_input`
   (`toml_input.c`): closed schema, every check that needs the
   scenario.
2. Run folder + log (`<ts>_<name>.uq.log`), scenario snapshot, the
   `.uq.toml` copy pointed at the snapshot, the samples CSV.
3. `strip_scenario`: output files and every event but the always-on
   impact are removed (and named in the log) — only an impact may end
   a case, and the time span is the scenario's.
4. Pre-check of every case (`spody_check_case`): any failure stops the
   run with the list (a skipped case would bias the statistics).
5. Case 0 (nominal) runs alone, kept in a `Track` (the sink).
6. Cases 1..N in chunks of `8 × threads` (capped at 512 MB of
   trajectories), propagated in parallel, each built by
   `case_config`: a one-case `BatchConfig` fed to
   `spody_apply_batch_case`, so a `spody batch` rerun of the samples
   file gives the same bits. Then folded **in case order** by
   `stats_add_case` (Welford on `d = x − x_n`).
7. Writers: `write_moments`, `write_clouds`, `write_sigma_csv`, then
   `report_impacts` (Wilson 95 % interval, or the one-sided
   `1 − 0.05^(1/N)` bound for zero impacts).

Random numbers come from spody-core `spody_random` (Philox4x64-10,
counter-based): word k of stream `(seed, case, substream, domain)` is
a pure function of those numbers, so any case can be drawn by any
thread in any order. The domain is the third counter word:
`SPODY_RANDOM_DOMAIN_DRAW` (0) for the once-per-case draws, where
substream 0 is the initial state and a parameter's substream is the
FNV-1a 64 hash of its target path (`spody_random_substream_id`);
`SPODY_RANDOM_DOMAIN_PROCESS_NOISE` (1) for the process noise, where
substreams 0, 1, 2 are reserved for the R, I, C random acceleration
and 3 is the density (`pn_substream_density` in `uncertainty.c`).
See §7 for the invariants.

**Process noise (`[montecarlo.process_noise]`).** Three entries. The
accelerations (`acceleration`, `acceleration_1rev`):
`noise_accel_table` builds, per dispersed case, the node columns of a
spody-core `SpodyEmpiricalAccel` on one grid (the smaller
`interval_s` of the two): `a_ric` from substreams 0-2, `a_cos` from
4-6, `a_sin` from 7-9, one Gauss-Markov process per axis with its own
tau; `run_case` points that worker's `ctx.empirical_accel` at it, and
`spody_force_empirical` interpolates in ET, multiplies the 1/rev
columns by cos u / sin u of the current state and rotates into its
RIC axes. The density:
`noise_density_table` builds, per dispersed case, a
`MappedDensityScale` with nodes every `interval_s` (Gauss-Markov
values from `spody_gauss_markov_nodes`, times the case's own
calibration; with `ap_doubling` it first builds the per-node scale
1 + Ap / `ap_doubling` from `spody_space_weather_msis_inputs` (the
3-hourly Ap of the bin, `ap[1]`) on a local `MappedSpaceWeather`
bound to `shared->sw_data`, and passes it as the `scale` argument,
so the noise uses the same space-weather table and UT bins as
NRLMSISE-00; scale NULL keeps the constant-sigma values bit for bit),
and `run_case` points that worker's
`ctx.density_scale` at it after `spody_build_worker`. The table is
owned by the case loop (allocated and freed per case, per thread),
never by `SimulationShared`. The force interpolates it linearly, so
no discontinuity stops are needed. What a change here must keep
passing: for the density, input refusals, bit-identity without noise,
common random numbers, thread invariance, the linear theory (with a
time-varying drag response when `ap_doubling` is on: in a storm a
single step response underestimates the 24 h sigma by 30 %) and node
spacing; for the acceleration, the same against the
Clohessy-Wiltshire covariance; for the force itself, the
interpolation, the RIC rotation and the 1/rev terms.

**`SpodyEmpiricalAccel` (spody-core).** A generic force, not tied to
the Monte Carlo: any caller can fill a node table (ET, R/I/C in
km/s^2, plus optional cos u / sin u columns; each column may be NULL)
and set `ForceModelContext.empirical_accel`. A three-field
initialiser `{ et, a_ric, n }` stays valid (the 1/rev columns default
to NULL). It is summed
after relativity in the RHS and enters `spody_force_breakdown`'s
total (keeping total == RHS) but has no SPDYACC_ column; a scenario
key for a deterministic empirical acceleration does not exist yet.

**GUI side.** The Uncertainty tab (`uncertainty_panel.py`, §1.3)
writes the file and launches the run; the Analysis tab reads the two
statistics files through `plots_uq.py` (`SPECS_MOMENTS`,
`SPECS_CLOUDS`, registered as kinds `uq_moments` / `uq_clouds`) and
`info.py` (`info_rows_uq_moments` / `_clouds`, plus the run's
`.uq.toml` settings). Every view rotates into the nominal's RIC with
`spopy.rotations.icrf_to_ric` (the engine's twin, never a GUI
re-implementation), and the curvilinear cloud coordinates repeat the
engine's `curvilinear()` formulas. The clouds file does not carry the
nominal: `nominal_at` takes it from the `_moments.uq.bin` of the same
run folder, and a cloud view without it says so instead of guessing.

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
   CLI / ch. 14 uncertainty (Monte Carlo), plus ch. 15 glossary for
   new terms. The HTML/PDF are build
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
   `KM2AU`), never `_TO_`. A public name must say **what it returns
   and in which direction**, and must not be confusable with a
   neighbour; generic words alone (`basis`, `eigen`, `matrix`) are not
   names. Two families are fixed:
   - **Rotation matrices between frames:**
     `spody_getrotmatrix_<from>2<to>(..., R)` returns R with
     x_to = R x_from. Provide **both directions** whenever both make
     sense: `spody_getrotmatrix_icrf2moonpa` / `_moonpa2icrf`,
     `spody_getrotmatrix_ric2icrf` / `_icrf2ric`. Their Python twins in
     `spopy/rotations.py` are named `<from>_to_<to>`
     (`moon_pa_to_icrf`, `ric_to_icrf`, `icrf_to_ric`).
   - **Routines on n x n matrices** (row-major `a[i*n + j]`) carry
     their precondition in the prefix and, when more than one method
     could exist, the method in the suffix:
     `spody_symmat_cholesky`, `spody_symmat_eigen_jacobi` (both valid
     for symmetric matrices only). The historical 3 x 3 helpers
     (`spody_transpose_matrix`, `spody_rotate_vector`) keep their names.
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
   **Second exemption: constants private to one algorithm** stay in
   that module as file-local `static const` (or an `enum` for counts):
   the Philox multipliers and Weyl increments and the AS241
   coefficient tables in `spody_random.c`, the RK45 tableau in
   `spody_integrators.c`, the RIC degeneracy thresholds in
   `spody_math.c`. `spody_const.h` is for constants shared across
   modules or physical in meaning. When such a module has a `spopy`
   twin, the twin copies the private constants verbatim inside the
   function (`spopy.rotations.ric_to_icrf`).
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
10. **Every message goes through the log functions — app and core.**
    The log mirror lives in spody-core (`spody_io.h`:
    `spody_log_open_mirror` / `spody_log_close_mirror` /
    `spody_log_printf` / `spody_log_eprintf`); the app only uses it
    (`app_diagnostics.h` includes `spody_io.h`). Anything printed while
    a run is set up or executed — progress, warnings, errors, and every
    diagnosis inside the library (loaders, integrator, converters) —
    uses `spody_log_printf` (stdout) or `spody_log_eprintf` (stderr).
    Both write the terminal *and* the run-log mirror when it is open; a bare `printf` / `fprintf(stderr, ...)` / `perror`
    reaches the terminal only, so the saved log silently misses it
    (this happened to the EOP-prediction and density-scale warnings,
    and to every core diagnosis until the mirror moved into the
    library). Both functions carry a printf format attribute: gcc
    checks every call's arguments (`-Wformat`). They are safe from
    OpenMP worker threads (one `vfprintf` per stream per call, locked
    by the C runtime: 8 threads x 20 000 lines, no line split); open
    and close the mirror outside parallel regions. Checklist for a new
    message:
    - warning or error → `spody_log_eprintf`, prefixed `spody:
      warning:` (or `<subcommand>: WARNING --` in a subcommand);
    - progress / summary → `spody_log_printf`;
    - check it with any run and `grep` its log.
    Exempt: usage lines and argument errors printed before any TOML
    is read; debug-build traces inside `#if DEBUG_*` blocks (they stay
    bare `printf` on purpose). The
    `validate` / `maxhgdegree` / `info` subcommands never open a
    mirror, so there the functions behave like plain printf — use
    them anyway.

    **The run log is unconditional.** Every subcommand that runs a
    simulation opens its mirror itself, before its banner, and refuses
    to start if it cannot (`spody_log_open_mirror` ≠ 0 → error, exit
    1), exactly as `convert` does with `<output>.log`:

    | subcommand | log file |
    |---|---|
    | `propagate` | `<ts>_<simulation.name>.log` in the run folder; `<name>_<ts>.log` in `--out` or beside the TOML when there is no `output_dir` |
    | `batch` | `<ts>_<batch.name>.log` (`spody_io_batch_log_path`) |
    | `calibrate` | `<ts>_<simulation.name>.log` in its run folder |
    | `uncertainty montecarlo` | `<ts>_<name>.uq.log`, opened right after the run folder so refusals are logged |

    `output.log_file` is **deprecated**: the parser still accepts it
    and stores the value in `cfg.log_file`, whose only reader is
    `spody_input_warn_deprecated` (toml_input.c). Each command calls
    that function right after opening its log, so the warning lands
    in the log too, and the run goes on. Checklist for a new
    subcommand that runs a simulation:
    - open the log before the banner, with the name pattern above;
    - call `spody_input_warn_deprecated(&cfg)` once the log is open;
    - close it on every exit path (`spody_log_close_mirror` is a no-op
      when nothing is open, so the early returns can call it too).
    Checklist for deprecating another key: keep parsing it, add one
    `if` + `spody_log_eprintf("spody: warning: ...")` to
    `spody_input_warn_deprecated`, drop it from what the GUI emits
    (and pop it on load in `form/roundtrip.py`), mark it deprecated in
    manual ch. 6.
11. **Every file written is checked at close.** Output goes through
    stdio buffers (1 MiB for the run outputs), so on a full disk the
    `fwrite` / `fprintf` calls keep succeeding and the loss shows up
    only as the stream error flag or as the final flush inside
    `fclose`. Checking the writes alone is not enough. Every file
    opened for writing, in the app and in spody-core, ends with:
    ```c
    int write_failed = ferror(fp);
    if (fclose(fp) != 0 || write_failed) { /* error naming the file */ }
    ```
    The failure becomes the command's error (exit ≠ 0), never a
    warning, unless the file is only a copy of something already
    delivered: the run-log mirror prints a stderr
    warning and keeps the exit code. In `sim_run.c` use
    `close_output`, which keeps the first error. A new writer (output
    file, converter, export) follows the same pattern. *Symptom of
    breakage: a run or conversion on a full disk prints "done", exits
    0, and leaves a file missing up to its last megabyte.*

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
   **Sentinel value that relaxes another key's requirement.** When a
   key gains an "off" value that makes a *sibling* key pointless
   (`harmonics_degree = 0` makes `harmonics_file` unnecessary), three
   places have to agree or the schema contradicts itself:

   - **Parse order**: read the switch key *before* the key it
     governs, then choose `req_string` or `opt_string` on the fly.
     `parse_force_model` parses `harmonics_degree` first for exactly
     this reason. Note the two helpers have different signatures —
     `opt_string(tbl, key, out, outsz, &present)` takes no section
     name and no `SpodyError`.
   - **Validator**: guard the file-exists check with the same
     condition (`if (degree > 0 && !file_exists(...))`), otherwise
     the run dies on a file it would never have opened.
   - **Setup**: skip the load and leave the `init_*` flag at 0, then
     make the consumer's pointer NULL (`w->ctx.hg = w->init_hg ?
     &w->hg : NULL`). The engine's force model already guards on
     `ctx->hg`, so a NULL is the supported way to say "not modelled" —
     don't invent a separate boolean.

   Keep rejecting values that are *meaningless* rather than merely
   off: `harmonics_degree = 1` stays an error, because silently
   accepting it would hide a user's confusion about what degree means.

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
   - `options_bar=factory` for a view with controls of its own:
     `factory(replot) -> QWidget`, built once by the panel, shown
     above the canvas only while the view is active, calling
     `replot()` after each change. Keep the view's settings in a
     small class-level state object the plot function reads (see
     `CloudView` in `plots_uq.py`). A **3D** view with an options bar
     is a dedicated scene: the panel hides the orbit scene's sun row,
     animation bar and Scene options for it, so do not draw orbit
     decoration there. Any text drawn inside a VTK scene (legends,
     labels) must stay ASCII: the VTK font has no Greek glyphs, so
     write `sigma`, not `σ` (matplotlib and Qt widgets are fine).

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
- *A second export action*: the export types live in one place, the
  `_EXPORT_TYPES` tuple in `plot_options.py` — `(id, radio label,
  tooltip)`. Adding one is: append an entry there; add
  `"<id>": <bool>` to `_export_availability()` in
  `analysis_panel.py`; add an `elif export_id == "<id>"` branch to
  `_on_export_requested`; write the `_export_<name>_csv` method. The
  dialog needs no new signal — `exportRequested` already carries the
  id — and the radio greys itself from the availability dict.
  Gate it on the DATA, not the figure, when the export derives from
  the loaded array rather than the drawn lines: the altitude-band CSV
  is enabled by `_can_export_altitude_bands_csv` (a central-body
  `ALT_CROSSING` **or** life marker present), independent of which
  plot is showing. Serialise in the analysis module, not in the panel
  (`per_object_bands_to_csv`, `band_snapshot_to_csv`), and put the
  provenance in a `#`-comment header — thresholds, their source,
  whether the rows are the whole population, the window. A CSV whose
  denominator is ambiguous is a CSV that will be misread.
- *A view that needs a user-chosen parameter*: most plots are a pure
  function of the file, but some need an input the file cannot supply
  (the band snapshot needs *which instant*). The path is: a field on
  `PlotContext` (`analysis/context.py`), set in
  `_build_plot_context`; panel state holding it; a control plus a
  `Signal` on `PlotOptionsDialog`; a panel slot that stores the value,
  re-syncs export availability and re-renders via
  `_on_plot_tree_clicked`. Emit on `editingFinished`, never
  `textChanged` — a re-render per keystroke on a million-segment batch
  makes the field unusable. When the parameter is unset, say so in the
  view instead of inventing a default: a silently chosen instant is
  read as if it had been asked for.

  The altitude-band feature is the worked template for all three
  points.

**Scale (millions of rows).** An events log routinely carries millions
of records &mdash; a debris batch of a few thousand cases crossing five
altitude bands reaches ten million triggers &mdash; and the Info tab
re-runs its analysis on *every* switch to the tab. Four rules keep that
from freezing the GUI. They are not optional polish: ignoring them once
turned a full pass over the event views into 335 s.

- *Vectorise the per-record work.* Do the O(N) step in numpy (one
  sort to group, `np.diff` / `np.bincount` / `cumsum` to aggregate),
  never a Python `for` over the records; leave Python loops only over
  the handful of bands / series. `altitude_bands.py` is the worked
  example (`_reconstruct` + the flat-segment plots). The rewrite must
  stay **bit-identical** to the readable version &mdash; guard it with
  the hand-computed + e2e cross-checks in `tests/analysis/`
  (local-only) before trusting a run.
- *Cache once per file.* Everything derived from a loaded array goes
  behind the identity-keyed memo in `analysis/derived.py` (`cache_key`
  / `cached`: keyed by the owning array + buffer address + size +
  first/last timestamps + params), so the Info tab, the plots and the
  exports share one computation per loaded file and repeat touches are
  free. The key is an identity, not a content hash: it stays sound
  only because `cache_key` puts a `weakref.finalize` on the owning
  array that drops its entries when the array is freed (numpy reuses
  the address for the next array of the same size). Never key on
  `ctypes.data` or `id()` without that tie; never hash the content
  either (a gigabyte events file would pay a full pass per click).
  Pass the loaded array (or a view of it), not a fresh copy: a copy
  is a new owner and always misses.
  For event files the shared product already exists: `events_digest`
  returns an `EventsDigest` with the per-kind split, the crossed-
  altitude clusters (direction split included), the eclipse pairing
  and the impact counts, and `impact_latlon` caches the body-fixed
  impact projection. **A new event view derives from the digest**;
  it does not re-scan the raw array. Adding a field to `EventsDigest`
  is the right move when two consumers would otherwise both compute
  it. Also: pick a readable time unit from the plotted span
  (`derived.time_axis`, shared by the event *and* acceleration views)
  — don't hardcode seconds. If the view is overlay-safe, memoise the
  chosen unit on the `Axes` (`_spody_time_unit`, see
  `plots_accel._time_axis_data`): `make_2d_overlay` calls the plot fn
  once per file against the **same** axes, so a per-call choice lets a
  12 h file pick hours while a 6-day file picks days and the two land
  on silently different scales.
- *Never one matplotlib artist (or polygon vertex) per record.* A
  canvas is ~1200 px wide; past that, extra markers buy nothing
  visible and cost seconds on **every** redraw, including each zoom
  and pan, which no amount of caching fixes. Run x data through
  `decimate_for_display` (marker series) or build the curve on a
  bounded node grid (`band_population`) instead. Two rules on top:
  stay **exact below the budget**, so small runs render byte-for-byte
  as before and the regression PNGs keep their meaning; and **say so
  in the plot title** when the budget engages, so nobody reads a
  sampled curve as an exact one. `_plot_events_survival_timeline`
  (bars &rarr; one `LineCollection` past 200 cases) and
  `_plot_bands_gantt` (one `broken_barh` collection per band) are the
  worked examples.
- *Watch for the numpy traps that only bite at scale.* Three cost real
  seconds here and none of them is visible in the source:
  - `kind="stable"` only selects **radix sort for 16-bit-and-narrower
    integers**; on anything wider it falls back to timsort. Grouping
    ten million crossings by `case_idx` took 5.2 s as `int64` and
    0.17 s once narrowed. Use `derived.stable_group_order`, which
    narrows and, past 65536 ids, does the two-pass LSD radix.
  - `legend(loc="best")` re-scans every plotted point hunting for a
    free corner. On a million-marker axes it costs more than the
    markers. Pin the location.
  - `np.add.at` is unbuffered and an order of magnitude slower than
    the equivalent `np.bincount` (flatten a 2D scatter-add into
    `group * n_cols + col` and reshape). Likewise, `events[mask]` on
    a structured array copies **every field** of the selected records
    (~900 MB at 10M rows) &mdash; mask one column at a time instead,
    and contract `y` on the strided `(N, 6)` view before masking. And
    `np.abs(x[:, None] - centers[None, :]).argmin(1)` allocates an
    (N x K) matrix; `derived.nearest_index` does it with
    `searchsorted` on the midpoints, ties broken identically.

**Verify:** launch the GUI, load the right file kind, read the Info
rows and (for an export) round-trip the CSV back through numpy;
confirm the button greys out on a file that shouldn't offer it. For a
vectorised or caching rewrite, three things, in this order:

1. re-run the `tests/analysis/` checks (local-only) — they are the
   bit-identity guard;
2. **profile before you optimise, on a synthetic file at the real
   scale** (a ten-million-record events log is a few lines of numpy to
   generate). Every large win in the 2026-08 rewrite came from a
   measurement that contradicted the obvious guess, and two of the
   biggest were in numpy itself rather than in this code;
3. prove you changed nothing visible: dump the Info rows to a file
   before and after and `diff` them, and render every 2D view of the
   kind to PNGs on both sides (`git stash` gives you the "before")
   and compare the hashes. Any hash that moves must be a change you
   can name.
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
tree). **Verify:** run a scenario that writes the new file; the
`spody_io` reader loads it (header, version, record count = file
size); the Analysis tree lists it under the new label and the views
render. (`spody info` prints only the app and core versions; it does
not read files.) **Document:** §1.2 table in this guide;
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
   The run's central term then takes the file's GM, not the registry
   `mu` (§7, "The central GM comes with the gravity file").
   Solid tides: point `.tides` at a `SpodySolidTides` template (Love
   numbers, max degree, `kplus`, tide-raising bodies with their GM)
   and set `.tide_a0h0` if the body has a permanent-tide convention
   (0 = only tide-free fields accepted). Leave `.tides` NULL and
   `force_model.solid_tides` is refused for the body (§7, "The tide
   model is the body's, the tide system is the file's").
5. **GUI mirror**: one `CentralBodySpec` in
   `spody_gui/central_bodies.py` (name, `naif_id`, `radius_km`,
   `mu_km3_s2`, `bf_frame_name`, `bf_orientation`,
   `bf_orientation_many`). The orientation provider is the spopy twin
   of the C rotation (see `_moon_orientation` for the pattern) — if
   you wrote a C provider, write the spopy sibling and keep them in
   lockstep (§4.5 spirit). `bf_orientation_many` is the same rotation
   over an array of epochs, `(et[n], eph) -> R[n, 3, 3]`: every
   per-sample use (body-fixed plots, impact lat/lon, animated triads,
   spoviz `orientation_for`) calls it once for the whole grid, so any
   per-call setup (file lookup, series nodes) is paid once.
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

**Files:** `spody_const.h`, `src/toml_input.c`, `constants.py`,
`form/catalog.py`.

1. Separation constant `<PAIR>_DISTANCE_KM` in `spody_const.h`
   (recipe 5.1 rules apply).
2. Row in `CR3BP_PAIRS` in `toml_input.c` (feeds
   `lookup_cr3bp_pair`; unknown pairs are rejected at load with a
   message that lists the known ones — your row updates that
   message for free).
3. Mirror tuple in `CR3BP_PAIRS` in `form/catalog.py` for the combo,
   plus its separation in `constants.CR3BP_PAIR_L_KM` (same constant
   as step 1; `catalog.CR3BP_L_KM` is an alias of that dict, do not
   re-declare the pair there).
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
6. Also free riders, and these need no central-body registration —
   the run-record blocks (`print_cr3bp_params` in `src/main.c`, the
   `[cr3bp]` comment provider in `toml_io.py`, §1.3). They resolve
   GM by name from `BODY_TABLE` / `constants.BODY_MU_KM3_S2`, so a
   pair of ordinary third bodies works; check the emitted comment
   actually appears (a missing GM entry silently drops the block).

**Verify:** a CR3BP run with the new pair (`cr3bp_em_l4` is the
template scenario); the synodic 3D view shows both primaries at the
right separation; a From CR3BP... conversion round-trips a state of
the new pair; the propagate banner and the saved TOML's `[cr3bp]`
comment agree on `L` / `mu1` / `mu2`. **Document:** manual ch. 6;
CHANGELOG.

### 5.8 New batch override target

Covered inside recipe 5.2 step 4 — the two tables (`FIELD_TABLE` in
C, `BATCH_TARGETS` in Python) are the whole feature. Remember the
exclusion rule: keys whose change would invalidate resources shared
across batch cases (central body, harmonics file/degree) are
excluded *on purpose* — batch shares one `SimulationShared` across
cases. A value a case can change must reach the force model from the
case config in `spody_build_worker`, never from `SimulationShared`
(§7, "A batch target never lives in `SimulationShared`").
**Verify:** 2-case CSV overriding the key; case 2 must be
bit-identical to a single run with that value written in the TOML
(differing from case 1 is not enough: a target read from the shared
data still makes every case equal to the nominal). **Document:** manual
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
   future debugging. Add it into `acc_total` at the same position
   and with the same grouping as in `spody_force_rhs_default`: the
   total is promised bit-identical to the right-hand side, and
   regrouping a sum changes its rounding.
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

Worked example of a body-driven force: the solid tide
(`spody_force_solidtides`). The engine function knows no body; the
numbers ride on `ctx->tides`, filled in `sim_setup.c` from the
registry row plus the gravity file. Its breakdown slot was *appended*
to `ForceBreakdown`, which bumped `SPDYACC_` to v2 with a reader that
still takes v1 (§1.2) &mdash; the pattern for the next force, and the
one general relativity followed right after (`acc_relativity`, v3).
The simplest shape of a force is `spody_force_relativity`: no
ephemeris, no rotation, only `r`, `v` and `mu_central`, switched by a
plain `ctx->enable_relativity` flag.

A body-restricted force: `spody_force_earthradiation` (albedo +
infrared). The model belongs to one body, so the registry row carries
a flag (`earth_radiation`, set only on the Earth row), the validator
refuses the key for any body without it, and the form hides the row
through the same central-body hook as drag (`_on_central_body_changed`
in `form/sections.py`, plus the pop in `form/roundtrip.py` for other
bodies). An integral over the visible Earth goes in solid angle seen
from the satellite (Gauss-Legendre in cos(nadir) x azimuth): exact on
a uniform sphere, cost independent of altitude.

**An external reference can be wrong: check it against an exact case
first.** Orekit 13.1's Knocke model gave 19x SpOdy in LEO and 0.03x in
GEO; reproducing its two choices (cap `asin(R/r)`, geocentric cosine)
on a uniformly bright sphere, whose irradiance is exactly `M (R/r)^2`,
matched its ratios to the percent. Checklist before accepting or
rejecting a cross-check: (1) a closed-form case; (2) an independent
fine quadrature; (3) only then the other tool, with its source read.

**Cross-checking a force as "on minus off": both runs must share
their integration errors.** Each run of a 7-day LEO carries metres of
integration error along track, far above the tolerance you set
(Orekit `tolerances(1e-5 m)`: 9.6 m; SpOdy `rel_tol = 1e-9`: 1.3 m).
The difference of two runs is clean only because those errors are
nearly the same in both and cancel. Anything that breaks the step
sequence breaks the cancellation: sampling Orekit with one
`propagate()` call per output epoch restarts the integrator every
sample, and turned the GRACE-FO relativity effect into 19.9 m instead
of 17.9 m. Checklist:
  1. One propagation per run; read the samples from the dense output
     (Orekit `getEphemerisGenerator()`, SpOdy fixed output mode).
  2. Tolerance tight enough that the single run is converged (Orekit
     1e-7 m, SpOdy 1e-11 for a LEO week).
  3. Repeat at a second tolerance; the effect must not move.

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
4. **Two times, two functions.** The integrator's `t` is not always
   ET - et0: with `integrator.time_scale = "tt"` it counts TT seconds.
   Every absolute time handed to physics (ephemeris, rotation, space
   weather, events) goes through `spody_ctx_et(ctx, t)`; every time
   written to a file goes through `spody_ctx_label(ctx, t)` (ET - et0),
   and file-side epochs come back with `spody_ctx_t_of_label` (the
   output grid, the end of the run). Never write `ctx->et0 + t` or
   emit a raw integrator `t`: in TDB mode the helpers return exactly
   what that would, so the mistake only shows in TT runs, as a
   1.7 ms-scale label error. Verify a change here with a two-body TT
   run against Orekit (1 mm on 7 days) and the TDB default against
   the bit-identity regression.
5. If the change alters results (physics): treat outputs as a
   deliberate compat break — measure, update stored example
   references and `et_start_s` values where the epoch semantics
   moved, and write the numbers in the CHANGELOG (the 2026-07 deltet
   entry is the template).

### 5.11 New CLI subcommand or format converter

Canonical examples: `spody convert oem` (converter, spody-core
`spody_oem.{h,c}`), `spody calibrate` (subcommand,
`src/calibrate.{h,c}`) and `spody uncertainty montecarlo` (subcommand
with its own input file `<name>.uq.toml` and a two-word verb,
`src/uncertainty.{h,c}`, §1.4). A subcommand with its own input file
also needs the two-way refusal: its file must be refused by
`propagate`/`batch`/`validate` (as `spody_load_input` refuses a
`[montecarlo]` table), and a plain scenario refused by it. The split rule decides where the code goes
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
   `spody_interp_linear`, the cubic and quintic Hermite dense output, future
   Lagrange/spline for an SPK reader) lives in `spody_interp`.
   Numeric defaults and thresholds go in `spody_const.h`
   (`SPODY_CAL_*` is the pattern), never inline.
4. **App side**: `src/<name>.c` in the app `CMakeLists.txt`,
   `cmd_<name>` + dispatch line + `usage()` entry + the subcommand
   list in `main.c`'s header comment. Outputs follow the run-folder
   convention: `spody_io_make_run_subdir` +
   `spody_io_run_subdir_filepath` + TOML snapshot, so every run is
   self-contained and ts-prefixed.
4b. **A converter that prints instead of writing skips the
    run-folder convention.** `convert gp` is the first: it emits an
    `[initial_state]` block on stdout and leaves nothing behind, so
    `spody_io_make_run_subdir` would create a directory to put
    nothing in it. It behaves like `help`. The convention exists so a
    run is reproducible from the folder it left; where there is no
    artefact there is nothing to reproduce, and the ceremony is only
    ceremony. Say so in the file, or the next reader will file it as
    an oversight.
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
   - **A subcommand with its own input file gets its own tab**, not
     a form button (template: `uncertainty_panel.py`). Checklist:
     the Run tab's combo must skip the new files
     (`_refresh_toml_combo` filters with a `toml_io` predicate like
     `is_uq_toml`); `_open_path` routes them to the tab and brings it
     to the front; `_menu_new` / `_menu_save` / `_menu_save_as` and
     `_refresh_title` follow the tab shown; `closeEvent` asks the
     tab's `maybe_save()` too; the launch reuses the shared runner
     with a `_run_owner` value, and `_on_run_finished` /
     `_on_run_error` / `_on_run_started` handle that owner on every
     path (terminal, `set_running`, finish). Reuse the RUN / Stop
     looks from `toml_form.RUN_BUTTON_QSS` / `STOP_BUTTON_QSS`.
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
   `orientation_for` (array providers, `(et[n], eph) -> R[n]`) /
   `texture_for` callables,
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

### 5.13 Touching the eclipse / occulter model

**Files:** spody-core `spody_eclipse.{h,c}` (geometry),
`spody_math.{h,c}` (`SpodyBodyShape`, `spody_iau_pole`,
`spody_body_shape_distance`), `spody_forcemodels.{h,c}`
(`srp_lit_fraction` + the two call sites), `spody_events.c` (the
eclipse event, IMPACT and altitude against the shape);
`src/sim_setup.c` (the list), `src/sim_run.c` (`set_event_body_shape`),
`src/toml_input.c` (`BODY_TABLE` radii + poles,
`spody_lookup_body_shape`, the required `body_shape` key); GUI
`spody_gui/analysis/derived.py` (impact latitude) with its twin
`spopy/geodesy.py`.

The split to respect:

- **`spody_eclipse.c` is pure geometry.** No `ForceModelContext`, no
  ephemeris, no NAIF ids — it takes vectors and radii and returns a
  number. That is what makes it testable on its own, without DE440.
- **Who may cast a shadow is application policy**, decided once in
  `sim_setup.c` before the run: central body ∪ third bodies, minus the
  Sun (`SUN_NAIF`) and minus radius ≤ 0. The core consumes the list and
  asks no questions — same layering as the central-body registry.
- **The list is built before the integration loop**, never inside the
  RHS: membership is epoch-independent. In batch it must be identical
  across cases, or two cases of the same sweep would be running
  different physics.

Rules baked into the current code, in decreasing order of "you will
regret ignoring this":

1. **Fractions, not areas.** Every term is divided by the same
   `PI * a²`, so the geometry returns *fractions of the solar disc*
   and the factor never enters the arithmetic. This is not cosmetic:
   it is what keeps the degenerate branches (`1.0`, `b²/a²`) exact
   instead of routing them through a multiply-then-divide pair, and
   therefore what keeps single-occulter runs bit-identical.
2. **Beware operator association when touching the formulas.**
   `PI*a*a` is `(PI*a)*a` and can differ from `PI*(a*a)` by 1 ULP,
   which is enough to move a step-size accept/reject decision and
   destroy the bit-identity regression of §6.1. Precompute `aa = a*a`.
3. **The union, not the sum.** Two bodies over the Sun at once have
   overlapping shadows on the disc; the hidden fraction is
   `Σ g_i − Σ_{i<j} g_ij`. Third-order terms are dropped and the
   result is clamped into `1 − Σg_i ≤ lit ≤ min_i(1 − g_i)`.
   **INVARIANT: never remove that clamp.** It is what makes the
   truncation safe — whatever the dropped terms would have been, the
   answer stays inside a bracket that provably contains the truth.
4. **Angles between occulters come from the chord**
   (`2·asin(|û_i − û_j|/2)`), never from `acos(û_i·û_j)`. The bodies
   are nearly aligned exactly when the correction matters and `acos`
   loses half its digits there; with `acos` two superposed discs stop
   cancelling and the pairwise term goes wrong in the 7th digit.
5. **The screening test is exact, not an approximation.** "Satellite
   on the sunward side of the body ⇒ lit" fails only below an altitude
   of `R·(1/cos a − 1)` ≈ 70 m for the Earth, because the penumbra
   never reaches the plane through the body centre. It is written as
   `|s→occ|² > s→occ · s→sun` — a sign test with no sqrt and no
   division.
6. **The eclipse EVENT stays single-occulter on purpose.** "Eclipsed
   by the Moon" and "eclipsed by the Earth" are separate events with
   their own thresholds. Do not "fix" the disagreement between the
   event fraction and the force fraction during a double eclipse.
7. **An event never roots a flat function.** The lit fraction is
   exactly 0 through the umbra and exactly 1 through sunlight, so
   "fraction − 0" and "fraction − 1" are flat on one side of their
   root: a bracketing solver stops on any probe of the flat side
   (threshold 0 once logged every contact inside the umbra, up to one
   step late) and "fraction − 1" never changes sign at all (threshold
   1 logged nothing). `spody_get_eclipse_residual` switches to the
   angular contacts `c − (b − a)` and `c − (a + b)` at those two ends.
   The same trap waits for any future event whose predicate saturates
   (a visibility mask, a lit/unlit flag): give the root finder a
   signed distance, not a clipped quantity. Check a new event against
   every external detector available, on the same trajectory (here
   Orekit to 0.3 µs, GMAT's `EclipseLocator` and Tudat's shadow
   function to their 1 ms resolution), not only against itself, and
   match the body shapes first: GMAT's locator takes the Earth
   ellipsoid from its SPICE PCK and ignores the script's radius, which
   alone shifts LEO contacts by up to 18 s.
8. **One shape per body, everywhere** (`force_model.body_shape`,
   required): shadow, IMPACT and altitude read the same
   `SpodyBodyShape`, as in Orekit. GMAT mixes a sphere for its SRP with
   an ellipsoid for its altitude and locator, and that mismatch is the
   thing to avoid. `r_pol <= 0` or `== r_eq` is a sphere and must take
   the exact code path of old: the `"equatorial_sphere"` regression
   (every trajectory AND events file byte-identical to the previous
   release) is the contract.
9. **Compute the spheroid only where it can change the answer.** The
   spheroid lies between its polar and equatorial spheres, so the limb
   angle lies between their two `asin(R/d)` and the geodetic altitude
   between `d − r_eq` and `d − r_pol`. Outside that band the sphere
   already decides (lit / total shadow; above / below the target
   altitude) and a bound with the right sign is returned. This is what
   keeps the always-on IMPACT check, run at every step, at one sqrt:
   without it the Bowring iteration cost +6 to +11 % of run time.
   The eclipse *residual* is the exception (limb always computed) so
   that Brent sees a continuous function; logged values
   (`distance_at_trigger`, life markers) always use the exact
   `spody_event_body_distance`.
10. **The limb is found in the plane (satellite, centre, Sun).** The
   affine stretch of the polar axis by `r_eq/r_pol` maps the spheroid
   to a sphere and keeps planes, lines and tangency; the tangent point
   comes in closed form and is mapped back. Off-plane contact points
   are second order over the Sun's 0.27 deg (GMAT/SPICE, the exact
   ellipsoid, agrees to 1.1 ms on LEO contacts).

**Adding a body that can occult** is therefore nothing but making it
available as a third body (recipe 5.6 / the app-side `BODY_TABLE`);
there is no eclipse-specific registration. Give its row the polar
radius and the four IAU pole elements from `pck00011` (constants in
`spody_const.h`, recipe 5.1) when the kernel models it as a spheroid;
leave them 0 for a sphere.

**Verify:** two checks are the contract — the combination against an
independent polar quadrature over the solar disc (an oracle that knows
nothing about lenses or three-circle areas), and a sweep of ~2.6M
configurations against a frozen copy of the pre-2026-07
implementation demanding *exact* equality. Then §6.1 bit-identity on
`gps_g11_validation`, and a cislunar arc through a lunar eclipse for
the multi-occulter path itself. For the shapes: the regression with
every input switched to `"equatorial_sphere"` must be byte-identical
(trajectories and events); eclipse contacts against GMAT's
`EclipseLocator` (default PCK = exact ellipsoid; a copied PCK with
equal radii, passed via `--startup_file`, for the sphere) and Orekit's
`OccultationEngine` on a `OneAxisEllipsoid`; geodetic altitudes of
trigger states against SPICE `recgeo` in the frame of the same pole
(round-off) and Orekit `OneAxisEllipsoid.transform` (ITRF, 0.3 m from
nutation). Tudat cannot check the ellipsoidal shadow: its occultation
takes a sphere of the mean radius even for an oblate shape.
**Document:** manual ch. 6 (*Which bodies cast a shadow*, *Body
shapes*), CHANGELOG with the measured effect.

### 5.14 New `[initial_state]` frame

**Files:** `src/toml_input.h` (enum), `src/toml_input.c`
(`parse_frame` + two validators), `src/sim_setup.c` (the rotation),
`python/spopy/rotations.py` (the twin), `python/spody_gui/form/
catalog.py` + `sections.py` (combo + swap cache).

The shape every non-inertial frame follows — `central_body_fixed`
and `orbit_plane` are both instances of it:

1. **Enum value** in `SpodyFrame` (`toml_input.h`). Mind the comma:
   the previous entry's trailing comment sits between the value and
   the `,`, so `... */,` is the correct placement and forgetting it
   produces a wall of cascading "syntax error" noise pointing at
   unrelated lines.
2. **Name** in `parse_frame` (`toml_input.c`), and add it to the
   `(supported: ...)` suffix in the same function so a typo'd frame
   lists it.
3. **Two validators**, both in `toml_input.c`: the Keplerian
   finalizer (`finalize_keplerian_initial_state`) and the
   high-fidelity validator. They are separate gates — a frame added
   to only one of them works for Cartesian input and is rejected for
   Keplerian, or vice versa. Any body/model restriction goes in the
   HF validator, with a message naming what the user actually
   selected.
4. **Rotation** in `sim_setup.c`, in the `if / else if` chain right
   before `spody_set_integrator_state`. Everything it needs is
   already on the worker: `w->ctx.get_bf_rotation` for the central
   body's pole/basis, `&w->eph` for ephemeris queries, `body->naif`
   for the central body id. **Pure rotation, no `omega x r`** — the
   GUI and the spopy plotting path use the same convention, and
   adding the frame-rate term here would make typed values fail to
   round-trip through the form.
5. **spopy twin** in `rotations.py`, taking *explicit vectors* rather
   than an ephemeris handle so it stays dependency-free and usable
   from both the GUI and offline analysis. §5.10 applies: the twin
   and the C branch must agree, and the cheapest proof is to
   propagate one step and read the engine's `t = 0` record back
   through the twin.
6. **GUI**: add the name to `FRAMES_BY_MODEL` (`catalog.py`), a
   resolver returning `R_icrf_to_<frame>` wired into
   `_resolve_ic_frame_rotation` (`sections.py`), two rows in
   `_IC_VARIANTS` (cartesian + keplerian), the name in
   `_IC_HF_FRAMES`, and any availability gate in
   `_refresh_input_frame_availability`. The conversion hub
   (`_ic_block_to_cart_inertial` / `_ic_block_from_cart_inertial`) is
   already frame-generic — it dispatches on "not `central_inertial`"
   — so a correct resolver is all the swap cache needs.

**Break risk:** a GUI gate that is looser than the engine validator
(the combo offers a frame the engine rejects at load); the swap cache
silently keeping a stale representation because a new variant was
added to `_IC_VARIANTS` but the frame was left out of
`_IC_HF_FRAMES`, which makes the combo flip skip the rotation and
*relabel* the numbers instead of converting them — the worst failure
mode here, because nothing errors, the state just silently becomes a
different orbit.

**Verify:** (a) engine `t = 0` record read back through the spopy
twin reproduces the typed elements — for `orbit_plane` this lands at
4.5e-13 km on the state, with the true anomaly only good to ~1e-8 rad
because `acos(r̂ · ê)` is ill-conditioned at periapsis in *any* frame;
(b) `spody validate` on the rejected combinations (wrong central
body, wrong dynamics model, typo'd name); (c) offscreen, every
*ordered* pair of HF frames flipped through the combo with the cache
cleared, each landing within ~1e-13 of the reference rotation —
ordered, because composing old→ICRF→new is not symmetric and a
transpose slip only shows in one direction; (d) form → TOML →
`spody validate` → reload, checking the frame name survives; (e)
§6.1 bit-identity on `gps_g11_validation`. **Document:** manual
ch. 6 (schema table + a subsection on what the frame *is* and why it
differs from the neighbouring one), ch. 5 (swap-cache count),
README feature list, CHANGELOG.

### 5.15 Retuning a force model per integrator step

Some force models can trade evaluation cost against an accuracy they
can only judge from the current state. The adaptive harmonics degree
is the worked example; read it before adding a second one, because
the two rules below are the whole difficulty and neither is obvious.

**Rule 1 — decide once per step, never inside the RHS.** The
temptation is to put the decision in the force function, where the
state is right there. That is wrong. RKDP45 calls the RHS seven times
per step; if the model changes between those calls, the stages no
longer sample one vector field, the embedded 5(4) error estimate
reads the model jump as truncation error, and the controller shrinks
`h` fighting a discontinuity that is not in the dynamics. You lose
more than you gain, and it looks like an accuracy problem rather than
a design one.

The correct place is the stepping loop, immediately before
`spody_propagate_onestep` and after `h` has been clipped — both loops
in `sim_run.c` do exactly that for `spody_adapt_hgdegree`. Retries
inside `step_rkdp45` reuse the decision, which is what you want: a
retry only ever shrinks `h`, so a choice made for the larger `h`
stays valid.

*This does not need a callback in the integrator.* An earlier cut of
this added a `pre_step` function pointer to `IntegratorAllData`; it
was removed. Every stepping loop already sits one line above
`spody_propagate_onestep`, so it can just call the function, and the
integrator stays ignorant of force models. Add machinery only if a
loop appears that genuinely cannot.

**Rule 1b — tell the integrator when the field moved.** RKDP45
carries the derivative it evaluated at the end of one step into the
first stage of the next (first-same-as-last: six RHS calls per step
instead of seven). That derivative belongs to the field it was
evaluated on. If your retuning function changed the model, the
carried stage is stale by exactly the amount the model moved, and the
step would sample two fields — the very thing Rule 1 exists to
prevent. So a retuning function **returns whether it changed
anything**, and the loop feeds that to
`spody_integrator_invalidate_fsal`:

```c
if (adapt_hg
    && spody_adapt_hgdegree(w->integ.t, w->integ.y, w->integ.h, &w->ctx)) {
    spody_integrator_invalidate_fsal(&w->integ);
}
```

That is the whole cost: one extra RHS on the steps where the knob
actually moved (237 out of 3637 on an eccentric lunar orbit), none on
the others. Forgetting it does not crash and does not fail a test that
checks accuracy; it shows up as the knob-on run no longer matching the
knob-off run bit for bit, which is check (b) below.

**Second example — discontinuity stops.** The same loops host a hook
that changes no model, only where the steps fall. One core function,
`spody_next_force_discontinuity(ctx, integ)` (next to
`spody_adapt_hgdegree`), is called once per step, **right after it**,
and returns the first jump of the force model after the start of that
step:

- *known in advance* — the 3-hour UTC grid of the NRLMSISE-00 inputs
  (drag on with space weather): the next boundary after `integ->t`.
  The UTC -> ET inversion goes through `spody_et_to_mjd_utc`, so the
  stop falls exactly where `spody_space_weather_msis_inputs` switches
  bin, leap seconds included (the chain folds 23:59:60 into the next
  day);
- *located after the fact* — the shadow contacts of every SRP
  occulter: `spody_get_eclipse_residual` with threshold 1 (penumbra)
  and 0 (umbra) at the step ends, interior dense samples only when the
  residual is within reach of zero (grazing passes), Brent on
  `spody_dense_state_rv6`. A contact found is kept in
  `ctx->disc_contact` (per thread, reset to INFINITY by the loop before
  a run) because it is the stop of the steps that redo the interval.

In `sim_run.c`, `next_stop_or_undo` calls it after the step: a result
`<= integ.t` means the step crossed a contact, so the state goes back
to `(t_old, y_old)` with `spody_set_integrator_state` and the loop
`continue`s **before** events and grid samples see that step.
Otherwise the result is the stop for `clip_to_discontinuity`, which
lands `SPODY_DISC_STOP_EPS_S` (1 ms, `spody_const.h`, shared by core
and app) before it, crosses it with a step of at most 2 ms, then
invalidates FSAL (the carried derivative was evaluated before the
jump) and restores the step the controller had proposed. Contacts
within 2 eps of a step start belong to that crossing step and are not
reported, which is what keeps the crossing from being undone forever.

Gated by `integrator.discontinuity_stops` (default on for DOP853, off
for RKDP45, bit-identical) **and** by drag or SRP being on: without
them the loop never calls the function. A new piecewise input (a table
switched by date, a mode change) belongs in that function, not in a
new loop hook. *Symptom of breakage: a run that never ends (a crossing
undone again and again), or outputs that differ between 1 and N
threads (the contact kept in a shared struct).*

**Rule 2 — tunable state goes in the per-thread handle.** The
harmonics split is the template: `HarmonicGravityData` (coefficients)
is shared read-only across workers and lives in `SimulationShared`,
while `HarmonicGravity` (scratch rows) is per-thread and lives in the
worker struct. The truncation degree is therefore a field on
`HarmonicGravity`, not on the Data struct. Writing it into the shared
struct would compile, run, and silently give one worker's degree to
all the others — and it would *pass* a single-threaded test.
`cmd_maxhgdegree` does mutate the shared `hgd.N`; that is safe only
because it is standalone and single-threaded. Do not copy it into the
propagation path.

Make the sentinel "0 = behave as before" so an untouched handle is
bit-identical to not having the feature, and keep the knob's own
constants in `spody_const.h`.

**Verify:** (a) §6.1 bit-identity with the flag absent, on
`gps_g11_validation` *and* `batch_demo`; (b) the feature on vs off on
a run that actually exercises it — for a cost-only optimisation the
two outputs should match bit for bit, and if they don't, the
threshold is not where you think it is; (c) accepted/rejected step
counts unchanged, which is what proves the step controller is not
being perturbed; (d) **batch at 1 thread vs N threads, plus repeated
threaded runs**, because a race here is intermittent and invisible in
a single run; (e) one batch case against the same case run as a
single `propagate`. **Document:** manual ch. 6 (schema row + a
subsection on what the knob buys and what it costs), README feature
list, CHANGELOG.

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

  **Toolchain trap:** it is only a valid reference if it was produced
  by the *same compiler with the same options*. MSVC and GCC disagree
  by 1–3 ULP on the `_hpc` harmonics acceleration, because GCC
  vectorises the reduction and MSVC does not, and a different
  summation order is a different rounding; `SPODY_ARCH_AVX2` moves it
  again, by 1–4 ULP, for the same reason. Everyone develops and
  regresses on MSVC while `release.yml` ships GCC, so the two never
  meet — keep it that way. A stored run compared against a
  differently-built binary shows a µm-level trajectory difference that
  is the compiler, not the change you are testing. (The reference
  kernel `spody_get_hgaccbodyfixed` *is* bit-identical everywhere,
  which is what makes it the audit baseline.)
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
- **Per-thread tunable state never goes in a shared struct.** The
  adaptive harmonics degree lives in `HarmonicGravity` (per worker),
  not in `HarmonicGravityData` (shared read-only in
  `SimulationShared`). The same split applies to anything a worker
  writes while running. *Symptom of breakage: single-threaded runs
  and `thread_number = 1` batches are perfectly correct, and a
  multi-thread batch differs run to run — or worse, differs only
  under load, when the workers actually interleave.* The check that
  catches it is a repeated N-thread batch compared against the
  1-thread one, bit for bit; see §5.15.
- **A batch target never lives in `SimulationShared`.**
  `SimulationShared` is built once, from the scenario, before any
  case; a value a batch column or a Monte Carlo parameter can change
  must reach the force model from the *case* config, i.e. in
  `spody_build_worker`. The scalar `force_model.density_scale` broke
  this until 2026-10-06: its one-node k(t) table sat in
  `SimulationShared.ds_data`, so every case ran with the scenario's
  value. It is now `SimulationWorker.ds_one` (inline storage, built
  per case); only the `density_scale_file` node table, which no case
  can change, stays shared. *Symptom of breakage: the batch or Monte
  Carlo case differs from the nominal in its config and its saved
  `samples.uq.csv`, but its trajectory is bit-identical to the
  nominal.* **Verify** every new batch target with the §5.8 two-case
  CSV: case 2 must be bit-identical to a single run with the value
  written in the TOML. The same family: a resource opened only when
  the scenario needs it (the space weather table, for drag) is
  missing for a case that switches the feature on. For this reason
  the force switches (`force_model.srp`, `force_model.drag`) are
  **not** batch targets (removed 2026-10-06): do not add an on/off
  switch to `FIELD_TABLE`; a batch varies values inside the model the
  scenario chose.
- **Resolve the initial state to ICRF before applying a batch case.**
  `[initial_state]` can be Keplerian or Cartesian in any of three
  frames, but the propagator consumes exactly one thing: a
  central-inertial Cartesian pair. `spody batch` therefore calls
  `spody_resolve_initial_state_icrf` once, before the case loop, and
  only then lets `spody_apply_batch_case` add per-row offsets. Do not
  move the frame rotation back after the case application "because
  `spody_build_worker` does it anyway" — the offsets would be read as
  components in the declared frame, and the sum rotated afterwards.
  Equally, do not call the resolver twice: it is guarded by the
  `central_inertial` early return, but a second rotation applied to an
  already-resolved state would be silent. *Symptom of breakage: every
  case in a sweep displaced by an amount comparable to (for a lunar
  `orbit_plane` state, larger than) the offset it asked for, with no
  error — while `propagate` on the same TOML stays correct, which is
  what makes it hard to spot.*
- **A batch offset lives in the propagator's frame, and so must
  whatever produces it.** The corollary of the invariant above: the
  offsets are added AFTER the collapse, so they are ICRF components
  under `high_fidelity` and synodic ones under `cr3bp`. Anything
  GUI-side that manufactures offsets — today the RIC / LVLH rotation
  in `form/roundtrip.py` — has to build its basis from the state in
  that same frame, which is why `_cases_target_frame()` keys off
  `dynamics_model` and the derived file is named `_wrt_<target>.csv`.
  Two traps in the CR3BP half. The synodic block is
  **barycentre-centred**, so a local-vertical axis built from it
  points along the primary-primary line — on a close lunar orbit that
  is nearly perpendicular to the real local vertical, so the primary's
  offset must be removed first. And the synodic velocity needs no
  correction: the primaries are stationary in the rotating frame, so
  it already is the velocity relative to them. *Symptom of breakage:
  a debris sweep whose cases are all rotated coherently but into the
  wrong plane — the cloud has the right shape and the wrong
  orientation, and every case still propagates without complaint.*
- **Recurring events need dense output.** Eclipse / altitude
  crossings fire from the RK45 dense-output path; other integrators
  don't provide it and `spody_event_check` has no fallback.
  *Symptom: recurring events silently absent from the events file.*
- **Frame chains are verified against SOFA, never against
  themselves.** `spody_iau2006_polar_motion` shipped for months with
  `x_p` inverted, under a comment asserting it was bit-perfect against
  `erfa.pom00`. It was &mdash; against `pom00(-x_p, y_p, s')`, because
  the check had been fed an already-negated `x_p`. A self-consistent
  test cannot catch a sign that is wrong on both sides of the
  comparison, and `test_earth_orientation` never called SOFA at all, so
  it passed throughout. When touching any rotation, compare the
  **matrix** against `erfa`/SOFA over a spread of inputs that includes
  negative and zero values, and decompose the residual into spin
  (about the pole) and tilt (of the pole) &mdash; they have very
  different consequences, since a zonal field is invariant under spin.
  *Symptom of breakage: an angular error that looks large but moves the
  orbit little, or Earth-fixed outputs (impact lat/lon, ground tracks)
  offset by metres while the orbit looks fine.*
- **The adaptive integrators share one tolerance, relative to the
  step.** `spody_integrator_method` has `RK4` (fixed step), `RK45`
  (Dormand-Prince 5(4)) and `DOP853` (Dormand-Prince 8(5,3)); a new
  method enters the enum only with its step function, never as a stub.
  `rel_tol` is the only tolerance: per 3-component block the embedded
  error estimate (for DOP853 the combination
  err5^2 / sqrt(err5^2 + 0.01 err3^2)) is divided by the step's own
  change of that block (absolute when its square is below 0.1), GMAT's
  RSS-step control, so `rel_tol` is not comparable with another tool's
  `atol + rtol|y|`. Changing the norm moves every result (LRO
  included). Both adaptive methods are FSAL and fill `f_now`/`f_new`
  the same way, which is what lets `spody_dense_state_rv6` and the
  event refinement serve both; DOP853's own dense output is not wired
  in, so its grid samples come from the endpoint quintic. *Symptom of
  breakage: every output changes with no input change; a method in
  the enum that fails its first step; DOP853 grid samples that drift
  from the step-mode states.*
- **The integrator cost counters count attempts, not successes.**
  `n_rhs` in `IntegratorAllData` is incremented at the RHS call site,
  so it includes evaluations spent on trial steps that were later
  rejected; `n_accepted` and `n_rejected` split the outcomes. They are
  zeroed in `spody_setup_integrator` and never reset afterwards, so a
  caller reusing one workspace across several drive calls reads the
  *cumulative* total. *Symptom of misuse: per-segment costs that look
  monotonically increasing because the counters were never
  re-zeroed.*
- **The eclipse bracket clamp** (§5.13) is load-bearing, not a
  safety net: the inclusion-exclusion sum is truncated after the
  pairwise term, and the clamp into
  `1 − Σg_i ≤ lit ≤ min_i(1 − g_i)` is what keeps the answer inside
  a range that provably contains the truth. *Symptom: with three or
  more bodies over the Sun the lit fraction drifts above the value
  any single occulter alone would allow — physically impossible, and
  it feeds straight into the SRP acceleration.*
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
- **RK45 carries a derivative across steps (FSAL).** The step keeps
  `f(t, y)` from its own last stage and uses it as the next step's
  first stage, so it assumes the RHS is the same function of `(t, y)`
  at the start of a step as at the end of the previous one. Anything
  that breaks that between two `spody_propagate_onestep` calls — a
  retuned force model (§5.15), a spacecraft parameter edited mid-run,
  a state written into `integ->y` by hand instead of through
  `spody_set_integrator_state` — must call
  `spody_integrator_invalidate_fsal`. The adaptive harmonics degree
  already does, through the return value of `spody_adapt_hgdegree`.
  RK4 has no such stage; a future method opts in where the two
  buffers are allocated in `spody_setup_integrator`. *Symptom of
  breakage: a run with the knob on stops matching the run with it off
  bit for bit, while every accuracy check still passes — the carried
  stage is off by the size of the model change, far below any
  tolerance.*
- **A run window is checked against the ephemeris before it starts.**
  `spody_get_ephposition` has no range check of its own: an epoch
  outside the mapped records indexes past the record array, and the
  process dies with an access violation (or worse, reads garbage).
  `check_ephemeris_window` in `sim_setup.c` refuses the window right
  after the load and again per worker, because batch columns and
  calibrate windows move `et_start_s` / `duration_s`. Any new code
  path that queries the ephemeris outside `[et_start_s, et_start_s +
  duration_s)` has to extend or repeat that check. *Symptom of
  breakage: `spody.exe` exits with `0xC0000005` and an empty output
  file instead of an error message.*
- **Nothing rotates to or from ITRF outside the EOP table.** The
  Earth rotation providers (`spody_bf_rotation_earth`,
  `spody_teme2icrf_rotation`) return `void`: when
  `spody_interpolate_eop` fails they fall back to the identity and
  nobody hears about it. So the check lives with the callers, all on
  the one predicate `spody_eop_covers_mjd` (the same rule
  `spody_interpolate_eop` applies):
  - `check_eop_window` in `sim_setup.c` — base window after the EOP
    load (it also prints the "past the last measured record" warning),
    then per worker, like the ephemeris check;
  - every converter that rotates (`spody_sp3.c`, `spody_gps.c`,
    `spody_glonass.c`, `spody_gp.c`) — per epoch, before the rotation,
    failing the conversion.
  A new caller of either provider (a converter, an output frame, a
  diagnostic) has to test the predicate first. The warning boundary
  is `mjd_last_measured` (Bulletin B or a Bulletin A UT1 flagged
  `I`), **not** `mjd_last_observed`, which is Bulletin B only and lags
  real time by about a month. *Symptom of breakage: an Earth run past
  the end of `finals2000A.all` finishes with exit 0 and drifts by
  kilometres within hours of the boundary (47.9 km after 8 days in
  LEO); a converted reference is off by the whole ICRF–ITRF angle.*
- **A `.spody` file is validated before any header field is used.**
  `ephemeris_map_file` (C) and `Ephemeris._parse_header`
  (`python/spopy/ephemeris.py`) apply the same rules, in the same
  order, with the same messages: the file is at least one header
  long; `seconds_per_record > 0`; every body slot in use fits its
  3 components x sets inside the record's coefficients;
  `bytes_per_record == 24 + 8 * number_coefficients_per_record`
  (exactly what the converter writes); the payload is a non-zero
  whole number of records; consecutive records join exactly
  (`start[i] == end[i-1]`: the record lookup is `(et - start) /
  seconds_per_record`, so a gap hands out a record from the wrong
  century and indexes past the array after it). A file that fails
  is refused, never
  loaded with a reduced coverage: a download cut mid-record is not a
  "subset file" (the converter always writes whole records). A new
  header field used for indexing gets its check in both places. The
  writer side, `spody_createfile_MappedEphemerisData`, keeps the
  same promise: any missing, damaged or unwritable ASCII chunk stops
  the conversion (never skipped, never read as end of file), and the
  output goes to `de<NNN>.spody.tmp`, moved over the destination
  only on success (`MoveFileExA` with `MOVEFILE_REPLACE_EXISTING` on
  Windows, `rename` elsewhere) — a failed conversion must leave the
  previous file untouched.
  *Symptom of breakage: a damaged file crashes the engine
  (`0xC0000094`, integer division by zero) or loads with its
  coverage silently shrunk, while the GUI refuses it.*
- **UT1 is interpolated on the TAI scale, not the UTC scale.**
  `UT1 - UTC` in `finals2000A.all` jumps by +1 s across every leap
  second; `UT1 - TAI` does not. `spody_interpolate_eop` (and its twin
  `MappedEOP.interpolate` in `python/spopy/eop.py`) subtracts the
  jump `TAI-UTC(hi) - TAI-UTC(lo)` from the upper node before the
  linear step. On days without a leap the jump is exactly `0.0`, so
  the result is bit-identical to a plain lerp; keep it that way (no
  algebraic rewrite such as "subtract TAI-UTC from both nodes, add it
  back", which changes the last bit everywhere). Leap values come
  only from `spody_tai_minus_utc` / `spopy.time.tai_minus_utc` —
  never a table here. Change the C and the Python together and check
  them bit-for-bit across the leap days. *Symptom of breakage: on a
  leap day UT1 is wrong by up to 1 s (0.5 km of frame rotation at
  LEO radius, 1.9 km at GPS radius by midnight); a 72-hour LEO run
  starting 2016-12-30 moves by about 5 m.*
- **Two body-fixed frames, one set of axes.** `central_body_fixed`
  reads the velocity as inertial on the body-fixed axes (pure rotation;
  the GUI's BF plots and frame flip share this convention);
  `central_body_fixed_rotating` reads it as relative to the rotating
  body and adds omega x r in `initial_state_to_icrf`. omega comes from
  `spody_bf_angular_velocity_icrf`, the ONE place an angular velocity
  is made: for every body, the Earth included, the rotation
  R(t+h) R(t-h)^T of the body-fixed provider (h =
  SPODY_BF_OMEGA_FD_STEP_S) read off in axis-angle form. The GNSS
  converters call it too (same `(v + a) - b` association, so an ECEF
  input and a converted state agree); twin
  `spopy.bf_angular_velocity_icrf` used by the form. Do not reintroduce
  a nominal spin about the body-fixed z axis: the Earth's axis is the
  CIP, tilted by polar motion, and EARTH_ROT_RATE_RADPS about ITRS z
  was ~1 mm/s off in every ECEF velocity (LAGEOS-2: 373 m instead of
  13.5 m at 60 h; fixed in spody-core 5b0a6c0). Do not go back to the
  difference quotient (R(t+h) - R(t-h))/(2h) R^T either: it returns
  omega sin(omega h)/(omega h), 6 mm/s short at GNSS radius for the
  Earth at h = 60 s. EARTH_ROT_RATE_RADPS stays for what prescribes it
  (the GPS broadcast orbit algorithm) and for the drag's co-rotating
  atmosphere. Never let one
  frame silently take the other's meaning, and keep Keplerian input
  off the rotating frame. *Symptom of breakage: an ECEF state starts
  on the wrong orbit, off by |omega x r| (1.9 km/s at GPS, 0.5 km/s in
  LEO).*
- **Dense output reads the FSAL derivatives, and their names are
  swapped after a step.** The fixed output grid and the event
  localisation both call `spody_dense_state_rv6`: a quintic Hermite
  on r, v and the accelerations at the two ends of the last accepted
  step. Those accelerations are components 3..5 of the RK45 FSAL
  buffers, already unscaled by h -- do not rebuild them as `k/h`, one
  rounding more for nothing. After acceptance the buffers are
  swapped: `f_now` is f at the END of the step and `f_new` f at its
  START (the names describe the next step). The dense state is valid
  only until the next step or state reset, and assumes the (r, v)
  layout with f = (v, a); a model with another state shape needs its
  own. *Symptom of breakage: grid velocities an order worse than the
  step nodes (mm/s at `rel_tol` 1e-9 in LEO), or event times off by
  hundreds of microseconds.*
- **The XYS nodes live on a fixed grid, and reuse never moves
  them.** `spody_iau2006_xys_interp` evaluates the IAU 2006 series
  (~70 us a call) only at multiples of SPODY_XYS_NODE_S from J2000 and
  interpolates a 4-node cubic between them. When the stencil slides by
  1..3 nodes it keeps the nodes still in the stencil and evaluates only
  the new ones; each node is computed at its own grid instant, so the
  result is a function of t alone, bit-identical to a cold recompute.
  Never anchor the nodes to the current time or to the run start: the
  force would then depend on the step sequence. The cache is per
  thread (`MappedIAU2006` in each worker) and restarts with every
  batch case. *Symptom of breakage: two runs of the same case, or the
  same case alone and in a batch, no longer bit-identical; or a series
  evaluation count far from one per hour crossed plus four at start.*
- **No converter output without its log.** Each `spody convert`
  branch in `main.c` opens `<output>.log` with `convlog_begin` before
  reading anything, logs every input and auxiliary file through
  `convlog_file` (size + SHA-256, `app_sha256.c`), and returns through
  `convlog_end`, which hashes the output or marks it not valid. A
  converter whose binary has a relative time column calls
  `spody_log_time_anchor` on success. A new converter follows the
  same three calls. *Symptom of breakage: an output with no `.log`
  beside it, or a log whose output checksum does not match the file.*
- **Text epochs never become a whole JD, and a file's time system is
  read, not assumed.** Every calendar epoch read from a file (SP3,
  RINEX TOC, OEM) goes through `spody_greg_to_sec_j2000`, the exact
  day difference from J2000: a JD in one double resolves 40 us (a few
  cm at GNSS speed). SP3 epochs are converted from the scale the
  header's `%c` line declares; an unknown scale refuses the file, it
  is never defaulted to GPS (UTC read as GPS is 18 s, ~100 km for
  LAGEOS). New readers of timed data follow the same two rules.
  *Symptom of breakage: converted labels scattered by tens of us
  around the nominal grid; an SLR reference 18 s off its propagation.*
- **Harmonics files are complete or refused.** The `.tab` loader stops
  at the first row past the requested degree and then checks that
  every (n, m) with 2 <= n <= N was seen exactly once. Do not relax it
  into "missing means zero": a file out of order would load with
  silent holes. *Symptom of breakage: a field that loads but gives a
  different acceleration from its source file.*
- **The central GM comes with the gravity file.** With a harmonics
  file loaded, `sim_setup.c` sets `ctx.mu_central` to the file's GM,
  not to the registry `mu`: normalized coefficients hold only with the
  GM they were estimated with. The registry value (`MOON_MU`,
  `EARTH_MU`) serves the no-harmonics case, the third bodies, CR3BP,
  Keplerian initial states and the GUI's elements. Checklist when you
  add a field or touch a GM:
  1. Read the file's GM from its first line and compare it with the
     registry constant; say the difference in the CHANGELOG.
  2. Do not "fix" a mismatch by editing the registry constant to the
     file's value: the next file would break it again.
  3. The `gravity` line of the setup log prints the GM in use to 17
     digits &mdash; that is where to look.
  *Symptom of breakage: a lunar orbit drifting along-track linearly
  (9.4e-8 of GM = 160 m in 6 days on LRO) with no other change.*
- **The tide model is the body's, the tide system is the file's.**
  Love numbers, degrees and tide-raising bodies sit on the central
  body's registry row (`central_body.c`, `SpodySolidTides`);
  `force_model.solid_tides` only says where the gravity file keeps the
  permanent tide. `sim_setup.c` completes the template with the file's
  GM and radius (the corrections are normalized with them) and, for
  `"zero_tide"`, `dc20_perm = tide_a0h0 * k20`. Checklist:
  1. A new Love number or body goes in `spody_const.h` + the registry
     row, never in `spody_forcemodels.c`.
  2. Read the tide system from the gravity file's header or label
     before choosing the value; if the file does not say, measure it
     (the Moon: fits of the NASA LRO orbit, see the CHANGELOG) and write it in the
     manual.
  3. The force is off unless the key is present: any change must keep
     runs without it byte-identical.
  4. The tide never reaches past the field: `sim_setup.c` caps
     `max_degree` at `harmonics_degree` and zeroes `kplus` below 4
     (the static ceiling, not the adaptive `N_eval`). A body with a
     higher-degree tide inherits the rule for free.
  5. Validate a change against an independent computation of the
     potential (not against the same recursion) and against Orekit
     (Earth) / Tudat (Moon).
  *Symptom of breakage: a zero-tide file run as "zero_tide" no longer
  matching the tide-free file run as "tide_free" (they agree to
  0.3 mm in 7 days on the ISS).*
- **UT1 dates travel in two parts.** `spody_iau2006_era` and
  `spody_gmst1982` take `(jd1, jd2)` like SOFA; the Earth chain passes
  `(JD_MJD_EPOCH, MJD_UT1)` and the spopy twin `erfa.era00(MJD_OFFSET,
  mjd_ut1)`. Never add the MJD into one JD first: a double of order
  2.4e6 days resolves 40 us of UT1, a staircase of up to 3e-9 rad in
  the ERA (7 cm at GNSS radius). SGP4 alone passes `(JD, 0.0)`, on
  purpose: its reference forms the epoch JD in one double and the
  verification vectors are its output. *Symptom of breakage: GNSS
  conversions move by a few cm with no input change; the GUI rotation
  and the engine disagree by more than a few mm at GNSS radius.*
- **The GUI's Earth rotation is the engine's chain.**
  `spopy.icrf_to_itrs(_many)` mirrors `spody_bf_rotation_earth` step
  for step: EOP interpolated linearly (UT1-TAI across leap seconds)
  with dX/dY added to X/Y, X/Y/s on the SPODY_XYS_NODE_S grid with
  the same Lagrange cubic, two-part UT1 for the ERA, SOFA composition.
  The only difference left is the series source (IERS tables in C,
  `erfa.xys06a` in Python): 2.5 mm rms at GNSS radius against `spody
  convert sp3`. Dropping a step (dX/dY is 1-2 cm at the surface) makes
  the body-fixed states the form writes disagree with how the engine
  reads them. *Symptom of breakage: a body-fixed initial state written
  by the form lands cm away from the state it came from.*
- **Every external table a run loads appears in the data sources
  block.** `print_data_sources` in `sim_setup.c`, called once at the
  end of `spody_build_shared`, lists each loaded table (path, size in
  bytes, modification date, coverage, and where the run window falls:
  Bulletin B / measured / prediction for EOP, observed / forecast for
  space weather). A new data file loaded by `spody_build_shared` gets
  its line there, in the same shape; a new horizon (a measured vs
  predicted boundary) gets both a *run window* word in the block and
  a warning in its window check with the `warn_*` flag set on the
  base window only. *Symptom of breakage: a log that does not say
  which download of a table produced the run, or a run resting on
  forecast data with no word about it.*
- **Every rule on a run's configuration lives in one per-case gate.**
  A batch case (base + overrides + deltas) or a calibrate arc is not
  the configuration `spody_validate_input` saw at load. So
  `spody_check_case` (`sim_setup.c`) re-runs the whole validator on
  the final values, then the window checks against every table
  `SimulationShared` opened: `check_ephemeris_window`,
  `check_eop_window`, `check_space_weather_window`. It is called
  (a) by `spody_build_worker`, first thing, so no worker is ever built
  on an unchecked config, and (b) by `cmd_batch` on every case before
  the parallel loop, so skipped cases are listed up front
  (`case_failed[i] == 2`). Checklist for a new rule:
  - depends only on the TOML text → `spody_validate_input`; it then
    runs at load *and* per case for free;
  - depends on a data table → a `check_*_window` next to the others,
    called from `spody_build_shared` (base window) and from
    `spody_check_case`;
  - never add a per-case check inside the `cmd_batch` loop body.
  Case ids are checked where the CSV is read (`load_cases_csv`):
  non-empty, unique, `[A-Za-z0-9_.-]` — they are file-name parts.
  *Symptom of breakage: a batch case runs with a value the single-run
  validator would refuse (mass ≤ 0 → A/m = 0 → no drag, no SRP), or
  two cases write the same files.*
- **Drag needs the DAILY space weather, not the whole file.**
  CelesTrak's `SW-All.csv` is daily up to ~45 days past the download
  and monthly for ~15 years after that, with the 3-hour Ap blank.
  `spody_space_weather_msis_inputs` indexes days as `mjd_first + i`,
  so it works only up to `MappedSpaceWeatherData.mjd_last_daily`;
  past it the density callback fails and the drag force is zero.
  `check_space_weather_window` therefore ends at `mjd_last_daily + 1`,
  not at `mjd_last_predicted`. *Symptom of breakage: a drag run past
  the daily horizon is bit-identical to the same run with drag off
  (measured: 0.0 m against 7.6 km per day for the ISS).*
- **Nothing later than the trigger goes into the output.** Both
  stepping loops in `sim_run.c` check the events *before* they write
  anything for the step: a stop-class trigger caps the fixed-grid
  drain strictly below `t_trigger`, and in step mode replaces the
  step-end record. The last record of a run that ended on an event is
  that event's state at its own time, off the grid, and time is
  strictly increasing in every trajectory and accelerations file. A
  new emit path (another writer, another output mode) has to keep
  that order. *Symptom of breakage: a sample inside the body just
  before the impact record, or a file whose last two records run
  backwards in time — invisible to every accuracy check, visible to
  anyone who sorts by `t`.*
- **Wire formats are append-only** (§1.2): readers in the wild parse
  old files. *Symptom: `spody_io` exceptions on historical runs.*
- **Every dispersed quantity has its own random substream.** A Monte
  Carlo draw is `spody_random` word k of `(seed, case, substream)`:
  substream 0 = the initial state, a parameter = FNV-1a 64 of its
  target path. Two quantities on the same substream would receive the
  same normal deviates — perfectly correlated, silently.
  `spody_validate_uq_input` refuses a collision. Checklist for a new
  dispersed quantity (a new `[montecarlo.*]` family, a new batch
  target): give it a substream id that cannot equal 0 or any target
  hash (a distinct, documented name hashed by
  `spody_random_substream_id`), and extend the collision check.
  Process noise lives in its own stream domain
  (`SPODY_RANDOM_DOMAIN_PROCESS_NOISE`, counter word 2 = 1), so its
  small fixed substreams (0-2 acceleration, 3 density, 4-6 / 7-9 the
  cos / sin once-per-revolution coefficients) cannot meet a
  domain-0 draw by construction; a new process-noise quantity takes
  the next free number there and documents it next to
  `pn_substream_density`.
  *Symptom: two parameters' samples columns move together
  (correlation 1 in the samples CSV).*
- **Monte Carlo statistics are folded in case order.** Threads only
  propagate; `stats_add_case` runs single-threaded on 1, 2, 3, ... per
  chunk, so every output is bit-identical for any `thread_number` and
  chunk size. Never accumulate inside the parallel loop (floating
  sums are not associative). The SPDYEVTB file is the one exception:
  its records follow thread completion, compare it sorted by
  `(case_idx, t, kind)`. *Symptom: moments differ in the last bits
  between 1 and 8 threads.*
- **A Monte Carlo case is applied exactly like a batch case.**
  `case_config` builds a one-case `BatchConfig` and calls
  `spody_apply_batch_case` (state offsets as delta columns,
  parameters as overrides). Do not assign fields by hand: the
  samples file rerun with `spody batch` must reproduce every case bit
  for bit, and that only holds while both paths are the same function.
  *Symptom: the rerun check in the local tests finds a case that
  differs.*
- **Only an impact ends a Monte Carlo case.** `strip_scenario` removes
  every scenario event except the always-on impact (and every output
  file) before case 0 runs, so the nominal and the cases span the same
  window and the statistics are not cut by a scenario stop event. A
  case counts at the nominal epochs while its record times equal the
  nominal's (`stats_add_case`); its impact record, off the grid, ends
  it. *Symptom: n(t) drops with no impact in the events file.*

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
