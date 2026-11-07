# Introduction

SpOdy is a desktop application for propagating spacecraft and debris
orbits around the Moon or the Earth, and in the Earth&ndash;Moon
circular restricted three-body problem. You give it a mission scenario
described in a plain-text file &mdash; central body, initial state,
force model, integrator settings &mdash; and it produces a trajectory
you can plot, compare against a reference, sweep across thousands of
variant cases in a batch run, or disperse statistically in a Monte
Carlo run.

The application is shipped as a single self-contained folder. There is
**no installer**, **no Python environment to configure**, and **no
network registration** beyond the one-time download of the public
ephemeris and gravity-model data the wizard handles for you on first
launch. Extract the archive, run `spody-gui.exe`, and you are minutes
away from your first propagation.

## Who this manual is for

This manual assumes you have received a SpOdy bundle &mdash; a folder
containing `spody-gui.exe` plus its supporting files &mdash; and want
to understand how to use it. No prior orbital-mechanics expertise is
required to follow the chapters in order, although a working knowledge
of state vectors, Keplerian elements, and inertial frames will help
you make the most of the analysis features.

If you are looking for the development guide (building from source,
extending the integrator, contributing to the C core) you want a
different document &mdash; that one ships separately.

## What SpOdy does

At its core, SpOdy is a numerical orbit propagator with a desktop
front-end. The split is deliberate: the integration physics lives in
a fast C engine (`spody.exe`) that runs on its own as a command-line
tool, and the graphical interface (`spody-gui.exe`) is a thin
PySide6 application that orchestrates the inputs, dispatches the
runs, and visualises the outputs. The two halves talk only through
files &mdash; **the GUI never links C code directly**, which makes
the same C engine usable from scripts, notebooks or a CI pipeline
without any GUI surface area.

The engine integrates the orbit numerically (adaptive Dormand&ndash;Prince
integrators of order 5 and 8) under a force model you assemble: the
central body's spherical-harmonic gravity field, the point-mass
attraction of the Sun, Moon and planets from the JPL DE440 ephemeris,
solar radiation pressure with the shadow of every body you model,
atmospheric drag (NRLMSISE-00), Earth albedo and infrared, solid-body
tides and the relativistic correction. It detects impacts, eclipses
and altitude crossings, converts public orbit products (SP3, RINEX
broadcast, CCSDS OEM, TLE/OMM elements) into reference trajectories,
fits the atmospheric density to a reference orbit, and propagates the
uncertainty of a scenario by Monte Carlo.

In one window you can:

- **Build** the simulation input through a structured form with live
  TOML preview, per-field range checks, and inline validation against
  the C parser, eliminating the need to remember any TOML syntax.
- **Run** a single propagation, a parameter sweep across thousands
  of cases, or a Monte Carlo, streaming the engine's terminal output
  into an embedded pane; re-run any subset of the cases of an earlier
  batch.
- **Analyse** the binary outputs through a catalogue of plots
  (state vectors, orbit shape, classical orbital elements,
  per-force acceleration breakdown, eclipse fractions, event
  timelines, impact maps, altitude bands, Monte Carlo spread and
  clouds) in both 2D matplotlib and embedded 3D VTK canvases.
- **Compare** two runs side-by-side through dedicated difference
  plots, with automatic cubic-Hermite interpolation when the two
  output grids do not align sample-by-sample.

## What is in the bundle

When you extract the distribution archive you get a folder structured
like this:

```
spody-gui/
├── spody-gui.exe          ← the desktop application (this manual's subject)
├── spody.exe              ← the C engine spody-gui drives
├── data/                  ← populated by the setup wizard on first launch
├── examples/              ← starter TOML scenarios you can open
└── _internal/             ← bundled Python interpreter + libraries
```

The `_internal/` folder is an implementation detail of the
PyInstaller bundling and you should treat it as opaque: never edit,
delete, or rename files inside it. Everything you interact with is
either the two executables, the example scenarios, or the
wizard-populated `data/` folder.

## How to read this manual

The first four chapters are sequential: chapter 2 covers installation
and the first launch, chapter 3 the setup wizard that downloads the
data, chapter 4 the main window and the run-time workflow. From
chapter 5 onward the organisation switches to reference-style:

| Chapter | Subject |
|---|---|
| 5, 6 | the input form and the schema of every TOML key, with the physics of each force model |
| 7 | batch runs |
| 8, 9 | the Analysis tab and the catalogue of every plot |
| 10 | reference frames and orbital elements |
| 11 | comparing two trajectories, and calibrating drag against a reference |
| 12 | the `spody.exe` command line and the binary file formats |
| 13 | troubleshooting |
| 14 | uncertainty propagation by Monte Carlo |
| 15 | validation and benchmarks of the whole software |
| 16, 17 | glossary and references |

Checks of a single feature are given next to the feature; benchmarks
of the whole software against measured orbits are collected in
chapter 15. Works cited as *(Author year)* are listed in chapter 17.

A consistent set of typographical conventions runs through the
chapters:

- `monospaced text` denotes literal file paths, code snippets, and
  things you type or that the application displays verbatim;
- <kbd>Ctrl</kbd>+<kbd>R</kbd> shows a keyboard shortcut;
- **Bold** highlights UI controls (menu items, button labels, panel
  headings) as they appear in the application;
- *italics* introduce a new term the chapter will then use.

Throughout the manual you will also encounter call-outs in the
margin:

> Quotation blocks are used for verbatim excerpts of file content,
> log messages, or longer passages from the application's
> interface.

Note, Tip, and Warning admonitions flag information that is, in order,
useful background, a practical shortcut, and something you should not
skip without thinking.
