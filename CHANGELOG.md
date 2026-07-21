# Changelog

All notable changes to SpOdy are listed here. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
match the git tags published on `github.com/ValeEng/spody/releases`.

## Unreleased

### Added

- **Exact ephemeris velocities and full state vectors.** spody-core
  gains two public queries next to `spody_get_ephposition`:
  `spody_get_ephvelocity` (ICRF km/s) and `spody_get_ephstate`
  (`[x, y, z, vx, vy, vz]`, km and km/s). Rates come from the
  analytic derivative of the DE440 position Chebyshev series
  (second-kind Clenshaw recurrence rescaled by the granule width) —
  exact by construction, no finite differences, no new data files;
  the position half is bit-identical to the existing query, and the
  per-handle cache keeps a separately-validated velocity slot so
  mixed position/state call patterns never see stale rates.
  `spopy.Ephemeris` mirrors the API with `velocity()` / `state()`
  in zero-ULP lockstep with the C side. Validated against SPICE
  (`spkezr` on `de440s.bsp`): max 1.2e-7 km / 1.4e-14 km/s over
  3200 states spanning 1900&ndash;2100. Scripts that estimated body
  velocities with 60 s finite differences (~4 &micro;m/s error on
  the Moon) can now query them exactly.

- **Stop button on the Run form.** A red **Stop** next to the green
  **RUN** kills the engine process in flight (same action as
  **Run &gt; Stop** / <kbd>Ctrl</kbd>+<kbd>.</kbd>, which stays);
  RUN is now disabled while a run is active instead of erroring in
  the console. On Windows the stop is an immediate kill &mdash;
  previously the GUI froze for a 2 s graceful-close attempt the
  console engine could never honour.

### Fixed

- **Re-run tab labelled altitude crossings as `kind=2`.** The
  "last event" column mapped only IMPACT and ECLIPSE, so an
  `ALT_CROSSING` (added later) fell through to the raw enum int. It
  now reads `ALT_CROSSING`, matching the Analysis events table.

### Changed

- **Re-run "Survivors" preset now means "did not impact".** It
  previously selected only cases with *zero* events, so a case that
  logged altitude crossings or eclipses but never crashed was
  excluded from both Survivors and Crashed. Survivors is now the
  exact complement of Crashed (IMPACT).

- **Engine module split: tabulated-data primitives consolidated in
  `spody_interp`.** `spody_bracket_index` and `spody_interp_linear`
  moved out of `spody_math` (verbatim, no behaviour change), so all
  interpolation — bracketing lookup, linear nodes, cubic Hermite
  dense output, and the future SPK Type 9/13 interpolants — lives
  in one module; `spody_math` stays algebra/geometry. Only affects
  code including the engine headers directly.

- **From CR3BP... converter uses exact ephemeris rates.** The
  pulsating-frame transform in the `[initial_state]` converter dialog
  derived the primary-primary relative velocity from a &plusmn;60 s
  central difference of ephemeris positions; it now queries
  `spopy.Ephemeris.state()` directly (~5 &micro;m/s correction on the
  Earth&ndash;Moon rate, which feeds l&#775;, the frame axes and the
  angular-rate term of the converted velocity).

### Fixed

- **&le;1 ULP Earth-position divergence in spopy.** The Earth branch
  of the EMB&harr;Earth/Moon split in `spopy.ephemeris` divided by
  `1+EMRAT` where spody-core multiplies by the rounded reciprocal;
  the operation order now matches the C engine exactly (sub-mm
  effect, but the bit-identity sweep demands zero-ULP).

## v0.3.1-beta &mdash; 2026-07-14

### Added

- **From CR3BP... state converter in the Run form.** A button next
  to the `[initial_state]` frame combo (high-fidelity only) opens a
  popup that converts a CR3BP synodic-rotating state &mdash; the JPL
  periodic-orbit catalog convention; dimensional or nondimensional
  with its characteristic length L, centered on the barycenter or
  either primary of a curated pair &mdash; into the central-body
  ICRF cartesian state at `et_start_s`, and inserts it into the form
  (frame/kind snap to `central_inertial`/`cartesian`, swap cache
  re-seeded). The mapping is the instantaneous pulsating-frame
  transform on the form's `[ephemeris]` file: actual primary
  separation, axes and angular rate (plus radial pulsation) at the
  epoch, so both primaries land exactly on their DE440 states.
  Pure in-process spopy math &mdash; no engine call; the dialog
  keeps its fields across reopens so testing several points along
  one catalog orbit is paste &rarr; Convert &rarr; Insert. Manual
  ch. 5 (dialog) + ch. 10 (pulsating frame convention).

- **spoviz in-scene playback widgets + API reference.**
  `spoviz.widgets` adds an opt-in, pure-VTK UI layer for standalone
  viewers (no Qt in the process): `PlaybackBar` (play/pause, speed
  cycling, click-to-jump timeline slider, epoch readout through a
  caller-supplied formatter) and `OptionsPanel` (menu button
  dropping down declarative checkbox toggles). Button icons are
  rendered at runtime with numpy — no font glyphs, no image assets.
  The spody GUI keeps its Qt animation bar and Scene-options dialog
  and never instantiates these. `python/spoviz/README.md` documents
  the full library API (Scene3D, decoration, bodies, textures,
  widgets, Qt host) with four end-to-end examples.

- **Events timeline (density) plot.** A companion to the marker
  *Events timeline* for large files: the same y-rows (IMPACT, ECLIPSE,
  one per crossed altitude) rendered as a time-binned count heatmap
  (`np.histogram` per row) instead of individual markers. The marker
  timeline is unchanged &mdash; on a million-event file its dense smear
  still reads as an at-a-glance pattern; the density view stays fast
  and legible where the scatter turns solid.

### Changed

- **The 3D engine is now `spoviz`, an in-repo visualization
  library.** `python/spoviz/` owns the whole time-dynamic 3D stack,
  Cesium-style: `Scene3D` (pure VTK + numpy scene engine &mdash; layered
  multi-frustum renderers, textured bodies, animated trajectories /
  frame triads / direction arrows, day/night sun light, star-map
  skybox, picking, camera), `spoviz.decoration` (ephemeris-driven
  third bodies, sun illumination and animated body-fixed frame,
  taking explicit callables/tables instead of GUI context),
  `spoviz.bodies` (NAIF/colour/sizing catalog) and `spoviz.textures`
  (equirectangular meridian-roll + ICRF skybox fixups). PySide6
  appears in exactly one module (`spoviz.qt.SceneWidget`), so the
  scene engine renders on an offscreen `vtkRenderWindow` with no Qt
  in the process &mdash; the enabler for scripted/notebook renders and a
  future bulk PNG export. The GUI is unchanged:
  `spody_gui/vtk_canvas.py` is now a compatibility shim (`VtkCanvas`
  subclasses `SceneWidget`) and `analysis/scene3d.py` keeps its
  historical signatures as glue that resolves run folders, texture
  assets and `spody_const.h` values before delegating to the
  library.
- **Altitude-band analysis is vectorised and cached.** The per-band
  occupancy reconstruction (Info tab, the four band plots, the CSV
  exports) no longer loops over the crossing records in Python: one
  `lexsort` groups every crossing by (object, time) and band
  membership / entries / dwell / population come from `np.diff` /
  `np.bincount` / `cumsum`. Results are bit-identical to the previous
  implementation; it runs ~6x faster (a 2-million-event file drops from
  ~6.2 s to ~1.1 s) and the only remaining loop is over the handful of
  bands. A content-keyed cache shared by the Info tab, the plots and
  the exports computes one reconstruction per loaded file instead of
  re-running it on every Info-tab switch / plot click (subsequent
  touches are effectively free), so large events files no longer freeze
  the Analysis tab.
- **Every events plot picks a readable time unit** (`s` / `min` / `h` /
  `days`) from the plotted span, so days-long batch runs stop labelling
  their axes with raw six-digit second counts.

### Fixed

- **License metadata in `python/pyproject.toml`** wrongly declared
  MIT; it now says Apache-2.0, matching the repository `LICENSE` and
  the per-file headers. Metadata-only &mdash; no code change.

## v0.3.0-beta &mdash; 2026-07-07

### Added

- **Altitude-band occupancy analysis (Analysis tab).** The
  `[[events.altitude_crossing]]` thresholds are now read as ordered
  altitude bands (0 < h_a < h_b < ...) and the Info tab reports, per
  band, the entries, time in band, per-visit duration and &mdash; in
  batch &mdash; population min/avg/max and objects visiting,
  reconstructed from the crossing records alone (direction from the
  radial velocity at each trigger; the per-object window truncates at
  impact or a stop-class crossing). Four dedicated plots join the
  events tree under an **Altitude bands** folder: *Time per band*
  (bars), *Band occupancy timeline* (single-object Gantt), *Band
  population over time* (batch stacked step-area of how many objects
  sit in each band) and *Per-case time in band* (batch heatmap). The
  **Event timeline** plot gains one row per crossed altitude (labelled
  with the altitude instead of the raw enum), ascending/descending
  split by marker. `spody_io` exposes `EVENT_KIND_ALT_CROSSING` and the
  events table labels the kind (previously shown as the raw `2`).

- **Reorganised CSV export (Plot options).** The single *Export CSV*
  button becomes an **Export CSV box**: a radio list of export types
  plus one *Export* button acting on the selected one. Each type greys
  out by data availability, so the user sees which exports exist and
  why one is unavailable. Three types today: **Plot lines (as drawn)**
  (every `Line2D` on the figure, as before), **Altitude bands (per
  batch element)** (one row per case, ascending, a `time / entries`
  pair per band &mdash; entries count crossings *into* a band only,
  never exits) and **Impact points (lat/lon + time of flight)** (one
  row per IMPACT: case id, body-fixed latitude / longitude and time of
  flight; enabled when the file has an impact on a body with a
  body-fixed frame).

- **Unit combo on `output.interval_s`.** The fixed-mode sampling
  interval in the `[output]` form now carries the same
  `s | min | h | days` unit combo as `simulation.duration_s`, so a
  daily or weekly ephemeris cadence no longer has to be typed as a raw
  second count. The TOML still stores SI seconds; the combo only
  changes the displayed / typed unit (auto-picked on load), so the
  engine and all readers are unchanged.

- **Density calibration in the GUI + bundled ISS bench.** The
  `[force_model]` form gains the `density_scale` /
  `density_scale_file` rows (Earth-only, dropped from the emitted
  TOML when drag is off) and a **Calibrate...** button next to the
  file row: a minimal dialog (reference `.bin` + fit window in
  hours) launches `spody calibrate` through the shared runner, the
  button flips to a disabled *Calibrating...*, the per-window
  report streams live into the Run-tab console (Stop works), and on
  success the emitted `k_nodes.csv` path is auto-filled into the
  form (clearing the constant key to honour the XOR). New bundled
  example `examples/iss_drag_calibration/`: the NASA/JSC ISS OEM of
  2024-07-03 (bundled &mdash; historical OEMs are not
  re-downloadable) with its converted 15-day reference and a
  ready-to-run TOML using the OEM's own mass / drag area / Cd;
  the full 15-day calibration fits 15 nodes (k sliding 0.79
  &rarr; 0.52 across the JSC forecast horizon, in-track rms
  2.5&ndash;3 km &rarr; 8&ndash;40 m per window) in ~3 minutes.

- **`spody calibrate` subcommand.** Fits the time-varying drag
  density-scale `k(t)` against a full-state reference trajectory,
  engine-side: the reference span is cut into sliding windows
  (`--window <hours>`, default 24), each window re-anchors the
  initial state on the reference and propagates a drag-off/drag-on
  arc pair, both are resampled onto the reference epochs (cubic
  Hermite) and the in-track residuals give the closed-form
  least-squares `k` per window. Emits `k_nodes.csv` in exactly the
  `density_scale_file` format plus a per-window report (k, 1-sigma,
  raw vs post-fit in-track rms) and a pooled constant-`k`
  equivalent. Degenerate windows (drag signal < 1 mm rms, or a
  non-positive fitted `k` from a manoeuvre) are skipped with a
  warning. Closed-loop check on a 3-day ISS arc vs the NASA/JSC
  OEM: in-track rms 8.35 km (`k = 1`) &rarr; 0.46 km with the
  fitted 3-node table. Manual chapter 12 documents the command,
  chapter 11 the method.

- **`spody convert oem`.** CCSDS OEM text (e.g. the NASA/JSC public
  ISS ephemerides) &rarr; `SPDYOUT_` full-state reference binary.
  Accepts `REF_FRAME` ICRF/EME2000/J2000 (SPICE convention: no
  rotation, no EOP needed) and `TIME_SYSTEM` UTC/TDB; skips
  comments and covariance blocks; multi-file concatenation with
  first-file-wins overlap deduplication. Epochs are built from the
  midnight JD so the text field's full resolution survives (a
  full-magnitude JD would quantise at ~40 &micro;s &asymp; 30 cm
  along a LEO track). Cross-checked bit-for-bit (states) and to
  0.0 s (time axis) against an independent Python parse of the ISS
  files.

- **Density calibration factor for drag** (`force_model.density_scale`
  / `density_scale_file`). The drag force can now apply a
  multiplicative correction to the NRLMSISE-00 density: a constant
  factor (`density_scale = 1.15`, batch-targetable) or a
  time-varying piecewise-linear node table (`density_scale_file`,
  plain-text `mjd,k` rows, clamped outside the node span with a
  console warning on partial run-window coverage). Rationale and
  numbers in the manual's *Drag validation and ballistic
  calibration* section (chapter 11): empirical thermosphere models
  run 20&ndash;40% hot at 400&ndash;500 km around solar maximum, and
  the calibrated factor belongs in a dedicated key rather than in a
  misdeclared `Cd`. Both keys are drag-only and mutually exclusive;
  the default (1.0 / absent) is bit-identical to previous behaviour.
  Engine side: `MappedDensityScale` loader/evaluator in
  spody-core's atmosphere module plus generic `spody_bracket_index`
  / `spody_interp_linear` primitives in `spody_math`.

- **Altitude-crossing events.** New
  `[[events.altitude_crossing]]` array-of-tables registers any
  number of altitude triggers, each measured against the central
  body or any third body (HF) / one of the two primaries (CR3BP).
  Fires on **every sign change** of `|r_sat - r_body| -
  body_radius - altitude_km`, so the same band logs both the
  ascending and the descending crossing of one orbit. Per-event
  refinement is on by default (Brent + dense output,
  sub-microsecond localisation inside the accepted step) and can
  be opted out with `refined = false` for catalog-style runs with
  many bands. Action is `log` by default and accepts `stop` /
  `log_and_stop`. Direction is recoverable post-hoc from the
  radial velocity at trigger. The form gains a collapsible
  `Enable altitude crossings` panel under `[events]` (parallel
  to the existing eclipse toggle) with a body / altitude /
  action / refined table and Add / Remove buttons; body combos
  auto-track the model's valid bodies.

- **Sun illumination in the 3D scene (day/night terminator).** New
  Scene-options checkbox (on by default, HF scenes only): the body
  spheres &mdash; central body and textured third-body markers
  &mdash; are lit by a directional light aimed from the Sun's true
  direction (spopy ephemeris, sampled on the trajectory time grid
  and re-aimed on every animation tick) instead of the camera
  headlight. The night side stays barely visible (low ambient, hard
  day/night contrast), a thin orange ring on the central body marks
  the terminator explicitly, the Moon marker shows its actual phase
  in Earth-centred scenes, and the terminator sweeps the rotating
  surface during playback.
  Trajectories, arrows, triads, marker pucks and the Sun's own
  marker keep their flat emissive colours. Hidden for CR3BP scenes
  (no epoch in the synodic frame). Also fixes a latent import bug
  from the July analysis-package split (`from . import assets,
  paths` in scene3d resolving inside the subpackage) that broke any
  HF 3D scene with third-body markers enabled.

- **NRLMSISE-00 atmosphere model in the engine (native port).**
  spody-core gains `spody_nrlmsise00.{h,c}`: a double-precision,
  fully re-entrant C translation of the official NRL Fortran
  distribution (Picone et al. 2002, a public-domain U.S. Government
  work) &mdash; `gtd7` plus the `gtd7d` "effective total mass
  density for drag" variant, daily-Ap and 3-hour-Ap-history modes,
  coefficient tables generated verbatim from the original `BLOCK
  DATA`. Validated against the reference test driver bundled with
  the NRL distribution: all 17 published cases match to the printed
  7 significant digits (max relative deviation 4.5e-7),
  plus a 20,000-case random sweep against an independent evaluation
  of the same Fortran with no deviation above single-precision
  noise.

- **Atmospheric drag (Earth).** New `force_model.drag` toggle
  (optional, default `false` &mdash; existing TOMLs are untouched)
  integrates cannonball drag with NRLMSISE-00 density: geodetic
  WGS-84 sub-satellite point, observed previous-day F10.7 + 81-day
  centered average + the 7-element 3-hour Ap history (storm-time
  mode) from a CelesTrak `SW-All.csv` given via the new
  `force_model.space_weather_file` key, air co-rotation in the
  relative velocity. Ballistic input via the new
  `[spacecraft.drag]` sub-block (`area_m2` XOR `am_drag`, plus
  `Cd`) or the `debris.am_drag`/`debris.Cd` pair; all four are
  batch override targets, as is the toggle. The engine refuses a
  run whose window falls outside the space-weather table (the Ap
  history needs 3 days of lead-in) and points at the CelesTrak
  update URL. The form gains the matching Earth-only rows, the
  SRP-style drag sub-blocks and the batch targets. Registry-driven
  under the hood: a future Mars atmosphere is one registry row,
  not a rewrite. Verified end-to-end on a 2-day ISS arc against
  the NASA/JSC public OEM (radial/cross residuals at tens of
  meters; the in-track drag signal has the right sign and
  magnitude, with the expected offset from JSC's forecast-based
  solar activity vs the observed July 2026 F10.7 spike + ap 76
  storm), and the density the engine applies matches an
  independent pymsis reconstruction to 1e-5 across the arc.
  GPS-G11 7-day regression (drag off) bit-identical.
  Setup-wizard integration: `SW-All.csv` is a new wizard card
  (*Space weather* group, shows the last-observed date after
  download) and joins `finals2000A.all` in the startup
  freshness probe &mdash; CelesTrak regenerates the table daily,
  so the probe offers a one-click refresh whenever the server
  copy is newer.

- **Manual: drag validation and ballistic calibration**
  (chapter 11). New section documenting how to validate a
  drag-enabled propagation against public orbit products
  (NASA/JSC ISS ephemeris pairs, ESA Swarm `SP3ACOM` POD +
  `DNSAPOD` density): the density/ballistic-coefficient
  degeneracy, the single-scale least-squares calibration with
  calibration/hold-out split, measured numbers (NRLMSISE-00
  median +38% vs POD-derived density at 470&ndash;500 km around
  the 2024 solar maximum; ~200 m/day in-track with a calibrated
  ballistic factor against cm-level truth), the pitfalls
  (forecast references, manoeuvres inside the arc), and the SRP
  analogue of the same calibration logic.

### Changed

- **Engine ET&harr;UTC chain now includes TDB&minus;TT (SPICE
  `deltet`) &mdash; deliberate physics change.** Previously the
  engine treated ET&nbsp;&asymp;&nbsp;TT on both directions of the
  conversion (self-consistently, so validation residuals cancelled),
  which mislabeled every ET by up to &plusmn;1.657&nbsp;ms against
  true TDB &mdash; visible as a &plusmn;25&nbsp;mas ERA offset
  (~3&nbsp;m at GPS radius, Earth-fixed) versus the GUI conversion
  and SPICE. `spody_et_to_mjd_utc` now subtracts the `deltet`
  periodic term (new `spody_tdb_minus_tt`, `DELTET_*` constants from
  the NAIF LSK in `spody_const.h`) before the leap chain, and the
  GNSS converters (`convert gps/glonass/sp3`) add it in the TT&rarr;
  TDB direction, so emitted ET timestamps are true TDB
  (+0.479&nbsp;ms at the 2024 GPS validation epoch). Effects:
  converted ICRF states are unchanged (the relabel cancels in the
  rotation), the 7-day GPS-G11 propagation moves &lt;0.2&nbsp;mm,
  and the day-1 / 7-day SP3 residuals are unchanged (45.97&nbsp;m /
  581.06&nbsp;m RMS); ET values in outputs and `et_start_s` written
  by the converters shift by the deltet term, breaking bit-compat
  with pre-change artifacts by design. Validated by a zero-ULP
  C&harr;Python sweep over 20&nbsp;000 epochs (1972&ndash;2035)
  against the SPICE-validated Python twin.
- **`spody_gui.time_conv` becomes `spopy.time`.** The UTC&harr;ET
  datetime/ISO helpers move into `spopy` next to the engine-twin
  chain (leap table &mdash; moved from `spopy.eop` &mdash;, `deltet`,
  ET&rarr;UTC MJD), making `spopy/time.py` the single Python home of
  time conversions, in lockstep with `spody_time.c`. GUI imports
  updated; no behavior change on the form fields beyond the engine
  alignment above.
- **Maintainability refactor (engine + GUI), behavior-preserving.**
  spody-core grows `spody_time.{h,c}` as the single home of the
  calendar/time-scale helpers (Meeus Gregorian&rarr;JD, the
  leap-second chain, ET&rarr;UTC MJD) that were previously
  copy-pasted across the GNSS converters and the EOP/atmosphere
  readers; `spody_const.h` is cleaned up (single J2000 name,
  `JD_MJD_EPOCH`, centralized GPS week constants). GLONASS TOCs and
  the ERA UT1 bridge now use the exact leap chain at any post-1972
  epoch instead of the fixed post-2017 offset (bit-identical for
  post-2017 data). On the Python side `spody_gui/constants.py`
  becomes the single reading point of `spody_const.h` &mdash; parsed
  in dev checkouts *and* in PyInstaller bundles (the header now
  ships inside the bundle) &mdash; and the Python leap table lives
  only in `spopy.eop.LEAP_TABLE_MJD`. The two GUI monoliths are
  split into packages with stable extension points:
  `spody_gui/analysis/` (PlotSpec registry &mdash; a new view is one
  function + one spec entry) and `spody_gui/form/` (declarative
  catalog + one builder per TOML section, composed as mixins).
  Verified by an identical assembled plot registry, a lossless
  form round-trip, and a byte-identical 7-day Earth propagation
  against the pre-refactor engine.
- **New developer guide.** `docs/developer-guide.md` documents the
  system map, build recipes, coding conventions (naming, constants,
  license headers) and step-by-step extension recipes (new TOML
  section, analysis view, event kind, central body, force model).

- **Lossless [initial_state] swaps.** The form's cart&harr;kep
  and ICRF&harr;BF swaps used to chain spopy conversions on
  every click; after a handful of back-and-forth flips the
  displayed numbers drifted at the ULP / 1e-12 level. A new
  four-representation cache (one entry per `(kind, frame)`
  combo, populated from the visible block on every
  `editingFinished`) makes toggles a plain lookup: zero
  in-loop conversions, bit-for-bit round-trips across repeated
  flips. The cache invalidates wholesale when something the
  conversion depends on changes (et_start_s, central_body,
  dynamics_model, anomaly_type, reference_body, ephemeris.file).
- `[events]` no longer requires `eclipse_threshold` when
  present; the section now hosts the
  `[[events.altitude_crossing]]` array alongside eclipse, and
  either feature can be enabled independently.

### Fixed

- **Partial DE440 conversions (wizard "modern era" profile) crashed
  the engine at load.** The `.spody` loader kept the memory-mapped
  header (read-only pages) and, on detecting a subset file &mdash;
  header epochs written from `header.440`'s full span while the
  records cover only the converted chunks &mdash; patched the epochs
  in place: an access violation right after the `!It is a subset!`
  console lines. The header is now always a private heap copy,
  reconciled against the records (one informative line instead of
  the alarming block), and the converter itself refreshes the
  on-disk header epochs after writing, so newly converted subsets
  are born self-consistent. `spopy.Ephemeris` applies the same
  reconciliation. Verified: a modern-era-only run is bit-identical
  to the same run against the full DE440.
- **Re-run tab could not open modern run folders.** It looked for
  the aggregated events file under its legacy name
  (`<batch>_events.bin`) while post-run-folder-refactor runs
  timestamp-prefix every file (`<ts>_<batch>_events.bin`). Both
  names are accepted now, like the snapshot lookup already did.
- **A WIP-launched run now reloads the per-run snapshot.** After a
  successful run from a `.wip.toml` draft the form used to reload
  the origin file; it now loads the `<ts>_input.toml` snapshot the
  engine copied into the run folder (the only surviving record of
  what actually ran &mdash; the draft is deleted). Non-WIP runs keep
  the current behaviour (form stays on the source TOML).
- Stray Italian words in user-facing messages ("re-run selettivo"
  in the Re-run tab warnings, "Errore file ..." in the converter's
  perror output) translated to English.

## v0.2.0-beta &mdash; 2026-06-25

Headline: **CR3BP joins high-fidelity as a selectable dynamics
model**, and the GUI grows three orthogonal usability slices on
top of that &mdash; Keplerian initial-state input, full run-folder
hygiene (timestamp-prefixed files + WIP-TOML protection), and a
third **Info** tab in the Analysis pane that summarises any loaded
binary together with the run snapshot. Other notable additions:
the engine grows a `central_body_fixed` initial-state frame so
the user can type IC directly in ITRS / PA; the Analysis tab gets
a Plot-options frame selector that re-projects state-vector and
Keplerian-angle plots into the central body's body-fixed basis;
a UTC overlay on the 3D scene tracks the playback epoch; a busy
cursor + status message smooth over long renders; third-body
markers now spin with their own body-fixed frame; an `Export CSV`
action lands on every 2D plot; and an optional ICRF-aligned star-
map background ships for the 3D scenes. The spopy package gains
`kepler` + `cr3bp` mirrors of the engine helpers.

### Added

- **CR3BP dynamics model.** `simulation.dynamics_model = "cr3bp"`
  switches the propagator to the synodic-rotating-frame
  Circular Restricted 3-Body Problem. A new `[cr3bp]` block selects
  the primary pair (today: `primary_1 = "Earth"`, `primary_2 = "Moon"`),
  the integrator uses dimensional km / km/s, and impact events are
  auto-wired on both primaries with their standard radii.
  Validated against scipy DOP853 on an L1 Lyapunov: one-period
  closure 30 microns / 1.6e-10 km/s, sample-by-sample agreement
  21 microns over the orbit. The new frame `synodic_rotating`
  is mutually exclusive with `central_inertial` per dynamics
  model.
- **CR3BP Analysis support.** The plot tree filters per
  `dynamics_model`: HF-only views (impact lat/lon equirect /
  Mollweide / heatmap / 3D-on-body, accel breakdown, eclipse)
  are hidden in CR3BP; a new **Jacobi constant** plot under
  the *CR3BP* category surfaces the integrator's conservation
  diagnostic (5e-9 relative drift at the RKDP45 1e-13 floor).
  Osculating orbital elements stay available with a primary
  selector in the Scene options dialog: the synodic state is
  shifted to one primary's frame and `omega x r_rel` is added to
  the velocity so a, e, i, raan, aop, nu are computed in the
  instantaneous inertial frame anchored on that primary.
- **CR3BP 3D scene.** The synodic-frame view renders the two
  primaries as fixed spheres at the cached barycenter-offset
  positions, plus the spacecraft trajectory; the Scene options
  dialog hides HF-only sections (third bodies, body-fixed triad,
  scene-frame switch) for CR3BP runs.
- **Timestamp-prefixed run-folder files.** Every file the engine
  writes inside `output/<ts>/` now carries that folder's timestamp
  as a prefix: snapshots become `<ts>_input.toml`, trajectories
  `<ts>_<scenario>_state.bin`, etc. Editors and re-load workflows
  cannot conflate a snapshot with the sibling source TOML up the
  tree.
- **Auto-create `output_dir` on first run.** The engine mkdir's
  the parent `output_dir` (single level) before the timestamped
  child &mdash; fresh checkouts no longer need a manual
  `mkdir output/` before the first propagation lands.
- **Unified load/save UX.** A always-visible top bar above the
  tabs hosts the shared **Working dir** field + **Browse&hellip;**
  button; both the Run tab's *.toml combo and the Analysis tab's
  bin tree consume that one path. Inside the Run tab a dedicated
  row above the form widget hosts the TOML combo (recursively
  scanned from the working dir, *output* included so snapshots
  appear) plus **Load TOML&hellip; / Save / Save As&hellip;** buttons.
  The combo entries display compactly as `<parent>/<file>` with
  the full relative path one hover away via tooltip; the working
  directory auto-adopts the closest ancestor with both `output/`
  and a TOML when a file is opened from outside the current scope
  (so a deep snapshot pulls up the scenario folder, not the run
  subdir).
- **WIP draft TOML.** Saving a TOML whose folder already contains
  `.bin` output (a snapshot, or a source TOML whose runs landed
  beside it) diverts to a sibling `<stem>.wip.toml` sidecar &mdash;
  the file every existing run depends on is preserved. A one-time
  popup announces the divert; subsequent saves to the same WIP
  are silent. WIPs are tagged `(draft)` in the TOML combo.
  Successful runs launched from a WIP unlink the draft (its
  content has just been snapshotted into the new run folder)
  and auto-load the "starting file" the WIP was derived from.
- **Keplerian initial state.** `[initial_state]` accepts an
  optional `kind = "keplerian"` switch that takes the six
  classical orbital elements (`semi_major_axis_km`, `eccentricity`,
  `inclination_deg`, `raan_deg`, `arg_periapsis_deg`, `anomaly_deg`
  with `anomaly_type = "true" | "mean"`) plus a `reference_body`
  selector. HF runs default `reference_body = "central"`; CR3BP
  runs require `"primary_1"` or `"primary_2"` and the engine
  chains the synodic-frame transformation so the Keplerian state
  around one primary lands in the integrator's synodic frame.
  Cartesian (`kind = "cartesian"`, the default) parses unchanged.
  The GUI form swaps blocks live with automatic conversion in
  either direction via the new `spopy.kepler` /  `spopy.cr3bp`
  modules, so flipping the selector keeps the user's input.
- **Plot options dialog + Export CSV.** A `Plot options&hellip;`
  button rides on the matplotlib toolbar row in the Analysis tab.
  Today it hosts a single Export CSV action that dumps every
  `Line2D` on the active figure (single, overlay, tile modes
  supported) as a `.csv` with one section per subplot; wait
  cursor + status line during the write, auto-close on success.
- **3D starfield background.** A `Show starfield` toggle in the
  Scene-options dialog replaces the dark background with an
  equirectangular star map wrapped via `vtkSkybox.Sphere`. The
  asset (Solar System Scope Milky Way 8K, CC BY 4.0) ships
  through the Setup wizard alongside the Moon / Earth textures
  and is re-projected on first use so the catalogue lines up with
  the ICRF axes (pole = +Z, RA=0 = +X) &mdash; the wizard image is
  in galactic coordinates, so the conversion chains a standard
  ICRF&rarr;galactic rotation (Liu et al. 2011) before sampling.
  The rotated copy is cached on disk; the toggle state is
  persisted in QSettings.
- **3D camera-pose preservation.** Re-renders of the SAME file
  (Scene-options toggle, animation refresh) keep the user's
  pan / zoom; only a switch to a different file triggers the
  ResetCamera auto-fit.
- **Analysis Info tab.** A third tab alongside *Plot* and *Table*
  in the Analysis right pane shows a per-kind key/value summary
  of the loaded binary: run-context block sourced from the
  snapshot TOML (central body, dynamics model, CR3BP primaries +
  mass ratio, ET start, planned duration, ephemeris, cases CSV)
  plus a kind-specific block. Trajectory files surface t-range /
  span / &Delta;t stats, |r| and |v| ranges, initial and final
  state, and osculating Kepler elements at t0 / tf (HF only).
  Acceleration files surface |a_total| min/max/mean/RMS, per-
  force RMS (2-body / harmonics / 3rd-body / SRP / drag), and
  integrated time in shadow. Events files surface IMPACT and
  ECLIPSE counts, impact timing min/mean/median/max in seconds
  with auto-scaled (min/h/d) labels, complete-eclipse pairing
  with min/avg/max duration, and for batch logs also the per-
  case stats (cases impacted, total cases from the CSV,
  survivors, impact rate). When the active plot is one of the
  *Diff (pick 2 files)* specs the tab appends |&Delta;r| / |&Delta;v|
  max/mean/RMS/final, linear |&Delta;r| growth in km/day, and the
  RIC-frame |&Delta;| breakdown (max + RMS) in A's frame.
- **`central_body_fixed` initial-state frame.**
  `[initial_state].frame = "central_body_fixed"` lets the user
  type the IC (Cartesian or Keplerian-derived) in the central
  body's body-fixed basis at `et_start_s` (Earth ITRS, Moon PA).
  `sim_setup` re-uses the same `get_bf_rotation` callback the
  force model evaluates at every step to lift the IC into the
  integrator's `central_inertial` frame before the run begins;
  downstream sees a plain inertial state. The GUI form's frame
  combo gains the value when the central body has a registered
  orientation provider (CR3BP and unsupported bodies hide the
  option); flipping the combo does a live in-place rotation of
  the typed values via spopy so the displayed numbers track
  the new basis without losing data.
- **Analysis Plot-options frame selector.** Plot Options grows
  a Plot-frame radio (ICRF / body-fixed). State-vector plots
  (|r|, |v|, x/y/z, vx/vy/vz, XY/XZ/YZ projections) and
  Keplerian-angle plots (RAAN, AOP, &nu;, e-vs-&omega;) re-
  render in the selected basis on the fly; magnitudes
  (a, e, i, |r|, |v|) plot identically in both frames and the
  title suffix is the only visible change there. CR3BP runs
  and central bodies without an orientation provider fall back
  to ICRF.
- **Eccentricity vs argument of periapsis plot.** A phase-space
  view under *Orbital elements*: e on Y, &omega; on X. Useful
  for spotting drift patterns (J2 / 3rd-body) that the per-
  element curves smooth out across the run.
- **3D scene UTC overlay.** A `vtkTextActor` 2D anchored at the
  bottom-right of the 3D canvas shows the UTC corresponding to
  the current animation tick (`et_start + t_anim_s` converted
  via `spody_gui.time_conv.et_to_utc`). Updates on every slider
  / play tick and on right-tab switches; clears when the canvas
  leaves 3D or the run has no `et_start_s`.
- **3rd-body markers spin in 3D scenes.** When a third body has
  a registered orientation provider (Earth ITRS via
  `spopy.icrf_to_itrs`, Moon PA via DE440 libration angles),
  the textured sphere now rotates per-tick alongside its
  position so the surface features (continents / mares) track
  the body's actual attitude across the run. Same provider the
  central body uses; bodies without one (Sun, planets) keep
  the previous behaviour.
- **Busy-cursor + status message around slow renders.** A
  reusable `_busy(message)` context manager flips the cursor to
  the wait shape and writes a "Working: ..." note in the panel
  info label across file loads, single / diff / tile / overlay
  dispatches, and the per-body loop in `_add_third_bodies`
  (periodic `processEvents` keeps the message pump alive).
  Quick-win against Windows' "Not Responding" label.

### Changed

- **GUI run CWD.** Snapshots and WIP files live deep inside
  `output/<ts>/`. Running them now uses the scenario root as
  CWD (via the same project-root walk-up the working dir uses)
  so the TOML's `output_dir = "output"` resolves to the scenario
  folder's `output/` and not to ANOTHER nested
  `output/<new-ts>/` &mdash; the latter would have hit Windows'
  MAX_PATH within a handful of iterations.
- **Form button strip.** The **Load&hellip;** and **Generate**
  buttons are gone from the form's internal top row. Load is now
  the top bar's **Load TOML&hellip;** button; Generate's job is
  fully covered by the top bar's **Save / Save As** (write the
  TOML + refresh recents / working dir / analysis tree).
- **Analysis tab full-depth scan.** The Analysis file tree scans
  `.bin` files fully recursively under the working dir (was
  capped at 3 levels). Picking a working dir that hosts many
  scenarios (e.g. `examples/`) surfaces every bin under it; only
  build / VCS / venv noise is pruned (`__pycache__`, `.git`,
  `.venv`, `venv`, `build`, `dist`, `node_modules`).
- **Analysis local working-dir row removed.** The Analysis tab's
  own *Working dir* field + *Change&hellip;* button are gone; the
  shared top-bar field is the single source of truth. A small
  **Refresh** button stays next to the file tree for manual
  re-scans after dropping bins in by hand.

### Fixed

- **Sun arrow / third-body markers missing at first render.** The
  per-body filter treated an empty `scene_options.show_bodies`
  set as "hide every body", which was the dataclass default
  before the user opened the Scene-options dialog. The Analysis
  panel now seeds the set from the loaded snapshot's
  `force_model.third_bodies` before the first 3D dispatch.
- **Wrong body orientation on equirectangular planetary
  textures.** The W/2-column meridian roll that the lunar SVS
  TIFF already went through is now applied to every body
  texture (JPEG / PNG / TIFF). Bodies whose published texture
  places the prime meridian at the image centre (Solar System
  Scope Earth, ...) used to land 180&deg; off, which only
  became visible once the 3rd-body markers started spinning
  (the "lit Australia at 14 UTC" report). Cache filename is
  `<stem>_uv0.png`; the lunar `<stem>_pa.png` predecessor is
  ignored on first run.
- **Wizard star-map asset min size.** The Solar System Scope 8K
  Milky Way JPEG (~1.9 MB on disk) was flagged truncated
  against a wrong 4 MB floor; lowered to 1 MB so the real file
  passes and a half-finished download still trips.

## v0.1.3-beta &mdash; 2026-06-22

The Phase 2 release. Earth joins the Moon as a supported central
body end-to-end (engine + GUI + validation example + manual). Two
new converter sub-commands extend the validation workflow to GNSS
ground-truth comparisons (IGS SP3 precise orbits and RINEX-NAV
broadcast). Bundled `spody-core` library bumped to **1.2.0** to
reflect the API and binary-format changes (see *Changed* below).

### Added

- **Earth as a supported `central_body`.** `force_model
  .central_body = "Earth"` selects an Earth-centred propagation
  with IAU 2006/2000A_R06 + IERS EOP for the inertial-to-ITRS
  rotation (driven by `R_GCRS->ITRS = W * R3(+ERA) * Q`, evaluated
  at every harmonics step). The schema gains two Earth-only
  required fields, `force_model.eop_file` (path to
  `finals2000A.all`) and `force_model.iau2006_dir` (path to the
  directory containing `tab5.2{a,b,d}.txt`).
- **Wizard manages the Earth data set.** Four new asset cards:
  the EIGEN-6C4 `.gfc` Earth gravity-model file, the IERS EOP
  `finals2000A.all`, the IAU 2006 X/Y/s+XY/2 conventions tables,
  and the NASA Blue Marble texture. The `.gfc` is auto-converted
  to GRGM-style `eigen-6c4.tab` via `spody convert
  harmonics_icgem` the same way DE440 ASCII chunks are turned
  into `de440.spody`. Per-100-degree progress is streamed into
  the wizard's status line.
- **EOP startup freshness check.** Every launch issues one
  HTTP HEAD request to the upstream `finals2000A.all` URL and
  compares the server `Last-Modified` + `Content-Length` against
  the local file's mtime + size. If the server's copy is newer, a
  non-blocking pop-up offers to open the wizard. Silent on
  success and on transient network failure.
- **`spody convert harmonics_icgem`** &mdash; CLI sub-command that
  converts an ICGEM `.gfc` file to the GRGM-style `.tab` format
  the engine reads. Optional `--max-degree N` truncates the
  output. Used by the wizard for EIGEN-6C4; usable manually for
  any other ICGEM model (EGM2008, etc.).
- **`spody convert sp3`** &mdash; CLI sub-command that converts
  one or more IGS SP3 precise-orbit files into a SpOdy `SPDYOUT_`
  reference binary in the central-body inertial frame, applying
  `R_ITRS->ICRF(t)` per record. **Multi-file mode** concatenates
  daily SP3 files into a week-or-more-long cm-precision reference;
  single-file calls are bit-for-bit identical to the previous
  behaviour. Used by `examples/gps_g11_validation/` (GPS-only IGS
  Final products) and `examples/glonass_r03_validation/` (CODE
  MGEX multi-GNSS for GLONASS R03).
- **`spody convert glonass`** &mdash; CLI sub-command that
  converts one or more RINEX-NAV files (GLONASS broadcast) into a
  single `SPDYOUT_` reference binary with continuous 0-anchored
  time axis. Multi-file input concatenates daily nav files into a
  week-or-more-long reference; calling with one file reproduces
  the single-file behaviour bit-for-bit. Used by the new
  `examples/glonass_r03_validation/` example (7 daily RINEX files,
  167.5 h reference).
- **`spody convert gps`** &mdash; CLI sub-command that converts
  one or more RINEX-NAV GPS files into a single `SPDYOUT_`
  reference binary. Unlike GLONASS broadcast (which carries
  `(r, v)` directly), GPS broadcast carries Kepler-with-corrections
  elements per record, so the converter propagates each record to
  its own TOC per IS-GPS-200 sect. 20.3.3.4.3 (positions) +
  Remondi 2004 (analytic velocity derivatives) to extract `(r, v)`
  at broadcast-OD precision (`~few m / few cm/s`). Multi-file from
  the start, same 0-anchored time convention as `sp3` / `glonass`.
  Used by `examples/gps_g11_validation/` to bootstrap the initial
  state, replacing the previous 4th-order Lagrange forward
  derivative on SP3 positions (which gave the SP3 secant rather
  than the true Keplerian tangent &mdash; `|v0|` was ~3.57 km/s vs
  the correct ~3.87 km/s, a 7-8% bootstrap artefact that swamped
  the residual at `t = 0`).
- **3D rotating ITRF triad and Earth animation.** The 3D orbit
  plot animates the active central body's body-fixed rotation in
  real time: IAU 2006 + EOP for Earth (textured globe and ITRF
  triad both rotate in lock-step), DE440 libration for the Moon
  (already shipped in v0.1.2-beta). The textured Moon now
  appears as the **third-body marker** in Earth-centred scenes
  too, so it stays recognisable at its true &sim;384,000 km
  distance.
- **Pure-Python Earth-orientation in `spopy`** &mdash; new
  modules `spopy.MappedEOP` (IERS finals2000A.all parser) and
  `spopy.icrf_to_itrs(et, eop)` (rotation), wrapping `pyerfa` and
  matching the C engine at machine epsilon. Mirrors the existing
  `spopy.lunar_libration_angles` / `spopy.icrf_to_moon_pa` pair.
- **Two GNSS validation examples shipped**:
  - `examples/glonass_r03_validation/` &mdash; propagates GLONASS
    slot 03 for 167.5 h from its first 2024-01-21 broadcast TOC.
    Day-by-day RMS vs broadcast (`srp=false`, `N=70`, Moon+Sun):
    176 -> 367 -> 577 -> 803 -> 1026 -> 1232 -> 1425 m. Day-1 RMS
    matches the 177 m broadcast-OD floor; the secular growth is
    the unmodelled in-track perturbation forces signature.
  - `examples/gps_g11_validation/` &mdash; propagates GPS PRN 11
    for 167.75 h with **broadcast IC** (via the new `convert gps`
    Kepler-with-corrections) and **cm-precision SP3 ground
    truth**. Day-by-day RMS: 46 -> 128 -> 212 -> 300 -> 390 ->
    484 -> 581 m. Day-1 RMS is &sim;4&times; smaller than the
    GLONASS baseline because (a) the broadcast IC is clean
    (`|Δr|` at `t = 0` = 2.3 m vs the previous 5-point Lagrange
    bootstrap's 7-8% velocity artefact, which translated to
    &gt; 120 m at `t = 0`), and (b) the SP3 reference is cm-level
    truth, whereas the GLONASS broadcast reference carries a
    &sim;258 m broadcast-OD floor of its own (independently
    verified by diffing the GLONASS broadcast directly against the
    CODE MGEX multi-GNSS SP3). See chapter 11 for the
    multi-reference comparison and the broadcast-OD floor
    derivation.

### Changed

- **`harmonics_degree` schema range bumped from `[2, 1200]` to
  `[2, 2200]`** to accommodate the EIGEN-6C4 / EGM2008 Earth
  coefficient sets (degree 2190). The effective upper bound at
  run time stays the degree declared in the chosen
  `harmonics_file`.
- **`|Δr| distribution` and `|Δr| CDF` stats boxes** now report
  **RMS** alongside the percentile budgets. The RMS is the
  canonical single-number summary in OD / conjunction work; it
  complements the distribution-free percentiles that already
  cover non-normal error distributions.
- **GLONASS / SP3 reference binaries are now time-0-anchored.**
  The time column of every record is `et_record - et_first`,
  matching the propagator's emit-trajectory convention so a diff
  against a propagation lines up sample-by-sample at `t = 0`.

### Fixed

- **`spody_bf_rotation_earth` prototype now visible to gcc /
  clang.** A missing forward `typedef` of `ForceModelContext` in
  `spody_earth_orientation.h` caused gcc / clang to construct a
  fresh function-local struct type from the parameter list,
  rejecting the implementation as a prototype mismatch. The
  Windows MSVC build was lenient and the bug only surfaced as a
  Linux / macOS CI failure on every commit since v0.1.2-beta.
  Forward typedef in the header fixes it.

## v0.1.2-beta &mdash; 2026-06-12

One day after v0.1.1-beta, mostly bug-fix + UX polish driven by
testing the bundle on a fresh Windows install and pushing a 9577-
case LRO debris run through the analysis tab.

### Fixed

- **Windows bundle no longer fails on fresh installs.** The MinGW-
  built `spody.exe` previously imported `libgcc_s_seh-1.dll`,
  `libwinpthread-1.dll`, `libstdc++-6.dll` and `libgomp-1.dll`
  from PATH, which a typical end-user Windows install does not
  have, so the wizard's first `spody convert` call exited with
  `STATUS_DLL_NOT_FOUND` (0xC0000135). The release CI now passes
  `-DCMAKE_EXE_LINKER_FLAGS=-static` so the runner is a single
  self-contained executable.
- **Settings paths self-heal on launch.** The Settings dialog
  previously inherited stale paths from a developer-mode QSettings
  on the same machine (e.g. an absolute build-folder path that did
  not exist on a fresh install), and never auto-populated on first
  run. `SettingsStore.ensure_bundled_defaults()` now overwrites
  any empty or now-missing path with the bundled fallback (the
  `spody.exe` next to the launcher, the wizard-downloaded Moon
  texture under `data/`). Custom paths that still resolve are
  preserved.
- **Wizard's auto-convert no longer hangs on fresh installs.**
  The C runner `spody.exe` now ships with unbuffered stdout
  (above) so the converter emits thousands of per-record lines
  one syscall at a time. The wizard previously waited for
  `finished` before reading the pipe, so Windows' default 64 KB
  pipe buffer filled, the converter blocked on `write`, and the
  GUI was stuck on `converting...` with no exit. The wizard
  now drains `readyReadStandardOutput` like `runner.py` already
  does for the propagation path, so the pipe always has free
  space.

  *Indirect consequence*: on machines where the prior hang
  produced a half-written `de440.spody`, subsequent
  `spody batch` invocations crashed at startup with
  `STATUS_ACCESS_VIOLATION` while reading the corrupt records.
  With the convert always running to completion, the on-disk
  ephemeris is consistent and the crash goes away.
- **Survival timeline no longer freezes the GUI on 9k+ cases.**
  Above 200 cases the per-row Rectangle artist path is bypassed
  in favour of a single `LineCollection`, and the 9000-text-label
  Y axis is replaced by a descriptive label
  (`<N> cases -- earliest impact at top, survivors at bottom`).
  Reverse-sort information is preserved without the rank-vs-
  case-idx ambiguity numeric ticks would imply.

### Changed

- **3D impact view: instanced GPU rendering.** 9k+ impact
  markers used to be drawn as N individual `vtkSphereSource`
  actors, which CPU-bottlenecks at ~1k+ markers and freezes the
  canvas on a 9577-case batch. The new `VtkCanvas.add_points()`
  consolidates them into a single `vtkGlyph3DMapper` actor with
  per-point uchar RGB scalars -- one GPU-instanced draw call
  regardless of count. Pan / rotate fluid again.
- **Frame triads unified across every 3D plot.** PA is the
  primary (bright RGB, `2.10 × R_moon`) and ICRF the secondary
  (muted RGB, `1.80 × R_moon`, opacity 0.25). Whichever frame
  the scene's coordinates use, the convention is identical so
  the reader always finds body-fixed in the saturated triad and
  inertial in the faded one. The orbit-3D plots gain the same
  triad pair (previously had none); they degrade gracefully to
  scene-frame only when the per-run `input.toml` snapshot or
  ephemeris is unreachable.
- **`spody.exe` stdout / stderr unbuffered.** Progress lines used
  to arrive at the GUI terminal pane in ~4 KB chunks because libc
  defaults stdout to fully block-buffered when piped. Both
  streams now use `setvbuf(_IONBF)` so output streams live as it
  is emitted. (`_IOLBF` was the original picks but the Microsoft
  UCRT silently treats it as `_IOFBF`, and `_IOLBF` / `_IOFBF`
  with a NULL buffer and size 0 is undefined and crashed the
  binary with `STATUS_STACK_BUFFER_OVERRUN`.)

### Added

- **`|Δr| distribution`** -- new diff plot. Histogram of the
  per-sample position-error magnitude with `min(60, sqrt(N))`
  bins. Descriptive-stats box (median / p95 / max) pinned in
  the bottom-right corner.
- **`|Δr| empirical CDF`** -- new diff plot. Steps-post line of
  the empirical CDF in [0..1]. Descriptive-stats box reports
  median, p95, p99, p99.9 and max -- the canonical
  **distribution-free** percentile budgets for regression work
  (no normality assumption, unlike `mean ± 2σ`).

## v0.1.1-beta &mdash; 2026-06-11

The second public drop, six days after the alpha. Focused on the
batch-input workflow, the analysis-tab impact views, and the form
ergonomics around frames, paths, and time units.

### Added

- **LVLH cases_frame for batch input** &mdash; rotating-frame batch
  CSVs can now declare `cases_frame = "lvlh"` alongside the existing
  `"ric"` option. The GUI rotates the source CSV to ICRF at
  Generate-TOML, applying the NASA / Goddard convention
  (`z = -r_hat`, `y = -h_hat`, `x = y x z`). Drops in directly for
  the output of debris-evolution tools that emit fragments in LVLH.
- **Metadata-column sentinel** &mdash; a `[batch.columns]` entry
  whose target is the empty string (`L_char_m = ""` or
  `{ target = "" }`) marks the column as bookkeeping. The C parser
  type-checks it but never applies it; the form surfaces this as
  the **"(unassigned)"** target choice. Lets a cases CSV preserve
  every column of its source binary (fragment characteristic
  length, debris IDs, classification tags) without dragging those
  into the propagator. Symmetric on load.
- **Aggregated batch-events file (SPDYEVTB)** &mdash; replaces the
  per-case `<batch>_<i>_evt.bin` files with a single
  `<batch>_events.bin` carrying `int32 case_idx + 4-byte pad +
  EventRecord` records (88 bytes each). Python reader auto-detects
  the magic so old files still read.
- **Per-run timestamp folder** &mdash; `spody.exe` creates
  `<output_dir>/<UTC-ISO8601>/` at launch, copies the source TOML
  inside as `input.toml`, and rewrites every output path to live
  there. Each invocation is self-contained and the Analysis tab
  groups files by run folder automatically.
- **Five new batch-event analysis views**:
  - Time-to-impact histogram
  - Survival timeline per case
  - Impact lat/lon equirectangular map (Moon Principal Axes)
  - Impact lat/lon Mollweide projection
  - Impact density heatmap (Mollweide, 2.5&deg; bins)
  - 3D impact view with PA + ICRF frame triads and Sun arrow on a
    textured Moon (uses the bundled NASA SVS LROC color texture)
- **`spopy` Python package** &mdash; pure-Python DE440 ephemeris
  reader + ICRF&lt;-&gt;Moon PA rotations, numpy-only. Bit-identical
  to the C engine (104/104 checks pass at `atol=1e-9` km/rad,
  `rtol=1e-14`, &sim;1 ULP IEEE 754). Powers the impact lat/lon
  views; available as a standalone re-implementation of the
  read-side spody-core helpers.
- **UTC &lt;-&gt; ET converter** &mdash; `simulation.et_start_s` now
  has a dual ET/UTC cell with arrow buttons. Conversion is
  bit-identical to SPICE `str2et` / `et2utc` (same `deltet`
  algorithm with `K`, `EB`, `M0`, `M1` constants plus the
  hard-coded IERS Bulletin C leap-seconds table).
- **Duration unit combo** &mdash; `simulation.duration_s` now has a
  `s | min | h | days` selector. The TOML always carries seconds;
  switching the combo rescales the visible number. Auto-picks the
  largest unit on load.
- **Data-dir-aware ephemeris / harmonics dropdowns** &mdash; the
  free-text path pickers in `[force_model]` and `[ephemeris]`
  became combo boxes populated from the wizard-managed data dir,
  filtered by category and central body. A `(custom)` fallback
  preserves out-of-data-dir paths so existing TOMLs round-trip.
- **Output naming refactor** &mdash; `[output]` is now 5 checkboxes
  + 1 `output_dir` picker with live path preview. Streams are
  auto-named `<sim_name>_state_icrf.{csv,bin}`,
  `<sim_name>_acc_icrf.bin`, `<sim_name>_events.bin`,
  `<sim_name>.log`.
- **Plot / Table tabs in Analysis** &mdash; the right pane became
  a QTabWidget. Table view shows the raw binary as a spreadsheet
  with Ctrl+C TSV copy.
- **Moon texture as an optional wizard asset** &mdash; the Setup
  wizard can download the NASA SVS LROC color 2K equirectangular
  TIFF; the impact views fall back to a flat-grey sphere when the
  texture is absent.

### Changed

- **`[batch.columns]` mode field** &mdash; columns can declare
  `{ target = ..., mode = "delta" }` to add the CSV value to the
  TOML base instead of replacing it. Pairs naturally with rotating-
  frame batches where the CSV cells are deltas in the rotating
  frame to be added to the inertial reference.
- **Form path semantics** &mdash; paths in TOML are now consistently
  resolved against the TOML's own directory (matching spody's own
  rule); relative paths in `cases_file` etc. no longer depend on
  the GUI's cwd.
- **Frame-aware rotated preview** &mdash; the cases-CSV rotated
  preview header and error strings now cite the active frame
  (`"post LVLH -&gt; ICRF"`, `"LVLH rotation has nothing to do"`)
  instead of hardcoding RIC.

### Fixed

- Column-mapping table no longer silently drops `(unassigned)`
  rows on emit; they now produce the metadata sentinel so the
  validator does not complain about the missing entry.
- `_apply_loaded_batch_columns` honours the empty-target sentinel
  on TOML load, preserving the user's explicit `(unassigned)`
  choice against the name-based heuristic.
- Survival-timeline view counts cases correctly even when only
  `input.toml` is in the run folder (cases CSV resolution walks
  up two ancestor levels).
- 3D impact-view Moon texture: PNG transcode cache works around
  the vtkTIFFReader / LZW-with-predictor bug; the cached PNG is
  rolled by W/2 so the prime meridian lines up with VTK's u=0.

### Documentation

- User manual brought current with every visible change since the
  alpha. New chapters covering the SPDYEVTB events file, the
  Plot/Table split, the impact-view catalog, the spopy package,
  the asset-dropdown UI, the UTC&lt;-&gt;ET converter, the rotating-
  frame batch input (RIC + LVLH), the metadata-column sentinel,
  and the duration unit combo.

### Internal

- `spody-core` bumped to track the kind-agnostic `check_events`
  refactor, the per-force acceleration outputs, and the opt-in
  eclipse-detection knob `[events].eclipse_threshold`.
- Filesystem / timestamp / path helpers extracted into `app_io`
  so future tooling can reuse them.

### Known limitations (unchanged from v0.1.0-alpha)

- Bundles ship for Windows / Linux x86_64 / macOS arm64 only.
- No drag model, no Moon-other-than-PA frame mode, no in-app cases
  CSV generator UI.
- Python is pinned to 3.9 in the release pipeline because of the
  PyInstaller / apiset interaction on some end-user Win10 builds;
  the workaround (runtime hook) is on the v0.1.2 backlog.

## v0.1.0-alpha &mdash; 2026-06-05

First public drop. Setup wizard + Run tab + Analysis tab + diff
plots + tile dashboard + 14-chapter user manual. Three OS bundles
(Win / Linux / macOS arm64) + sha256 sidecars + the user manual PDF.

Published at
[github.com/ValeEng/spody/releases/tag/v0.1.0-alpha](https://github.com/ValeEng/spody/releases/tag/v0.1.0-alpha).
