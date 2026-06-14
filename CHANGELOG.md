# Changelog

All notable changes to SpOdy are listed here. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
match the git tags published on `github.com/ValeEng/spody/releases`.

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
