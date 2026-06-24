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
"""CR3BP frame transformations at t = 0.

Mirrors `spody_inertial_to_cr3bp_synodic` in spody-core and exposes
its inverse, used by the GUI to swap a [initial_state] entry between
cartesian (synodic) and keplerian (primary-centered inertial) without
losing the user's input.

The synodic rotating frame has its origin at the barycenter, x-axis
along primary_1 -> primary_2, z along the orbital angular momentum.
Primaries sit at fixed positions on the x-axis:
    primary_1 at -mu2/(mu1+mu2) * L
    primary_2 at +mu1/(mu1+mu2) * L
At t = 0 the synodic axes coincide with the underlying inertial
axes; the rotation is identity and only the translation by the
primary's synodic position plus the omega-x-r velocity correction
need to be applied.
"""
from __future__ import annotations

import math
from typing import Tuple

import numpy as np


def _primary_x(mu1: float, mu2: float, L: float, primary_index: int) -> float:
    mu_tot = mu1 + mu2
    if primary_index == 2:
        return  (mu1 / mu_tot) * L
    return -(mu2 / mu_tot) * L


def inertial_to_synodic(r_primary_inertial: np.ndarray,
                        v_primary_inertial: np.ndarray,
                        mu1_km3_s2: float, mu2_km3_s2: float, L_km: float,
                        primary_index: int
                        ) -> Tuple[np.ndarray, np.ndarray]:
    """Primary-centered inertial -> synodic rotating frame, t = 0.
    Bit-equivalent to spody_inertial_to_cr3bp_synodic."""
    mu_tot = mu1_km3_s2 + mu2_km3_s2
    omega  = math.sqrt(mu_tot / (L_km ** 3))
    x_p    = _primary_x(mu1_km3_s2, mu2_km3_s2, L_km, primary_index)
    r_pi   = np.asarray(r_primary_inertial, dtype=float)
    v_pi   = np.asarray(v_primary_inertial, dtype=float)
    r_bary = np.array([r_pi[0] + x_p, r_pi[1],                 r_pi[2]])
    v_bary = np.array([v_pi[0],       v_pi[1] + omega * x_p,   v_pi[2]])
    r_syn  = r_bary.copy()
    v_syn  = np.array([
        v_bary[0] + omega * r_syn[1],
        v_bary[1] - omega * r_syn[0],
        v_bary[2],
    ])
    return r_syn, v_syn


def synodic_to_inertial(r_synodic: np.ndarray, v_synodic: np.ndarray,
                        mu1_km3_s2: float, mu2_km3_s2: float, L_km: float,
                        primary_index: int
                        ) -> Tuple[np.ndarray, np.ndarray]:
    """Synodic rotating frame -> primary-centered inertial, t = 0.
    Inverse of inertial_to_synodic. Both compose to identity to
    machine precision."""
    mu_tot = mu1_km3_s2 + mu2_km3_s2
    omega  = math.sqrt(mu_tot / (L_km ** 3))
    x_p    = _primary_x(mu1_km3_s2, mu2_km3_s2, L_km, primary_index)
    r_syn  = np.asarray(r_synodic, dtype=float)
    v_syn  = np.asarray(v_synodic, dtype=float)
    # Reverse step 2: synodic -> barycenter-inertial at t = 0.
    r_bary = r_syn.copy()
    v_bary = np.array([
        v_syn[0] - omega * r_syn[1],
        v_syn[1] + omega * r_syn[0],
        v_syn[2],
    ])
    # Reverse step 1: barycenter-inertial -> primary-centered inertial.
    r_pi = np.array([r_bary[0] - x_p, r_bary[1],                 r_bary[2]])
    v_pi = np.array([v_bary[0],       v_bary[1] - omega * x_p,   v_bary[2]])
    return r_pi, v_pi
