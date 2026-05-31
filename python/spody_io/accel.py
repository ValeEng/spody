"""Reader for the SPDYACC_ per-force acceleration breakdown binary.

One record = one `ForceBreakdown` C struct (360 bytes on x86_64 with
`SPODY_FM_MAX_THIRD = 8`). Layout mirrors `spody_forcemodels.h`:

    double t                       sim time [s]
    double acc_total[3]            sum of all forces  [km/s^2]
    double acc_2body[3]            central two-body
    double acc_sphericalharmonics[3]
    double acc_thirdbody_total[3]  sum across third bodies
    int32  n_third                 # populated entries below
    (4 bytes padding to 8-byte align)
    double acc_thirdbody[8][3]     per-body breakdown (unused slots = 0)
    double acc_srp[3]
    double acc_drag[3]             placeholder today
    double eclipse_fraction        1=full sun, 0=full umbra
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .headers import SPODY_ACC_MAGIC, _resolve_path, read_header

# Mirror of SPODY_FM_MAX_THIRD in external/spody-core/include/spody_forcemodels.h
SPODY_FM_MAX_THIRD = 8

# align=True asks NumPy to insert the same padding the C compiler does
# between `n_third` (int32) and the following `acc_thirdbody[8][3]`
# (which requires 8-byte alignment). The resulting itemsize must equal
# the sizeof(ForceBreakdown) recorded in the header at write time.
ACCEL_DTYPE = np.dtype({
    "names": [
        "t",
        "acc_total",
        "acc_2body",
        "acc_sphericalharmonics",
        "acc_thirdbody_total",
        "n_third",
        "acc_thirdbody",
        "acc_srp",
        "acc_drag",
        "eclipse_fraction",
    ],
    "formats": [
        "<f8",
        ("<f8", 3),
        ("<f8", 3),
        ("<f8", 3),
        ("<f8", 3),
        "<i4",
        ("<f8", (SPODY_FM_MAX_THIRD, 3)),
        ("<f8", 3),
        ("<f8", 3),
        "<f8",
    ],
}, align=True)
assert ACCEL_DTYPE.itemsize == 360, (
    f"ForceBreakdown size drift: dtype is {ACCEL_DTYPE.itemsize}, expected 360"
)


def read_accelerations(path: str | Path) -> np.ndarray:
    """Load a SPDYACC_ binary into a structured NumPy array.

    Returns an `ndarray` with `dtype = ACCEL_DTYPE`. Cross-check that
    the header's record size matches `ACCEL_DTYPE.itemsize` so a
    spody-core ABI change (more third bodies, new force) is detected
    instead of silently misread.
    """
    path = _resolve_path(path)
    with path.open("rb") as fp:
        version, record_size = read_header(fp, SPODY_ACC_MAGIC)
        if version != 1:
            raise ValueError(f"{path}: unsupported accelerations format v{version}")
        if record_size != ACCEL_DTYPE.itemsize:
            raise ValueError(
                f"{path}: record_size={record_size} but reader expects "
                f"{ACCEL_DTYPE.itemsize} -- spody-core ABI may have changed"
            )
        return np.fromfile(fp, dtype=ACCEL_DTYPE)
