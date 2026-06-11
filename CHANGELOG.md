# Changelog

All notable changes to SpOdy are listed here. Format roughly follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
match the git tags published on `github.com/ValeEng/spody/releases`.

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
