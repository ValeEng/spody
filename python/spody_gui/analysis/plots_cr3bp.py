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

"""CR3BP-specific views: Jacobi conservation + the synodic 3D scene.

`TRAJ_SPECS` is spliced into the "traj" kind by registry.py (CR3BP
runs share the trajectory file format). New CR3BP diagnostics land
here: add the function, append a PlotSpec to TRAJ_SPECS.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from matplotlib.axes import Axes

from ..vtk_canvas import VtkCanvas
from .context import PlotContext, ctx_missing_message
from .scene3d import turbo_color
from .spec import PlotSpec


# ----------------------------------------------------------------------
# CR3BP-specific plots
# ----------------------------------------------------------------------
def _cr3bp_jacobi(d: np.ndarray, primaries
                   ) -> tuple[np.ndarray, float]:
    """Compute the CR3BP Jacobi constant at every sample:

        C = 2*Omega - |v|^2
        Omega = omega^2 * (x^2 + y^2) / 2 + mu1/r1 + mu2/r2

    where (x, y, z) and (vx, vy, vz) are synodic-frame state from `d`,
    `mu_i` and primary positions come from `primaries` (tuple of two
    `CR3BPPrimary`), and `omega = sqrt((mu1+mu2)/L^3)` with `L` the
    primary separation. Returns `(C_array, C0)` so the caller can plot
    `C - C0` for the conservation diagnostic and stash `C0` in the
    title."""
    p1, p2 = primaries
    L_km   = abs(p2.position_km[0] - p1.position_km[0])
    mu_tot = p1.mu_km3_s2 + p2.mu_km3_s2
    omega  = math.sqrt(mu_tot / (L_km ** 3))
    x, y, z    = d["x"], d["y"], d["z"]
    vx, vy, vz = d["vx"], d["vy"], d["vz"]
    r1 = np.sqrt((x - p1.position_km[0]) ** 2 + y * y + z * z)
    r2 = np.sqrt((x - p2.position_km[0]) ** 2 + y * y + z * z)
    Omega = 0.5 * (omega ** 2) * (x * x + y * y) \
            + p1.mu_km3_s2 / r1 + p2.mu_km3_s2 / r2
    v2 = vx * vx + vy * vy + vz * vz
    C  = 2.0 * Omega - v2
    return C, float(C[0])


def _plot_cr3bp_jacobi(ax: Axes, d: np.ndarray,
                        ctx: "PlotContext | None" = None) -> None:
    """Jacobi-constant conservation diagnostic. Should stay flat to
    integrator precision (~1e-12 relative on a well-behaved CR3BP
    run); systematic drift means the RHS or step controller is
    leaking energy. Plots `C(t) - C(0)` so the eye reads the deviation
    directly; the absolute `C0` is reported in the title."""
    if (ctx is None
            or ctx.dynamics_model != "cr3bp"
            or not ctx.cr3bp_primaries):
        ctx_missing_message(
            ax, "Jacobi constant",
            "Jacobi conservation is defined only for CR3BP runs.")
        return
    C, C0 = _cr3bp_jacobi(d, ctx.cr3bp_primaries)
    dC = C - C0
    ax.plot(d["t"], dC)
    ax.set_xlabel("t [s]"); ax.set_ylabel("C(t) - C(t₀) [km²/s²]")
    ax.set_title(f"Jacobi constant conservation  (C₀ = {C0:.9g} km²/s²)")
    ax.grid(True, alpha=0.3)


# ----------------------------------------------------------------------
# CR3BP synodic 3D scene
# ----------------------------------------------------------------------
# Colour palette for the primaries: a warm blue for the bigger one
# (Earth) and a cool grey for the smaller (Moon). Plain colours rather
# than textures keep the scene readable when the camera fits the whole
# synodic span (~400 000 km) instead of a single body.
_CR3BP_PRIMARY_COLORS: tuple[tuple[float, float, float], ...] = (
    (0.35, 0.55, 0.85),   # primary 1: blue-ish
    (0.70, 0.70, 0.72),   # primary 2: grey
)


def plot_cr3bp_3d_orbit(canvas: VtkCanvas, d: np.ndarray,
                          ctx: PlotContext) -> None:
    """Synodic-frame view for a CR3BP run: two primary spheres at fixed
    positions + the satellite trajectory + a viewport legend. The
    barycenter sits at the scene origin; the +x axis points from
    primary 1 (bigger) to primary 2 (smaller). No third bodies, no
    body-fixed triad: the scene IS the rotating frame, and the corner
    ICRF triad would be misleading here -- we suppress it via the
    Scene options like every other plot does."""
    opts = ctx.scene_options
    # Static primaries at their cached synodic positions.
    for primary, color in zip(ctx.cr3bp_primaries, _CR3BP_PRIMARY_COLORS):
        canvas.add_secondary_body(
            position_km=primary.position_km,
            radius_km=primary.radius_km,
            color=color,
            label=primary.name,
        )
    legend_entries: list[tuple[str, tuple[float, float, float]]] = []
    legend_entries.extend(
        (f"{p.name}  (synodic x = {p.position_km[0]:+.0f} km)", c)
        for p, c in zip(ctx.cr3bp_primaries, _CR3BP_PRIMARY_COLORS)
    )
    if opts.show_trajectory:
        ts  = d["t"].astype(float)
        pts = np.column_stack([d["x"], d["y"], d["z"]])
        canvas.add_animated_trajectory(
            pts, ts, color=(1.0, 0.85, 0.20),
            marker_radius_km=_cr3bp_marker_radius_km(ctx, pts),
        )
        legend_entries.append(("trajectory + moving marker",
                               (1.0, 0.85, 0.20)))
    if legend_entries:
        canvas.add_legend(legend_entries)


def _cr3bp_marker_radius_km(ctx: PlotContext, pts: np.ndarray) -> float:
    """Marker radius rule for CR3BP scenes. The synodic bbox spans
    ~L (~3.8e5 km for Earth-Moon), so HF's 3 %-of-trajectory-diagonal
    rule degenerates near libration-point equilibria where the orbit
    collapses to a point (L4 at v=0). Floor at 1 % of the primary
    separation so the marker is always visible against the scene
    diagonal; bump up with the trajectory bbox for larger orbits
    (Lyapunov, halo) so it scales sensibly when the orbit is itself
    a sizable fraction of L."""
    if not ctx.cr3bp_primaries:
        return 500.0
    L_km = abs(ctx.cr3bp_primaries[1].position_km[0]
               - ctx.cr3bp_primaries[0].position_km[0])
    traj_diag = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
    return max(L_km * 0.01, traj_diag * 0.03)


def overlay_cr3bp_3d_orbit(canvas: VtkCanvas,
                             items: list[tuple[Path, np.ndarray]],
                             ctx: PlotContext) -> None:
    """N-trajectory variant of `plot_cr3bp_3d_orbit`. Same scene
    geometry; trajectories colour-cycle through the turbo palette and
    each is picker-registered via `source_path`."""
    opts = ctx.scene_options
    for primary, color in zip(ctx.cr3bp_primaries, _CR3BP_PRIMARY_COLORS):
        canvas.add_secondary_body(
            position_km=primary.position_km,
            radius_km=primary.radius_km,
            color=color,
            label=primary.name,
        )
    legend_items: list[tuple[str, tuple[float, float, float]]] = []
    legend_items.extend(
        (f"{p.name}  (synodic x = {p.position_km[0]:+.0f} km)", c)
        for p, c in zip(ctx.cr3bp_primaries, _CR3BP_PRIMARY_COLORS)
    )
    if opts.show_trajectory:
        n = len(items)
        for i, (path, data) in enumerate(items):
            color = turbo_color(i, n)
            pts = np.column_stack([data["x"], data["y"], data["z"]])
            canvas.add_animated_trajectory(
                pts, data["t"].astype(float), color=color,
                source_path=path,
                marker_radius_km=_cr3bp_marker_radius_km(ctx, pts),
            )
            legend_items.append((path.name, color))
    if legend_items:
        canvas.add_legend(legend_items)


# Spliced into PLOTS["traj"] by registry.py, after the orbital-element
# specs. Jacobi-constant conservation is the rigorous CR3BP integrator
# check: should sit at ~1e-12 relative drift on a healthy run.
# Restricted to CR3BP runs -- the definition is specific to the
# synodic three-body system.
TRAJ_SPECS: list[PlotSpec] = [
    PlotSpec("Jacobi constant  C",          "2d", _plot_cr3bp_jacobi,
             category="CR3BP", mode="context",
             models=("cr3bp",)),
]
