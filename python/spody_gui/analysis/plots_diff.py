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

"""Diff views (two trajectories required).

Diffs subtract trajectory B from trajectory A sample-by-sample. The
dispatcher (`AnalysisPanel._plot_diff`) aligns the two grids upfront
via `align_or_interp` -- if they match, both pass through unchanged;
if not, B is interpolated onto A's grid (cubic Hermite for position
using v as derivative, linear for velocity) restricted to the
overlapping time window. The plot functions below trust the
alignment and just compute the deltas. New diff views append to
SPECS.
"""

from __future__ import annotations

import numpy as np
from matplotlib.axes import Axes

from .spec import PlotSpec


def _hermite_interp_pos(t_q: np.ndarray, t: np.ndarray,
                         r: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Cubic Hermite interpolation of position using v as derivative.

    `t` (N,), `r` (N, 3), `v` (N, 3), `t_q` (M,). Returns r_q (M, 3).
    Caller must ensure `t_q` lies inside `[t[0], t[-1]]`; out-of-range
    queries return NaN.

    Hermite basis at s = (t_q - t_i) / h, h = t_{i+1} - t_i:
        H0 = 2s^3 - 3s^2 + 1     (value at left)
        H1 = s^3 - 2s^2 + s       (deriv at left,  scaled by h)
        H2 = -2s^3 + 3s^2         (value at right)
        H3 = s^3 - s^2            (deriv at right, scaled by h)
    """
    # Validity is `t_q in [t[0], t[-1]]`, not `idx in [0, N-2]` --
    # at the exact endpoints s collapses to 0 or 1 and the Hermite
    # basis trivially returns r[0] or r[-1], which is correct.
    in_range = (t_q >= t[0]) & (t_q <= t[-1])
    idx = np.searchsorted(t, t_q) - 1
    idx_safe = np.clip(idx, 0, len(t) - 2)
    t0 = t[idx_safe]
    h  = t[idx_safe + 1] - t0
    s  = (t_q - t0) / h
    s2, s3 = s * s, s * s * s
    H0 = 2 * s3 - 3 * s2 + 1
    H1 = s3 - 2 * s2 + s
    H2 = -2 * s3 + 3 * s2
    H3 = s3 - s2
    out = (H0[:, None] * r[idx_safe]
         + (h * H1)[:, None] * v[idx_safe]
         + H2[:, None] * r[idx_safe + 1]
         + (h * H3)[:, None] * v[idx_safe + 1])
    out[~in_range] = np.nan
    return out


def align_or_interp(a: np.ndarray, b: np.ndarray
                      ) -> tuple[np.ndarray, np.ndarray, bool, str]:
    """Return (a_aligned, b_aligned, was_interpolated, note).

    Fast path: A and B share the same time grid sample-by-sample
    (within 1 ms) -> both passed through unchanged.

    Slow path: B is interpolated onto A's grid restricted to the
    overlapping time window [max(t_A[0], t_B[0]),
    min(t_A[-1], t_B[-1])]. Position uses cubic Hermite with B's v
    as the derivative; velocity uses linear interp on each
    component. The returned `note` is short, human-readable, and
    rendered into the plot title so the user sees the diff is
    interpolated, not direct.

    Raises ValueError if there's no time overlap at all (the two
    runs cover disjoint windows).

    Note: an earlier version of this function fast-pathed on length +
    endpoint match alone. That misfires badly for two adaptive (`mode
    = "step"`) runs that happen to land on the same accepted-step
    count: their endpoints coincide (both stop at the requested
    duration_s) but the middle samples can drift apart by *seconds*,
    turning what should be a m-level diff into a km-level garbage
    plot. The current check requires every t-pair to match within
    1 ms, which costs one O(N) numpy pass but stays honest."""
    same_len = len(a) == len(b)
    same_grid = (
        same_len
        and np.allclose(a["t"], b["t"], atol=1e-3, rtol=0.0)
    )
    if same_grid:
        return a, b, False, ""

    t_lo = max(a["t"][0],  b["t"][0])
    t_hi = min(a["t"][-1], b["t"][-1])
    if t_hi - t_lo < 1.0:
        raise ValueError(
            f"diff requires overlapping time windows "
            f"(A: [{a['t'][0]:.1f}, {a['t'][-1]:.1f}] s, "
            f"B: [{b['t'][0]:.1f}, {b['t'][-1]:.1f}] s -- no overlap).")

    # Clip A to the overlap so the dense reference doesn't extrapolate.
    mask = (a["t"] >= t_lo) & (a["t"] <= t_hi)
    a_clip = a[mask]
    if len(a_clip) < 2:
        raise ValueError(
            "diff: less than 2 overlapping samples after restricting "
            "to the common window.")

    t_q = a_clip["t"]
    b_r = np.column_stack((b["x"],  b["y"],  b["z"]))
    b_v = np.column_stack((b["vx"], b["vy"], b["vz"]))
    r_i = _hermite_interp_pos(t_q, b["t"], b_r, b_v)
    v_i = np.column_stack([
        np.interp(t_q, b["t"], b_v[:, i]) for i in range(3)
    ])

    b_aligned = np.empty(len(a_clip), dtype=a.dtype)
    b_aligned["t"]  = t_q
    b_aligned["x"]  = r_i[:, 0]
    b_aligned["y"]  = r_i[:, 1]
    b_aligned["z"]  = r_i[:, 2]
    b_aligned["vx"] = v_i[:, 0]
    b_aligned["vy"] = v_i[:, 1]
    b_aligned["vz"] = v_i[:, 2]

    note = (f"B interpolated onto A's grid "
            f"({len(b)} -> {len(a_clip)} samples, "
            f"cubic Hermite on r, linear on v)")
    return a_clip, b_aligned, True, note


def _plot_diff_r(ax: Axes, a: np.ndarray, b: np.ndarray) -> None:
    dx = a["x"] - b["x"]; dy = a["y"] - b["y"]; dz = a["z"] - b["z"]
    dr = np.sqrt(dx * dx + dy * dy + dz * dz)
    ax.semilogy(a["t"], dr)
    ax.set_xlabel("t [s]"); ax.set_ylabel("|Δr| [km]")
    ax.set_title("Position-error magnitude  |r_A - r_B|")
    ax.grid(True, which="both", alpha=0.3)


def _plot_diff_r_linear(ax: Axes, a: np.ndarray, b: np.ndarray) -> None:
    dx = a["x"] - b["x"]; dy = a["y"] - b["y"]; dz = a["z"] - b["z"]
    dr = np.sqrt(dx * dx + dy * dy + dz * dz)
    ax.plot(a["t"], dr)
    ax.set_xlabel("t [s]"); ax.set_ylabel("|Δr| [km]")
    ax.set_title("Position-error magnitude  |r_A - r_B|")
    ax.grid(True, alpha=0.3)


def _plot_diff_v(ax: Axes, a: np.ndarray, b: np.ndarray) -> None:
    dvx = a["vx"] - b["vx"]; dvy = a["vy"] - b["vy"]; dvz = a["vz"] - b["vz"]
    dv = np.sqrt(dvx * dvx + dvy * dvy + dvz * dvz)
    ax.semilogy(a["t"], dv)
    ax.set_xlabel("t [s]"); ax.set_ylabel("|Δv| [km/s]")
    ax.set_title("Velocity-error magnitude  |v_A - v_B|")
    ax.grid(True, which="both", alpha=0.3)


def _plot_diff_v_linear(ax: Axes, a: np.ndarray, b: np.ndarray) -> None:
    dvx = a["vx"] - b["vx"]; dvy = a["vy"] - b["vy"]; dvz = a["vz"] - b["vz"]
    dv = np.sqrt(dvx * dvx + dvy * dvy + dvz * dvz)
    ax.plot(a["t"], dv)
    ax.set_xlabel("t [s]"); ax.set_ylabel("|Δv| [km/s]")
    ax.set_title("Velocity-error magnitude  |v_A - v_B|")
    ax.grid(True, alpha=0.3)


def _plot_diff_xyz(ax: Axes, a: np.ndarray, b: np.ndarray) -> None:
    for name in ("x", "y", "z"):
        ax.plot(a["t"], a[name] - b[name], label=f"Δ{name}")
    ax.set_xlabel("t [s]"); ax.set_ylabel("Δposition [km]")
    ax.set_title("Position error per component  (A - B)")
    ax.legend(loc="best"); ax.grid(True, alpha=0.3)


def _add_stats_box(ax: Axes,
                    rows: list[tuple[str, float]],
                    unit: str = "") -> None:
    """Drop a tidy descriptive-stats box in the bottom-right corner
    of the axes. Each `(label, value)` row is rendered in a monospace
    font and column-aligned so labels and numbers line up across
    rows. Used by the diff distribution + CDF plots so the numerical
    summary (median, p95, p99, ...) lives **inside** the figure
    instead of cramming the title.

    The bottom-right corner is empty for both the right-skewed
    histogram and the saturating-toward-1 CDF, so the box does not
    overlap the data."""
    if not rows:
        return
    label_w = max(len(label) for label, _ in rows)
    unit_suffix = f" {unit}" if unit else ""
    lines = [f"{label:<{label_w}} = {v:>8.3g}{unit_suffix}"
             for label, v in rows]
    ax.text(0.98, 0.04, "\n".join(lines),
            transform=ax.transAxes, ha="right", va="bottom",
            family="monospace", fontsize="small",
            bbox=dict(boxstyle="round,pad=0.4",
                      facecolor="white", alpha=0.85,
                      edgecolor="lightgray"))


def _plot_diff_r_distribution(ax: Axes, a: np.ndarray,
                                b: np.ndarray) -> None:
    """Histogram of the per-sample position-error magnitude `|Δr|`.

    Bin count is min(60, sqrt(N)) so a 6-day LRO regression
    (~9k samples at 1-minute cadence) gets ~60 bins and a short
    smoke test still produces a readable histogram. Descriptive
    stats (median / p95 / max) are pinned in the title."""
    dx = a["x"] - b["x"]; dy = a["y"] - b["y"]; dz = a["z"] - b["z"]
    dr = np.sqrt(dx * dx + dy * dy + dz * dz)
    if dr.size == 0:
        ax.text(0.5, 0.5, "No samples", transform=ax.transAxes,
                ha="center", va="center")
        return
    n_bins = min(60, max(10, int(np.sqrt(dr.size))))
    ax.hist(dr, bins=n_bins, color="tab:blue", alpha=0.7,
            edgecolor="black", linewidth=0.4)
    ax.set_xlabel("|Δr| [km]")
    ax.set_ylabel("# samples per bin")
    med = float(np.median(dr))
    # RMS = sqrt(mean(dr^2)). For a non-negative residual it is the
    # square-weighted "typical" magnitude -- always >= the median and
    # heavier on the tail than the mean, so it is the canonical
    # single-number summary in orbit-determination work.
    rms = float(np.sqrt(np.mean(dr * dr)))
    p95 = float(np.percentile(dr, 95.0))
    mx  = float(dr.max())
    ax.set_title("|Δr| distribution")
    _add_stats_box(ax, [("median", med),
                        ("RMS",    rms),
                        ("p95",    p95),
                        ("max",    mx)], unit="km")
    ax.grid(True, alpha=0.3)


def _plot_diff_r_cdf(ax: Axes, a: np.ndarray, b: np.ndarray) -> None:
    """Empirical CDF of the per-sample position-error magnitude
    `|Δr|`. Reading the CDF at a horizontal value tells the user
    'what fraction of samples is below this error?' -- the natural
    question for regression budgets.

    The canonical percentiles (median / p95 / p99 / p99.9 / max)
    are pinned in the title so the dominant numbers are visible
    without cluttering the curve with dashed cross-hairs. They are
    read directly from the empirical CDF and are
    **distribution-free** -- the underlying `|Δr|` does not need to
    be normal for the percentiles to be the right answer."""
    dx = a["x"] - b["x"]; dy = a["y"] - b["y"]; dz = a["z"] - b["z"]
    dr = np.sqrt(dx * dx + dy * dy + dz * dz)
    n = int(dr.size)
    if n == 0:
        ax.text(0.5, 0.5, "No samples", transform=ax.transAxes,
                ha="center", va="center")
        return
    sorted_dr = np.sort(dr)
    cdf = np.arange(1, n + 1) / n
    ax.plot(sorted_dr, cdf, color="tab:red", linewidth=1.6,
            drawstyle="steps-post")
    ax.set_xlabel("|Δr| [km]")
    ax.set_ylabel("CDF")
    ax.set_ylim(0.0, 1.0)
    med  = float(np.median(dr))
    # See _plot_diff_r_distribution for the RMS rationale; pinned in
    # the same place across both stats boxes so the eye finds it
    # between the median (central tendency) and the tail percentiles.
    rms  = float(np.sqrt(np.mean(dr * dr)))
    p95  = float(np.percentile(dr, 95.0))
    p99  = float(np.percentile(dr, 99.0))
    p999 = float(np.percentile(dr, 99.9))
    mx   = float(dr.max())
    ax.set_title("|Δr| empirical CDF")
    _add_stats_box(ax, [("median", med),
                        ("RMS",    rms),
                        ("p95",    p95),
                        ("p99",    p99),
                        ("p99.9",  p999),
                        ("max",    mx)], unit="km")
    ax.grid(True, alpha=0.3)


def _plot_diff_ric(ax: Axes, a: np.ndarray, b: np.ndarray) -> None:
    """Position-error decomposition in the RIC (Radial / In-track /
    Cross-track) frame of trajectory A.

    Standard for orbital regression: errors split by direction tell
    you whether your delta is mostly down-track (timing / energy
    drift), radial (altitude error), or out-of-plane (RAAN / i drift).
    """
    r_A = np.stack((a["x"],  a["y"],  a["z"]),  axis=-1)
    v_A = np.stack((a["vx"], a["vy"], a["vz"]), axis=-1)
    dr_in = np.stack((a["x"] - b["x"],
                      a["y"] - b["y"],
                      a["z"] - b["z"]), axis=-1)

    # Build the RIC frame at every sample from A's state.
    r_hat = r_A / np.linalg.norm(r_A, axis=-1, keepdims=True)
    h     = np.cross(r_A, v_A)
    c_hat = h / np.linalg.norm(h, axis=-1, keepdims=True)
    i_hat = np.cross(c_hat, r_hat)        # right-handed: i = c x r

    radial    = np.einsum("...j,...j->...", dr_in, r_hat)
    in_track  = np.einsum("...j,...j->...", dr_in, i_hat)
    cross_tr  = np.einsum("...j,...j->...", dr_in, c_hat)

    ax.plot(a["t"], radial,   label="radial")
    ax.plot(a["t"], in_track, label="in-track")
    ax.plot(a["t"], cross_tr, label="cross-track")
    ax.set_xlabel("t [s]"); ax.set_ylabel("Δr [km]")
    ax.set_title("Position error in RIC frame of A")
    ax.legend(loc="best"); ax.grid(True, alpha=0.3)


# Spliced into PLOTS["traj"] by registry.py (diffs read trajectory
# files). mode='diff' specs require exactly two files selected in the
# file tree (sorted top-down = A then B); mismatched grids fail with
# a clear message.
SPECS: list[PlotSpec] = [
    PlotSpec("|Δr| (log y)",                "2d", _plot_diff_r,
             category="Diff (pick 2 files)", mode="diff"),
    PlotSpec("|Δr| (linear y)",             "2d", _plot_diff_r_linear,
             category="Diff (pick 2 files)", mode="diff"),
    PlotSpec("|Δv| (log y)",                "2d", _plot_diff_v,
             category="Diff (pick 2 files)", mode="diff"),
    PlotSpec("|Δv| (linear y)",             "2d", _plot_diff_v_linear,
             category="Diff (pick 2 files)", mode="diff"),
    PlotSpec("Δx, Δy, Δz per component", "2d", _plot_diff_xyz,
             category="Diff (pick 2 files)", mode="diff"),
    PlotSpec("RIC frame  (radial/in-tr/cross-tr)", "2d", _plot_diff_ric,
             category="Diff (pick 2 files)", mode="diff"),
    PlotSpec("|Δr| distribution",            "2d", _plot_diff_r_distribution,
             category="Diff (pick 2 files)", mode="diff"),
    PlotSpec("|Δr| empirical CDF",           "2d", _plot_diff_r_cdf,
             category="Diff (pick 2 files)", mode="diff"),
]
