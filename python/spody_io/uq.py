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
"""Readers for the `spody uncertainty montecarlo` binaries.

Both start with the standard 24-byte preamble (see `headers.py`); the
payload is the record size in bytes and the first reserved uint32 is N,
the number of dispersed cases.

- **SPDYUQM_** (UQ Moments, v1) -- one record per output epoch of the
  nominal, 44 doubles (352 bytes):

      t         [s]      same label as the SPDYOUT_ trajectory
      n         cases with a state at t (they stop counting at impact)
      nominal   6        nominal state x_n, ICRF [km, km/s]
      bias      6        b = mean(x_i - x_n), ICRF
      cov       21       C about the mean (divisor n - 1), ICRF, lower
                         triangle by rows: C11, C21, C22, C31, C32, C33...
                         (the CCSDS OEM COVARIANCE order)
      curv_bias 3        curvilinear position bias (radius, in-plane arc,
                         out-of-plane arc) wrt the nominal [km]
      curv_cov  6        its 3x3 covariance, lower triangle [km^2]

  Undefined fields are NaN: all but t, n, nominal when n = 0; the
  covariances when n = 1; the curvilinear part where the nominal's RIC
  axes are undefined. The second moment about the nominal is
  M = C (n - 1) / n + b b^T.

- **SPDYUQC_** (UQ Clouds, v1) -- at the snapshot epochs, one record per
  case with a state there, 8 doubles (64 bytes): t, case index, d = x_i -
  x_n (6, ICRF). Ordered by snapshot, then by case. The second reserved
  uint32 is the number of snapshots requested.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .headers import HEADER_BYTES, SPODY_UQC_MAGIC, SPODY_UQM_MAGIC, _resolve_path

UQ_MOMENTS_DTYPE = np.dtype([
    ("t",         "<f8"),
    ("n",         "<f8"),
    ("nominal",   "<f8", (6,)),
    ("bias",      "<f8", (6,)),
    ("cov",       "<f8", (21,)),
    ("curv_bias", "<f8", (3,)),
    ("curv_cov",  "<f8", (6,)),
])
assert UQ_MOMENTS_DTYPE.itemsize == 352, "SPDYUQM_ record size drift"

UQ_CLOUDS_DTYPE = np.dtype([
    ("t",    "<f8"),
    ("case", "<f8"),
    ("d",    "<f8", (6,)),
])
assert UQ_CLOUDS_DTYPE.itemsize == 64, "SPDYUQC_ record size drift"


def _read(path: str | Path, magic: bytes, dtype: np.dtype):
    path = _resolve_path(path)
    with path.open("rb") as fp:
        head = fp.read(HEADER_BYTES)
        if len(head) != HEADER_BYTES or head[:8] != magic:
            raise ValueError(f"{path}: not a {magic.decode()} file")
        version, rec, res1, res2 = np.frombuffer(head[8:], dtype="<u4")
        if version != 1:
            raise ValueError(f"{path}: unsupported {magic.decode()} v{version}")
        if rec != dtype.itemsize:
            raise ValueError(f"{path}: record {rec} B, reader expects {dtype.itemsize}")
        return np.fromfile(fp, dtype=dtype), int(res1), int(res2)


def lower_to_full(tri: np.ndarray, dim: int) -> np.ndarray:
    """Expand lower-triangle-by-rows arrays (..., dim(dim+1)/2) into full
    symmetric (..., dim, dim) matrices."""
    out = np.empty(tri.shape[:-1] + (dim, dim))
    k = 0
    for a in range(dim):
        for b in range(a + 1):
            out[..., a, b] = out[..., b, a] = tri[..., k]
            k += 1
    return out


def read_uq_moments(path: str | Path) -> tuple[np.ndarray, int]:
    """Load a SPDYUQM_ file: (records with UQ_MOMENTS_DTYPE, N)."""
    arr, n_cases, _ = _read(path, SPODY_UQM_MAGIC, UQ_MOMENTS_DTYPE)
    return arr, n_cases


def read_uq_clouds(path: str | Path) -> tuple[np.ndarray, int, int]:
    """Load a SPDYUQC_ file: (records with UQ_CLOUDS_DTYPE, N, snapshots)."""
    return _read(path, SPODY_UQC_MAGIC, UQ_CLOUDS_DTYPE)
