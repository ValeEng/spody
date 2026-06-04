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
"""Low-precision celestial-geometry helpers for the GUI's 3D view.

Just enough to draw an indicator -- arcminute accuracy, not validated
for any physical computation. Where the spody propagator wants real
ephemerides it goes through SPICE / DE440 via spody-core.
"""
from __future__ import annotations

import math


# Conversion constants for the analytic Sun position.
_J2000_JD       = 2451545.0
_SECONDS_PER_DAY = 86400.0
_DAYS_PER_CENT   = 36525.0


def sun_direction_j2000(et_seconds: float) -> tuple[float, float, float]:
    """Unit vector pointing from Earth to the Sun in the J2000 mean
    equator frame (≈ ICRF for visualisation purposes).

    `et_seconds` is the TDB epoch as seconds past J2000 -- the same
    convention spody uses for `simulation.et_start_s` in the TOML.

    Source: standard low-precision analytic formulae (Meeus / Curtis),
    truncated to terms in `T` -- accuracy is a few arcminutes over the
    21st century. Adequate for drawing a "Sun direction" arrow next to
    a Moon-centred orbit; the Earth→Sun ↔ Moon→Sun parallax is < 0.5°
    so we do not bother with a Moon-centric correction.
    """
    T = et_seconds / (_SECONDS_PER_DAY * _DAYS_PER_CENT)
    # Mean longitude and mean anomaly of the Sun (degrees, reduced to [0,360)).
    L_deg = (280.4665 + 36000.7698 * T) % 360.0
    g_deg = (357.5291 + 35999.0503 * T) % 360.0
    g = math.radians(g_deg)
    # Ecliptic longitude (equation of centre correction).
    lam_deg = L_deg + 1.9146 * math.sin(g) + 0.0200 * math.sin(2.0 * g)
    lam = math.radians(lam_deg)
    # Mean obliquity of the ecliptic.
    eps = math.radians(23.439 - 0.0130 * T)
    # Cartesian direction in mean-equator J2000.
    x = math.cos(lam)
    y = math.cos(eps) * math.sin(lam)
    z = math.sin(eps) * math.sin(lam)
    n = math.sqrt(x * x + y * y + z * z)
    return (x / n, y / n, z / n)
