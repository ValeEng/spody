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

New per-force diagnostics append to SPECS.
"""

from __future__ import annotations

import numpy as np
from matplotlib.axes import Axes

from .overlays import make_2d_overlay
from .spec import PlotSpec


def _norm3(v: np.ndarray) -> np.ndarray:
    return np.sqrt(np.sum(v * v, axis=-1))


def _plot_acc_total(ax: Axes, d: np.ndarray) -> None:
    ax.semilogy(d["t"], _norm3(d["acc_total"]))
    ax.set_xlabel("t [s]"); ax.set_ylabel("|a_total| [km/s²]")
    ax.set_title("Total acceleration magnitude")
    ax.grid(True, which="both", alpha=0.3)


def _plot_acc_breakdown(ax: Axes, d: np.ndarray) -> None:
    ax.semilogy(d["t"], _norm3(d["acc_2body"]),              label="2-body")
    ax.semilogy(d["t"], _norm3(d["acc_sphericalharmonics"]), label="harmonics")
    ax.semilogy(d["t"], _norm3(d["acc_thirdbody_total"]),    label="3rd-body")
    ax.semilogy(d["t"], _norm3(d["acc_srp"]),                label="SRP")
    ax.semilogy(d["t"], _norm3(d["acc_drag"]),               label="drag")
    ax.set_xlabel("t [s]"); ax.set_ylabel("|a| [km/s²]")
    ax.set_title("Per-force acceleration magnitude")
    ax.legend(loc="best", fontsize="small")
    ax.grid(True, which="both", alpha=0.3)


def _plot_acc_eclipse(ax: Axes, d: np.ndarray) -> None:
    ax.plot(d["t"], d["eclipse_fraction"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("eclipse fraction")
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
             models=("high_fidelity",)),
    PlotSpec("Eclipse fraction",            "2d", _plot_acc_eclipse,
             overlay_fn=make_2d_overlay(_plot_acc_eclipse),
             models=("high_fidelity",)),
]
