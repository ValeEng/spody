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
"""Classical Keplerian element <-> Cartesian state conversions.

Mirrors `spody_kepler.c` in spody-core (bit-equivalent for the
forward direction). Used by the GUI to swap a [initial_state] entry
between cartesian and keplerian without losing the user's input.

All angles are in radians at the function boundary; the caller
converts to / from degrees.
"""
from __future__ import annotations

import math
from typing import Tuple

import numpy as np

_TWO_PI = 2.0 * math.pi


def _wrap_pi(a: float) -> float:
    a = math.fmod(a, _TWO_PI)
    if a >  math.pi: a -= _TWO_PI
    if a < -math.pi: a += _TWO_PI
    return a


def kepler_solve_E(mean_anom_rad: float, ecc: float) -> float:
    """Solve M = E - e*sin(E) for E (Newton iteration, Danby seed).
    Matches spody_kepler_solve_E to machine precision for e in [0, 1)."""
    M = _wrap_pi(mean_anom_rad)
    E = M + ecc * math.sin(M)
    for _ in range(30):
        f  = E - ecc * math.sin(E) - M
        fp = 1.0 - ecc * math.cos(E)
        dE = f / fp
        E -= dE
        if abs(dE) < 1.0e-14:
            break
    return E


def mean_to_true_anom(mean_anom_rad: float, ecc: float) -> float:
    """Mean -> true anomaly (rad). Returns nu in (-pi, pi]."""
    E = kepler_solve_E(mean_anom_rad, ecc)
    sqrt_1me2 = math.sqrt(1.0 - ecc * ecc)
    return math.atan2(sqrt_1me2 * math.sin(E), math.cos(E) - ecc)


def true_to_mean_anom(true_anom_rad: float, ecc: float) -> float:
    """Inverse: true -> mean anomaly (rad). Returns M in (-pi, pi]."""
    nu = _wrap_pi(true_anom_rad)
    E  = 2.0 * math.atan2(
            math.sqrt(1.0 - ecc) * math.sin(0.5 * nu),
            math.sqrt(1.0 + ecc) * math.cos(0.5 * nu))
    M  = E - ecc * math.sin(E)
    return _wrap_pi(M)


def keplerian_to_cartesian(sma_km: float, ecc: float,
                           inc_rad: float, raan_rad: float,
                           argp_rad: float, true_anom_rad: float,
                           mu_km3_s2: float
                           ) -> Tuple[np.ndarray, np.ndarray]:
    """Classical elements -> Cartesian state in the same inertial
    frame the elements reference. Identical math to
    spody_keplerian_to_cartesian; returns (r_km, v_kms) as length-3
    numpy arrays."""
    p     = sma_km * (1.0 - ecc * ecc)
    cnu   = math.cos(true_anom_rad)
    snu   = math.sin(true_anom_rad)
    r_mag = p / (1.0 + ecc * cnu)
    r_pf  = np.array([r_mag * cnu, r_mag * snu, 0.0])
    mu_p  = math.sqrt(mu_km3_s2 / p)
    v_pf  = np.array([-mu_p * snu, mu_p * (ecc + cnu), 0.0])

    co, so = math.cos(raan_rad), math.sin(raan_rad)
    ci, si = math.cos(inc_rad),  math.sin(inc_rad)
    cw, sw = math.cos(argp_rad), math.sin(argp_rad)
    R = np.array([
        [ co*cw - so*sw*ci, -co*sw - so*cw*ci,  so*si ],
        [ so*cw + co*sw*ci, -so*sw + co*cw*ci, -co*si ],
        [             sw*si,             cw*si,     ci ],
    ])
    return R @ r_pf, R @ v_pf


def cartesian_to_keplerian(r_km, v_kms, mu_km3_s2: float) -> dict:
    """Cartesian state -> classical orbital elements.

    Accepts either a single state pair (`r_km` of shape (3,), `v_kms`
    of shape (3,)) or a batch (`(N, 3)` for both) and broadcasts the
    arithmetic over the leading axis. Returns a dict whose values
    have the same leading shape as the input -- 0-d for a single
    state, 1-d for a batch.

    Keys: `sma_km`, `ecc`, `inc_rad`, `raan_rad`, `argp_rad`,
    `true_anom_rad`. Degenerate cases (equatorial, circular) collapse
    the undefined angle into zero, folding the rotation into the
    well-defined sibling so a switch (or a round-trip) produces a
    deterministic set of values.

    Vectorisation matters for the GUI analysis plots, which call this
    once per loaded trajectory and need elements at every recorded
    sample. The scalar caller (TOML form swap) gets the same code
    path with shape (3,)."""
    r = np.asarray(r_km,  dtype=float)
    v = np.asarray(v_kms, dtype=float)

    r_mag = np.linalg.norm(r, axis=-1)
    v_mag = np.linalg.norm(v, axis=-1)
    if np.any(r_mag <= 0.0):
        raise ValueError("position magnitude is zero")

    # Specific angular momentum h = r x v -- normal to the orbit plane.
    h     = np.cross(r, v)
    h_mag = np.linalg.norm(h, axis=-1)

    # Eccentricity vector e = (v x h)/mu - r_hat.
    e_vec = np.cross(v, h) / mu_km3_s2 - r / r_mag[..., None]
    e_mag = np.linalg.norm(e_vec, axis=-1)

    # Vis-viva: a = 1 / (2/r - v^2/mu).
    sma = 1.0 / (2.0 / r_mag - v_mag * v_mag / mu_km3_s2)

    # Inclination from h_z / |h|, clipped against rounding excursions
    # that would NaN-out arccos.
    safe_h = np.where(h_mag > 0.0, h_mag, 1.0)
    cos_i  = np.clip(h[..., 2] / safe_h, -1.0, 1.0)
    inc    = np.arccos(cos_i)

    # Node line n = z_hat x h = (-h_y, h_x, 0).
    n     = np.stack((-h[..., 1], h[..., 0], np.zeros_like(h_mag)), axis=-1)
    n_mag = np.linalg.norm(n, axis=-1)

    EPS = 1.0e-10
    equatorial = n_mag < EPS
    circular   = e_mag < EPS

    # RAAN = acos(n_x / |n|); quadrant flip from sign of n_y. Folded
    # into 0 when the orbit is equatorial (RAAN undefined).
    safe_n   = np.where(equatorial, 1.0, n_mag)
    cos_O    = np.clip(n[..., 0] / safe_n, -1.0, 1.0)
    raan     = np.arccos(cos_O)
    raan     = np.where(n[..., 1] < 0, _TWO_PI - raan, raan)
    raan     = np.where(equatorial, 0.0, raan)

    # AOP = acos((n.e)/(|n||e|)); quadrant flip from sign of e_z.
    denom_w = np.where(equatorial | circular, 1.0, n_mag * e_mag)
    cos_w   = np.clip(np.einsum("...j,...j->...", n, e_vec) / denom_w,
                      -1.0, 1.0)
    argp    = np.arccos(cos_w)
    argp    = np.where(e_vec[..., 2] < 0, _TWO_PI - argp, argp)
    argp    = np.where(equatorial | circular, 0.0, argp)

    # True anomaly nu = acos((e.r)/(|e||r|)); flip from sign of r.v.
    denom_nu = np.where(circular, 1.0, e_mag * r_mag)
    cos_nu   = np.clip(np.einsum("...j,...j->...", e_vec, r) / denom_nu,
                       -1.0, 1.0)
    nu       = np.arccos(cos_nu)
    rdotv    = np.einsum("...j,...j->...", r, v)
    nu       = np.where(rdotv < 0, _TWO_PI - nu, nu)
    nu       = np.where(circular, 0.0, nu)

    return {
        "sma_km":         sma,
        "ecc":            e_mag,
        "inc_rad":        inc,
        "raan_rad":       raan,
        "argp_rad":       argp,
        "true_anom_rad":  nu,
    }
