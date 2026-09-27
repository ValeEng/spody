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
"""Earth orientation matrix (ICRF -> ITRS), twin of spody-core's
`spody_bf_rotation_earth`.

Same chain as the engine: IERS EOP interpolated linearly (xp, yp,
UT1-UTC, dX, dY); CIP X, Y and the CIO locator s from the IAU
2006/2000A series on the engine's fixed grid of SPODY_XYS_NODE_S
(hourly) nodes, with the same 4-node Lagrange cubic as
`spody_iau2006_xys_interp`; dX, dY added to X, Y; then the SOFA
iauC2t06a composition W . R3(+ERA) . Q^T. The series nodes come from
`erfa.xys06a` (SOFA's own IAU 2006/2000A X, Y, s), the composition
from `erfa.c2ixys`, `era00`, `sp00`, `pom00`, `c2tcio`.

The dX, dY corrections are part of the chain on purpose: they are
0.1-0.4 mas in current EOP, about 1-2 cm on the Earth's surface and
5 cm at GNSS radius, and the GUI writes body-fixed initial states
that the engine reads back with its own rotation.

`icrf_to_itrs_many` evaluates a whole array of epochs at once (one
series evaluation per grid node spanned, array SOFA calls); the
scalar `icrf_to_itrs` is the same function on one epoch.
"""
from __future__ import annotations

import math

import numpy as np

import erfa

from .eop import MappedEOP
from .time import (JD_J2000_TT, MJD_OFFSET, SECONDS_PER_DAY,
                   et_to_mjd_utc)


_ARCSEC2RAD = math.pi / (180.0 * 3600.0)
_MAS2RAD = _ARCSEC2RAD * 1.0e-3
_DAYS_PER_JULIAN_CY = 36525.0

# Mirror of SPODY_XYS_NODE_S in spody_const.h (the GUI passes the
# header's value explicitly through spody_gui.constants).
XYS_NODE_S = 3600.0


def _xys_interp(t_cy: np.ndarray, node_s: float
                ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """X, Y, s (rad) at TT Julian centuries `t_cy`, interpolated on the
    fixed grid of `node_s` nodes anchored at J2000 exactly as
    spody_iau2006_xys_interp does: bracket with floor(), stencil
    base-1 .. base+2, Lagrange in u = (t - t_1) / h."""
    h = node_s / SECONDS_PER_DAY / _DAYS_PER_JULIAN_CY
    base = np.floor(t_cy / h).astype(np.int64) - 1
    first = int(base.min())
    grid = np.arange(first, int(base.max()) + 4)
    nX, nY, ns = erfa.xys06a(JD_J2000_TT,
                             (grid * h) * _DAYS_PER_JULIAN_CY)
    k = base - first
    u = t_cy / h - (base + 1).astype(np.float64)
    L0 = -u * (u - 1.0) * (u - 2.0) / 6.0
    L1 = (u + 1.0) * (u - 1.0) * (u - 2.0) / 2.0
    L2 = -(u + 1.0) * u * (u - 2.0) / 2.0
    L3 = (u + 1.0) * u * (u - 1.0) / 6.0
    return tuple(L0 * n[k] + L1 * n[k + 1] + L2 * n[k + 2] + L3 * n[k + 3]
                 for n in (nX, nY, ns))


def icrf_to_itrs_many(et: np.ndarray, eop: MappedEOP | None,
                      node_s: float = XYS_NODE_S) -> np.ndarray:
    """ICRF -> ITRS rotations at the TDB epochs `et` (array, seconds
    past J2000): shape (n, 3, 3), each R with v_itrs = R @ v_icrf.

    Epochs outside the EOP table's coverage (or every epoch, when
    `eop` is None) get the identity, so the 3D scene degrades to a
    non-rotating Earth as the scalar provider always did."""
    et = np.asarray(et, dtype=np.float64).reshape(-1)
    R = np.tile(np.eye(3), (len(et), 1, 1))
    if eop is None or len(et) == 0:
        return R
    mjd_utc = np.array([et_to_mjd_utc(float(e)) for e in et])
    vals, ok = eop.interpolate_many(et, mjd_utc)
    if not ok.any():
        return R
    e = et[ok]
    xp, yp, dut1, dx, dy = vals[ok].T

    # TT taken as TDB (as the engine does: 1.7 ms at most).
    t_cy = (e / SECONDS_PER_DAY) / _DAYS_PER_JULIAN_CY
    X, Y, s = _xys_interp(t_cy, node_s)
    rc2i = erfa.c2ixys(X + dx * _MAS2RAD, Y + dy * _MAS2RAD, s)

    # UT1 as (JD_MJD_EPOCH, MJD_UT1), never one JD: a double of order
    # 2.4e6 days resolves only 40 us of UT1 (3e-9 rad of ERA).
    mjd_ut1 = mjd_utc[ok] + dut1 / SECONDS_PER_DAY
    era = erfa.era00(MJD_OFFSET, mjd_ut1)
    sp = erfa.sp00(JD_J2000_TT, e / SECONDS_PER_DAY)
    rpom = erfa.pom00(xp * _ARCSEC2RAD, yp * _ARCSEC2RAD, sp)
    R[ok] = erfa.c2tcio(rc2i, era, rpom)
    return R


def icrf_to_itrs(et: float, eop: MappedEOP | None,
                 node_s: float = XYS_NODE_S) -> np.ndarray:
    """ICRF -> ITRS rotation (3x3) at one TDB epoch `et`: v_itrs =
    R @ v_icrf. The single-epoch case of `icrf_to_itrs_many`; identity
    when `eop` is None or `et` is outside its coverage."""
    return icrf_to_itrs_many(np.array([et], dtype=np.float64), eop,
                             node_s)[0]
