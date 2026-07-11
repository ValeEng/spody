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

"""Shared 3D scene decoration for the VTK canvas views -- the GUI
glue over `spoviz.decoration`.

The actual decoration engine (reference triads, third-body markers,
sun illumination, animated body-fixed frame) lives in the spoviz
library and is GUI-agnostic. This module keeps the historical
call signatures the plot modules use (canvas + PlotContext + times)
and resolves everything app-specific before delegating: the
run-folder snapshot (`resolve_run_context`), the spopy ephemeris,
the wizard-managed texture assets, the central-body registry's
orientation providers, the `spody_const.h` radius table, and the Qt
event-loop pump. Pure glue: this module never imports the plot
modules, so any view (traj 3D, events 3D, CR3BP 3D, future ones)
can build on it without cycles.
"""

from __future__ import annotations

import numpy as np
from matplotlib import colormaps as mpl_colormaps
from PySide6.QtWidgets import QApplication
from spoviz import bodies as sv_bodies
from spoviz import decoration as sv_decoration

from .. import constants
from ..central_bodies import default_central_body, resolve_central_body
from ..toml_io import read_toml
from ..vtk_canvas import MOON_RADIUS_KM, VtkCanvas
from .context import PlotContext, resolve_run_context

# Physical mean radii in km, read from spody_const.h through the
# constants module (same values the engine uses). spoviz takes this
# as a plain mapping so the library carries no constants-file
# dependency of its own.
_BODY_RADIUS_PHYS_KM: dict[str, float] = dict(constants.BODY_RADIUS_KM)


def add_reference_triads(canvas: VtkCanvas,
                           scene_frame: str,
                           R_icrf_to_bf: np.ndarray | None,
                           radius_km: float = MOON_RADIUS_KM,
                           bf_frame_label: str = "PA") -> None:
    """Body-fixed (bright) + ICRF (muted) reference triads; see
    `spoviz.decoration.add_reference_triads` for the convention."""
    sv_decoration.add_reference_triads(canvas.scene, scene_frame,
                                        R_icrf_to_bf, radius_km,
                                        bf_frame_label)


def body_marker_radius_km(name: str,
                             ref_radius_km: float = MOON_RADIUS_KM
                             ) -> float:
    """Display radius for a third-body marker, using the engine's
    `spody_const.h` radius table (see `spoviz.bodies`)."""
    return sv_bodies.body_marker_radius_km(name, ref_radius_km,
                                            _BODY_RADIUS_PHYS_KM)


def _run_ephemeris(ctx: "PlotContext | None"):
    """Resolve the run-folder snapshot and open its ephemeris.
    Returns (info, spopy.Ephemeris) or None on any failure -- every
    caller degrades silently (decoration is opt-in scene garnish,
    not a hard contract)."""
    if ctx is None:
        return None
    info = resolve_run_context(ctx.path)
    if info is None or info["ephemeris_path"] is None:
        return None
    from spopy import Ephemeris
    try:
        return info, Ephemeris(str(info["ephemeris_path"]))
    except (OSError, ValueError):
        return None


def add_third_bodies(canvas: VtkCanvas, ctx: "PlotContext",
                       times_s: np.ndarray,
                       only: set[str] | None = None) -> None:
    """Decorate the 3D scene with one animated marker per body in
    `force_model.third_bodies` (read from the run-folder TOML
    snapshot). Body-agnostic: the central body's NAIF id (from
    ctx.central_body) is the only thing that decides "relative to
    whom" body positions are queried.

    `times_s` is the simulation time grid of the spacecraft
    trajectory; `only` restricts to the user-checked subset from the
    Scene options dialog (empty set = show none, None = no filter)."""
    resolved = _run_ephemeris(ctx)
    if resolved is None:
        return
    info, eph = resolved
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

    from .. import assets, paths

    def texture_for(name: str):
        # Wizard-managed texture asset for this body, if the user has
        # downloaded it. paths.data_dir() is the same QSettings-backed
        # lookup the wizard uses, so this stays in sync with the
        # user's choice. (spoviz guards the call, so a lookup failure
        # just means a flat-colour marker.)
        return assets.central_body_texture_path(paths.data_dir(), name)

    def orientation_for(name: str):
        # Orientation provider from the central-body registry (Earth
        # ITRS via spopy.icrf_to_itrs, Moon PA via libration angles);
        # None for bodies without one (Sun, planets).
        spec = resolve_central_body(name)
        return spec.bf_orientation if spec is not None else None

    sv_decoration.add_third_bodies(
        canvas.scene,
        ephemeris=eph,
        central_naif=ctx.central_body.naif_id,
        central_radius_km=ctx.central_body.radius_km,
        body_names=bodies_raw,
        times_s=times_s,
        et_start_s=float(info["et_start_s"]),
        only=only,
        radius_km_by_name=_BODY_RADIUS_PHYS_KM,
        texture_for=texture_for,
        orientation_for=orientation_for,
        # Keep the cursor + status responsive when N is large (e.g.
        # multi-day batch with a dense trajectory).
        pump=QApplication.processEvents,
    )


def add_sun_illumination(canvas: VtkCanvas, ctx: "PlotContext",
                            times_s: np.ndarray) -> None:
    """Install the day/night sunlight on an ICRF-centric HF scene.
    Call it LAST in the scene build (set_sun_light freezes the
    lighting recipe of every actor present at call time)."""
    resolved = _run_ephemeris(ctx)
    if resolved is None:
        return
    info, eph = resolved
    sv_decoration.add_sun_illumination(
        canvas.scene,
        ephemeris=eph,
        central_naif=ctx.central_body.naif_id,
        times_s=times_s,
        et_start_s=float(info["et_start_s"]),
        pump=QApplication.processEvents,
    )


def add_animated_pa_decoration(canvas: VtkCanvas, ctx: "PlotContext",
                                  times_s: np.ndarray,
                                  show_icrf: bool = True,
                                  show_pa:   bool = True) -> None:
    """Drop the ICRF + body-fixed triads AND bind a libration-driven
    orientation on the central body, all wired into the playback
    bar. Body-agnostic: the triad axis labels and the orientation
    provider come from `ctx.central_body.bf_frame_name` /
    `bf_orientation`. When the spec has no orientation provider (or
    the ephemeris is unreachable) we degrade to "just the static
    ICRF triad" rather than crashing."""
    body = ctx.central_body if ctx is not None else default_central_body()
    eph = None
    et_start = 0.0
    bf_orientation = None
    if show_pa and ctx is not None and body.bf_orientation is not None:
        resolved = _run_ephemeris(ctx)
        if resolved is not None:
            info, eph = resolved
            et_start = float(info["et_start_s"])
            bf_orientation = body.bf_orientation
    sv_decoration.add_animated_body_frame(
        canvas.scene,
        times_s=times_s,
        radius_km=body.radius_km,
        bf_frame_name=body.bf_frame_name,
        ephemeris=eph,
        bf_orientation=bf_orientation,
        et_start_s=et_start,
        show_icrf=show_icrf,
        show_bf=show_pa,
    )


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
    resolved = _run_ephemeris(ctx)
    if resolved is None:
        return None
    info, eph = resolved
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
