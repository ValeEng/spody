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

"""Lagrange interpolation with its derivative.

Twin of `spody_interp_lagrange` in spody-core `src/spody_interp.c`:
the same Lagrange form (Abramowitz & Stegun 1964, eq. 25.2.2), the
derivative written as a sum of products so it stays finite on a node,
and the same order of operations, so every output element is the
double the C function returns for the same nodes and abscissa.

The C function takes one abscissa; this one takes an array of them,
each with its own node window, and runs the C loops element-wise over
that array. numpy applies each IEEE operation per element in the
order written, so vectorising over queries changes no bit.
"""
from __future__ import annotations

import numpy as np


def interp_lagrange(xs: np.ndarray, ys: np.ndarray, x: np.ndarray
                    ) -> tuple[np.ndarray, np.ndarray]:
    """Lagrange polynomial through n nodes, and its derivative, at x.

    `xs` (M, n): the nodes of each of the M queries, distinct.
    `ys` (M, n, dim): the values at those nodes.
    `x`  (M,): the abscissae. No clamping: outside the nodes it
    extrapolates, as the C function does.
    Returns (y, dy), each (M, dim).
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    x = np.asarray(x, dtype=float)
    m_q, n = xs.shape
    dim = ys.shape[2]
    y = np.zeros((m_q, dim))
    dy = np.zeros((m_q, dim))
    for j in range(n):
        l = np.ones(m_q)
        for m in range(n):
            if m != j:
                l *= (x - xs[:, m]) / (xs[:, j] - xs[:, m])
        dl = np.zeros(m_q)
        for m in range(n):
            if m == j:
                continue
            term = 1.0 / (xs[:, j] - xs[:, m])
            for q in range(n):
                if q != j and q != m:
                    term *= (x - xs[:, q]) / (xs[:, j] - xs[:, q])
            dl += term
        yj = ys[:, j, :]
        y += l[:, None] * yj
        dy += dl[:, None] * yj
    return y, dy
