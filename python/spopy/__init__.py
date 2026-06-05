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
"""spopy -- pure-Python re-implementation of the spody-core read-side
helpers the GUI needs for analysis and 3D visualisation.

Today the package covers two things spody-core does in C that the
GUI needs at interactive speed (no subprocess, no shared library):

- `ephemeris.Ephemeris`: reader for a `.spody` JPL binary, with body
  position queries via Chebyshev evaluation and lunar libration
  angles. Mirrors `spody_get_ephposition` / `spody_get_lunarlibration
  angles` in `external/spody-core/src/spody_ephemeris.c`.
- `rotations.icrf_to_moon_pa` / `rotations.moon_pa_to_icrf`: rotation
  matrices from libration angles, matching
  `spody_getrotmatrix_icrf2moonpa` in the same C file.

The two together let the GUI compute, for any ET instant, the ICRF
positions of Sun/Earth/Moon and the ICRF<->Moon PA rotation needed
to project impact points onto the lunar body-fixed frame for the
lat/lon impact map.

spopy is intentionally read-only and side-effect free: it never
writes files, never spawns processes, and depends only on numpy +
stdlib. Validation is done by cross-checking values against a known
SPICE / spody.exe run; the test scripts live under `tests/spopy/`
when added.
"""
from .ephemeris import (
    Ephemeris,
    NAIF_SSB, NAIF_SUN, NAIF_MERCURY, NAIF_VENUS, NAIF_EARTH,
    NAIF_MOON, NAIF_MARS, NAIF_JUPITER, NAIF_SATURN, NAIF_URANUS,
    NAIF_NEPTUNE, NAIF_PLUTO,
)
from .rotations import icrf_to_moon_pa, moon_pa_to_icrf

__all__ = [
    "Ephemeris",
    "NAIF_SSB", "NAIF_SUN", "NAIF_MERCURY", "NAIF_VENUS", "NAIF_EARTH",
    "NAIF_MOON", "NAIF_MARS", "NAIF_JUPITER", "NAIF_SATURN", "NAIF_URANUS",
    "NAIF_NEPTUNE", "NAIF_PLUTO",
    "icrf_to_moon_pa", "moon_pa_to_icrf",
]
