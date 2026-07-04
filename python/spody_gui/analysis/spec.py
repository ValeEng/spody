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

"""Plot-registry entry type and plot-function signatures.

`PlotSpec` is the contract between the plot modules and the panel:
every view (2D matplotlib or 3D VTK) is described by one spec and
registered in a module-level SPECS list; `registry.py` assembles the
per-kind dict the panel dispatches on. Adding a view never requires
touching the panel.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from matplotlib.axes import Axes

from ..vtk_canvas import VtkCanvas


# 2D plots receive a matplotlib Axes; 3D plots receive a VtkCanvas to
# add actors onto. The dispatcher (`AnalysisPanel._on_plot`) handles
# clear/reset/render so the plot fn only needs to express its content.
PlotFn2D = Callable[[Axes,      np.ndarray], None]
PlotFn3D = Callable[[VtkCanvas, np.ndarray], None]

# Overlay variants take a list of (source path, data array) so they
# can render N files together with a legend. Signature mirrors the
# single-file ones modulo the items list.
OverlayFn2D = Callable[..., None]  # (ax, items[, ctx]) -> None
OverlayFn3D = Callable[[VtkCanvas, list[tuple[Path, np.ndarray]]], None]


@dataclass(frozen=True)
class PlotSpec:
    label:      str
    dim:        str           # "2d" or "3d" -- selects which canvas page is shown
    fn:         Callable      # PlotFn2D for dim == "2d", PlotFn3D for dim == "3d"
    overlay_fn: Callable | None = None
    """Optional N-file overlay variant. None means the plot is single-
    file only (e.g. it draws multiple lines per file -- overlaying it
    would produce 3N or 5N illegible lines). The Overlay button is
    disabled with an explanation when the active spec lacks it."""
    category:   str = ""
    """Grouping label shown as a collapsible folder in the plot tree.
    Empty string puts the plot at root level (appropriate when a file
    kind has only a few plots that don't need grouping). Plots are
    rendered in registry order so categories stack in the order they
    first appear."""
    mode:       str = "single"
    """Dispatch mode. 'single' (default) calls `fn(ax_or_canvas, data)`
    against the currently-loaded file. 'diff' calls `fn(ax, data_a,
    data_b)` against exactly two selected files in the file tree
    (sorted top-down). 'context' calls `fn(ax, data, ctx)` with a
    `PlotContext` carrying the loaded file's path so the plot fn can
    locate the per-run input.toml snapshot (used by batch-event views
    that need et_start_s / ephemeris path / duration). Diff and
    context specs ignore `overlay_fn` -- they aren't single-file
    plots."""
    projection: str | None = None
    """matplotlib `add_subplot` projection kwarg for 2D plots. None
    (default) builds a regular Cartesian axis; 'mollweide' / 'aitoff'
    / 'hammer' produce the geographic ellipse projections used by
    the impact lat/lon views. Ignored for 3D plots (their canvas is
    VTK, not matplotlib) and by tile mode (mixing projections in one
    figure would force per-subplot axis creation, not worth the
    complexity yet)."""
    models:     tuple[str, ...] = ("high_fidelity", "cr3bp")
    """Dynamics models this plot applies to. The plot tree filters
    PLOTS by `ctx.dynamics_model in spec.models`, so a CR3BP-only
    plot (Jacobi conservation) advertises `("cr3bp",)` and an HF-
    only one (impact lat/lon on a body-fixed frame -- meaningless in
    the synodic CR3BP frame) advertises `("high_fidelity",)`.
    Default `("high_fidelity", "cr3bp")` means 'works for both'."""
