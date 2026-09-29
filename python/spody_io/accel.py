# Copyright 2026 ValeEng
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Reader for the SPDYACC_ per-force acceleration breakdown binary.

One record = one `ForceBreakdown` C struct (v3: 408 bytes on x86_64
with `SPODY_FM_MAX_THIRD = 8`; v2: 384 and v1: 360, the same layout
cut short).
Layout mirrors `spody_forcemodels.h`:

    double t                       sim time [s]
    double acc_total[3]            sum of all forces  [km/s^2]
    double acc_2body[3]            central two-body
    double acc_sphericalharmonics[3]
    double acc_thirdbody_total[3]  sum across third bodies
    int32  n_third                 # populated entries below
    (4 bytes padding to 8-byte align)
    double acc_thirdbody[8][3]     per-body breakdown (unused slots = 0)
    double acc_srp[3]
    double acc_drag[3]             atmospheric drag (zero when drag off)
    double eclipse_fraction        1=full sun, 0=full umbra
    double acc_solidtides[3]       solid-body tide (v2+; zero when off)
    double acc_relativity[3]       general relativity, Schwarzschild (v3)

An older file is returned with the v3 dtype and the fields it lacks at
zero: it was written before those forces existed, so zero is what it
modelled.
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
_V1_FIELDS = {
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
}
ACCEL_DTYPE_V1 = np.dtype(_V1_FIELDS, align=True)
ACCEL_DTYPE_V2 = np.dtype({
    "names": _V1_FIELDS["names"] + ["acc_solidtides"],
    "formats": _V1_FIELDS["formats"] + [("<f8", 3)],
}, align=True)
ACCEL_DTYPE = np.dtype({
    "names": list(ACCEL_DTYPE_V2.names) + ["acc_relativity"],
    "formats": _V1_FIELDS["formats"] + [("<f8", 3), ("<f8", 3)],
}, align=True)
assert ACCEL_DTYPE_V1.itemsize == 360, (
    f"ForceBreakdown v1 size drift: dtype is {ACCEL_DTYPE_V1.itemsize}, expected 360"
)
assert ACCEL_DTYPE_V2.itemsize == 384, (
    f"ForceBreakdown v2 size drift: dtype is {ACCEL_DTYPE_V2.itemsize}, expected 384"
)
assert ACCEL_DTYPE.itemsize == 408, (
    f"ForceBreakdown size drift: dtype is {ACCEL_DTYPE.itemsize}, expected 408"
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
        dtypes = {1: ACCEL_DTYPE_V1, 2: ACCEL_DTYPE_V2, 3: ACCEL_DTYPE}
        if version not in dtypes:
            raise ValueError(f"{path}: unsupported accelerations format v{version}")
        dtype = dtypes[version]
        if record_size != dtype.itemsize:
            raise ValueError(
                f"{path}: record_size={record_size} but reader expects "
                f"{dtype.itemsize} for v{version} -- spody-core ABI may have changed"
            )
        data = np.fromfile(fp, dtype=dtype)
    if version == 3:
        return data
    out = np.zeros(len(data), dtype=ACCEL_DTYPE)
    for name in dtype.names:
        out[name] = data[name]
    return out
