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

from dataclasses import dataclass
from typing import Callable

import numpy as np

# Physical constants come from the single GUI-side reading point of
# spody-core's spody_const.h (see constants.py: header parsing with
# clearly-marked fallbacks). Re-exported here because VtkCanvas and
# older call sites import them from this module.
from .constants import (
    EARTH_MU_KM3_S2,
    EARTH_RADIUS_KM,
    MOON_MU_KM3_S2,
    MOON_RADIUS_KM,
)


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


# Lazy-loaded EOP handle. None on first call (triggers load); a
# spopy.MappedEOP after a successful load; the literal False sentinel
# means "tried and failed, do not retry" (no EOP file under the
# wizard data dir, or unreadable file). Cached alongside the file's
# mtime so a wizard re-download in the same session invalidates the
# cache automatically.
_earth_eop_cache: object = None
_earth_eop_mtime: float = 0.0


def _earth_orientation(et: float, eph) -> np.ndarray:
    """ICRF -> ITRS rotation at TDB epoch `et`. Mirrors spody-core's
    spody_bf_rotation_earth: IAU 2006/2000A_R06 + IERS EOP, composed
    via spopy.icrf_to_itrs (which wraps erfa.c2t06a).

    The `eph` argument is part of the BfOrientationFn contract but
    unused here -- Earth's rotation parameters are independent of
    any planetary ephemeris.

    Returns the identity matrix (and disables further attempts) when
    the wizard's `<data_dir>/eop/finals2000A.all` is unreachable, so
    the 3D scene degrades to a non-rotating Earth instead of crashing.

    Cache invalidation: on every call we cheaply stat the EOP file and
    reload the table if the mtime has advanced. That lets a wizard
    re-download in the same GUI session refresh the rotation
    without restarting the app.
    """
    global _earth_eop_cache, _earth_eop_mtime
    from . import paths
    eop_path = paths.data_dir() / "eop" / "finals2000A.all"
    try:
        mtime = eop_path.stat().st_mtime
    except OSError:
        _earth_eop_cache = False
        _earth_eop_mtime = 0.0
        return np.eye(3)
    # File present, but cache may be stale or never built.
    if _earth_eop_cache is None or _earth_eop_cache is False or mtime != _earth_eop_mtime:
        try:
            from spopy import MappedEOP
            _earth_eop_cache = MappedEOP(eop_path)
            _earth_eop_mtime = mtime
        except (OSError, ValueError, ImportError):
            _earth_eop_cache = False
            _earth_eop_mtime = 0.0
            return np.eye(3)

    from spopy import icrf_to_itrs
    return icrf_to_itrs(et, _earth_eop_cache)


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
    "Earth": CentralBodySpec(
        name="Earth",
        naif_id=399,
        radius_km=EARTH_RADIUS_KM,
        mu_km3_s2=EARTH_MU_KM3_S2,
        bf_frame_name="ITRF",
        # Earth orientation via spopy.icrf_to_itrs (erfa.c2t06a + the
        # wizard's finals2000A.all). Mirrors spody-core's
        # spody_bf_rotation_earth at the SOFA precision floor. When
        # the EOP file is missing under <data_dir>/eop/, the provider
        # returns identity so the 3D scene still renders -- just with
        # no Earth rotation animation.
        bf_orientation=_earth_orientation,
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


def known_central_body_names() -> tuple[str, ...]:
    """Names of all registered central bodies, in declaration order.
    Used by the TOML form (toml_form.CENTRAL_BODIES) and any other
    place that needs to enumerate supported bodies for the user."""
    return tuple(_KNOWN_BODIES.keys())
