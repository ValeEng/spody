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

The five commands are:

| Command           | Purpose                                      |
|-------------------|----------------------------------------------|
| `validate`        | parse + sanity-check an input TOML, no run   |
| `propagate`       | run a single-case simulation                 |
| `batch`           | run a multi-case sweep                       |
| `convert`         | data-file conversions (ephemeris today)      |
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

The umbrella command for data-file conversions. Today only the
ephemeris conversion is implemented.

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
