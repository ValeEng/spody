# tomlc99 (vendored)

This directory contains a vendored copy of [cktan/tomlc99](https://github.com/cktan/tomlc99),
a small TOML 1.0 parser in C99. It is consumed by the `spody` CLI to read
input files; it is **not** a dependency of `spody-core`.

## Source

| Field    | Value                                      |
|----------|--------------------------------------------|
| Upstream | https://github.com/cktan/tomlc99           |
| Commit   | `29076dfd095bbbbd50a3c1b2760d29f4b83e74ac` |
| Date     | 2026-01-30                                 |
| License  | MIT (see `LICENSE`)                        |

## Why vendored

- Single translation unit (`toml.c` + `toml.h`, ~1.6k LOC).
- Zero external dependencies, no build-system requirements.
- A submodule would force `--recursive` clones for two levels of indirection
  for a trivial parser.

## Files

```
toml.c     # implementation
toml.h     # public API
LICENSE    # MIT, kept upstream verbatim
```

## Updating

Replace `toml.c`, `toml.h` and `LICENSE` with the corresponding files from a
newer upstream commit, then bump the commit hash and date in this README.
No build-system changes are required as long as the upstream API stays
backward compatible.
