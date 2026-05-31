"""Reader for the SPDYOUT_ trajectory binary.

One record = 7 doubles in this order:
    t  [s past simulation start]
    x, y, z   [km]
    vx, vy, vz [km/s]

All values are in the central-body inertial frame the propagation ran
in (ICRF-aligned for v0).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .headers import SPODY_BIN_MAGIC, _resolve_path, read_header

TRAJ_DTYPE = np.dtype([
    ("t",  "<f8"),
    ("x",  "<f8"),
    ("y",  "<f8"),
    ("z",  "<f8"),
    ("vx", "<f8"),
    ("vy", "<f8"),
    ("vz", "<f8"),
])
assert TRAJ_DTYPE.itemsize == 56, "trajectory record size drift"


def read_trajectory(path: str | Path) -> np.ndarray:
    """Load a SPDYOUT_ binary into a structured NumPy array.

    Returns an `ndarray` with `len = N records` and `dtype = TRAJ_DTYPE`.
    Access columns by name (`arr["t"]`, `arr["x"]`, etc.) or convert to
    plain float64 columns via `arr.view(("<f8", 7))`.
    """
    path = _resolve_path(path)
    with path.open("rb") as fp:
        version, state_dim = read_header(fp, SPODY_BIN_MAGIC)
        if version != 1:
            raise ValueError(f"{path}: unsupported trajectory format v{version}")
        if state_dim != 6:
            raise ValueError(
                f"{path}: state_dim={state_dim}, reader supports only 6"
            )
        return np.fromfile(fp, dtype=TRAJ_DTYPE)
