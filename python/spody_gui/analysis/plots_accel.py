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

"""Acceleration-kind views (SPDYACC_ files, high-fidelity only).

The per-force views resolve each `acc_thirdbody[i]` slot back to its
body name through the run's input.toml snapshot, so they are
`mode="context"`; see `_third_body_labels`.

New per-force diagnostics append to SPECS.
"""

from __future__ import annotations

import numpy as np
from matplotlib.axes import Axes

from .context import PlotContext, ctx_missing_message, resolve_run_context
from .derived import time_axis
from .overlays import make_2d_overlay
from .spec import PlotSpec


def _norm3(v: np.ndarray) -> np.ndarray:
    return np.sqrt(np.sum(v * v, axis=-1))


def _third_body_labels(d: np.ndarray, ctx: "PlotContext | None") -> list[str]:
    """Display names for the populated `acc_thirdbody[i]` slots.

    The binary carries only `n_third`, never the names: the engine
    fills slot `i` from `force_model.third_bodies[i]` (sim_setup.c
    resolves the name list to (NAIF, mu) pairs index by index), so the
    run's input.toml snapshot is the only place the mapping survives.

    Falls back to positional labels when the snapshot is missing or
    disagrees on the count -- mislabelling the Moon as the Sun would
    invert the physics reading of the plot, so a wrong name is much
    worse than no name.
    """
    n = int(d["n_third"][0]) if d.size else 0
    names: list[str] = []
    if ctx is not None:
        run = resolve_run_context(ctx.path)
        if run is not None:
            names = run["third_bodies"]
    if len(names) != n:
        names = [f"3rd-body #{i}" for i in range(n)]
    return names


def _perturbation_channels(d: np.ndarray, ctx: "PlotContext | None"
                           ) -> list[tuple[str, np.ndarray]]:
    """(label, |a|) for every perturbing force, central monopole
    EXCLUDED, ordered slowest-varying first so a stack reads bottom-up
    from 'always there' to 'intermittent'.

    Channels that are identically zero (drag off, SRP off, no third
    bodies) are dropped rather than drawn flat at the axis: an empty
    legend entry invites the reader to conclude the force is
    negligible, when in fact it was never modelled."""
    channels = [("harmonics", _norm3(d["acc_sphericalharmonics"]))]
    for i, name in enumerate(_third_body_labels(d, ctx)):
        channels.append((name, _norm3(d["acc_thirdbody"][:, i, :])))
    channels.append(("SRP",  _norm3(d["acc_srp"])))
    channels.append(("drag", _norm3(d["acc_drag"])))
    return [(name, v) for name, v in channels if np.any(v > 0.0)]


def _time_axis_data(ax: Axes, d: np.ndarray) -> tuple[np.ndarray, str]:
    """Simulation time rescaled to a readable unit, plus its axis
    label. An accelerations file routinely spans days at a 60 s output
    cadence, where a raw second count prints as a six-digit axis.

    The choice is memoised on the Axes because `make_2d_overlay` calls
    the plot fn once per file against the SAME axes: letting a 12 h
    file pick hours while a 6-day file picks days would draw the two
    curves on silently different scales."""
    choice = getattr(ax, "_spody_time_unit", None)
    if choice is None:
        span = float(d["t"][-1] - d["t"][0]) if d.size else 0.0
        choice = time_axis(span)
        ax._spody_time_unit = choice
    div, label = choice
    return d["t"] / div, label


def _plot_acc_total(ax: Axes, d: np.ndarray) -> None:
    t, unit = _time_axis_data(ax, d)
    ax.semilogy(t, _norm3(d["acc_total"]))
    ax.set_xlabel(f"t [{unit}]"); ax.set_ylabel("|a_total| [km/s²]")
    ax.set_title("Total acceleration magnitude")
    ax.grid(True, which="both", alpha=0.3)


def _plot_acc_breakdown(ax: Axes, d: np.ndarray,
                        ctx: "PlotContext | None" = None) -> None:
    t, unit = _time_axis_data(ax, d)
    ax.semilogy(t, _norm3(d["acc_2body"]), label="2-body")
    for name, mag in _perturbation_channels(d, ctx):
        ax.semilogy(t, mag, label=name)
    ax.set_xlabel(f"t [{unit}]"); ax.set_ylabel("|a| [km/s²]")
    ax.set_title("Per-force acceleration magnitude")
    # Fixed corner, never loc="best": with one line per third body this
    # plot can carry 10+ series, and "best" re-scans every point of
    # every series on each draw to score the candidate corners.
    ax.legend(loc="upper right", fontsize="small", framealpha=0.85)
    ax.grid(True, which="both", alpha=0.3)


# The budget divides MAGNITUDES, so the shares sum to the sum of the
# norms -- not to |Σa|, which is smaller wherever two forces partly
# cancel. That makes it a "who is pushing, and how hard" chart, not an
# exact vector decomposition of the net perturbation. It is the right
# reading for attribution (the question 'is this drift solar or
# lunar?') and the wrong one for reconstructing the net acceleration;
# the axis label says Σ|a| rather than |a| to keep that visible.
#
# The shares are drawn on a LOG axis as filled curves, not as a linear
# 100%-stack. A linear stack only works when the contributors are
# within about a decade of each other -- true at GNSS/GEO/high-lunar
# altitudes, false in LEO, where the harmonics take 99.99% and every
# other band lands below one pixel (measured on the ISS bench: drag
# 0.0058%, Moon 0.0053%, Sun 0.0023%). Log shares keep all six decades
# readable, and normalising still buys what the km/s^2 breakdown does
# not: a decaying orbit's overall growth in |a| divides out, so what is
# left is pure composition.
_BUDGET_TITLE = "Perturbation budget (share of Σ|a|, two-body excluded)"


def _plot_acc_budget(ax: Axes, d: np.ndarray,
                     ctx: "PlotContext | None" = None) -> None:
    channels = _perturbation_channels(d, ctx)
    if not channels:
        ctx_missing_message(
            ax, _BUDGET_TITLE,
            "No perturbing force is active in this run:\n"
            "the acceleration is 100% central two-body.")
        return
    mags  = np.vstack([mag for _, mag in channels])
    total = mags.sum(axis=0)
    # `total` is only zero if every channel vanished at that sample,
    # in which case every share is 0/1 = 0 and the column reads empty.
    share = 100.0 * mags / np.where(total > 0.0, total, 1.0)
    # Axis floor from a low percentile of the positive shares, NOT from
    # their minimum: SRP collapses towards zero inside an eclipse (and
    # to exactly zero in umbra, which the log axis simply masks), and a
    # single penumbral sample at 1e-14% would otherwise stretch the axis
    # over a dozen empty decades. Clipping that dip at the bottom of the
    # frame still reads correctly as "this force switched off".
    positive = share[share > 0.0]
    floor = (max(float(np.percentile(positive, 0.5)), 1e-6) * 0.5
             if positive.size else 1e-6)
    t, unit = _time_axis_data(ax, d)
    for (name, _), row, mean in zip(channels, share, share.mean(axis=1)):
        # Time-averaged share in the legend text: it ranks the forces
        # without the reader having to eyeball a log axis.
        line, = ax.semilogy(t, row, label=f"{name}  ({mean:#.3g}%)")
        ax.fill_between(t, row, floor, color=line.get_color(), alpha=0.15)
    ax.set_xlabel(f"t [{unit}]"); ax.set_ylabel("share of Σ|a| [%]")
    ax.set_title(_BUDGET_TITLE)
    # 200 not 100 so the dominant curve does not sit on the frame.
    ax.set_ylim(floor, 200.0)
    ax.set_xlim(float(t[0]), float(t[-1]))
    # Centre-right: on a log share axis the dominant force pins to the
    # top and the rest to the bottom, leaving the middle decades empty.
    ax.legend(loc="center right", fontsize="small", framealpha=0.85)
    ax.grid(True, which="both", alpha=0.3)


def _plot_acc_eclipse(ax: Axes, d: np.ndarray) -> None:
    t, unit = _time_axis_data(ax, d)
    ax.plot(t, d["eclipse_fraction"])
    ax.set_xlabel(f"t [{unit}]"); ax.set_ylabel("eclipse fraction")
    ax.set_title("Sunlight fraction (1 = full sun, 0 = full umbra)")
    ax.set_ylim(-0.05, 1.05); ax.grid(True, alpha=0.3)


# CR3BP runs disable accelerations output (no force-model
# bookkeeping), so an accel file implies HF -- but the `models` tag
# is set explicitly for symmetry with the rest of the registry.
SPECS: list[PlotSpec] = [
    PlotSpec("Total  |a_total|",            "2d", _plot_acc_total,
             overlay_fn=make_2d_overlay(_plot_acc_total),
             models=("high_fidelity",)),
    PlotSpec("Per-force breakdown (log y)", "2d", _plot_acc_breakdown,
             mode="context", models=("high_fidelity",)),
    PlotSpec("Perturbation budget (share, log y)", "2d", _plot_acc_budget,
             mode="context", models=("high_fidelity",)),
    PlotSpec("Eclipse fraction",            "2d", _plot_acc_eclipse,
             overlay_fn=make_2d_overlay(_plot_acc_eclipse),
             models=("high_fidelity",)),
]
