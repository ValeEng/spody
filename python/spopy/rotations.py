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
"""Rotation matrices between the ICRF (J2000-aligned) frame and

- the Moon Principal Axes (PA) body-fixed frame (below), and
- the RIC frame of a reference state (`ric_to_icrf` / `icrf_to_ric`,
  twins of `spody_getrotmatrix_ric2icrf` / `_icrf2ric` in
  spody-core `spody_math.c`, bit-identical: same operations, same
  order, same thresholds).

Moon PA is parametrised by the lunar mantle Euler 313 angles
`(phi, theta, psi)`.

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

import math

import numpy as np


def ric_to_icrf(r, v) -> np.ndarray:
    """3x3 rotation matrix R with x_ICRF = R @ x_RIC for the reference
    state (r [km], v [km/s]), central-inertial ICRF. Columns are

        r_hat = r / |r|
        i_hat = c_hat x r_hat          (in-track, not v unless circular)
        c_hat = (r x v) / |r x v|      (cross-track)

    Rotation only, no omega x r (the RTN convention of CCSDS
    covariances). Twin of `spody_getrotmatrix_ric2icrf`: scalar float
    arithmetic in the C operation order (not np.linalg.norm / np.cross,
    which sum differently), so the matrix is bit-identical.

    Raises ValueError when |r| < 1e-9 km or |r x v| < 1e-12 (axes
    undefined), the thresholds of the C function."""
    min_r_km = 1.0e-9      # private thresholds, verbatim from spody_math.c
    min_h = 1.0e-12
    r0, r1, r2 = (float(x) for x in r)
    v0, v1, v2 = (float(x) for x in v)
    rn = math.sqrt(r0 * r0 + r1 * r1 + r2 * r2)
    if rn < min_r_km:
        raise ValueError("reference position is at the origin; RIC undefined")
    h0 = r1 * v2 - r2 * v1
    h1 = r2 * v0 - r0 * v2
    h2 = r0 * v1 - r1 * v0
    hn = math.sqrt(h0 * h0 + h1 * h1 + h2 * h2)
    if hn < min_h:
        raise ValueError("reference r and v are parallel (r x v = 0); "
                         "RIC undefined")
    rh0, rh1, rh2 = r0 / rn, r1 / rn, r2 / rn
    ch0, ch1, ch2 = h0 / hn, h1 / hn, h2 / hn
    ih0 = ch1 * rh2 - ch2 * rh1
    ih1 = ch2 * rh0 - ch0 * rh2
    ih2 = ch0 * rh1 - ch1 * rh0
    return np.array([[rh0, ih0, ch0],
                     [rh1, ih1, ch1],
                     [rh2, ih2, ch2]])


def icrf_to_ric(r, v) -> np.ndarray:
    """Inverse of `ric_to_icrf`: x_RIC = R @ x_ICRF, the exact
    transpose. Twin of `spody_getrotmatrix_icrf2ric`."""
    return ric_to_icrf(r, v).T.copy()


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


def icrf_to_orbit_plane(pole_icrf, r_pert_km, v_pert_kms) -> np.ndarray:
    """3x3 rotation matrix mapping ICRF components to Ely's orbit-plane
    (OP) frame: r_OP = R @ r_ICRF. Use `R.T` for the inverse.

    Twin of the `SPODY_FRAME_ORBIT_PLANE` branch in
    [src/sim_setup.c](../../src/sim_setup.c) -- keep the two in
    lockstep. Frozen lunar orbits (ELFO) are defined in this frame,
    NOT against the lunar equator: for the Moon the two poles sit
    ~6.8 deg apart, so the same inclination denotes two different
    orbits.

        z_op = (r_pert x v_pert) / |r_pert x v_pert|
        x_op = (pole x z_op) / |pole x z_op|
        y_op = z_op x x_op

    Inputs are explicit vectors rather than an ephemeris handle so this
    stays dependency-free and callable from both the GUI form and
    offline analysis.

    Parameters
    ----------
    pole_icrf : array_like, shape (3,)
        Central body's north pole in ICRF components -- the +Z row of
        the ICRF->body-fixed matrix (`icrf_to_moon_pa(...)[2]`).
    r_pert_km, v_pert_kms : array_like, shape (3,)
        State of the perturbing body relative to the central body, at
        the epoch the frame is anchored to (instantaneous, not
        averaged -- matches the paper's definition and the engine).

    Returns
    -------
    R : ndarray, shape (3, 3)
        Rows are (x_op, y_op, z_op) in ICRF components.

    Raises
    ------
    ValueError
        If the perturber state is degenerate (collinear r and v), or
        if its orbit plane is parallel to the central body's equator,
        which leaves the x-axis undefined.
    """
    pole = np.asarray(pole_icrf, dtype=float)
    z = np.cross(np.asarray(r_pert_km, dtype=float),
                 np.asarray(v_pert_kms, dtype=float))
    zn = np.linalg.norm(z)
    if not zn > 0.0:
        raise ValueError("degenerate perturber orbit normal "
                         "(collinear position and velocity)")
    z /= zn

    x = np.cross(pole, z)
    xn = np.linalg.norm(x)
    # |pole x z| is the sine of the pole separation: ~0.118 for the
    # Moon/Earth pair. Near zero the perturber orbits in the central
    # body's equatorial plane and x_op is undefined.
    if not xn > np.sqrt(np.finfo(float).eps):
        raise ValueError("perturber orbit plane is parallel to the "
                         "central body equator; OP x-axis undefined")
    x /= xn

    return np.stack((x, np.cross(z, x), z))


def bf_angular_velocity_icrf(icrf_to_bf, et: float, step_s: float,
                             earth_rate: float | None = None) -> np.ndarray:
    """Angular velocity of a body-fixed frame in ICRF (rad/s) at `et`.

    Twin of `spody_bf_angular_velocity_icrf` in spody-core
    (spody_forcemodels.c) -- keep the two in lockstep. It is the omega
    of the transport theorem, v_icrf = R_bf2icrf v_rot + omega x r,
    behind `frame = "central_body_fixed_rotating"`.

    `icrf_to_bf(t)` returns the ICRF -> body-fixed matrix. With
    `earth_rate` set (the Earth: EARTH_ROT_RATE_RADPS) omega is that
    rate about the body-fixed z axis, as the GNSS converters take it;
    otherwise a central difference over +-`step_s`
    (SPODY_BF_OMEGA_FD_STEP_S), read off the skew matrix dR/dt R^T with
    R = body-fixed -> ICRF. Same formula as the C; the summation order
    differs, so agreement is to the last bits, not bit for bit.
    """
    if earth_rate is not None:
        return earth_rate * np.asarray(icrf_to_bf(et), dtype=float)[2]
    rp = np.asarray(icrf_to_bf(et + step_s), dtype=float).T
    rm = np.asarray(icrf_to_bf(et - step_s), dtype=float).T
    r0 = np.asarray(icrf_to_bf(et), dtype=float).T
    w = (rp - rm) / (2.0 * step_s) @ r0.T
    return 0.5 * np.array([w[2, 1] - w[1, 2],
                           w[0, 2] - w[2, 0],
                           w[1, 0] - w[0, 1]])


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
