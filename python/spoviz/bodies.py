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

"""Visual catalog for solar-system bodies in the 3D scene.

NAIF ids and display colours for the third-body markers, plus the
sizing / distance-compression knobs the decoration layer uses to fit
the Sun (~150M km) and the Moon-scale orbit (~1700 km) into one view.

Physical body radii are deliberately NOT tabulated here: the host app
owns its numeric constants (spody reads them from `spody_const.h`
via `spody_gui.constants`) and passes them in as a plain
`{name: radius_km}` mapping where needed.
"""

from __future__ import annotations

import math

import numpy as np

BODY_NAIF: dict[str, int] = {
    "Sun":     10,    # NAIF_SUN
    "Mercury": 199,
    "Venus":   299,
    "Earth":   399,
    "Moon":    301,
    "Mars":    499,
    "Jupiter": 599,
    "Saturn":  699,
    "Uranus":  799,
    "Neptune": 899,
}

BODY_COLORS: dict[str, tuple[float, float, float]] = {
    "Sun":     (1.00, 0.90, 0.25),
    "Mercury": (0.55, 0.50, 0.45),
    "Venus":   (0.92, 0.80, 0.55),
    "Earth":   (0.30, 0.55, 0.95),
    "Moon":    (0.78, 0.78, 0.82),
    "Mars":    (0.90, 0.40, 0.30),
    "Jupiter": (0.85, 0.70, 0.50),
    "Saturn":  (0.90, 0.80, 0.60),
    "Uranus":  (0.65, 0.85, 0.90),
    "Neptune": (0.30, 0.40, 0.85),
}

# Power-law distance compression knob. 1.0 = identity (true physical
# distances). Since Scene3D uses Cesium-style multi-frustum rendering
# (two layered renderers with independent depth scopes), bodies can
# stay at their real 150M-km / 384k-km positions without z-fighting
# the central body. Set < 1.0 if you want them squeezed closer for a
# more compact view (see `power_compress_positions`).
DIST_EXPONENT = 1.0

# Body radii follow the same opt-in: True = physical km, False =
# log-compressed for a "didactic" comparable-size layout. Multi-
# frustum rendering makes True usable -- Sun (~696k km) renders in
# its own depth scope so it doesn't blow the central body's clipping.
USE_TRUE_RADII       = True
RADIUS_PER_DECADE_KM = 600.0
RADIUS_BASE_KM       = 150.0

# Direction-arrow length in central-body radii. 3 * R_body puts the
# arrow tip just outside a typical low-altitude orbit so the arrow
# is fully visible at the default body-zoom but doesn't dwarf the
# orbit. Multiplied by the central body's radius_km at call time so
# the scale follows the body (Earth: ~19000 km, Moon: ~5200 km).
BODY_ARROW_LEN_RBODY = 3.0


def power_compress_positions(positions_km: np.ndarray,
                              ref_radius_km: float,
                              exponent: float = DIST_EXPONENT
                              ) -> np.ndarray:
    """Compress positions radially while preserving direction:
        r_out = ref * (r / ref)^exponent

    `exponent` in (0, 1) compresses; smaller = more squish. The body
    surface (r = ref) stays at r=ref, and 0 stays at 0. Used to fold
    Earth (~221 R_moon) and Sun (~86354 R_moon) into the same scene
    as an LRO-scale orbit (~1 R_moon)."""
    r = np.linalg.norm(positions_km, axis=1)
    safe_r = np.maximum(r, 1e-12)
    new_r  = ref_radius_km * (safe_r / ref_radius_km) ** exponent
    ratio  = np.where(r > 0, new_r / safe_r, 0.0)
    return positions_km * ratio[:, None]


def body_marker_radius_km(name: str,
                             ref_radius_km: float,
                             radius_km_by_name: "dict[str, float] | None"
                             ) -> float:
    """Display radius for a third-body marker. Two modes selected at
    module load by `USE_TRUE_RADII`:

    * True: return the tabulated physical radius (km), so Sun -> ~696k
      km, Earth -> ~6371 km, etc. Correct relative to the bodies'
      physical distances but invisible at low-orbit zoom unless the
      camera is way out.
    * False: log-compress to `RADIUS_BASE_KM + decades *
      RADIUS_PER_DECADE_KM`, clamped to >= RADIUS_BASE_KM. Order
      is preserved; everything fits comfortably alongside the
      central body.

    `ref_radius_km` is the central body's mean radius (e.g. Moon
    1737 km, Earth 6371 km), used as the log reference so the
    compressed sizes look comparable across central bodies.
    `radius_km_by_name` is the caller's physical-radius table
    ({name: mean radius km}); spody feeds the values parsed from
    `spody_const.h` so the markers match the engine's constants.

    Unknown / un-tabulated body names always fall back to
    `RADIUS_BASE_KM` so a marker still draws."""
    r_phys = (radius_km_by_name or {}).get(name)
    if r_phys is None:
        return RADIUS_BASE_KM
    if USE_TRUE_RADII:
        return r_phys
    if r_phys <= ref_radius_km:
        return RADIUS_BASE_KM
    decades = math.log10(r_phys / ref_radius_km)
    return RADIUS_BASE_KM + decades * RADIUS_PER_DECADE_KM
