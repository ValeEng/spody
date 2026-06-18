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
"""GUI-side mirror of spody-core's central-body registry.

`CentralBodySpec` carries everything a 3D plot function needs to
know about the body the satellite orbits, without hardcoding
"Moon" anywhere:

    * `name`           -- display label ("Moon", "Earth", ...)
    * `naif_id`        -- NAIF integer, used to exclude the central
                          body from third-body lists
    * `radius_km`      -- mean radius; drives camera auto-fit, the
                          central-body sphere, arrow / triad
                          default lengths
    * `mu_km3_s2`      -- standard gravitational parameter GM; used
                          by the Python orbital-elements solver
                          (vis-viva, eccentricity vector). Must
                          match what the engine used to integrate
                          the trajectory or the post-hoc Kepler
                          plots (a, e, i, raan, aop, nu) read out
                          a slightly biased orbit
    * `bf_frame_name`  -- short tag for the body-fixed frame, used
                          in triad axis labels ("PA" for Moon,
                          "ITRF" for Earth, ...)
    * `bf_orientation` -- callable `(et_s, ephemeris) -> R_icrf_to_bf`
                          giving the 3x3 rotation from ICRF to the
                          body-fixed frame at the given Ephemeris
                          Time. None when the body has no rotation
                          model exposed to the GUI yet (we degrade
                          to a static body and no PA triad).

`resolve_central_body(name)` is the only entry point: it looks up
the name (case-insensitive, "Moon" / "moon" / "MOON" all map to the
Moon spec) and returns the spec or None. Adding a new body in the
future is one entry in `_KNOWN_BODIES` + one orientation function
(or None if no rotation model yet).

Phase 1 of the central-body refactor ships with Moon as the only
registered body, matching what the engine currently supports.
Phase 2 adds Earth (GMST or IAU 2006 based orientation) and any
other bodies the engine grows orientation providers for.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np


# ----------------------------------------------------------------------
# Read spody-core constants (single source of truth)
# ----------------------------------------------------------------------
# spody-core/include/spody_const.h hosts the canonical MOON_MU,
# MOON_RADIUS, EARTH_MU, EARTH_RADIUS values used by the C engine.
# We parse the simple `#define NAME number[/* comment */]` lines here
# so the Python side never drifts from the engine. Fallback values
# below are used when the .h isn't reachable (e.g. a PyInstaller
# bundle that ships only Python sources); these duplicates are
# clearly marked and any drift would be caught by the matching tvb
# regression tests.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SPODY_CONST_H = (_REPO_ROOT / "external" / "spody-core" /
                    "include" / "spody_const.h")

# Plain-number define pattern: tolerates trailing comments and `// `
# notes after the value, but does NOT try to evaluate expressions
# (so derived macros like `ET_FROM_JD(jd)` are skipped automatically
# by the regex's failure to match).
_DEFINE_RE = re.compile(
    r"^\s*#define\s+(\w+)\s+([-+]?[\d.]+(?:[eE][-+]?\d+)?)\s*(?:/[/*].*)?$"
)


def _load_spody_const() -> dict[str, float]:
    """Parse all `#define NAME <float>` entries in spody_const.h.
    Returns an empty dict when the file isn't found (PyInstaller
    bundle, broken submodule, ...); callers fall back to
    hardcoded values."""
    if not _SPODY_CONST_H.is_file():
        return {}
    out: dict[str, float] = {}
    for line in _SPODY_CONST_H.read_text(encoding="utf-8").splitlines():
        m = _DEFINE_RE.match(line)
        if m:
            try:
                out[m.group(1)] = float(m.group(2))
            except ValueError:
                continue
    return out


_CONST = _load_spody_const()


def _const(name: str, fallback: float) -> float:
    """Look up a `#define` from spody_const.h, fall back to the
    hardcoded value when the .h isn't available. Logs nothing --
    the fallback path is expected in PyInstaller-bundled installs."""
    return _CONST.get(name, fallback)


# Re-export the central-body radius constant (used by VtkCanvas as
# the legacy default for add_central_body). When this module is the
# source of truth, vtk_canvas.MOON_RADIUS_KM stays in sync via the
# constant below.
MOON_RADIUS_KM = _const("MOON_RADIUS", 1737.4)
MOON_MU_KM3_S2 = _const("MOON_MU",     4902.8005821478)
EARTH_RADIUS_KM = _const("EARTH_RADIUS", 6378.1366)
EARTH_MU_KM3_S2 = _const("EARTH_MU",     398600.4415)


# Orientation provider type:
#   et:    Ephemeris Time, seconds past J2000 TDB
#   eph:   spopy.Ephemeris instance (the loaded de440.spody, or
#          whatever ephemeris file the run used). Bodies whose
#          orientation lives in the ephemeris (Moon librations
#          via DE440 slot 12) read it here; bodies whose
#          orientation comes from an analytic model independent
#          of the ephemeris (Earth via GMST) simply ignore it.
# Returns:
#   R_icrf_to_bf -- 3x3 numpy array (float). Columns are ICRF
#   basis vectors expressed in body-fixed coords; equivalently
#   rows are body-fixed axes expressed in ICRF. Apply as
#   `r_bf = R @ r_icrf`.
BfOrientationFn = Callable[[float, "object"], np.ndarray]


@dataclass(frozen=True)
class CentralBodySpec:
    """Immutable bundle of central-body metadata + orientation
    provider. Constructed by `resolve_central_body` from a TOML
    snapshot's `force_model.central_body` string; passed through
    `PlotContext.central_body` so every 3D plot fn reads from one
    place instead of hardcoding Moon-isms."""
    name:           str
    naif_id:        int
    radius_km:      float
    mu_km3_s2:      float
    bf_frame_name:  str
    bf_orientation: BfOrientationFn | None


# ----------------------------------------------------------------------
# Concrete orientation providers (one per supported body)
# ----------------------------------------------------------------------
def _moon_orientation(et: float, eph) -> np.ndarray:
    """ICRF -> Moon Principal Axes rotation at ET from DE440
    libration angles (slot 12). Mirrors spody-core's
    spody_bf_rotation_moon: spopy.lunar_libration_angles +
    spopy.icrf_to_moon_pa is the exact same calculation in
    Python."""
    # Local import: spopy is only needed when the spec is
    # actually exercised (i.e. when a 3D plot renders the PA
    # triad / Moon libration). Keeps headless `_plot_*` imports
    # cheap.
    from spopy import icrf_to_moon_pa
    angles = eph.lunar_libration_angles(et)
    return icrf_to_moon_pa(*angles)


# ----------------------------------------------------------------------
# Registry
# ----------------------------------------------------------------------
_KNOWN_BODIES: dict[str, CentralBodySpec] = {
    "Moon": CentralBodySpec(
        name="Moon",
        naif_id=301,
        radius_km=MOON_RADIUS_KM,
        mu_km3_s2=MOON_MU_KM3_S2,
        bf_frame_name="PA",
        bf_orientation=_moon_orientation,
    ),
}


def resolve_central_body(name: str) -> CentralBodySpec | None:
    """Look up the body name (case-insensitive match against the
    `name` field of each `CentralBodySpec`). Returns None when
    the name is empty or unknown; callers fall back to either a
    Moon default (legacy) or a no-op 3D scene."""
    if not name:
        return None
    canonical = name.strip()
    for spec in _KNOWN_BODIES.values():
        if spec.name.lower() == canonical.lower():
            return spec
    return None


def default_central_body() -> CentralBodySpec:
    """Fallback used when the snapshot TOML is missing or its
    `force_model.central_body` doesn't resolve. Returns the Moon
    spec -- matches the legacy assumption that "if no info, it
    must be the Moon", so loading bare `.bin` files (no snapshot)
    keeps rendering the Moon as before."""
    return _KNOWN_BODIES["Moon"]
