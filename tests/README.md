# Tests

End-to-end tests of the `spody` executable.

Each test feeds a TOML input file, runs `spody propagate`, and compares the
output to a stored reference. Unit tests for the core numerics live in
spody-core (`external/spody-core/tvb/`), not here.
