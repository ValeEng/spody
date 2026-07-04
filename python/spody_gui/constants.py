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
"""Single GUI-side reading point for spody-core's physical constants.

spody-core/include/spody_const.h is the canonical home of every
numeric constant the C engine integrates with (GM values, body radii,
the CR3BP Earth-Moon separation, time-scale offsets). This module
parses the simple `#define NAME <number>` lines out of that header so
the Python side can never drift from the engine, and provides
clearly-marked fallback values for installs where the header is not
on disk.

Header lookup order:
  1. Dev checkout:      <repo>/external/spody-core/include/spody_const.h
  2. PyInstaller bundle: <_MEIPASS>/spody-core/spody_const.h
     (shipped via the `datas` list in spody_gui.spec, so bundled
     installs read the exact header the engine was built from)

Every physical constant used anywhere in spody_gui must go through
`const(name, fallback)` (or the named module attributes below) --
never hardcode the number at the point of use.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


def _candidate_header_paths() -> tuple[Path, ...]:
    paths: list[Path] = []
    # Dev checkout: python/spody_gui/constants.py -> repo root is two up.
    repo_root = Path(__file__).resolve().parents[2]
    paths.append(repo_root / "external" / "spody-core"
                 / "include" / "spody_const.h")
    # PyInstaller bundle: datas land under sys._MEIPASS (one-folder
    # mode: <app>/_internal). See spody_gui.spec.
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / "spody-core" / "spody_const.h")
    return tuple(paths)


# Plain-number define pattern: tolerates trailing comments after the
# value and a parenthesized value like `(-32.184)`. Expression macros
# (`DEG2RAD (PI/180.0)`, `ET_FROM_JD(jd) ...`) simply fail to match
# and are skipped -- by design, they are derivable in Python.
_DEFINE_RE = re.compile(
    r"^\s*#define\s+(\w+)\s+\(?\s*([-+]?[\d.]+(?:[eE][-+]?\d+)?)\s*\)?"
    r"\s*(?:/[/*].*)?$"
)


def _load_spody_const() -> tuple[dict[str, float], Path | None]:
    """Parse all `#define NAME <float>` entries from the first header
    candidate that exists. Returns ({}, None) when no header is found;
    callers then run on the fallback values."""
    for path in _candidate_header_paths():
        if not path.is_file():
            continue
        out: dict[str, float] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            m = _DEFINE_RE.match(line)
            if m:
                try:
                    out[m.group(1)] = float(m.group(2))
                except ValueError:
                    continue
        return out, path
    return {}, None


_CONST, HEADER_PATH = _load_spody_const()


def const(name: str, fallback: float) -> float:
    """Look up a `#define` from spody_const.h, falling back to the
    hardcoded value when the header isn't available. The fallback path
    is expected only when a bundle predates the header shipping."""
    return _CONST.get(name, fallback)


# ----------------------------------------------------------------------
# Named constants. Fallback values mirror spody_const.h; any drift is
# caught in dev checkouts, where the header always wins.
# ----------------------------------------------------------------------
MOON_RADIUS_KM   = const("MOON_RADIUS",   1737.4)
MOON_MU_KM3_S2   = const("MOON_MU",       4902.8005821478)
EARTH_RADIUS_KM  = const("EARTH_RADIUS",  6378.1366)
EARTH_MU_KM3_S2  = const("EARTH_MU",      398600.4415)

# CR3BP primary-pair mean separation (mirrors lookup_cr3bp_pair in
# src/toml_input.c, which reads the same #define).
EARTH_MOON_DISTANCE_KM = const("EARTH_MOON_DISTANCE_KM", 384400.0)

# Mean radii [km] for every body the engine knows (SPICE pck00011 via
# spody_const.h). Used for the third-body markers in the 3D scene and
# any display feature that wants proportionally-scaled bodies.
BODY_RADIUS_KM: dict[str, float] = {
    "Mercury": const("MERCURY_RADIUS", 2440.53),
    "Venus":   const("VENUS_RADIUS",   6051.8),
    "Earth":   EARTH_RADIUS_KM,
    "Moon":    MOON_RADIUS_KM,
    "Mars":    const("MARS_RADIUS",    3376.20),
    "Jupiter": const("JUPITER_RADIUS", 71492.0),
    "Saturn":  const("SATURN_RADIUS",  60268.0),
    "Uranus":  const("URANUS_RADIUS",  25559.0),
    "Neptune": const("NEPTUNE_RADIUS", 24764.0),
    "Sun":     const("SUN_RADIUS",     695700.0),
}
