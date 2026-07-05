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

"""Trajectory-kind views: state vectors, projections, orbital
elements, and the 3D orbit scene.

`SPECS` (in registry order: State vectors -> Orbit shape -> Orbital
elements) is the "traj" backbone assembled by registry.py; CR3BP and
diff specs are appended there. A new trajectory view = one function
+ one PlotSpec appended to SPECS.
"""

from __future__ import annotations

import math

import numpy as np
from matplotlib.axes import Axes

from ..central_bodies import MOON_MU_KM3_S2 as _MOON_MU_KM3S2_FALLBACK
from ..central_bodies import default_central_body
from ..scene_options import SceneOptions
from ..vtk_canvas import VtkCanvas
from .context import PlotContext, resolve_run_context
from .overlays import make_2d_overlay, overlay_3d_orbit
from .plots_cr3bp import plot_cr3bp_3d_orbit
from .scene3d import (
    add_animated_pa_decoration,
    add_sun_illumination,
    add_third_bodies,
)
from .spec import PlotSpec


def _plot_traj_r(ax: Axes, d: np.ndarray,
                  ctx: "PlotContext | None" = None) -> None:
    # |r| is rotation-invariant so the BF / ICRF choice does not
    # change the curve; calling `_state_in_plot_frame` anyway keeps
    # the title suffix consistent with the rest of the first-block
    # plots, so the user can tell at a glance which frame is active.
    d_view, suffix = _state_in_plot_frame(d, ctx)
    r = np.sqrt(d_view["x"] ** 2 + d_view["y"] ** 2 + d_view["z"] ** 2)
    ax.plot(d_view["t"], r)
    ax.set_xlabel("t [s]"); ax.set_ylabel("|r| [km]")
    ax.set_title(f"Radial distance{suffix}"); ax.grid(True, alpha=0.3)


def _plot_traj_v(ax: Axes, d: np.ndarray,
                  ctx: "PlotContext | None" = None) -> None:
    d_view, suffix = _state_in_plot_frame(d, ctx)
    v = np.sqrt(d_view["vx"] ** 2 + d_view["vy"] ** 2 + d_view["vz"] ** 2)
    ax.plot(d_view["t"], v)
    ax.set_xlabel("t [s]"); ax.set_ylabel("|v| [km/s]")
    ax.set_title(f"Speed{suffix}"); ax.grid(True, alpha=0.3)


def _plot_traj_xyz(ax: Axes, d: np.ndarray,
                    ctx: "PlotContext | None" = None) -> None:
    d_view, suffix = _state_in_plot_frame(d, ctx)
    for name in ("x", "y", "z"):
        ax.plot(d_view["t"], d_view[name], label=name)
    ax.set_xlabel("t [s]"); ax.set_ylabel("position [km]")
    ax.set_title(f"Position components{suffix or '  (inertial)'}")
    ax.legend(loc="best"); ax.grid(True, alpha=0.3)


def _plot_traj_vxyz(ax: Axes, d: np.ndarray,
                     ctx: "PlotContext | None" = None) -> None:
    d_view, suffix = _state_in_plot_frame(d, ctx)
    for name in ("vx", "vy", "vz"):
        ax.plot(d_view["t"], d_view[name], label=name)
    ax.set_xlabel("t [s]"); ax.set_ylabel("velocity [km/s]")
    ax.set_title(f"Velocity components{suffix or '  (inertial)'}")
    ax.legend(loc="best"); ax.grid(True, alpha=0.3)


def _plot_traj_projection(ax: Axes, d: np.ndarray, a: str, b: str,
                           ctx: "PlotContext | None" = None) -> None:
    d_view, suffix = _state_in_plot_frame(d, ctx)
    ax.plot(d_view[a], d_view[b], lw=0.8)
    ax.scatter([d_view[a][0]],  [d_view[b][0]],
                color="green", s=30, zorder=3, label="t=0")
    ax.scatter([d_view[a][-1]], [d_view[b][-1]],
                color="red",   s=30, zorder=3, label="end")
    ax.set_xlabel(f"{a} [km]"); ax.set_ylabel(f"{b} [km]")
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_title(f"Orbit projection: {a.upper()}{b.upper()}{suffix}")
    ax.legend(loc="best"); ax.grid(True, alpha=0.3)


# Fallback gravitational parameter used by `_state_for_elements` when
# no PlotContext is available (bare .bin loaded without a snapshot).
# The constant lives in central_bodies (sourced from spody_const.h
# at import time). For the normal context-aware path we read
# ctx.central_body.mu_km3_s2 instead.
MU_MOON_KM3_S2 = _MOON_MU_KM3S2_FALLBACK


def orbital_elements(r: np.ndarray, v: np.ndarray, mu: float
                      ) -> dict[str, np.ndarray]:
    """Classical orbital elements at every sample of (r, v).

    Thin wrapper over `spopy.cartesian_to_keplerian` that converts
    the canonical (sma_km/ecc/inc_rad/raan_rad/argp_rad/true_anom_rad)
    dict into the analysis-panel convention
    (`a`/`e`/`i`/`raan`/`aop`/`nu`, angles in **degrees**). One
    shared implementation across the engine input path (TOML form
    swap, kepler.h) and the post-run plots -- no two-codebase drift."""
    from spopy import cartesian_to_keplerian
    el = cartesian_to_keplerian(r, v, mu)
    return {
        "a":    el["sma_km"],
        "e":    el["ecc"],
        "i":    np.degrees(el["inc_rad"]),
        "raan": np.degrees(el["raan_rad"]),
        "aop":  np.degrees(el["argp_rad"]),
        "nu":   np.degrees(el["true_anom_rad"]),
    }


def _state_in_plot_frame(d: np.ndarray, ctx: "PlotContext | None"
                          ) -> tuple[np.ndarray, str]:
    """Return `(d_view, frame_suffix)` where `d_view` is either the
    original structured array (when plot_frame is 'icrf' or the run
    doesn't support a body-fixed view) or a rotated copy whose
    `x,y,z,vx,vy,vz` columns are expressed in the central body's
    body-fixed basis at the corresponding ET.

    Pure rotation only: `r_bf = R_icrf->bf @ r_icrf` and
    `v_bf = R_icrf->bf @ v_icrf`. The velocity transformation is NOT
    the true rotating-frame velocity (which would subtract ω × r);
    "expressing the inertial state in the BF basis at instant t" is
    what the orbital-elements code already does for CR3BP per-primary
    osculating views, and it keeps the plot meaning consistent across
    state-vector / projection / angles plots.

    Silently degrades to ICRF when:
      - `plot_frame` is not "bf";
      - the run is CR3BP (no body-fixed concept applies to the
        synodic frame -- the [cr3bp] runs already use a non-inertial
        basis);
      - the central body has no registered `bf_orientation`;
      - the per-run snapshot / ephemeris is missing or unreadable.
    """
    if ctx is None or getattr(ctx, "plot_frame", "icrf") != "bf":
        return d, ""
    if ctx.dynamics_model == "cr3bp":
        return d, ""
    if ctx.central_body.bf_orientation is None:
        return d, ""
    info = resolve_run_context(ctx.path)
    if info is None or info["ephemeris_path"] is None:
        return d, ""
    from spopy import Ephemeris
    try:
        eph = Ephemeris(str(info["ephemeris_path"]))
    except (OSError, ValueError):
        return d, ""
    et_start = float(info["et_start_s"])
    # Sample R_icrf_to_bf at every trajectory time; one numpy op per
    # column instead of a Python loop over `len(d)` keeps the call
    # cheap on dense trajectories.
    n = len(d)
    Rs = np.empty((n, 3, 3), dtype=float)
    for i in range(n):
        try:
            Rs[i] = np.asarray(
                ctx.central_body.bf_orientation(
                    et_start + float(d["t"][i]), eph),
                dtype=float)
        except (ValueError, IndexError):
            return d, ""
    r_icrf = np.stack((d["x"],  d["y"],  d["z"]),  axis=-1)
    v_icrf = np.stack((d["vx"], d["vy"], d["vz"]), axis=-1)
    r_bf = np.einsum("nij,nj->ni", Rs, r_icrf)
    v_bf = np.einsum("nij,nj->ni", Rs, v_icrf)
    out = d.copy()
    out["x"],  out["y"],  out["z"]  = r_bf[:, 0],  r_bf[:, 1],  r_bf[:, 2]
    out["vx"], out["vy"], out["vz"] = v_bf[:, 0],  v_bf[:, 1],  v_bf[:, 2]
    return out, f"  ({ctx.central_body.bf_frame_name})"


def _state_for_elements(d: np.ndarray, ctx: "PlotContext | None"
                          ) -> tuple[np.ndarray, np.ndarray, float, str]:
    """Build the (r, v, mu, label_suffix) tuple the orbital-elements
    solver consumes for the loaded trajectory.

    HF: raw state vector + central body's GM, no title suffix.

    CR3BP: shift to the selected primary's fixed synodic position and
    rotate the rotating-frame velocity into an inertial frame
    expressed in the synodic basis. The synodic basis itself rotates
    at `omega` about +z; computing elements in the snapshot of the
    inertial frame that coincides with the synodic basis at time `t`
    gives osculating-orbit elements -- magnitudes (`a`, `e`, `i`) are
    basis-independent and read cleanly, while RAAN and AOP retrograde
    at -omega per the rotating basis (label flags the relative
    primary so the reader knows what's being plotted)."""
    if (ctx is not None
            and ctx.dynamics_model == "cr3bp"
            and ctx.cr3bp_primaries):
        idx = max(1, min(2, ctx.scene_options.cr3bp_elements_primary)) - 1
        primary = ctx.cr3bp_primaries[idx]
        p1, p2 = ctx.cr3bp_primaries
        L_km    = abs(p2.position_km[0] - p1.position_km[0])
        mu_tot  = p1.mu_km3_s2 + p2.mu_km3_s2
        omega   = math.sqrt(mu_tot / (L_km ** 3))
        px, py, pz = primary.position_km
        rx = d["x"] - px
        ry = d["y"] - py
        rz = d["z"] - pz
        # omega vector = (0, 0, omega); omega x r_rel = (-omega*ry, +omega*rx, 0).
        vx_inertial = d["vx"] - omega * ry
        vy_inertial = d["vy"] + omega * rx
        vz_inertial = d["vz"]
        r = np.stack((rx, ry, rz), axis=-1)
        v = np.stack((vx_inertial, vy_inertial, vz_inertial), axis=-1)
        return r, v, primary.mu_km3_s2, f"  (rel. to {primary.name})"
    # HF: optionally rotate the inertial state into the central body's
    # body-fixed basis at each sample, so the resulting RAAN / AOP /
    # ν reflect angles measured relative to the BF X-axis.
    d_view, suffix = _state_in_plot_frame(d, ctx)
    r = np.stack((d_view["x"],  d_view["y"],  d_view["z"]),  axis=-1)
    v = np.stack((d_view["vx"], d_view["vy"], d_view["vz"]), axis=-1)
    mu = ctx.central_body.mu_km3_s2 if ctx is not None else MU_MOON_KM3_S2
    return r, v, mu, suffix


def _plot_traj_a(ax: Axes, d: np.ndarray,
                  ctx: "PlotContext | None" = None) -> None:
    r, v, mu, suffix = _state_for_elements(d, ctx)
    el = orbital_elements(r, v, mu)
    ax.plot(d["t"], el["a"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("a [km]")
    ax.set_title(f"Semi-major axis{suffix}"); ax.grid(True, alpha=0.3)


def _plot_traj_e(ax: Axes, d: np.ndarray,
                  ctx: "PlotContext | None" = None) -> None:
    r, v, mu, suffix = _state_for_elements(d, ctx)
    el = orbital_elements(r, v, mu)
    ax.plot(d["t"], el["e"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("e [-]")
    ax.set_title(f"Eccentricity{suffix}"); ax.grid(True, alpha=0.3)


def _plot_traj_i(ax: Axes, d: np.ndarray,
                  ctx: "PlotContext | None" = None) -> None:
    r, v, mu, suffix = _state_for_elements(d, ctx)
    el = orbital_elements(r, v, mu)
    ax.plot(d["t"], el["i"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("i [deg]")
    ax.set_title(f"Inclination{suffix}"); ax.grid(True, alpha=0.3)


def _plot_traj_raan(ax: Axes, d: np.ndarray,
                     ctx: "PlotContext | None" = None) -> None:
    r, v, mu, suffix = _state_for_elements(d, ctx)
    el = orbital_elements(r, v, mu)
    ax.plot(d["t"], el["raan"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("Ω [deg]")
    ax.set_title(f"RAAN (right ascension of ascending node){suffix}")
    ax.grid(True, alpha=0.3)


def _plot_traj_aop(ax: Axes, d: np.ndarray,
                    ctx: "PlotContext | None" = None) -> None:
    r, v, mu, suffix = _state_for_elements(d, ctx)
    el = orbital_elements(r, v, mu)
    ax.plot(d["t"], el["aop"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("ω [deg]")
    ax.set_title(f"Argument of periapsis{suffix}"); ax.grid(True, alpha=0.3)


def _plot_traj_e_vs_aop(ax: Axes, d: np.ndarray,
                         ctx: "PlotContext | None" = None) -> None:
    """e (y) vs argument of periapsis (x), one point per sample with a
    fine line. Useful as a 'how does the orbit shape drift in
    (e, ω) space' phase diagram -- circular orbits collapse to a
    horizontal stripe at low e (ω is then ill-defined and shows the
    numerical sweep), eccentric orbits trace an arc as ω regresses
    under J2 / 3rd bodies."""
    r, v, mu, suffix = _state_for_elements(d, ctx)
    el = orbital_elements(r, v, mu)
    ax.plot(el["aop"], el["e"], lw=0.6)
    ax.set_xlabel("ω [deg]"); ax.set_ylabel("e")
    ax.set_title(f"Eccentricity vs argument of periapsis{suffix}")
    ax.grid(True, alpha=0.3)


def _plot_traj_nu(ax: Axes, d: np.ndarray,
                   ctx: "PlotContext | None" = None) -> None:
    # Per-revolution saw-tooth is the correct shape for the wrapped
    # true anomaly. On long propagations this gets visually busy;
    # the user can zoom in on the toolbar.
    r, v, mu, suffix = _state_for_elements(d, ctx)
    el = orbital_elements(r, v, mu)
    ax.plot(d["t"], el["nu"], lw=0.6)
    ax.set_xlabel("t [s]"); ax.set_ylabel("ν [deg]")
    ax.set_title(f"True anomaly{suffix}"); ax.grid(True, alpha=0.3)


# ----------------------------------------------------------------------
# 3D plots
# ----------------------------------------------------------------------
def _plot_traj_3d_orbit(canvas: VtkCanvas, d: np.ndarray,
                         ctx: PlotContext | None = None) -> None:
    """Moon-centred view: grey sphere + yellow trajectory polyline +
    green/red start/end markers + PA / ICRF reference triads + a
    viewport legend explaining which marker is which. Camera fitted
    to the trajectory. The polyline is *not* registered as pickable
    here because picking one of one trajectory adds no information.

    `ctx` carries the per-run input.toml location, used to resolve
    the lunar libration angles at the trajectory's first sample so
    the PA triad can be drawn alongside the ICRF triad. When ctx is
    None (dev path) or the ephemeris is unreachable, only the ICRF
    triad is drawn -- the convention is symmetric with the impact 3D
    view.

    CR3BP runs render a separate scene: two static primary spheres at
    the synodic positions + trajectory + a simple legend. No central
    body, no third bodies, no body-fixed orientation -- the synodic
    rotating frame IS the working frame.
    """
    if ctx is not None and ctx.dynamics_model == "cr3bp" and ctx.cr3bp_primaries:
        plot_cr3bp_3d_orbit(canvas, d, ctx)
        return
    body = ctx.central_body if ctx is not None else default_central_body()
    canvas.add_central_body(radius_km=body.radius_km)
    opts = ctx.scene_options if ctx is not None else SceneOptions()
    ts = d["t"].astype(float)
    legend_entries: list[tuple[str, tuple[float, float, float]]] = []
    if opts.show_trajectory:
        pts = np.column_stack([d["x"], d["y"], d["z"]])
        # Anchor marker size to the central body radius (3 % of R_body)
        # so the spacecraft puck reads consistently across very
        # different orbits: at LEO around Moon (R=1737 km) it was ~50
        # km, at GLONASS around Earth (R=6378 km) it is ~190 km. The
        # default scaling rule inside add_animated_trajectory uses the
        # trajectory bounding box (3 % of diagonal) which over-blows
        # the marker for high-altitude orbits like GLONASS (where the
        # orbit diag ~50,000 km would make the marker ~1500 km, ~24 %
        # of Earth's radius).
        canvas.add_animated_trajectory(
            pts, ts, color=(1.0, 0.85, 0.20),
            marker_radius_km=0.030 * body.radius_km,
        )
        legend_entries.append(("trajectory + moving marker",
                                (1.0, 0.85, 0.20)))
    if opts.show_third_bodies:
        # Per-body filtering: pass opts.show_bodies through so we
        # only build markers for the user's selection.
        add_third_bodies(canvas, ctx, ts, only=opts.show_bodies)
    if legend_entries:
        canvas.add_legend(legend_entries)
    # Animated body-fixed triad + central-body libration; ICRF triad
    # static. Per-frame toggles honoured inside the helper.
    add_animated_pa_decoration(canvas, ctx, ts,
                                  show_icrf=opts.show_icrf_triad,
                                  show_pa=opts.show_pa_triad)
    # LAST: the sunlight walks the actors added above and freezes
    # their lighting recipe (see set_sun_light docstring).
    if opts.sun_illumination:
        add_sun_illumination(canvas, ctx, ts)


# Inline lambdas wrapping `_plot_traj_projection` for the XY / XZ / YZ
# variants -- kept as named locals so they can be reused by their
# matching overlay helpers.
_p_xy = lambda ax, d, ctx=None: _plot_traj_projection(ax, d, "x", "y", ctx)
_p_xz = lambda ax, d, ctx=None: _plot_traj_projection(ax, d, "x", "z", ctx)
_p_yz = lambda ax, d, ctx=None: _plot_traj_projection(ax, d, "y", "z", ctx)


# "traj" kind backbone, assembled (with CR3BP + diff appended) by
# registry.py. Categories drive the collapsible folders in the plot
# tree; list order is preserved.
SPECS: list[PlotSpec] = [
    # ----- State vectors -----------------------------------------
    # All four come straight from the columns in the trajectory
    # dtype; the XYZ / VxVyVz ones draw three lines per file so
    # the overlay variant is intentionally None (3N lines would
    # be illegible).
    PlotSpec("Radial distance |r|",         "2d", _plot_traj_r,
             overlay_fn=make_2d_overlay(_plot_traj_r),
             category="State vectors", mode="context"),
    PlotSpec("Speed |v|",                   "2d", _plot_traj_v,
             overlay_fn=make_2d_overlay(_plot_traj_v),
             category="State vectors", mode="context"),
    PlotSpec("Position x, y, z",            "2d", _plot_traj_xyz,
             category="State vectors", mode="context"),
    PlotSpec("Velocity vx, vy, vz",         "2d", _plot_traj_vxyz,
             category="State vectors", mode="context"),
    # ----- Orbit shape --------------------------------------------
    PlotSpec("XY projection",               "2d", _p_xy,
             overlay_fn=make_2d_overlay(_p_xy),
             category="Orbit shape", mode="context"),
    PlotSpec("XZ projection",               "2d", _p_xz,
             overlay_fn=make_2d_overlay(_p_xz),
             category="Orbit shape", mode="context"),
    PlotSpec("YZ projection",               "2d", _p_yz,
             overlay_fn=make_2d_overlay(_p_yz),
             category="Orbit shape", mode="context"),
    PlotSpec("3D orbit + central body",     "3d", _plot_traj_3d_orbit,
             overlay_fn=overlay_3d_orbit,
             mode="context",
             category="Orbit shape"),
    # ----- Orbital elements ---------------------------------------
    # Derived from r, v. All single-line so overlay-safe out of the
    # box. See orbital_elements for the degenerate-case handling
    # (equatorial / circular).
    # mode="context" so the kepler solver gets the central body's
    # mu from ctx.central_body.mu_km3_s2 instead of the Moon
    # fallback. Critical for non-Moon runs (Earth's mu is ~80x
    # larger -- using Moon's mu would skew `a` by ~80x and bias
    # `e`).
    PlotSpec("Semi-major axis  a",          "2d", _plot_traj_a,
             overlay_fn=make_2d_overlay(_plot_traj_a),
             category="Orbital elements", mode="context"),
    PlotSpec("Eccentricity  e",             "2d", _plot_traj_e,
             overlay_fn=make_2d_overlay(_plot_traj_e),
             category="Orbital elements", mode="context"),
    PlotSpec("Inclination  i",              "2d", _plot_traj_i,
             overlay_fn=make_2d_overlay(_plot_traj_i),
             category="Orbital elements", mode="context"),
    PlotSpec("RAAN  Ω",                 "2d", _plot_traj_raan,
             overlay_fn=make_2d_overlay(_plot_traj_raan),
             category="Orbital elements", mode="context"),
    PlotSpec("Arg. periapsis  ω",       "2d", _plot_traj_aop,
             overlay_fn=make_2d_overlay(_plot_traj_aop),
             category="Orbital elements", mode="context"),
    PlotSpec("True anomaly  ν",         "2d", _plot_traj_nu,
             overlay_fn=make_2d_overlay(_plot_traj_nu),
             category="Orbital elements", mode="context"),
    PlotSpec("Eccentricity vs argument of periapsis",
             "2d", _plot_traj_e_vs_aop,
             overlay_fn=make_2d_overlay(_plot_traj_e_vs_aop),
             category="Orbital elements", mode="context"),
]
