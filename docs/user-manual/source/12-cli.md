# Command-line reference

The engine `spody.exe` is a standalone command-line tool. The GUI
drives it through subprocess calls, but you can drive it yourself
&mdash; from a PowerShell prompt, from a Python script, from a CI
pipeline. This chapter documents the subcommands and their flags.

The conventions throughout the chapter:

- All path arguments are resolved relative to the **current working
  directory** of the shell that launches `spody.exe`, *except* for
  the paths inside a TOML, which are resolved relative to the
  **TOML's directory**.
- The engine returns **exit code 0** on success and **non-zero** on
  any failure (parse error, validation error, runtime error). A
  human-readable error message is printed to stderr before exit.

## Global structure

```
spody.exe <command> [arguments]
```

The six commands are:

| Command           | Purpose                                      |
|-------------------|----------------------------------------------|
| `validate`        | parse + sanity-check an input TOML, no run   |
| `propagate`       | run a single-case simulation                 |
| `batch`           | run a multi-case sweep                       |
| `convert`         | data-file conversions (ephemeris, harmonics, SP3, GLONASS, GPS, OEM) |
| `calibrate`       | fit the drag density-scale `k(t)` nodes against a reference |
| `info`            | print version + build info                   |

Pass `--help` or no command to print a short usage summary.

## `spody validate`

```
spody.exe validate <input.toml>
```

Loads the TOML, runs the engine's parser and the validator, and
prints either:

- a multi-line summary of the parsed configuration starting with
  `OK`, on success;
- a single-line `error: <file>:<reason>` message on failure.

The validator runs the same checks the **Validate** button in the
GUI triggers, so the verdicts agree by construction. Useful in
scripts to gate downstream work on a clean input.

**Exit codes.**

- `0` &mdash; the TOML is well-formed and internally consistent.
- `1` &mdash; parse error, validation error, or unreadable file.

## `spody propagate`

```
spody.exe propagate <input.toml> [--out <dir>]
```

Reads a TOML describing a single scenario, integrates the
trajectory, and writes the output files specified in the
`[output]` block.

Flags:

- `--out <dir>` (optional) &mdash; override the `output.*_file`
  paths' parent directory. Useful when you want to dispatch the
  same TOML against many output folders without editing the file.

**Stdout/stderr.** The engine prints a header line and per-step
diagnostics to stdout, with errors on stderr. The GUI streams
this into the terminal pane; on the command line you see it in
your shell.

**Exit codes.**

- `0` &mdash; the propagation reached `duration_s` without an
  unrecoverable error.
- `1` &mdash; parse error, validation error, integrator failure
  (step size driven below `h_min_s`), or I/O error.

## `spody batch`

```
spody.exe batch <input.toml> [--out <dir>]
```

Reads a TOML with a `[batch]` block and runs the parameter sweep
described by `batch.cases_file`. The TOML must contain a `[batch]`
section; running `propagate` against a batch TOML is an error,
and vice versa.

Flags:

- `--out <dir>` (optional) &mdash; override `batch.output_dir`.

**Threading.** Reads `batch.thread_number` and dispatches that
many concurrent cases through OpenMP. Use `1` for sequential
execution; the engine handles any value up to the host's logical-
CPU count.

**Exit codes.**

- `0` &mdash; every case completed successfully.
- `1` &mdash; one or more cases failed; the engine prints a
  summary line `batch stopped at case N/M after T s` and exits.

## `spody convert`

The umbrella command for data-file conversions. Five sub-commands
are implemented today: planetary ephemeris (`ephemeris`), ICGEM
spherical-harmonic gravity coefficients (`harmonics_icgem`), IGS
SP3 precise orbits (`sp3`), RINEX-NAV GLONASS broadcast (`glonass`),
and RINEX-NAV GPS broadcast (`gps`). The first two are the
conversions the setup wizard triggers automatically; the last
three produce reference binaries used in the diff-validation
workflow (chapter 11).

### `spody convert ephemeris`

```
spody.exe convert ephemeris <folder> <de_family> <date1> [date2 ...]
```

Converts the JPL ASCII DE-family chunks present in `<folder>`
into the internal `.spody` binary format SpOdy uses for ephemeris
queries.

Arguments:

- `<folder>` &mdash; directory containing the `header.<de_family>`
  file and the `ascpXXXXX.<de_family>` chunks. The output
  `de<de_family>.spody` is written into the **same folder**.
- `<de_family>` &mdash; the DE-family identifier (`440` for DE440,
  the only supported value today).
- `<date1> [date2 ...]` &mdash; one or more chunk identifiers
  (without the `ascp` prefix and the `.<de_family>` suffix).
  Order does not matter; the converter sorts them internally.

This is the same conversion the setup wizard runs automatically
on first launch (chapter 3, section 3.5). You typically invoke it
manually only when you have downloaded additional chunks outside
the wizard or want to regenerate `de440.spody` for any other
reason.

**Example.**

```powershell
spody.exe convert ephemeris .\data\DE440 440 01950 02050
```

Reads `data\DE440\header.440`, `data\DE440\ascp01950.440`, and
`data\DE440\ascp02050.440`. Writes `data\DE440\de440.spody`.

**Exit codes.**

- `0` &mdash; conversion succeeded.
- `1` &mdash; missing file, unreadable folder, or library failure.

### `spody convert harmonics_icgem`

```
spody.exe convert harmonics_icgem <input.gfc> <output.tab> [--max-degree N]
```

Converts an ICGEM-format spherical-harmonic gravity coefficients
file (the `.gfc` format published by GFZ Potsdam for EIGEN-6C4,
EGM2008, and most modern Earth gravity models) into the GRGM-style
`.tab` format the engine reads at run time.

Arguments:

- `<input.gfc>` &mdash; the ICGEM source file.
- `<output.tab>` &mdash; destination for the GRGM-style table.
- `--max-degree N` (optional) &mdash; truncate the output at
  degree N. Default is the full degree declared in the source
  file. Useful when you want a slimmer file for a known
  altitude regime (e.g. EIGEN-6C4 truncated to 250 is &sim;3 MB
  vs the full 252 MB).

This is the second conversion the setup wizard runs automatically
when the Earth gravity-model raw file is downloaded (chapter 3).
Manually invoking it is useful when you want a custom-truncated
table or you have downloaded a different ICGEM model.

**Example.**

```powershell
spody.exe convert harmonics_icgem .\data\EIGEN-6C4\EIGEN-6C4.gfc `
                                  .\data\EIGEN-6C4\eigen-6c4.tab
```

**Exit codes.**

- `0` &mdash; conversion succeeded.
- `1` &mdash; missing file, parse error, or write failure.

### `spody convert sp3`

```
spody.exe convert sp3 <input.sp3> [input.sp3 ...] <output.bin> <sat_id> --eop <file> --iau2006-dir <dir>
```

Converts one satellite's track from one or more IGS SP3 precise-
orbit files into a SpOdy `SPDYOUT_` reference binary in the
central-body inertial frame. SP3 records carry ITRS position only
(no velocity); the converter applies `R_ITRS→ICRF(t)` per record
using the EOP + IAU 2006 tables, writes position with zero
velocity, and emits one record per SP3 epoch.

**Multi-file mode**: IGS final products ship one SP3 file per UTC
day, so a week-long cm-precision reference is 7 daily files passed
in chronological order. The converter concatenates them into one
binary with a continuous 0-anchored time axis; single-file calls
behave bit-for-bit identically to before.

Arguments:

- `<input.sp3> [input.sp3 ...]` &mdash; one or more IGS SP3-c /
  SP3-d files in chronological order. For the GPS-only IGS Final
  products (`IGS0OPSFIN_*_ORB.SP3` on BKG) only `G<NN>` ids are
  resolvable; for multi-GNSS reference (G + R + E + C + J) use a
  multi-GNSS analysis-centre product such as CODE's
  `COD0MGXFIN_*_ORB.SP3` (mirrored at `ftp.aiub.unibe.ch/CODE_MGEX/`).
- `<output.bin>` &mdash; destination `SPDYOUT_` binary (always the
  *second-to-last* positional argument).
- `<sat_id>` &mdash; 3-character SP3 satellite id (`G11`, `R03`,
  `E14`, &hellip;); always the *last* positional argument.
- `--eop <file>` &mdash; path to `finals2000A.all`.
- `--iau2006-dir <dir>` &mdash; path to the directory containing
  `tab5.2{a,b,d}.txt`.

The time column of the emitted binary is **0-anchored** at the
first record across all inputs (`t_record - t_first_overall`),
matching the propagator's convention so a diff against a
propagation lines up sample-by-sample at `t = 0`.

**Example.** Used by the bundled `gps_g11_validation` example to
build the 7-day SP3 reference:

```powershell
spody.exe convert sp3 .\examples\gps_g11_validation\IGS0OPSFIN_20240210000_01D_15M_ORB.SP3 `
                      .\examples\gps_g11_validation\IGS0OPSFIN_20240220000_01D_15M_ORB.SP3 `
                      ... `
                      .\examples\gps_g11_validation\IGS0OPSFIN_20240270000_01D_15M_ORB.SP3 `
                      .\examples\gps_g11_validation\gps_g11_2024_01_21_7d_sp3_ref.bin `
                      G11 `
                      --eop .\data\eop\finals2000A.all `
                      --iau2006-dir .\data\iau2006
```

### `spody convert glonass`

```
spody.exe convert glonass <input.rnx> [input.rnx ...] <output.bin> <sat_id> --eop <file> --iau2006-dir <dir>
```

Converts one GLONASS satellite's broadcast nav track from one or
more RINEX-NAV files into a SpOdy `SPDYOUT_` reference binary in
the Earth-centred ICRF frame. Unlike SP3, GLONASS broadcast nav
carries position **and** velocity in PZ-90 every &sim;30 min, so
the resulting binary has true `(r, v)` per record (no
finite-difference velocity needed).

The converter accepts **multiple input files** as positional
arguments &mdash; the workhorse pattern for week-long-or-more
validations, since IGS-BKG ships GLONASS broadcast as one
RINEX-NAV file per UTC day. Pass the files in chronological
order; the converter walks them in sequence, scans each for the
requested slot, and appends `SPDYOUT_` records to a single
binary with a continuous 0-anchored time axis (no gap at day
boundaries). Calling with a single input reproduces the single-
file behaviour bit-for-bit.

Arguments:

- `<input.rnx> [input.rnx ...]` &mdash; one or more RINEX 3.x
  GLONASS-or-mixed nav files in chronological order.
- `<output.bin>` &mdash; destination `SPDYOUT_` binary
  (always the *second-to-last* positional argument).
- `<sat_id>` &mdash; the 3-character GLONASS slot id, e.g. `R03`
  (always the *last* positional argument).
- `--eop <file>` &mdash; path to `finals2000A.all`.
- `--iau2006-dir <dir>` &mdash; path to the directory containing
  `tab5.2{a,b,d}.txt`.

**Example.** Used by the bundled `glonass_r03_validation` example
to build the 7-day reference binary from 7 daily RINEX files:

```powershell
spody.exe convert glonass `
    .\examples\glonass_r03_validation\BRDC00WRD_R_20240210000_01D_RN.rnx `
    .\examples\glonass_r03_validation\BRDC00WRD_R_20240220000_01D_RN.rnx `
    .\examples\glonass_r03_validation\BRDC00WRD_R_20240230000_01D_RN.rnx `
    .\examples\glonass_r03_validation\BRDC00WRD_R_20240240000_01D_RN.rnx `
    .\examples\glonass_r03_validation\BRDC00WRD_R_20240250000_01D_RN.rnx `
    .\examples\glonass_r03_validation\BRDC00WRD_R_20240260000_01D_RN.rnx `
    .\examples\glonass_r03_validation\BRDC00WRD_R_20240270000_01D_RN.rnx `
    .\examples\glonass_r03_validation\glonass_r03_2024_01_21_7d_ref.bin `
    R03 `
    --eop .\data\eop\finals2000A.all `
    --iau2006-dir .\data\iau2006
```

Each file contributes its own per-file summary line on stderr
(`-> 48 records (sat=R03, &hellip;)`), followed by an aggregate
line covering the whole 7-day track.

### `spody convert gps`

```
spody.exe convert gps <input.rnx> [input.rnx ...] <output.bin> <sat_id> --eop <file> --iau2006-dir <dir>
```

Converts one GPS satellite's broadcast nav track from one or more
RINEX-NAV files into a SpOdy `SPDYOUT_` reference binary in the
Earth-centred ICRF frame. Unlike GLONASS broadcast (which carries
`(r, v, a_lunisolar)` directly in PZ-90), GPS broadcast carries a
*Kepler-with-corrections* element set per record (`sqrt_A, e, i0,
Omega0, omega, M0` + `Delta_n, OmegaDot, iDot` + harmonic
corrections `Cuc/Cus/Crc/Crs/Cic/Cis`). The converter propagates
each record to its own TOC per IS-GPS-200 sect. 20.3.3.4.3
(positions) + Remondi 2004 (analytic velocity derivatives), giving
`(r, v)` at broadcast-OD precision (`~few m / few cm/s`).

This is the recommended **initial-state bootstrap** for any
SP3-based GPS validation: replaces the 4th-order Lagrange forward
derivative of 5 SP3 positions, which gave the SP3 secant rather
than the true Keplerian tangent (`|v0|` came out 7-8% off, swamping
the propagation residual at `t = 0`). With broadcast IC the day-1
RMS measures force-model error, not bootstrap artefact.

Like `convert glonass` and `convert sp3`, multi-file inputs are
concatenated into one binary with a continuous 0-anchored time
axis.

Arguments:

- `<input.rnx> [input.rnx ...]` &mdash; one or more RINEX 3.x
  GPS-or-mixed nav files in chronological order (BKG hosts a
  GPS-only `_GN.rnx` variant; the multi-GNSS `_MN.rnx` file also
  works).
- `<output.bin>` &mdash; destination `SPDYOUT_` binary (always the
  *second-to-last* positional argument).
- `<sat_id>` &mdash; the 3-character GPS PRN, e.g. `G11`
  (always the *last* positional argument).
- `--eop <file>` &mdash; path to `finals2000A.all`.
- `--iau2006-dir <dir>` &mdash; path to the directory containing
  `tab5.2{a,b,d}.txt`.

**Example.** Used by the bundled `gps_g11_validation` example to
build the 7-day broadcast IC file:

```powershell
spody.exe convert gps `
    .\examples\gps_g11_validation\BRDC00WRD_R_20240210000_01D_GN.rnx `
    ... `
    .\examples\gps_g11_validation\BRDC00WRD_R_20240270000_01D_GN.rnx `
    .\examples\gps_g11_validation\gps_g11_2024_01_21_7d_brdc.bin `
    G11 `
    --eop .\data\eop\finals2000A.all `
    --iau2006-dir .\data\iau2006
```

Each file contributes its own per-file summary line on stderr,
followed by an aggregate line covering the whole track.

### `spody convert oem`

```
spody.exe convert oem <input.oem> [input.oem ...] <output.bin>
```

Converts one or more CCSDS OEM (Orbit Ephemeris Message) text files
&mdash; the format used by the NASA/JSC public ISS trajectory
ephemerides, among many others &mdash; into a SpOdy `SPDYOUT_`
reference binary with full `(r, v)` states. This is the reference
of choice for `spody calibrate` on LEO spacecraft: operator OEMs
carry tracking-fresh states plus, usually, mass / drag-area /
event-summary comments.

Supported subset (anything else is rejected with a message naming
the offending line):

- `REF_FRAME` &mdash; `ICRF`, `EME2000` or `J2000`. Per the SPICE
  convention J2000 &equiv; ICRF, so no rotation (and no EOP data)
  is involved.
- `TIME_SYSTEM` &mdash; `UTC` (full leap-second chain + TDB
  periodic term) or `TDB`.
- Epochs in calendar ISO form `YYYY-MM-DDThh:mm:ss[.sss]` (the
  day-of-year OEM variant is not supported).
- Ephemeris rows `epoch x y z vx vy vz` in km / km/s; optional
  trailing acceleration columns are ignored, `COMMENT` lines and
  covariance blocks are skipped, multi-segment files (several
  `META_START` blocks) are fine.

Multi-file inputs are concatenated into one binary with a
continuous 0-anchored time axis; pass them in **chronological
order**. Consecutive operator releases overlap in span, so any
record that does not advance past the last written epoch is
dropped (**first file wins**) and counted in the summary line. To
calibrate against the freshest prediction of each day, convert the
files one at a time instead.

**Example.** The ISS reference used in chapter 11's bench:

```powershell
spody.exe convert oem .\ISS_OEM_2024-07-03.txt .\iss_ref.bin
```

The stderr summary reports the record count, the ET span in hours
and the number of skipped overlapping records.

**Exit codes** (sp3, glonass, gps, oem).

- `0` &mdash; conversion succeeded (or wrote zero records with a
  WARNING when the requested `<sat_id>` was absent across all
  inputs).
- `1` &mdash; missing file, parse error, frame-rotation setup
  failure, or write failure.

## `spody calibrate`

```
spody.exe calibrate <input.toml> <reference.bin> [--window <hours>]
```

Fits the time-varying density-scale table `k(t)` of chapter 11's
*Drag validation and ballistic calibration* section &mdash;
entirely inside the engine &mdash; and writes it in exactly the
format `[force_model].density_scale_file` consumes (chapter 6).
One command closes the calibration loop: convert a reference, run
`calibrate`, point the TOML at the emitted `k_nodes.csv`. The GUI
form wraps the same command behind the **Calibrate...** button
next to `density_scale_file` (chapter 5), streaming this report
into the Run-tab console and auto-filling the path on success.
The bundled `examples/iss_drag_calibration/` scenario ships the
NASA/JSC ISS OEM plus its converted reference, ready for the whole
workflow.

Requirements, checked up front:

- the TOML is a valid single-scenario input with
  `force_model.drag = true` (the whole drag stack &mdash;
  `[spacecraft.drag]`, `space_weather_file` &mdash; must be
  configured; a density scale is unobservable otherwise);
- `[simulation].et_start_s` equals the epoch of the reference's
  first record &mdash; the same 0-anchored-time contract as the
  Analysis-tab diff workflow;
- the reference is a `SPDYOUT_` binary with **full states**
  (`convert gps`, `convert glonass` or `convert oem`). SP3-derived
  binaries carry zero velocities and are rejected: each window
  re-anchors the propagation on a reference state.

Any `density_scale` / `density_scale_file` already in the TOML is
ignored (with a warning): the fit must price the raw, uncalibrated
model. The TOML's `[initial_state]` and `duration_s` are ignored
too &mdash; the calibration span is the reference span, and every
window anchors on the reference itself.

**Method.** The reference span is cut into windows of `--window`
hours (default 24; at least 6 reference samples per window, a runt
tail is folded into the last window). Per window the engine:

1. anchors the initial state on the reference record at the window
   start;
2. propagates the window twice &mdash; drag **off** and drag **on**
   at `k = 1`;
3. resamples both arcs onto the reference epochs (cubic Hermite
   between accepted steps) and projects the position residuals on
   the **in-track** axis of the reference's RIC triad;
4. solves the closed-form least squares
   `k* = -sum(I_off * dI) / sum(dI^2)` with `dI = I_on - I_off`
   (in-track drift is linear in the density scale &mdash; chapter
   11 derives why);
5. emits one node `(mjd_utc_of_window_centre, k*)`.

Windows with no usable fit are **skipped with a warning** rather
than failing the run: a drag signal below 1 mm rms in-track (widen
`--window`), or a non-positive fitted `k` (typically a manoeuvre
inside the window). The piecewise-linear evaluator bridges the gap
between the surviving nodes; if *no* window survives, the command
fails.

**Outputs.** A fresh timestamped run folder under the TOML's
`output_dir` (self-contained, like every run): the snapshot TOML,
the per-window `cal_wNNN_off.bin` / `cal_wNNN_on.bin` arcs (kept
for inspection &mdash; they are ordinary trajectory binaries the
Analysis tab can open), and `<ts>_k_nodes.csv`. The report goes to
stdout, one line per window:

```
  [ 1/ 3] t=    0.00..   24.00 h  n=  360  k=0.7931 +/- 0.0006  in-track rms 2685.1 -> 36.7 m
```

`n` is the number of fitted reference samples, `+/-` the 1-sigma
of the fit, and the two rms values are the raw (`k = 1`) and
post-fit in-track residuals over the window. The footer reports
the node file path and the **pooled k** (the constant-scale
equivalent over the whole span, i.e. what you would put in
`force_model.density_scale` if a single number is enough).

To use the result, copy (or reference) the node file from the
scenario and set:

```toml
[force_model]
density_scale_file = "k_nodes.csv"
```

Mind the node span: nodes sit at window *centres*, so a
propagation covering the full reference span extends half a window
past the first/last node on each side &mdash; the engine holds the
end values there and prints the chapter-6 clamp warning. That is
by design and harmless.

**Cost.** Two short propagations per window &mdash; a 3-day ISS
reference with 24 h windows (6 day-arcs) fits in ~35 s.

**Exit codes.**

- `0` &mdash; at least one window produced a node and the file was
  written.
- `1` &mdash; parse/validation failure, reference format error,
  a propagation failure inside a window, or every window skipped.

## `spody info`

```
spody.exe info
```

Prints the SpOdy application version, the engine library version,
the engine library's git commit hash, and the build timestamp.

No flags; no error path.

## Examples

Quickly validating every example TOML at once on PowerShell:

```powershell
Get-ChildItem .\examples -Recurse -Filter input.toml | ForEach-Object {
    .\spody.exe validate $_.FullName
}
```

Re-running the LRO 6-day example in a fresh output folder:

```powershell
.\spody.exe propagate .\examples\lro_6day\input.toml --out C:\Temp\lro_run
```

Dispatching a batch across all logical CPUs from a shell:

```powershell
# Set thread_number = N inside the TOML, then:
.\spody.exe batch .\examples\batch_demo\input.toml
```

## Output binary formats

For completeness, the three binary formats SpOdy writes are
documented here so external tools (Python notebooks, CI
pipelines) can read them without going through the GUI.

Every binary starts with the same **24-byte header**:

| Offset | Bytes | Content                                 |
|--------|-------|-----------------------------------------|
| 0      | 8     | ASCII magic (no NUL terminator)         |
| 8      | 4     | uint32 little-endian version            |
| 12     | 4     | uint32 little-endian payload metadata   |
| 16     | 8     | reserved (two zeroed uint32)            |

The three magics are `SPDYOUT_` for trajectories, `SPDYACC_` for
the per-force accelerations breakdown, and `SPDYEVT_` for events.
The payload metadata is interpreted per format:

### `SPDYOUT_` &mdash; trajectory

- Payload: `state_dim` (always `6` today, meaning `r, v`).
- Record: 7 little-endian doubles in order
  `t, x, y, z, vx, vy, vz`.
- Record size: 56 bytes.

### `SPDYACC_` &mdash; per-force accelerations

- Payload: record size in bytes (varies with the number of active
  forces).
- Record: a header double `t`, followed by per-force triples
  `ax, ay, az` for each active force in the order documented in
  the engine's source. The total `a_total` triple and the
  `eclipse_fraction` scalar are included.

### `SPDYEVT_` &mdash; events

- Payload: record size in bytes (80).
- Record: an `EVENT_KIND` uint32, a flags uint32, the event time
  as a double, and a 64-byte body whose layout depends on the
  kind (IMPACT, ECLIPSE_ENTER, ECLIPSE_EXIT).

A NumPy-based Python reader for these formats is the standard
companion to the GUI and lives in the `spody_io` package
distributed alongside the GUI bundle. Importing it from a script
gives you `read_trajectory`, `read_accelerations`, and
`read_events` functions that return structured `ndarray`s.
