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

"""N-file overlay machinery.

`make_2d_overlay(fn)` lifts any single-file 2D plot into its overlay
variant (one colour per file + legend); `overlay_3d_orbit` is the
3D counterpart. Plot modules use these when declaring their SPECS.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from matplotlib.axes import Axes

from ..central_bodies import default_central_body
from ..scene_options import SceneOptions
from ..vtk_canvas import VtkCanvas
from .context import PlotContext
from .plots_cr3bp import overlay_cr3bp_3d_orbit
from .scene3d import add_animated_pa_decoration, add_third_bodies, turbo_color
from .spec import OverlayFn2D, PlotFn2D


# ----------------------------------------------------------------------
# Overlay variants
# ----------------------------------------------------------------------


def make_2d_overlay(single_fn: PlotFn2D) -> OverlayFn2D:
    """Lift a single-file 2D plot to an N-file overlay. Pre-seeds the
    axes colour cycle so each `single_fn` call picks the next slot of
    the turbo palette; after each call we attach the file basename as
    the line label so the final `ax.legend()` lists them in order.

    Only safe for plots that add **one** line per call (otherwise the
    label tagging picks the wrong line). The registry annotates which
    `PlotSpec` entries qualify.

    Auto-detects whether `single_fn` accepts a `ctx` argument (via
    `inspect.signature`) and forwards the overlay's context to it
    when it does. Plot fns that don't take ctx (e.g. |r|(t)) stay
    callable with their existing (ax, d) signature."""
    import inspect
    sig = inspect.signature(single_fn)
    forwards_ctx = len(sig.parameters) >= 3

    def overlay(ax: Axes, items: list[tuple[Path, np.ndarray]],
                 ctx: "PlotContext | None" = None) -> None:
        n = len(items)
        colors = [turbo_color(i, n) for i in range(n)]
        ax.set_prop_cycle(color=colors)
        for path, data in items:
            if forwards_ctx:
                single_fn(ax, data, ctx)
            else:
                single_fn(ax, data)
            lines = ax.get_lines()
            if lines:
                lines[-1].set_label(path.name)
        # Decorate the title set by the single-file fn so the overlay
        # nature is visible without us re-implementing the title text.
        ax.set_title(f"{ax.get_title()}  --  {n} files")
        ax.legend(loc="best", fontsize="small")
    return overlay


def overlay_3d_orbit(canvas: VtkCanvas,
                       items: list[tuple[Path, np.ndarray]],
                       ctx: PlotContext | None = None) -> None:
    """3D Moon scene with N trajectories stacked, each in its own
    turbo colour, plus a viewport legend, PA / ICRF reference triads,
    and Ctrl+click picking enabled on every polyline (via
    `source_path`).

    Triads use the libration at the *first* trajectory's start time;
    the lunar libration evolves on a ~1-day scale so the cross-file
    discrepancy is visually negligible inside a single batch.

    CR3BP runs delegate to `overlay_cr3bp_3d_orbit`: two static
    primaries + the N trajectories in the synodic frame.
    """
    if ctx is not None and ctx.dynamics_model == "cr3bp" and ctx.cr3bp_primaries:
        overlay_cr3bp_3d_orbit(canvas, items, ctx)
        return
    body = ctx.central_body if ctx is not None else default_central_body()
    canvas.add_central_body(radius_km=body.radius_km)
    opts = ctx.scene_options if ctx is not None else SceneOptions()
    n = len(items)
    legend_items: list[tuple[str, tuple[float, float, float]]] = []
    if opts.show_trajectory:
        for i, (path, data) in enumerate(items):
            color = turbo_color(i, n)
            pts = np.column_stack([data["x"], data["y"], data["z"]])
            canvas.add_animated_trajectory(
                pts, data["t"].astype(float), color=color,
                source_path=path,
            )
            legend_items.append((path.name, color))
    # Third bodies / triads / Moon libration all read from the FIRST
    # file's snapshot -- batches share et_start_s + force_model, so
    # one set describes the whole overlay correctly.
    if items:
        first_path, first_data = items[0]
        body_ctx = PlotContext(
            path=first_path,
            central_body_texture=ctx.central_body_texture if ctx else None,
            scene_options=opts,
            central_body=ctx.central_body if ctx is not None else default_central_body(),
            dynamics_model=ctx.dynamics_model if ctx is not None else "high_fidelity",
            cr3bp_primaries=ctx.cr3bp_primaries if ctx is not None else (),
        )
        first_ts = first_data["t"].astype(float)
        if opts.show_third_bodies:
            add_third_bodies(canvas, body_ctx, first_ts,
                                only=opts.show_bodies)
        add_animated_pa_decoration(canvas, body_ctx, first_ts,
                                      show_icrf=opts.show_icrf_triad,
                                      show_pa=opts.show_pa_triad)
    if legend_items:
        canvas.add_legend(legend_items)
