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
"""Rotation matrices between the ICRF (J2000-aligned) frame and the
Moon Principal Axes (PA) body-fixed frame, parametrised by the lunar
mantle Euler 313 angles `(phi, theta, psi)`.

Mirrors `spody_getrotmatrix_icrf2moonpa` and
`spody_getrotmatrix_moonpa2icrf` in
[external/spody-core/src/spody_ephemeris.c](../../../external/spody-core/src/spody_ephemeris.c).

Convention (DE440 lunar mantle, Park et al. 2021):

    C = Rz(psi) * Rx(theta) * Rz(phi)

with all angles in radians, such that

    r_PA = C @ r_ICRF.

The closed-form expansion of the three elementary rotations is hard-
coded below (instead of building it from three numpy matmuls) so the
output is bit-identical with the C reference -- handy when cross-
checking spopy against spody.exe.

Lunar libration angles for any ET come from
`spopy.ephemeris.Ephemeris.lunar_libration_angles(et)`; feed the
return value straight into `icrf_to_moon_pa`.
"""
from __future__ import annotations

import numpy as np


def icrf_to_moon_pa(phi: float, theta: float, psi: float) -> np.ndarray:
    """3x3 rotation matrix mapping ICRF components to Moon PA
    components: r_PA = R @ r_ICRF.

    Parameters
    ----------
    phi, theta, psi : float
        Euler 313 angles in radians, in the DE440 lunar mantle
        convention. Typically obtained from
        `spopy.ephemeris.Ephemeris.lunar_libration_angles(et)`.

    Returns
    -------
    R : ndarray, shape (3, 3)
    """
    cphi, sphi = np.cos(phi),   np.sin(phi)
    cth,  sth  = np.cos(theta), np.sin(theta)
    cpsi, spsi = np.cos(psi),   np.sin(psi)

    # Expansion of Rz(psi) * Rx(theta) * Rz(phi); see C reference for
    # the same row/col convention.
    return np.array([
        [ cpsi * cphi - spsi * cth * sphi,
          cpsi * sphi + spsi * cth * cphi,
          spsi * sth],
        [-spsi * cphi - cpsi * cth * sphi,
         -spsi * sphi + cpsi * cth * cphi,
          cpsi * sth],
        [ sth  * sphi,
         -sth  * cphi,
          cth],
    ])


def moon_pa_to_icrf(phi: float, theta: float, psi: float) -> np.ndarray:
    """Inverse of `icrf_to_moon_pa`: r_ICRF = R @ r_PA. Returned matrix
    is the transpose of the forward one (the forward is orthogonal by
    construction)."""
    return icrf_to_moon_pa(phi, theta, psi).T


if __name__ == "__main__":
    # Self-test: round-trip + orthogonality + agreement with a
    # numpy-built reference for a few sample angles.
    import sys

    failed = []
    def _check(name: str, cond: bool, extra: str = "") -> None:
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {name}" + (f" -- {extra}" if extra else ""))
        if not cond:
            failed.append(name)

    print("rotations.py self-test")

    # 1. Zero angles -> identity.
    R = icrf_to_moon_pa(0.0, 0.0, 0.0)
    _check("zero angles -> identity",
           np.allclose(R, np.eye(3), atol=1e-15), f"R=\n{R}")

    # 2. R is orthogonal (R @ R.T == I) for arbitrary angles.
    rng = np.random.default_rng(seed=7)
    for trial in range(20):
        a, b, c = rng.uniform(-np.pi, np.pi, size=3)
        R = icrf_to_moon_pa(a, b, c)
        if not np.allclose(R @ R.T, np.eye(3), atol=1e-12):
            _check(f"orthogonality trial {trial}", False,
                   f"angles={a, b, c}")
            break
    else:
        _check("orthogonality on 20 random angle triples", True)

    # 3. Round-trip: moon_pa_to_icrf(angles) @ icrf_to_moon_pa(angles) == I.
    for trial in range(20):
        a, b, c = rng.uniform(-np.pi, np.pi, size=3)
        if not np.allclose(moon_pa_to_icrf(a, b, c) @ icrf_to_moon_pa(a, b, c),
                           np.eye(3), atol=1e-12):
            _check(f"round-trip trial {trial}", False)
            break
    else:
        _check("round-trip on 20 random angle triples", True)

    # 4. Compose 3 elementary rotations the textbook way and compare.
    def _rz(t):
        c, s = np.cos(t), np.sin(t)
        return np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]])
    def _rx(t):
        c, s = np.cos(t), np.sin(t)
        return np.array([[1, 0, 0], [0, c, s], [0, -s, c]])

    for trial in range(20):
        a, b, c = rng.uniform(-np.pi, np.pi, size=3)
        R_text = _rz(c) @ _rx(b) @ _rz(a)
        if not np.allclose(R_text, icrf_to_moon_pa(a, b, c), atol=1e-12):
            _check(f"matches textbook composition trial {trial}", False)
            break
    else:
        _check("matches textbook Rz(psi)*Rx(theta)*Rz(phi) on 20 triples",
               True)

    print()
    if failed:
        print(f"FAILED: {len(failed)} check(s): {failed}")
        sys.exit(1)
    print("OK -- all checks passed")
