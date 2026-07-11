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

"""Ephemeris-driven decoration for a `Scene3D`.

Reference triads, animated third-body markers, day/night sun
illumination and the animated body-fixed frame -- everything that
needs positions/orientations sampled over the run's timeline.

Host-agnostic by construction: no Qt, no run-folder resolution, no
settings store. The caller passes

* `ephemeris`  -- any object with `position(center_naif, target_naif,
                  et_s) -> (3,) km` (duck-typed on `spopy.Ephemeris`);
* `bf_orientation` / `orientation_for` -- callables `(et_s, ephemeris)
                  -> R_icrf_to_bf (3, 3)` per body;
* `texture_for` -- `name -> Path | None` texture lookup;
* `radius_km_by_name` -- `{name: mean radius km}` physical radii;
* `pump`       -- optional zero-arg callable invoked every 512
                  samples so a GUI host can keep its event loop
                  responsive during the sampling loops (spody passes
                  `QApplication.processEvents`).

Every function is silent on failure (unreadable ephemeris, ET outside
coverage, unknown body name): decoration is opt-in scene garnish, not
a hard contract -- the scene simply degrades to fewer props.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from .bodies import (
    BODY_ARROW_LEN_RBODY,
    BODY_COLORS,
    BODY_NAIF,
    DIST_EXPONENT,
    body_marker_radius_km,
    power_compress_positions,
)
from .scene import Scene3D

# Project-wide triad colour convention: the scene's PRIMARY frame
# (body-fixed in a BF scene, the animated BF triad in an ICRF scene)
# gets the full-saturation RGB triplet; the secondary frame gets the
# muted variant, usually with sub-1 opacity on top. Keeping the two
# palettes here means every 3D view reads the same way.
TRIAD_BRIGHT_COLORS = ((1.00, 0.30, 0.30),
                       (0.30, 0.95, 0.40),
                       (0.40, 0.55, 1.00))
TRIAD_MUTED_COLORS  = ((0.85, 0.55, 0.55),
                       (0.55, 0.80, 0.60),
                       (0.55, 0.65, 0.90))

# How often the sampling loops hand control back to the host's event
# pump: every 512 samples (bitmask test) is invisible on the hot path
# but enough to keep Windows from labelling the window "Not
# Responding".
PUMP_MASK = 0x1FF


def add_reference_triads(scene: Scene3D,
                           scene_frame: str,
                           R_icrf_to_bf: "np.ndarray | None",
                           radius_km: float,
                           bf_frame_label: str = "PA") -> None:
    """Drop the body-fixed + ICRF reference triads with the project-
    wide convention: body-fixed bright (primary frame), ICRF muted
    with sub-1 opacity (secondary). The convention is identical
    across every 3D plot so the reader always finds body-fixed in
    the full-saturation triad and inertial in the faded one,
    regardless of which frame the scene's coordinates are expressed
    in.

    `scene_frame` is 'bf' (= body-fixed) for impact-style views
    (markers placed in the body-fixed frame) and 'icrf' for
    trajectory views (points in the inertial frame). When
    `R_icrf_to_bf` is None (no per-run ephemeris, or the central
    body has no orientation provider) we draw only the scene-frame
    triad -- the secondary frame has no defined direction without
    the rotation.

    `radius_km` is the central body's mean radius -- triad arm
    lengths scale with it (2.1*R for bright, 1.8*R for muted) so
    the triads stay visually right both on the Moon (R~1737 km)
    and on Earth (R~6378 km).

    `bf_frame_label` is the short tag for the body-fixed frame
    used in axis labels ('PA' for the Moon, 'ITRF' for Earth, ...)."""
    bf_len   = 2.10 * radius_km
    icrf_len = 1.80 * radius_km

    # Accept the legacy 'pa' tag for callers that haven't switched
    # to 'bf' yet; both name the same scene (body-fixed primary).
    if scene_frame in ("bf", "pa"):
        bf_basis   = np.eye(3)
        icrf_basis = R_icrf_to_bf            # ICRF basis transported into BF
    elif scene_frame == "icrf":
        icrf_basis = np.eye(3)
        # BF basis vectors expressed in ICRF = columns of R_bf_to_icrf
        # = columns of R_icrf_to_bf.T. None when no rotation is available.
        bf_basis = R_icrf_to_bf.T if R_icrf_to_bf is not None else None
    else:
        raise ValueError(
            f"scene_frame must be 'bf' or 'icrf', got {scene_frame!r}")

    bf_tag = bf_frame_label.lower()
    if bf_basis is not None:
        scene.add_frame_triad(
            basis_in_scene=bf_basis,
            length_km=bf_len,
            colors_xyz=TRIAD_BRIGHT_COLORS,
            labels_xyz=(f"X_{bf_tag}", f"Y_{bf_tag}", f"Z_{bf_tag}"),
        )
    if icrf_basis is not None:
        scene.add_frame_triad(
            basis_in_scene=icrf_basis,
            length_km=icrf_len,
            colors_xyz=TRIAD_MUTED_COLORS,
            labels_xyz=("X_icrf", "Y_icrf", "Z_icrf"),
            opacity=0.25,
        )


def sample_positions(ephemeris: Any, center_naif: int, target_naif: int,
                       times_s: np.ndarray, et_start_s: float,
                       pump: "Callable[[], None] | None" = None
                       ) -> "np.ndarray | None":
    """Evaluate `target` relative to `center` (km, ICRF) at every
    `et_start_s + times_s[i]`. Returns the (N, 3) array, or None as
    soon as ANY sample fails (e.g. ET outside ephemeris coverage) --
    a half-sampled body would draw as a half-orbit, worse than no
    body at all. `ephemeris.position` is per-call; ~few microseconds
    each, so no batching is needed at typical sample counts."""
    n = len(times_s)
    pts = np.empty((n, 3), dtype=float)
    for i in range(n):
        try:
            pts[i] = ephemeris.position(center_naif, target_naif,
                                         et_start_s + float(times_s[i]))
        except (ValueError, IndexError):
            return None
        if pump is not None and (i & PUMP_MASK) == 0:
            pump()
    return pts


def add_third_bodies(scene: Scene3D, *,
                       ephemeris: Any,
                       central_naif: int,
                       central_radius_km: float,
                       body_names: Iterable[str],
                       times_s: np.ndarray,
                       et_start_s: float,
                       only: "set[str] | None" = None,
                       radius_km_by_name: "dict[str, float] | None" = None,
                       texture_for: "Callable[[str], Path | None] | None" = None,
                       orientation_for: "Callable[[str], Callable | None] | None" = None,
                       pump: "Callable[[], None] | None" = None) -> None:
    """Decorate the 3D scene with one animated marker per named body.

    Each body gets (1) a sphere + orbital arc at true physical scale
    on the decoration layer -- visible when the user zooms out from
    the default body-zoom view -- and (2) a fixed-length direction
    arrow anchored at the origin on the sharp layer, so the body's
    direction is ALWAYS readable even when the body itself sits
    150M km out of frame.

    `times_s` is the simulation time grid of the spacecraft
    trajectory (one entry per sample, seconds past `et_start_s`); we
    evaluate each body at exactly those instants so a shared
    animation bar moves every marker in lockstep along one timeline.

    `only`: when not None, restrict to that subset of names. An
    empty set means "show no bodies" (all toggled off); None means
    "no filter".

    `orientation_for(name)` returns the body's orientation provider
    `(et_s, ephemeris) -> R_icrf_to_bf` or None; when present the
    marker actor spins so the texture features (continents, mares)
    track the physical rotation. `texture_for(name)` returns the
    equirectangular map path or None (flat-colour glowing puck)."""
    for name in body_names:
        if not isinstance(name, str):
            continue
        if only is not None and name not in only:
            continue
        naif = BODY_NAIF.get(name)
        if naif is None:
            continue
        if naif == central_naif:
            # Defensive: a body declared both as central and third
            # should have been rejected upstream, but skip it here
            # too so a manually-tweaked input doesn't crash.
            continue
        color = BODY_COLORS.get(name, (0.85, 0.85, 0.85))
        pts_icrf = sample_positions(ephemeris, central_naif, naif,
                                     times_s, et_start_s, pump=pump)
        if pts_icrf is None:
            continue
        # Body sphere + arc at true (or compressed) scale, marked
        # `is_decoration` so the camera auto-fit ignores it.
        # Compression and marker scaling are referenced to the
        # central body's radius so the look is consistent across
        # central bodies (Moon, Earth, ...).
        pts_display = power_compress_positions(
            pts_icrf, ref_radius_km=central_radius_km) \
            if DIST_EXPONENT < 0.9999 else pts_icrf
        marker_texture_path: "Path | None" = None
        if texture_for is not None:
            try:
                marker_texture_path = texture_for(name)
            except Exception:  # noqa: BLE001 -- decoration stays silent
                marker_texture_path = None
        # Body-fixed -> ICRF rotation per sample. Bodies without a
        # provider (Sun, planets) stay un-rotated -- the texture is
        # still correct at t=0, and visual rotation is academic when
        # the body is a speck on the horizon. None on any failure ->
        # no rotation animation, no other change required.
        marker_R_seq: "np.ndarray | None" = None
        provider = orientation_for(name) if orientation_for is not None else None
        if provider is not None:
            n = len(times_s)
            try:
                marker_R_seq = np.empty((n, 3, 3), dtype=float)
                for i in range(n):
                    R_icrf_to_bf = provider(
                        et_start_s + float(times_s[i]), ephemeris)
                    # SetUserMatrix takes a model-to-world rotation; we
                    # want the body-fixed texture rotated INTO the ICRF
                    # scene, so transpose.
                    marker_R_seq[i] = np.asarray(R_icrf_to_bf,
                                                  dtype=float).T
            except Exception:  # noqa: BLE001 -- decoration stays silent
                marker_R_seq = None
        scene.add_animated_trajectory(
            pts_display, np.asarray(times_s, dtype=float),
            color=color, line_width=1.2,
            marker_radius_km=body_marker_radius_km(
                name, central_radius_km, radius_km_by_name),
            marker_texture_path=marker_texture_path,
            marker_R_bf_to_scene_sequence=marker_R_seq,
            # The Sun's own marker must never be day/night-shaded by
            # the sun-illumination light: it IS the source.
            marker_shadable=(name != "Sun"),
            is_decoration=True,
        )
        # Direction arrows are UI indicators, not far-scale geometry:
        # is_decoration=False keeps them on the SHARP top layer with
        # the central body and the orbit, sharing the tight clip
        # range instead of the wide-frustum depth imprecision the
        # body spheres tolerate.
        scene.add_animated_arrow(
            np.asarray(times_s, dtype=float), pts_icrf,
            color=color,
            length_km=BODY_ARROW_LEN_RBODY * central_radius_km,
            is_decoration=False,
        )


def add_sun_illumination(scene: Scene3D, *,
                            ephemeris: Any,
                            central_naif: int,
                            times_s: np.ndarray,
                            et_start_s: float,
                            pump: "Callable[[], None] | None" = None) -> None:
    """Install the day/night sunlight on an ICRF-centric scene: query
    the Sun's direction from the central body at every sample of the
    trajectory time grid and hand the unit vectors to
    `scene.set_sun_light`, which re-aims the light on every
    animation tick.

    Call it LAST in the scene build (set_sun_light freezes the
    lighting recipe of every actor present at call time). Silent on
    every failure mode: the scene falls back to the default
    headlight look."""
    sun_naif = BODY_NAIF["Sun"]
    if central_naif == sun_naif:
        return  # heliocentric scene: no external sun to shade from
    if len(times_s) == 0:
        return
    pts = sample_positions(ephemeris, central_naif, sun_naif,
                            times_s, et_start_s, pump=pump)
    if pts is None:
        return
    norms = np.linalg.norm(pts, axis=1)
    if np.any(norms <= 0.0):
        return
    scene.set_sun_light(np.asarray(times_s, dtype=float),
                        pts / norms[:, None])


def add_animated_body_frame(scene: Scene3D, *,
                               times_s: np.ndarray,
                               radius_km: float,
                               bf_frame_name: str = "BF",
                               ephemeris: Any = None,
                               bf_orientation: "Callable | None" = None,
                               et_start_s: float = 0.0,
                               show_icrf: bool = True,
                               show_bf: bool = True) -> None:
    """Drop the ICRF + body-fixed triads AND bind an orientation-
    driven animation on the central body, all wired into the
    playback timeline.

    For the ICRF-aligned scene:
      - ICRF triad: identity in scene coords, drawn once as a
        static muted decoration.
      - Body-fixed triad: columns of R_bf_in_icrf(t). Animated
        via `add_animated_frame_triad` -- rotates with the body's
        physical attitude (lunar libration for the Moon, IAU 2006
        rotation for Earth, ...).
      - Central body: rotated with R_bf_in_icrf(t) so the
        texture's surface features track the body-fixed axes.
        Without this the axes would visibly slide over a frozen
        surface.

    `bf_orientation` is the `(et_s, ephemeris) -> R_icrf_to_bf`
    provider; when it is None (or `ephemeris` is) we degrade to
    "just the static ICRF triad" rather than crashing.

    The design is symmetric: a future "scene_frame='pa'" mode flips
    which frame gets which R sequence (body-fixed static at
    identity, ICRF animated with R_icrf_to_bf, body identity-
    rotated), and every Scene3D API stays the same."""
    # ICRF triad is identity in this scene frame; draw it as the
    # static muted triad unless the caller hid it.
    if show_icrf:
        scene.add_frame_triad(
            basis_in_scene=np.eye(3),
            length_km=1.80 * radius_km,
            colors_xyz=TRIAD_MUTED_COLORS,
            labels_xyz=("X_icrf", "Y_icrf", "Z_icrf"),
            opacity=0.25,
        )

    if not show_bf or bf_orientation is None or ephemeris is None:
        return

    # Sample R_icrf_to_bf at each trajectory time; columns of its
    # transpose are body-fixed axes expressed in ICRF -- what
    # add_animated_frame_triad expects for an ICRF-frame scene.
    n = len(times_s)
    R_bf_in_icrf = np.empty((n, 3, 3), dtype=float)
    for i in range(n):
        try:
            R = bf_orientation(et_start_s + float(times_s[i]), ephemeris)
        except (ValueError, IndexError):
            return  # ET out of coverage; skip animation entirely
        R_bf_in_icrf[i] = np.asarray(R).T

    frame_tag = bf_frame_name.lower()
    scene.add_animated_frame_triad(
        np.asarray(times_s, dtype=float),
        R_bf_in_icrf,
        length_km=2.10 * radius_km,
        colors_xyz=TRIAD_BRIGHT_COLORS,
        labels_xyz=(f"X_{frame_tag}", f"Y_{frame_tag}", f"Z_{frame_tag}"),
    )
    # Rotate the central body with the same R sequence so the
    # surface stays glued to the body-fixed axes.
    scene.set_central_body_animated_orientation(
        np.asarray(times_s, dtype=float), R_bf_in_icrf)
