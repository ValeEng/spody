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

"""Shared 3D scene decoration for the VTK canvas views.

Reference triads, third-body markers (positions from the run's
ephemeris via spopy), animated PA/body-fixed decoration, and the
ICRF->body-fixed rotation resolver. Pure helpers: this module never
imports the plot modules, so any view (traj 3D, events 3D, CR3BP 3D,
future ones) can build on it without cycles.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from matplotlib import colormaps as mpl_colormaps
from PySide6.QtWidgets import QApplication

from .. import constants
from ..central_bodies import default_central_body, resolve_central_body
from ..toml_io import read_toml
from ..vtk_canvas import MOON_RADIUS_KM, VtkCanvas
from .context import PlotContext, resolve_run_context


def add_reference_triads(canvas: VtkCanvas,
                           scene_frame: str,
                           R_icrf_to_bf: np.ndarray | None,
                           radius_km: float = MOON_RADIUS_KM,
                           bf_frame_label: str = "PA") -> None:
    """Drop the body-fixed + ICRF reference triads with the project-
    wide convention: body-fixed bright (primary frame), ICRF muted
    with sub-1 opacity (secondary). The convention is identical
    across every 3D plot so the reader always finds body-fixed in
    the full-saturation triad and inertial in the faded one,
    regardless of which frame the scene's coordinates are expressed
    in.

    `scene_frame` is 'bf' (= body-fixed) for the impact 3D view
    (markers placed in the body-fixed frame) and 'icrf' for the
    trajectory 3D plots (trajectory points in the inertial frame).
    When `R_icrf_to_bf` is None (no per-run ephemeris, or the
    central body has no orientation provider) we draw only the
    scene-frame triad -- the secondary frame has no defined direction
    without the rotation.

    `radius_km` is the central body's mean radius -- triad arm
    lengths scale with it (2.1*R for bright, 1.8*R for muted) so
    the triads stay visually right both on the Moon (R~1737 km)
    and on Earth (R~6378 km).

    `bf_frame_label` is the short tag for the body-fixed frame
    used in axis labels ('PA' for the Moon, 'ITRF' for Earth, ...).
    Comes from `CentralBodySpec.bf_frame_name`."""
    bf_len   = 2.10 * radius_km
    icrf_len = 1.80 * radius_km
    bf_colors = ((1.00, 0.30, 0.30),
                 (0.30, 0.95, 0.40),
                 (0.40, 0.55, 1.00))
    icrf_colors = ((0.85, 0.55, 0.55),
                   (0.55, 0.80, 0.60),
                   (0.55, 0.65, 0.90))

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
        canvas.add_frame_triad(
            basis_in_scene=bf_basis,
            length_km=bf_len,
            colors_xyz=bf_colors,
            labels_xyz=(f"X_{bf_tag}", f"Y_{bf_tag}", f"Z_{bf_tag}"),
        )
    if icrf_basis is not None:
        canvas.add_frame_triad(
            basis_in_scene=icrf_basis,
            length_km=icrf_len,
            colors_xyz=icrf_colors,
            labels_xyz=("X_icrf", "Y_icrf", "Z_icrf"),
            opacity=0.25,
        )


# ----------------------------------------------------------------------
# Third-body decoration for the 3D orbit views.
#
# Reads `force_model.third_bodies` from the run-folder TOML snapshot,
# evaluates each body's position (relative to the Moon, ICRF, km) at
# every trajectory sample time via spopy, applies a power-law
# distance compression so Earth (~384k km) and Sun (~150M km) both
# fit alongside the LRO-scale orbit (~1700 km radius), and drops
# each body as its own animated trajectory so the shared playback
# bar moves them in parallel with the spacecraft marker.
#
# Distance compression:
#   r_display(km) = R_moon * (r / R_moon)^_DIST_EXPONENT
# with _DIST_EXPONENT = 0.3. Numerical examples at this exponent:
#   Earth (~221 R_moon):   221^0.3   ~=  5.0 R_moon ~=  8700 km
#   Sun   (~86354 R_moon): 86354^0.3 ~= 28.5 R_moon ~= 49500 km
# Power-law (vs log) gives a Sun/Earth visual ratio of ~5.7 instead
# of ~1.8, which restores some sense of "Sun is much farther than
# Earth" while still keeping both visible at the camera's auto-fit.
# Directions are preserved bit-exact; only the radial magnitude is
# squeezed.
#
# Body radii are similarly compressed (log10 of physical radius vs
# R_moon, plus a small offset so even Mercury reads as a recognisable
# spot). All bodies end up smaller than the Moon visually, with the
# physical ordering preserved (Mercury < Mars < Venus < Earth < ...
# < Jupiter < Sun). See `body_marker_radius_km`.
# ----------------------------------------------------------
_BODY_NAIF: dict[str, int] = {
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

_BODY_COLORS: dict[str, tuple[float, float, float]] = {
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

# Physical mean radii in km, read from spody_const.h through the
# constants module (same values the engine uses). Used to derive the
# displayed marker radius via `body_marker_radius_km`; also handy for
# any future feature that wants a body texture at proportional scale.
_BODY_RADIUS_PHYS_KM: dict[str, float] = dict(constants.BODY_RADIUS_KM)

# Power-law distance compression knob. 1.0 = identity (true physical
# distances). Now that VtkCanvas uses Cesium-style multi-frustum
# rendering (two layered renderers with independent depth scopes),
# we can keep bodies at their real 150M-km / 384k-km positions
# without z-fighting the Moon. Set < 1.0 if you want them squeezed
# closer for a more compact view (see `_power_compress_positions`).
_DIST_EXPONENT = 1.0

# Body radii follow the same opt-in: True = physical km, False =
# log-compressed for a "didactic" comparable-size layout. Multi-
# frustum rendering makes True usable -- Sun (~696k km) renders in
# its own depth scope so it doesn't blow the Moon's clipping.
_USE_TRUE_RADII       = True
_RADIUS_PER_DECADE_KM = 600.0
_RADIUS_BASE_KM       = 150.0

# Direction-arrow length in central-body radii. 3 * R_body puts the
# arrow tip just outside a typical low-altitude orbit so the arrow
# is fully visible at the default body-zoom but doesn't dwarf the
# orbit. Multiplied by ctx.central_body.radius_km at call time so
# the scale follows the body (Earth: ~19000 km, Moon: ~5200 km).
_BODY_ARROW_LEN_RBODY = 3.0


def _power_compress_positions(positions_km: np.ndarray,
                                ref_radius_km: float = MOON_RADIUS_KM,
                                exponent: float = _DIST_EXPONENT
                                ) -> np.ndarray:
    """Compress positions radially while preserving direction:
        r_out = ref * (r / ref)^exponent

    `exponent` in (0, 1) compresses; smaller = more squish. The Moon
    surface (r = ref) stays at r=ref, and 0 stays at 0. Used to fold
    Earth (~221 R_moon) and Sun (~86354 R_moon) into the same scene
    as the LRO orbit (~1 R_moon)."""
    r = np.linalg.norm(positions_km, axis=1)
    safe_r = np.maximum(r, 1e-12)
    new_r  = ref_radius_km * (safe_r / ref_radius_km) ** exponent
    ratio  = np.where(r > 0, new_r / safe_r, 0.0)
    return positions_km * ratio[:, None]


def body_marker_radius_km(name: str,
                             ref_radius_km: float = MOON_RADIUS_KM
                             ) -> float:
    """Display radius for a third-body marker. Two modes selected at
    module load by `_USE_TRUE_RADII`:

    * True: return the tabulated physical radius (km), so Sun -> ~696k
      km, Earth -> ~6371 km, etc. Correct relative to the bodies'
      physical distances but invisible at low-orbit zoom unless the
      camera is way out.
    * False: log-compress to `_RADIUS_BASE_KM + decades *
      _RADIUS_PER_DECADE_KM`, clamped to >= _RADIUS_BASE_KM. Order
      is preserved; everything fits comfortably alongside the
      central body.

    `ref_radius_km` is the central body's mean radius (e.g. Moon
    1737 km, Earth 6371 km). Used as the log reference so the
    compressed sizes look comparable across central bodies.

    Unknown / un-tabulated body names always fall back to
    `_RADIUS_BASE_KM` so a marker still draws."""
    r_phys = _BODY_RADIUS_PHYS_KM.get(name)
    if r_phys is None:
        return _RADIUS_BASE_KM
    if _USE_TRUE_RADII:
        return r_phys
    if r_phys <= ref_radius_km:
        return _RADIUS_BASE_KM
    decades = math.log10(r_phys / ref_radius_km)
    return _RADIUS_BASE_KM + decades * _RADIUS_PER_DECADE_KM


def add_third_bodies(canvas: VtkCanvas, ctx: "PlotContext",
                       times_s: np.ndarray,
                       only: set[str] | None = None) -> None:
    """Decorate the 3D scene with one animated marker per body in
    `force_model.third_bodies`. Body-agnostic: the central body's
    NAIF id (from ctx.central_body) is the only thing that decides
    "relative to whom" body positions are queried. Silent on every
    failure mode (missing snapshot, ephemeris unreadable, unknown
    body name) -- opt-in scene decoration, not a hard contract.

    `times_s` is the simulation time grid of the spacecraft trajectory
    (one entry per sample, seconds). We evaluate each body at exactly
    those instants so the shared animation bar moves every marker in
    lockstep along the same timeline.
    """
    if ctx is None:
        return
    info = resolve_run_context(ctx.path)
    if info is None or info["ephemeris_path"] is None:
        return
    central_naif = ctx.central_body.naif_id
    # `resolve_run_context` doesn't expose third_bodies today; re-read
    # the snapshot toml directly to avoid bloating its return shape
    # for a single caller.
    try:
        cfg = read_toml(info["toml_path"])
    except (OSError, ValueError):
        return
    bodies_raw = cfg.get("force_model", {}).get("third_bodies", [])
    if not isinstance(bodies_raw, list) or not bodies_raw:
        return

    from spopy import Ephemeris
    try:
        eph = Ephemeris(str(info["ephemeris_path"]))
    except (OSError, ValueError):
        return

    et_start  = float(info["et_start_s"])
    n         = len(times_s)
    # spopy.position is per-call; ~few microseconds each, so 60 samples
    # x 3 bodies = ~180 calls is negligible. No need to batch.
    for name in bodies_raw:
        if not isinstance(name, str):
            continue
        # `only`: when not None, restrict to the user-checked subset.
        # An empty set means "show no bodies" (toggle all off in the
        # Scene options dialog); None means "no filter" (legacy call).
        if only is not None and name not in only:
            continue
        naif = _BODY_NAIF.get(name)
        if naif is None:
            continue
        if naif == central_naif:
            # Defensive: a body declared both as central and third
            # would have been rejected by sim_setup, but skip it
            # here too so a manually-tweaked snapshot doesn't crash.
            continue
        color = _BODY_COLORS.get(name, (0.85, 0.85, 0.85))
        pts_icrf = np.empty((n, 3), dtype=float)
        # Periodic event-loop pump for the cursor + status to stay
        # responsive when N is large (e.g. multi-day batch with a
        # dense trajectory): batched every 512 samples, cheap enough
        # to be invisible on the hot path but enough to keep
        # Windows from labelling the window "Not Responding".
        for i in range(n):
            try:
                pts_icrf[i] = eph.position(central_naif, naif,
                                            et_start + float(times_s[i]))
            except (ValueError, IndexError):
                # Single bad sample (e.g. ET outside ephemeris coverage):
                # skip the whole body rather than draw a half-orbit.
                pts_icrf = None  # type: ignore[assignment]
                break
            if (i & 0x1FF) == 0:
                QApplication.processEvents()
        if pts_icrf is None:
            continue
        # 1) Body sphere + orbital arc at true (or compressed) scale
        # so the body itself is in the scene -- visible if the user
        # zooms out from the default body-zoom view. Marked
        # `is_decoration` so the camera auto-fit ignores it.
        # Compression and marker scaling are referenced to the
        # central body's radius so the look is consistent across
        # bodies (Moon, Earth, ...).
        pts_display = _power_compress_positions(
            pts_icrf, ref_radius_km=ctx.central_body.radius_km) \
            if _DIST_EXPONENT < 0.9999 else pts_icrf
        # Look up the wizard-managed texture asset for this body, if
        # the user has downloaded it. When present the 3rd-body marker
        # is drawn as a textured sphere instead of the flat-color
        # glowing puck -- so e.g. the Moon stays recognisable in the
        # Earth-centric scene even at its true ~384,000 km distance.
        # paths.data_dir() is the same QSettings-backed lookup the
        # wizard uses, so this stays in sync with the user's choice.
        from . import assets, paths
        try:
            marker_texture_path = assets.central_body_texture_path(
                paths.data_dir(), name)
        except Exception:
            marker_texture_path = None
        # Body-fixed -> ICRF rotation per sample, sourced from the
        # central-body registry's orientation provider. When the body
        # has one (Earth ITRS via spopy.icrf_to_itrs, Moon PA via
        # libration angles), the marker actor spins in the 3D scene
        # so the texture features (continents, mares) track the
        # physical rotation. Bodies without a provider (Sun, planets)
        # stay un-rotated -- the texture orientation is still correct
        # at t=0, and visual rotation is academic when the body is a
        # spec on the horizon. None on lookup failure -> no rotation
        # animation, no other code change required.
        marker_R_seq: np.ndarray | None = None
        body_spec = resolve_central_body(name)
        if (body_spec is not None
                and body_spec.bf_orientation is not None):
            try:
                marker_R_seq = np.empty((n, 3, 3), dtype=float)
                for i in range(n):
                    R_icrf_to_bf = body_spec.bf_orientation(
                        et_start + float(times_s[i]), eph)
                    # SetUserMatrix takes a model-to-world rotation; we
                    # want the body-fixed texture rotated INTO the ICRF
                    # scene, so transpose.
                    marker_R_seq[i] = np.asarray(R_icrf_to_bf,
                                                  dtype=float).T
            except Exception:
                marker_R_seq = None
        canvas.add_animated_trajectory(
            pts_display, np.asarray(times_s, dtype=float),
            color=color, line_width=1.2,
            marker_radius_km=body_marker_radius_km(
                name, ref_radius_km=ctx.central_body.radius_km),
            marker_texture_path=marker_texture_path,
            marker_R_bf_to_scene_sequence=marker_R_seq,
            is_decoration=True,
        )
        # 2) Fixed-length direction arrow anchored at the origin so
        # the body's direction is ALWAYS visible at the default
        # Moon-zoom regardless of how far the body actually is. The
        # arrow rotates each tick to track the true body direction.
        # is_decoration=False puts it on the SHARP top layer with the
        # Moon/orbit -- arrows are UI indicators, not far-scale
        # geometry, so they should share the tight clip range to
        # avoid the wide-frustum depth imprecision the body spheres
        # tolerate.
        canvas.add_animated_arrow(
            np.asarray(times_s, dtype=float), pts_icrf,
            color=color,
            length_km=_BODY_ARROW_LEN_RBODY * ctx.central_body.radius_km,
            is_decoration=False,
        )


def add_animated_pa_decoration(canvas: VtkCanvas, ctx: "PlotContext",
                                  times_s: np.ndarray,
                                  show_icrf: bool = True,
                                  show_pa:   bool = True) -> None:
    """Drop the ICRF + body-fixed triads AND bind a libration-driven
    orientation on the central body, all wired into the playback
    bar.

    For the ICRF-aligned scene:
      - ICRF triad: identity in scene coords, drawn once as a
        static muted decoration.
      - Body-fixed triad: columns of R_bf_in_icrf(t). Animated
        via `add_animated_frame_triad` -- rotates with the body's
        physical attitude (lunar libration for Moon, GMST/IAU for
        Earth in the future, ...).
      - Central body: rotated with R_bf_in_icrf(t) so the
        texture's surface features track the body-fixed axes.
        Without this the axes would visibly slide over a frozen
        surface.

    Body-agnostic: the triad axis labels and the orientation
    provider come from `ctx.central_body.bf_frame_name` /
    `bf_orientation`. When the spec has no orientation provider
    (or the ephemeris is unreachable) we degrade to "just the
    static ICRF triad" rather than crashing.

    The design is symmetric: when we eventually add a "scene_frame=
    'pa'" mode the call site flips which frame gets which R
    sequence (body-fixed static at identity, ICRF animated with
    R_icrf_to_bf, body identity-rotated), and every VtkCanvas API
    stays the same."""
    body = ctx.central_body if ctx is not None else default_central_body()
    # ICRF triad is identity in this scene frame; draw it as the
    # static muted triad unless the user hid it.
    if show_icrf:
        icrf_colors = ((0.85, 0.55, 0.55),
                       (0.55, 0.80, 0.60),
                       (0.55, 0.65, 0.90))
        canvas.add_frame_triad(
            basis_in_scene=np.eye(3),
            length_km=1.80 * body.radius_km,
            colors_xyz=icrf_colors,
            labels_xyz=("X_icrf", "Y_icrf", "Z_icrf"),
            opacity=0.25,
        )

    if not show_pa or ctx is None or body.bf_orientation is None:
        return
    info = resolve_run_context(ctx.path)
    if info is None or info["ephemeris_path"] is None:
        return

    # Local import: spopy only needed when an orientation provider
    # actually exercises the ephemeris.
    from spopy import Ephemeris
    try:
        eph = Ephemeris(str(info["ephemeris_path"]))
    except (OSError, ValueError):
        return

    # Sample R_icrf_to_bf at each trajectory time; columns of its
    # transpose are body-fixed axes expressed in ICRF -- what
    # add_animated_frame_triad expects for an ICRF-frame scene.
    et_start = float(info["et_start_s"])
    n = len(times_s)
    R_bf_in_icrf = np.empty((n, 3, 3), dtype=float)
    for i in range(n):
        try:
            R = body.bf_orientation(et_start + float(times_s[i]), eph)
        except (ValueError, IndexError):
            return  # ET out of coverage; skip animation entirely
        R_bf_in_icrf[i] = np.asarray(R).T

    pa_colors = ((1.00, 0.30, 0.30),
                 (0.30, 0.95, 0.40),
                 (0.40, 0.55, 1.00))
    frame_tag = body.bf_frame_name.lower()
    canvas.add_animated_frame_triad(
        np.asarray(times_s, dtype=float),
        R_bf_in_icrf,
        length_km=2.10 * body.radius_km,
        colors_xyz=pa_colors,
        labels_xyz=(f"X_{frame_tag}", f"Y_{frame_tag}", f"Z_{frame_tag}"),
    )
    # Rotate the central body with the same R sequence so the
    # surface stays glued to the body-fixed axes.
    canvas.set_central_body_animated_orientation(
        np.asarray(times_s, dtype=float), R_bf_in_icrf)


def resolve_R_icrf_to_bf(ctx: "PlotContext", t_sim_s: float
                          ) -> np.ndarray | None:
    """Best-effort: resolve the per-run input.toml, load the
    ephemeris, and return `R_icrf_to_bf` at `et_start_s + t_sim_s`
    using the central body's registered orientation provider.

    Returns None on any failure (missing snapshot, body has no
    `bf_orientation` callable, unreadable ephemeris). Used by the
    3D plot functions to decorate the scene with reference triads
    when an orientation is available, and to gracefully degrade to
    a scene-frame-only triad when it is not."""
    if ctx is None or ctx.central_body.bf_orientation is None:
        return None
    info = resolve_run_context(ctx.path)
    if info is None or info["ephemeris_path"] is None:
        return None
    from spopy import Ephemeris
    try:
        eph = Ephemeris(str(info["ephemeris_path"]))
    except (OSError, ValueError):
        return None
    et = info["et_start_s"] + float(t_sim_s)
    return ctx.central_body.bf_orientation(et, eph)


def turbo_color(i: int, n: int) -> tuple[float, float, float]:
    """Evenly-spaced colour from the matplotlib 'turbo' palette so the
    extremes (low/high cases) don't both pin to the cmap endpoints when
    only two files are overlaid."""
    cmap = mpl_colormaps["turbo"]
    t = 0.5 if n <= 1 else i / (n - 1)
    r, g, b, _a = cmap(t)
    return (r, g, b)
