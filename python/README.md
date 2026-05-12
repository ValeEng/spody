# SpOdy GUI (placeholder)

This directory will host the Python GUI that generates TOML input files for
the `spody` executable and visualises its output.

Design intent (Patran/Nastran-style):
- the GUI never calls C code directly
- it produces a TOML on disk and invokes `spody propagate input.toml --out results/`
- it then reads back the output files (CSV / binary) and renders plots/tables

This decoupling means the same `spody` binary serves desktop, batch HPC, and
(eventually) a web backend with no changes -- only the front-end wrapping
differs.
