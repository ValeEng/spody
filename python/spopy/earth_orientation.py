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
"""Earth orientation matrix (ICRF -> ITRS) via SOFA bindings.

Mirrors `spody_bf_rotation_earth` in spody-core: IAU 2006 precession +
IAU 2000A_R06 nutation + ERA + polar motion, composed per the SOFA
iauC2t06a chain (W . R3(+ERA) . Q with Q in the GCRS->CIRS orientation).

Implementation note: spody-core's C code re-derives the SOFA chain by
hand from the IERS Conventions 2010 tables (Sec. 5). Here we delegate
to `erfa.c2t06a` which is the official Python wrapper of the same SOFA
routine -- numerically identical to mas precision, in 3 MB of compiled
deps instead of ~500 lines of pure-Python series evaluator.

CIP corrections (dX, dY) from the EOP file are intentionally NOT
applied here. They are sub-mas and invisible in the GUI's 3D scene
(at Earth's radius 6378 km, 1 mas ~ 30 micron). The C engine uses
them because its rotation drives the gravity-field point evaluation,
where mas-level pointing errors compound into m-level orbit errors.
"""
from __future__ import annotations

import math

import numpy as np

import erfa

from .eop import MappedEOP, _et_to_mjd_utc, _JD_J2000_TT, _MJD_OFFSET


_ARCSEC2RAD = math.pi / (180.0 * 3600.0)


def icrf_to_itrs(et: float, eop: MappedEOP) -> np.ndarray:
    """ICRF (=GCRS, sub-mm) -> ITRS rotation at TDB epoch `et`.

    Returns the 3x3 numpy matrix R such that
        v_itrs = R @ v_icrf
    matching spody-core's `spody_bf_rotation_earth(R_icrf_to_bf, ...)`.

    When `eop` is None or `et` falls outside its coverage, returns the
    identity matrix so the 3D scene degrades to a non-rotating Earth.
    """
    if eop is None:
        return np.eye(3)
    sample = eop.interpolate(et)
    if sample is None:
        return np.eye(3)
    xp_arcsec, yp_arcsec, dut1_sec, _dx_mas, _dy_mas = sample

    # Split JD parts. The (jd1, jd2) split keeps microsecond precision
    # in jd2 over multi-decade epochs.
    tt_jd2 = et / 86400.0
    mjd_utc = _et_to_mjd_utc(et)
    mjd_ut1 = mjd_utc + dut1_sec / 86400.0
    ut1_jd2 = mjd_ut1 + _MJD_OFFSET - _JD_J2000_TT

    xp_rad = xp_arcsec * _ARCSEC2RAD
    yp_rad = yp_arcsec * _ARCSEC2RAD

    R = erfa.c2t06a(
        _JD_J2000_TT, tt_jd2,
        _JD_J2000_TT, ut1_jd2,
        xp_rad, yp_rad,
    )
    return np.asarray(R)
