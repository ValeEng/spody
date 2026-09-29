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
"""Body-fixed cartesian -> geodetic coordinates on an oblate spheroid.

Twin of `spody_bf_to_geodetic` in spody-core `src/spody_math.c`, over
arrays: Bowring (1976) with the same fixed three-pass refinement of the
parametric latitude, the same polar guard and the same choice between
the two altitude forms, so the GUI reports the latitude and altitude
the engine's IMPACT / altitude events use with
`force_model.body_shape = "ellipsoid"`.
"""
from __future__ import annotations

import numpy as np


def bf_to_geodetic(r_bf_km: np.ndarray, a_km: float, inv_f: float
                   ) -> "tuple[np.ndarray, np.ndarray, np.ndarray]":
    """(lat_rad, lon_rad, alt_km) of body-fixed points `r_bf_km`,
    shape (n, 3) or (3,), on the spheroid of semi-major axis `a_km`
    and inverse flattening `inv_f`."""
    r = np.atleast_2d(np.asarray(r_bf_km, dtype=float))
    x, y, z = r[:, 0], r[:, 1], r[:, 2]
    f   = 1.0 / inv_f
    e2  = f * (2.0 - f)
    b   = a_km * (1.0 - f)
    ep2 = e2 / (1.0 - e2)
    p   = np.hypot(x, y)
    lon = np.arctan2(y, x)

    theta = np.arctan2(z * a_km, p * b)
    lat = np.zeros_like(p)
    for _ in range(3):
        st, ct = np.sin(theta), np.cos(theta)
        lat = np.arctan2(z + ep2 * b * st ** 3, p - e2 * a_km * ct ** 3)
        theta = np.arctan2((1.0 - f) * np.sin(lat), np.cos(lat))
    sl, cl = np.sin(lat), np.cos(lat)
    N = a_km / np.sqrt(1.0 - e2 * sl * sl)
    with np.errstate(divide="ignore", invalid="ignore"):
        alt = np.where(np.abs(cl) > 0.17, p / cl - N, z / sl - N * (1.0 - e2))

    polar = p < 1e-3            # within ~1 m of the spin axis
    lat = np.where(polar, np.where(z >= 0.0, np.pi / 2, -np.pi / 2), lat)
    alt = np.where(polar, np.abs(z) - b, alt)
    return lat, lon, alt
