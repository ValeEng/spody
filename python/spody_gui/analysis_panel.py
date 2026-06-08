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
"""Analysis mode panel.

Layout:

    [Working dir: <path>]  [Change]  [Refresh]
    +-------------------+----------------------------------+
    | Files             | Plot: [<select>]  [Plot]         |
    | --- In folder --- | +------------------------------+ |
    |   run.bin         | |                              | |
    |   batch/x.bin     | |   matplotlib canvas          | |
    | --- External ---  | |                              | |
    |   /tmp/other.bin  | +------------------------------+ |
    | [+ Add external]  | type / record count / full path  |
    +-------------------+----------------------------------+

Selecting an item in the tree loads it into the canvas and rebuilds
the per-kind plot menu. Working dir is set externally by the main
window (after a Run, or when a TOML is opened) and can also be
changed manually with the Change button.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from matplotlib.axes import Axes
from matplotlib.backends.backend_qtagg import (
    FigureCanvasQTAgg,
    NavigationToolbar2QT,
)
from matplotlib.figure import Figure
from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from matplotlib import colormaps as mpl_colormaps

from spody_io import (
    EVENT_KIND_ECLIPSE,
    EVENT_KIND_IMPACT,
    SPODY_ACC_MAGIC,
    SPODY_BIN_MAGIC,
    SPODY_EVT_MAGIC,
    read_accelerations,
    read_events,
    read_trajectory,
)
# The aggregated batch-events magic is exposed through the same
# package but not re-exported by spody_io.__init__; import directly so
# _detect_kind can tell the two events formats apart.
from spody_io.headers import SPODY_EVTB_MAGIC
from .astronomy import sun_direction_j2000
from .settings import SettingsStore
from .vtk_canvas import VtkCanvas

# Recurse this many levels under the working dir when scanning for
# *.bin files. 3 covers the common `output/<UTC-ISO8601>/<case>.bin`
# pattern without crawling huge data trees by accident.
SCAN_MAX_DEPTH = 3

# Matches the per-run subfolder names spody.exe creates at launch
# (compact ISO 8601 UTC, see spody_io_make_run_subdir in app_io.c).
# Used by _refresh_tree to group output files by run instead of
# listing them in a flat tree.
import re as _re
_RUN_FOLDER_RE = _re.compile(r"^\d{4}-\d{2}-\d{2}T\d{6}Z$")

# Roles used to store the per-item full path on tree items, so we
# don't have to re-resolve from display text.
_PATH_ROLE = Qt.ItemDataRole.UserRole
# Same trick on the plot-selection tree: each leaf carries its
# PlotSpec so the click handler dispatches without index bookkeeping.
_SPEC_ROLE = Qt.ItemDataRole.UserRole + 1


# ----------------------------------------------------------------------
# Plot function signatures + per-kind plot registry
# ----------------------------------------------------------------------
# 2D plots receive a matplotlib Axes; 3D plots receive a VtkCanvas to
# add actors onto. The dispatcher (`AnalysisPanel._on_plot`) handles
# clear/reset/render so the plot fn only needs to express its content.
PlotFn2D = Callable[[Axes,      np.ndarray], None]
PlotFn3D = Callable[[VtkCanvas, np.ndarray], None]

# Overlay variants take a list of (source path, data array) so they
# can render N files together with a legend. Signature mirrors the
# single-file ones modulo the items list.
OverlayFn2D = Callable[[Axes,      list[tuple[Path, np.ndarray]]], None]
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
    (sorted top-down). Diff specs ignore `overlay_fn` -- they don't
    overlay, they subtract."""


def _plot_traj_r(ax: Axes, d: np.ndarray) -> None:
    r = np.sqrt(d["x"] ** 2 + d["y"] ** 2 + d["z"] ** 2)
    ax.plot(d["t"], r)
    ax.set_xlabel("t [s]"); ax.set_ylabel("|r| [km]")
    ax.set_title("Radial distance"); ax.grid(True, alpha=0.3)


def _plot_traj_v(ax: Axes, d: np.ndarray) -> None:
    v = np.sqrt(d["vx"] ** 2 + d["vy"] ** 2 + d["vz"] ** 2)
    ax.plot(d["t"], v)
    ax.set_xlabel("t [s]"); ax.set_ylabel("|v| [km/s]")
    ax.set_title("Speed"); ax.grid(True, alpha=0.3)


def _plot_traj_xyz(ax: Axes, d: np.ndarray) -> None:
    for name in ("x", "y", "z"):
        ax.plot(d["t"], d[name], label=name)
    ax.set_xlabel("t [s]"); ax.set_ylabel("position [km]")
    ax.set_title("Position components (inertial)")
    ax.legend(loc="best"); ax.grid(True, alpha=0.3)


def _plot_traj_vxyz(ax: Axes, d: np.ndarray) -> None:
    for name in ("vx", "vy", "vz"):
        ax.plot(d["t"], d[name], label=name)
    ax.set_xlabel("t [s]"); ax.set_ylabel("velocity [km/s]")
    ax.set_title("Velocity components (inertial)")
    ax.legend(loc="best"); ax.grid(True, alpha=0.3)


def _plot_traj_projection(ax: Axes, d: np.ndarray, a: str, b: str) -> None:
    ax.plot(d[a], d[b], lw=0.8)
    ax.scatter([d[a][0]],  [d[b][0]],  color="green", s=30, zorder=3, label="t=0")
    ax.scatter([d[a][-1]], [d[b][-1]], color="red",   s=30, zorder=3, label="end")
    ax.set_xlabel(f"{a} [km]"); ax.set_ylabel(f"{b} [km]")
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_title(f"Orbit projection: {a.upper()}{b.upper()}")
    ax.legend(loc="best"); ax.grid(True, alpha=0.3)


# Gravitational parameter of the Moon (km^3/s^2). spody v0 only
# supports the Moon as the central body, so this is also the only mu
# the orbital-elements solver needs today. When more bodies land
# (point G), thread mu through PlotSpec or read it from the binary.
MU_MOON_KM3_S2 = 4902.800066


def _orbital_elements(d: np.ndarray, mu: float = MU_MOON_KM3_S2
                      ) -> dict[str, np.ndarray]:
    """Classical orbital elements from state vectors at every sample.

    Returns a dict with the per-sample arrays:
        a    [km]     -- semi-major axis (vis-viva)
        e    [-]      -- eccentricity (magnitude of eccentricity vector)
        i    [deg]    -- inclination
        raan [deg]    -- right ascension of ascending node
        aop  [deg]    -- argument of periapsis
        nu   [deg]    -- true anomaly

    All math is vectorised across the full trajectory. The classical
    set has two degenerate cases that we handle explicitly:
        * Equatorial orbit (i ~ 0): RAAN is undefined; we fold its
          rotation into AOP and set RAAN = 0.
        * Circular orbit (e ~ 0): AOP and true anomaly are undefined;
          we set both to 0.
    The thresholds are tight (1e-8) so any realistic propagated orbit
    is unaffected.
    """
    r = np.stack((d["x"],  d["y"],  d["z"]),  axis=-1)         # (N, 3) km
    v = np.stack((d["vx"], d["vy"], d["vz"]), axis=-1)         # (N, 3) km/s
    r_mag = np.linalg.norm(r, axis=-1)
    v_mag = np.linalg.norm(v, axis=-1)

    # Specific angular momentum h = r x v -- normal to the orbit plane.
    h = np.cross(r, v)
    h_mag = np.linalg.norm(h, axis=-1)

    # Eccentricity vector e = (v x h)/mu - r_hat. |e| is the scalar
    # eccentricity; the vector points toward periapsis.
    r_hat = r / r_mag[..., None]
    e_vec = np.cross(v, h) / mu - r_hat
    e_mag = np.linalg.norm(e_vec, axis=-1)

    # Vis-viva: 1/a = 2/r - v^2/mu  ->  a = 1 / (2/r - v^2/mu).
    a = 1.0 / (2.0 / r_mag - v_mag ** 2 / mu)

    # Inclination from h_z / |h|. Clip to dodge tiny floating-point
    # excursions outside [-1, 1] that would NaN out arccos.
    cos_i = np.clip(h[..., 2] / h_mag, -1.0, 1.0)
    i_rad = np.arccos(cos_i)

    # Node line n = z_hat x h = (-h_y, h_x, 0).
    n = np.stack((-h[..., 1], h[..., 0], np.zeros_like(h_mag)), axis=-1)
    n_mag = np.linalg.norm(n, axis=-1)

    EPS = 1e-8
    equatorial = n_mag < EPS
    circular   = e_mag < EPS

    # RAAN = acos(n_x / |n|); quadrant flip from sign of n_y.
    safe_n = np.where(equatorial, 1.0, n_mag)
    cos_O = np.clip(n[..., 0] / safe_n, -1.0, 1.0)
    raan_rad = np.arccos(cos_O)
    raan_rad = np.where(n[..., 1] < 0, 2 * np.pi - raan_rad, raan_rad)
    raan_rad = np.where(equatorial, 0.0, raan_rad)

    # AOP = acos((n.e)/(|n||e|)); quadrant flip from sign of e_z.
    denom_w = np.where(equatorial | circular, 1.0, n_mag * e_mag)
    cos_w = np.clip(np.einsum("...j,...j->...", n, e_vec) / denom_w, -1.0, 1.0)
    aop_rad = np.arccos(cos_w)
    aop_rad = np.where(e_vec[..., 2] < 0, 2 * np.pi - aop_rad, aop_rad)
    aop_rad = np.where(equatorial | circular, 0.0, aop_rad)

    # True anomaly nu = acos((e.r)/(|e||r|)); flip from sign of r.v
    # (positive r.v means we're past periapsis but before apoapsis).
    denom_nu = np.where(circular, 1.0, e_mag * r_mag)
    cos_nu = np.clip(np.einsum("...j,...j->...", e_vec, r) / denom_nu, -1.0, 1.0)
    nu_rad = np.arccos(cos_nu)
    rdotv = np.einsum("...j,...j->...", r, v)
    nu_rad = np.where(rdotv < 0, 2 * np.pi - nu_rad, nu_rad)
    nu_rad = np.where(circular, 0.0, nu_rad)

    return {
        "a":    a,
        "e":    e_mag,
        "i":    np.degrees(i_rad),
        "raan": np.degrees(raan_rad),
        "aop":  np.degrees(aop_rad),
        "nu":   np.degrees(nu_rad),
    }


def _plot_traj_a(ax: Axes, d: np.ndarray) -> None:
    el = _orbital_elements(d)
    ax.plot(d["t"], el["a"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("a [km]")
    ax.set_title("Semi-major axis"); ax.grid(True, alpha=0.3)


def _plot_traj_e(ax: Axes, d: np.ndarray) -> None:
    el = _orbital_elements(d)
    ax.plot(d["t"], el["e"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("e [-]")
    ax.set_title("Eccentricity"); ax.grid(True, alpha=0.3)


def _plot_traj_i(ax: Axes, d: np.ndarray) -> None:
    el = _orbital_elements(d)
    ax.plot(d["t"], el["i"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("i [deg]")
    ax.set_title("Inclination"); ax.grid(True, alpha=0.3)


def _plot_traj_raan(ax: Axes, d: np.ndarray) -> None:
    el = _orbital_elements(d)
    ax.plot(d["t"], el["raan"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("Ω [deg]")
    ax.set_title("RAAN (right ascension of ascending node)")
    ax.grid(True, alpha=0.3)


def _plot_traj_aop(ax: Axes, d: np.ndarray) -> None:
    el = _orbital_elements(d)
    ax.plot(d["t"], el["aop"])
    ax.set_xlabel("t [s]"); ax.set_ylabel("ω [deg]")
    ax.set_title("Argument of periapsis"); ax.grid(True, alpha=0.3)


def _plot_traj_nu(ax: Axes, d: np.ndarray) -> None:
    # Per-revolution saw-tooth is the correct shape for the wrapped
    # true anomaly. On long propagations this gets visually busy;
    # the user can zoom in on the toolbar.
    el = _orbital_elements(d)
    ax.plot(d["t"], el["nu"], lw=0.6)
    ax.set_xlabel("t [s]"); ax.set_ylabel("ν [deg]")
    ax.set_title("True anomaly"); ax.grid(True, alpha=0.3)


# ----------------------------------------------------------------------
# Diff plots (two trajectories required).
#
# Diffs subtract trajectory B from trajectory A sample-by-sample. The
# dispatcher (_plot_diff, _on_tile_clicked) aligns the two grids
# upfront via `_align_or_interp` -- if they match, both pass through
# unchanged; if not, B is interpolated onto A's grid (cubic Hermite
# for position using v as derivative, linear for velocity) restricted
# to the overlapping time window. The plot functions below trust the
# alignment and just compute the deltas.
# ----------------------------------------------------------------------
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


def _align_or_interp(a: np.ndarray, b: np.ndarray
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


def _plot_events_timeline(ax: Axes, d: np.ndarray) -> None:
    if len(d) == 0:
        ax.set_title("No events recorded"); ax.set_xlabel("t [s]"); return
    labels = {EVENT_KIND_IMPACT: "IMPACT", EVENT_KIND_ECLIPSE: "ECLIPSE"}
    colors = {EVENT_KIND_IMPACT: "tab:red", EVENT_KIND_ECLIPSE: "tab:blue"}
    kinds  = sorted(int(k) for k in np.unique(d["kind"]))
    for k in kinds:
        mask = d["kind"] == k
        ax.scatter(d["t"][mask], np.full(mask.sum(), k),
                   color=colors.get(k, "tab:gray"),
                   label=labels.get(k, f"kind {k}"),
                   marker="|", s=200)
    ax.set_yticks(kinds)
    ax.set_yticklabels([labels.get(k, str(k)) for k in kinds])
    ax.set_xlabel("t [s]")
    ax.set_title(f"Event timeline ({len(d)} triggers)")
    ax.grid(True, axis="x", alpha=0.3)


# ----------------------------------------------------------------------
# 3D plots
# ----------------------------------------------------------------------
def _plot_traj_3d_orbit(canvas: VtkCanvas, d: np.ndarray) -> None:
    """Moon-centred view: grey sphere + yellow trajectory polyline +
    green/red start/end markers. Camera fitted to the trajectory.
    The polyline is *not* registered as pickable here because picking
    one of one trajectory adds no information."""
    canvas.add_central_body()
    pts = np.column_stack([d["x"], d["y"], d["z"]])
    canvas.add_trajectory(pts)


# ----------------------------------------------------------------------
# Overlay variants
# ----------------------------------------------------------------------
def _turbo_color(i: int, n: int) -> tuple[float, float, float]:
    """Evenly-spaced colour from the matplotlib 'turbo' palette so the
    extremes (low/high cases) don't both pin to the cmap endpoints when
    only two files are overlaid."""
    cmap = mpl_colormaps["turbo"]
    t = 0.5 if n <= 1 else i / (n - 1)
    r, g, b, _a = cmap(t)
    return (r, g, b)


def _make_2d_overlay(single_fn: PlotFn2D) -> OverlayFn2D:
    """Lift a single-file 2D plot to an N-file overlay. Pre-seeds the
    axes colour cycle so each `single_fn` call picks the next slot of
    the turbo palette; after each call we attach the file basename as
    the line label so the final `ax.legend()` lists them in order.

    Only safe for plots that add **one** line per call (otherwise the
    label tagging picks the wrong line). The registry annotates which
    `PlotSpec` entries qualify."""
    def overlay(ax: Axes, items: list[tuple[Path, np.ndarray]]) -> None:
        n = len(items)
        colors = [_turbo_color(i, n) for i in range(n)]
        ax.set_prop_cycle(color=colors)
        for path, data in items:
            single_fn(ax, data)
            lines = ax.get_lines()
            if lines:
                lines[-1].set_label(path.name)
        # Decorate the title set by the single-file fn so the overlay
        # nature is visible without us re-implementing the title text.
        ax.set_title(f"{ax.get_title()}  --  {n} files")
        ax.legend(loc="best", fontsize="small")
    return overlay


def _overlay_3d_orbit(canvas: VtkCanvas,
                       items: list[tuple[Path, np.ndarray]]) -> None:
    """3D Moon scene with N trajectories stacked, each in its own
    turbo colour, plus a viewport legend and Ctrl+click picking
    enabled on every polyline (via `source_path`)."""
    canvas.add_central_body()
    n = len(items)
    legend_items: list[tuple[str, tuple[float, float, float]]] = []
    for i, (path, data) in enumerate(items):
        color = _turbo_color(i, n)
        pts = np.column_stack([data["x"], data["y"], data["z"]])
        canvas.add_trajectory(
            pts, color=color, endpoint_markers=False, source_path=path,
        )
        legend_items.append((path.name, color))
    canvas.add_legend(legend_items)


# Inline lambdas wrapping `_plot_traj_projection` for the XY / XZ / YZ
# variants -- kept as named locals so they can be reused by their
# matching overlay helpers.
_p_xy = lambda ax, d: _plot_traj_projection(ax, d, "x", "y")
_p_xz = lambda ax, d: _plot_traj_projection(ax, d, "x", "z")
_p_yz = lambda ax, d: _plot_traj_projection(ax, d, "y", "z")


# Plot registry, grouped by file kind. Categories drive the visual
# grouping in the right-pane tree: same `category` string -> same
# folder; registry order is preserved so the folders stack in the
# order they first appear. Plots with empty `category` go at root
# level -- the kinds with only a few plots (accel, events) use that.
PLOTS: dict[str, list[PlotSpec]] = {
    "traj": [
        # ----- State vectors -----------------------------------------
        # All four come straight from the columns in the trajectory
        # dtype; the XYZ / VxVyVz ones draw three lines per file so
        # the overlay variant is intentionally None (3N lines would
        # be illegible).
        PlotSpec("Radial distance |r|",         "2d", _plot_traj_r,
                 overlay_fn=_make_2d_overlay(_plot_traj_r),
                 category="State vectors"),
        PlotSpec("Speed |v|",                   "2d", _plot_traj_v,
                 overlay_fn=_make_2d_overlay(_plot_traj_v),
                 category="State vectors"),
        PlotSpec("Position x, y, z",            "2d", _plot_traj_xyz,
                 category="State vectors"),
        PlotSpec("Velocity vx, vy, vz",         "2d", _plot_traj_vxyz,
                 category="State vectors"),
        # ----- Orbit shape --------------------------------------------
        PlotSpec("XY projection",               "2d", _p_xy,
                 overlay_fn=_make_2d_overlay(_p_xy),
                 category="Orbit shape"),
        PlotSpec("XZ projection",               "2d", _p_xz,
                 overlay_fn=_make_2d_overlay(_p_xz),
                 category="Orbit shape"),
        PlotSpec("YZ projection",               "2d", _p_yz,
                 overlay_fn=_make_2d_overlay(_p_yz),
                 category="Orbit shape"),
        PlotSpec("3D orbit + Moon",             "3d", _plot_traj_3d_orbit,
                 overlay_fn=_overlay_3d_orbit,
                 category="Orbit shape"),
        # ----- Orbital elements ---------------------------------------
        # Derived from r, v. All single-line so overlay-safe out of the
        # box. See _orbital_elements for the degenerate-case handling
        # (equatorial / circular).
        PlotSpec("Semi-major axis  a",          "2d", _plot_traj_a,
                 overlay_fn=_make_2d_overlay(_plot_traj_a),
                 category="Orbital elements"),
        PlotSpec("Eccentricity  e",             "2d", _plot_traj_e,
                 overlay_fn=_make_2d_overlay(_plot_traj_e),
                 category="Orbital elements"),
        PlotSpec("Inclination  i",              "2d", _plot_traj_i,
                 overlay_fn=_make_2d_overlay(_plot_traj_i),
                 category="Orbital elements"),
        PlotSpec("RAAN  Ω",                     "2d", _plot_traj_raan,
                 overlay_fn=_make_2d_overlay(_plot_traj_raan),
                 category="Orbital elements"),
        PlotSpec("Arg. periapsis  ω",           "2d", _plot_traj_aop,
                 overlay_fn=_make_2d_overlay(_plot_traj_aop),
                 category="Orbital elements"),
        PlotSpec("True anomaly  ν",             "2d", _plot_traj_nu,
                 overlay_fn=_make_2d_overlay(_plot_traj_nu),
                 category="Orbital elements"),
        # ----- Diff (pick 2 files) ------------------------------------
        # mode='diff' specs subtract B from A sample-by-sample. The
        # dispatcher requires exactly two files to be selected in the
        # file tree on the left (sorted top-down = A then B). Files
        # must share the same sample count + endpoints; mismatched
        # grids fail with a clear message.
        PlotSpec("|Δr| (log y)",                "2d", _plot_diff_r,
                 category="Diff (pick 2 files)", mode="diff"),
        PlotSpec("|Δr| (linear y)",             "2d", _plot_diff_r_linear,
                 category="Diff (pick 2 files)", mode="diff"),
        PlotSpec("|Δv| (log y)",                "2d", _plot_diff_v,
                 category="Diff (pick 2 files)", mode="diff"),
        PlotSpec("|Δv| (linear y)",             "2d", _plot_diff_v_linear,
                 category="Diff (pick 2 files)", mode="diff"),
        PlotSpec("Δx, Δy, Δz per component",    "2d", _plot_diff_xyz,
                 category="Diff (pick 2 files)", mode="diff"),
        PlotSpec("RIC frame  (radial/in-tr/cross-tr)", "2d", _plot_diff_ric,
                 category="Diff (pick 2 files)", mode="diff"),
    ],
    "accel": [
        # Three entries -- flat at the root, no point grouping.
        PlotSpec("Total  |a_total|",            "2d", _plot_acc_total,
                 overlay_fn=_make_2d_overlay(_plot_acc_total)),
        PlotSpec("Per-force breakdown (log y)", "2d", _plot_acc_breakdown),
        PlotSpec("Eclipse fraction",            "2d", _plot_acc_eclipse,
                 overlay_fn=_make_2d_overlay(_plot_acc_eclipse)),
    ],
    "events": [
        PlotSpec("Events timeline",             "2d", _plot_events_timeline),
    ],
}


# Friendly names for the kind tag shown in the type label.
_KIND_LABEL = {
    "traj":         "trajectory  (SPDYOUT_)",
    "accel":        "accelerations  (SPDYACC_)",
    "events":       "events log  (SPDYEVT_)",
    "events_batch": "events log  (SPDYEVTB, batch-aggregated)",
}

# `read_events` auto-detects per-run vs batch by peeking the magic and
# returns the matching numpy dtype; both kinds share the reader. The
# split in this map is only there so PLOTS / _KIND_LABEL can address
# them separately (batch events carry a `case_idx` column).
_READERS = {
    "traj":         read_trajectory,
    "accel":        read_accelerations,
    "events":       read_events,
    "events_batch": read_events,
}


def _detect_kind(path: Path) -> str | None:
    """Read the first 8 bytes and match against the known magics."""
    try:
        with path.open("rb") as fp:
            m = fp.read(8)
    except OSError:
        return None
    if m == SPODY_BIN_MAGIC:  return "traj"
    if m == SPODY_ACC_MAGIC:  return "accel"
    if m == SPODY_EVT_MAGIC:  return "events"
    if m == SPODY_EVTB_MAGIC: return "events_batch"
    return None


def _scan_bin_files(root: Path, max_depth: int) -> list[Path]:
    """Return all *.bin files under `root`, sorted by relative path,
    descending no deeper than `max_depth` directories. Silently skips
    anything we can't traverse."""
    out: list[Path] = []
    if not root.is_dir():
        return out
    # rglob would walk arbitrarily deep; we limit depth manually so a
    # huge tree pointed at by accident doesn't lock up the UI.
    def walk(d: Path, depth: int) -> None:
        try:
            entries = list(d.iterdir())
        except OSError:
            return
        for p in entries:
            if p.is_file() and p.suffix.lower() == ".bin":
                out.append(p)
            elif p.is_dir() and depth < max_depth:
                walk(p, depth + 1)
    walk(root, 0)
    out.sort(key=lambda p: str(p.relative_to(root)).lower())
    return out


# ----------------------------------------------------------------------
# Table view backing model (Tables tab)
# ----------------------------------------------------------------------
# Per-field display name maps for the events kinds, applied by
# NumpyTableModel.data when the cell value is an integer code we
# want to surface as a label (instead of the raw enum int).
_EVENT_KIND_LABEL = {
    EVENT_KIND_IMPACT:  "IMPACT",
    EVENT_KIND_ECLIPSE: "ECLIPSE",
}


# Display-name overrides for fields whose on-disk name is misleading
# in human display. Keyed by kind ("events" / "events_batch") so the
# rename only kicks in where it makes semantic sense.
#
# distance_km is the EventRecord's "trigger metric" slot: it carries
# whatever quantity tripped the predicate (distance in km for IMPACT,
# eclipse fraction in [0, 1] for ECLIPSE, etc.). The on-disk name is
# kept for backward compat but the table header surfaces the generic
# meaning.
_FIELD_DISPLAY_RENAME: dict[str, dict[str, str]] = {
    "events":       {"distance_km": "trigger_value"},
    "events_batch": {"distance_km": "trigger_value"},
}


def _expand_columns(arr: np.ndarray,
                    rename: dict[str, str] | None = None
                    ) -> list[tuple[str, str, int | None]]:
    """Flatten a structured numpy dtype into a list of display columns.
    Each tuple is `(display_name, field_name, sub_index)`:
    - field_name is the dtype field; sub_index is None for scalar
      fields or 0..N-1 for the components of a nested array field.
    - Fields whose name starts with an underscore (e.g. the `_pad`
      padding byte in BATCH_EVENT_DTYPE) are skipped so they don't
      clutter the view.
    - `rename` swaps the display name for fields whose on-disk name is
      misleading (see _FIELD_DISPLAY_RENAME)."""
    rename = rename or {}
    cols: list[tuple[str, str, int | None]] = []
    if arr.dtype.names is None:
        # Plain ndarray: one column per component.
        n = 1 if arr.ndim == 1 else arr.shape[1]
        for i in range(n):
            cols.append((f"col{i}", "", i))
        return cols
    for name in arr.dtype.names:
        if name.startswith("_"):
            continue
        display = rename.get(name, name)
        sub_dtype, _ = arr.dtype.fields[name]
        if sub_dtype.subdtype is not None:
            # Nested array, e.g. y[6] in EventRecord -> y0..y5
            length = sub_dtype.subdtype[1][0]
            for i in range(length):
                cols.append((f"{display}{i}", name, i))
        else:
            cols.append((display, name, None))
    return cols


def _format_cell(value, field_name: str) -> str:
    """Stringify one cell value for QTableView display. Floats get 12
    significant digits (round-trips a typical km-scale state vector
    without surprises); integers stay raw; the `kind` field gets the
    IMPACT/ECLIPSE label instead of its enum int."""
    if field_name == "kind":
        try:
            iv = int(value)
        except (TypeError, ValueError):
            return str(value)
        return _EVENT_KIND_LABEL.get(iv, str(iv))
    if isinstance(value, (float, np.floating)):
        return f"{float(value):.12g}"
    return str(value)


class NumpyTableModel(QAbstractTableModel):
    """QAbstractTableModel over a 1-D numpy structured array (events,
    accel, trajectory). Nested array fields (e.g. EventRecord.y[6])
    are flattened into N columns; private fields (starting with '_')
    are hidden so dtype padding never leaks into the view."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._arr: np.ndarray | None = None
        self._cols: list[tuple[str, str, int | None]] = []

    def set_array(self, arr: np.ndarray | None,
                  rename: dict[str, str] | None = None) -> None:
        """Swap the backing array. `rename` is a map of dtype-field
        name -> display name, used to relabel columns whose on-disk
        name doesn't match how the value is interpreted (e.g.
        EventRecord.distance_km is really a 'trigger_value' jolly)."""
        self.beginResetModel()
        self._arr = arr
        self._cols = (_expand_columns(arr, rename)
                      if arr is not None else [])
        self.endResetModel()

    def rowCount(self, _parent=QModelIndex()) -> int:  # noqa: B008
        return 0 if self._arr is None else int(len(self._arr))

    def columnCount(self, _parent=QModelIndex()) -> int:  # noqa: B008
        return len(self._cols)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if self._arr is None or not index.isValid():
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            return None
        display_name, field_name, sub_idx = self._cols[index.column()]
        row = self._arr[index.row()]
        if field_name == "":
            # Plain (non-structured) ndarray fallback.
            value = row if sub_idx is None else row[sub_idx]
        else:
            cell = row[field_name]
            value = cell if sub_idx is None else cell[sub_idx]
        return _format_cell(value, field_name)

    def headerData(self, section, orientation,
                   role=Qt.ItemDataRole.DisplayRole):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal:
            return self._cols[section][0]
        # Row header: 1-based index, easier to read off than 0-based.
        return str(section + 1)


class AnalysisPanel(QWidget):
    """File browser (working dir + external) + plot selector + canvas.

    State:
      * _working_dir : root for the auto-scanned section
      * _external    : list of Paths added via "+ Add external"
      * _kind/_data  : currently loaded binary's type tag + numpy array
    """

    def __init__(self, store: SettingsStore | None = None) -> None:
        super().__init__()
        # Settings store is used to look up the configured Moon-texture
        # path on every 3D plot dispatch (live, so changes via the
        # Settings dialog take effect on the next plot without restart).
        self._store = store if store is not None else SettingsStore()
        self._working_dir: Path | None = None
        self._external:    list[Path] = []
        self._kind: str | None = None
        self._data: np.ndarray | None = None
        self._path: Path | None = None
        self._loading_item = False   # guard against itemClicked re-entry

        # Top row: working dir ---------------------------------------
        self._dir_edit = QLineEdit()
        self._dir_edit.setReadOnly(True)
        self._dir_edit.setPlaceholderText("(no working dir -- run something or pick a folder)")
        btn_change  = QPushButton("Change...")
        btn_change.clicked.connect(self._on_change_dir)
        btn_refresh = QPushButton("⟳ Refresh")
        btn_refresh.clicked.connect(self._refresh_tree)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Working dir:"))
        dir_row.addWidget(self._dir_edit, 1)
        dir_row.addWidget(btn_change)
        dir_row.addWidget(btn_refresh)

        # Left pane: file tree + action buttons ----------------------
        # Multi-selection (Ctrl/Shift-click) feeds the overlay button;
        # single-click still triggers single-file load via itemClicked.
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(False)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.itemClicked.connect(self._on_tree_item_clicked)

        btn_add = QPushButton("+ Add external file...")
        btn_add.clicked.connect(self._on_add_external)
        # Overlay button uses the active plot (set by the plot tree
        # below the splitter): produces a 2D overlay when a 2D plot is
        # active and a 3D overlay otherwise (subject to spec.overlay_fn).
        btn_overlay = QPushButton("→ Overlay selected")
        btn_overlay.clicked.connect(self._on_overlay_selected)

        files_box = QWidget()
        files_lay = QVBoxLayout(files_box)
        files_lay.setContentsMargins(0, 0, 0, 0)
        files_lay.addWidget(self._tree, 1)
        files_lay.addWidget(btn_add)
        files_lay.addWidget(btn_overlay)

        # Plot tree (click-to-plot, grouped by category) lives in the
        # left column under a vertical splitter so the user can size
        # files vs plots to taste. The Plot button is gone -- selecting
        # a leaf in the tree fires the plot immediately and re-clicking
        # the active leaf re-plots.
        self._plot_tree = QTreeWidget()
        self._plot_tree.setHeaderHidden(True)
        self._plot_tree.setRootIsDecorated(True)
        self._plot_tree.setIndentation(14)
        self._plot_tree.setMinimumHeight(120)
        # ExtendedSelection: plain click resets to one leaf (fires
        # itemClicked = single-plot dispatch); Ctrl/Shift-click extends
        # the selection set, which the Tile button consumes.
        self._plot_tree.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._plot_tree.itemClicked.connect(self._on_plot_tree_clicked)
        self._plot_tree.itemSelectionChanged.connect(self._refresh_tile_button)

        # Dashboard / tile-mode: render N selected plots as subplots in
        # a single matplotlib figure. Counter on the button tells the
        # user how many leaves are currently selected.
        self._btn_tile = QPushButton("▦ Tile selected  (0)")
        self._btn_tile.setEnabled(False)
        self._btn_tile.clicked.connect(self._on_tile_clicked)

        plots_box = QWidget()
        plots_lay = QVBoxLayout(plots_box)
        plots_lay.setContentsMargins(0, 0, 0, 0)
        plots_lay.addWidget(QLabel("Plots:"))
        plots_lay.addWidget(self._plot_tree, 1)
        plots_lay.addWidget(self._btn_tile)

        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.addWidget(files_box)
        left_splitter.addWidget(plots_box)
        # 60/40 default, both stretch when the user resizes the window.
        left_splitter.setStretchFactor(0, 3)
        left_splitter.setStretchFactor(1, 2)
        left_splitter.setSizes([360, 240])

        left = left_splitter

        # Last-clicked PlotSpec, set by _on_plot_tree_clicked and read
        # by Plot / Overlay so both share one notion of "active plot".
        self._active_spec: PlotSpec | None = None

        # Sun-arrow controls (3D only). The epoch field auto-fills from
        # the TOML currently open in the Run tab; user can override.
        # Wrapped in its own widget so the whole row can be hidden when
        # the active plot is 2D (Sun arrow has no meaning there).
        self._epoch_edit = QLineEdit()
        self._epoch_edit.setPlaceholderText("et_start_s (TDB sec past J2000)")
        btn_sun = QPushButton("+ Sun arrow")
        btn_sun.clicked.connect(self._on_add_sun)
        sun_row = QHBoxLayout()
        sun_row.setContentsMargins(0, 0, 0, 0)
        sun_row.addWidget(QLabel("Epoch:"))
        sun_row.addWidget(self._epoch_edit, 1)
        sun_row.addWidget(btn_sun)
        self._sun_widget = QWidget()
        self._sun_widget.setLayout(sun_row)
        self._sun_widget.setVisible(False)   # hidden until a 3D plot fires

        # 2D page: matplotlib canvas + toolbar in a sub-widget.
        self._figure  = Figure(figsize=(6, 4))
        self._canvas  = FigureCanvasQTAgg(self._figure)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)
        mpl_page = QWidget()
        mpl_lay = QVBoxLayout(mpl_page)
        mpl_lay.setContentsMargins(0, 0, 0, 0)
        mpl_lay.addWidget(self._toolbar)
        mpl_lay.addWidget(self._canvas, 1)

        # 3D page: VTK widget with its own built-in mouse controls,
        # plus Ctrl+left-click picking wired to highlight the source
        # file in the tree and the info label.
        self._vtk = VtkCanvas()
        self._vtk.set_pick_callback(self._on_pick)

        # Stack switched by the dispatcher in `_on_plot` based on
        # PlotSpec.dim. Index 0 = 2D, index 1 = 3D.
        self._stack = QStackedWidget()
        self._stack.addWidget(mpl_page)
        self._stack.addWidget(self._vtk)

        self._info_label = QLabel("(no file loaded)")
        self._info_label.setStyleSheet("color: gray;")
        self._info_label.setWordWrap(True)

        # Plot tab content: sun bar + 2D/3D stack. Stays unchanged.
        plot_tab = QWidget()
        plot_lay = QVBoxLayout(plot_tab)
        plot_lay.setContentsMargins(0, 0, 0, 0)
        plot_lay.addWidget(self._sun_widget)
        plot_lay.addWidget(self._stack, 1)

        # Table tab content: raw record view of the loaded file. The
        # selection model is spreadsheet-style:
        #   - click on a cell            -> select that cell
        #   - click on a column header   -> select the whole column
        #   - click on a row index       -> select the whole row
        #   - Shift/Ctrl click extend the selection (rectangular or
        #     additive); Ctrl+C copies the selection as TSV into the
        #     system clipboard (paste straight into Excel / Sheets).
        self._table_model = NumpyTableModel()
        self._table_view  = QTableView()
        self._table_view.setModel(self._table_model)
        self._table_view.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table_view.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems)
        self._table_view.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table_view.setAlternatingRowColors(True)
        h_header = self._table_view.horizontalHeader()
        h_header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        h_header.setSectionsClickable(True)
        # Clicking a column header selects the whole column. Same for
        # the vertical (row-index) header below. Qt fires sectionClicked
        # with the section's int index; selectColumn/selectRow take
        # exactly that, so the wiring is one connect each.
        h_header.sectionClicked.connect(self._table_view.selectColumn)
        v_header = self._table_view.verticalHeader()
        v_header.setSectionsClickable(True)
        v_header.sectionClicked.connect(self._table_view.selectRow)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._table_view.setFont(mono)
        # Ctrl+C handler. Qt's default for QTableView only copies the
        # current item (one cell); our shortcut serialises the whole
        # selection rectangle as TSV (Tab-separated, one row per line),
        # which is what spreadsheets and notebooks expect on paste.
        copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy,
                                  self._table_view)
        copy_shortcut.activated.connect(self._copy_table_selection)
        table_tab = QWidget()
        table_lay = QVBoxLayout(table_tab)
        table_lay.setContentsMargins(0, 0, 0, 0)
        table_lay.addWidget(self._table_view, 1)

        # Top-level tabs: clicking a file populates whichever tab is
        # active right now; switching tab on an already-loaded file
        # repopulates the new view from the cached array (no re-read).
        self._right_tabs = QTabWidget()
        self._right_tabs.addTab(plot_tab,  "Plot")
        self._right_tabs.addTab(table_tab, "Table")
        self._right_tabs.currentChanged.connect(self._on_right_tab_changed)

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.addWidget(self._right_tabs, 1)
        right_lay.addWidget(self._info_label)

        # Body splitter: left files | right plot ----------------------
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 1020])

        layout = QVBoxLayout(self)
        layout.addLayout(dir_row)
        layout.addWidget(splitter, 1)

        self._refresh_tree()

    # ------------------------------------------------------------------
    # Public API (used by MainWindow)
    # ------------------------------------------------------------------
    def set_working_dir(self, path: Path | None) -> None:
        """Set the directory that the 'In folder' section scans. Called
        by the main window after a Run finishes or a TOML is opened."""
        if path is None:
            self._working_dir = None
        else:
            self._working_dir = Path(path)
        self._dir_edit.setText(str(self._working_dir) if self._working_dir else "")
        self._refresh_tree()

    def set_default_epoch(self, et_seconds: float | None) -> None:
        """Pre-fill the Sun-arrow epoch from the currently-open TOML.
        Called by MainWindow on Open / Save. Does not overwrite a value
        the user has already typed manually."""
        if et_seconds is None:
            return
        if self._epoch_edit.text().strip():
            return
        self._epoch_edit.setText(repr(float(et_seconds)))

    # ------------------------------------------------------------------
    # Tree management
    # ------------------------------------------------------------------
    def _refresh_tree(self) -> None:
        """Rebuild the tree from the current working dir + external list.

        Output files are grouped by the per-run timestamp folder spody.exe
        creates at launch (`<output_dir>/<UTC-ISO8601>/...`): each run
        becomes its own collapsible section, most-recent first. Anything
        outside a run folder lands in a 'Loose files' tail group so the
        flat layout from before this slice still surfaces.

        Selection is dropped on every refresh (no auto-load); the user
        picks an item to load explicitly."""
        self._tree.clear()

        if self._working_dir is None:
            self._tree.addTopLevelItem(self._make_header("In folder (none)"))
        else:
            files = _scan_bin_files(self._working_dir, SCAN_MAX_DEPTH)
            grouped = self._group_files_by_run(files, self._working_dir)
            self._tree.addTopLevelItem(self._make_header(
                f"In folder ({self._working_dir}) -- "
                f"{len(grouped)} run group(s), {len(files)} file(s)"))
            # Render run groups newest-first (timestamps sort
            # lexicographically the same as chronologically). Loose
            # files (None key) go last.
            run_keys = sorted((k for k in grouped if k is not None),
                              reverse=True)
            for key in run_keys:
                header = self._make_header(f"  run: {key}")
                self._tree.addTopLevelItem(header)
                for p in grouped[key]:
                    child = QTreeWidgetItem([p.name])
                    child.setData(0, _PATH_ROLE, str(p))
                    child.setToolTip(0, str(p))
                    header.addChild(child)
                header.setExpanded(True)
            if None in grouped:
                loose_header = self._make_header(
                    f"  loose files ({len(grouped[None])})")
                self._tree.addTopLevelItem(loose_header)
                for p in grouped[None]:
                    rel = p.relative_to(self._working_dir)
                    child = QTreeWidgetItem([str(rel).replace("\\", "/")])
                    child.setData(0, _PATH_ROLE, str(p))
                    child.setToolTip(0, str(p))
                    loose_header.addChild(child)
                loose_header.setExpanded(False)

        external_header = self._make_header(
            f"External ({len(self._external)})"
        )
        self._tree.addTopLevelItem(external_header)
        for p in self._external:
            child = QTreeWidgetItem([p.name])
            child.setData(0, _PATH_ROLE, str(p))
            child.setToolTip(0, str(p))
            external_header.addChild(child)
        external_header.setExpanded(True)

    @staticmethod
    def _group_files_by_run(files: list[Path], root: Path
                            ) -> dict[str | None, list[Path]]:
        """Walk each file's ancestors and find the closest one whose
        name matches the run-folder pattern (compact ISO 8601 UTC, e.g.
        '2026-06-05T195819Z'). The returned dict keys are those folder
        names; files with no run-folder ancestor are bucketed under
        None. Within each bucket files keep their scan order (which is
        already path-sorted)."""
        out: dict[str | None, list[Path]] = {}
        try:
            root_resolved = root.resolve()
        except OSError:
            root_resolved = root
        for p in files:
            run_key: str | None = None
            try:
                for ancestor in p.resolve().parents:
                    # Stop once we reach the working dir; no point
                    # looking further up.
                    if ancestor == root_resolved:
                        break
                    if _RUN_FOLDER_RE.match(ancestor.name):
                        run_key = ancestor.name
                        break
            except OSError:
                pass
            out.setdefault(run_key, []).append(p)
        return out

    @staticmethod
    def _make_header(text: str) -> QTreeWidgetItem:
        """A non-selectable section header inside the tree."""
        item = QTreeWidgetItem([text])
        flags = item.flags()
        flags &= ~Qt.ItemFlag.ItemIsSelectable
        item.setFlags(flags)
        f = QFont()
        f.setBold(True)
        item.setFont(0, f)
        return item

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------
    def _on_change_dir(self) -> None:
        start = str(self._working_dir) if self._working_dir else ""
        path = QFileDialog.getExistingDirectory(self, "Working directory", start)
        if path:
            self.set_working_dir(Path(path))

    def _on_right_tab_changed(self, idx: int) -> None:
        """Switching to the Plot tab on an already-loaded file that has
        no current plot triggers the default render -- otherwise the
        canvas would stay blank until the user clicked something in the
        plot tree. No-op on the Table side: the model is always in
        sync with `self._data`."""
        if idx == 0 and self._data is not None and self._kind is not None:
            current = self._plot_tree.currentItem()
            if current is None or current.data(0, _SPEC_ROLE) is None:
                first = self._first_plot_leaf()
                if first is not None:
                    self._plot_tree.setCurrentItem(first)
                    self._on_plot_tree_clicked(first, 0)

    def _copy_table_selection(self) -> None:
        """Dump the current table selection to the clipboard as TSV.

        Layout: rows in ascending row order, columns in ascending
        column order; cells outside the selection (when the user has
        picked a non-rectangular set) are emitted as empty fields so
        the row alignment is preserved. Numbers reuse the same
        12-significant-digit format the cells show on screen, so the
        text round-trips back into the same value after parsing."""
        sel = self._table_view.selectionModel().selectedIndexes()
        if not sel:
            return
        rows = sorted({idx.row()    for idx in sel})
        cols = sorted({idx.column() for idx in sel})
        selected = {(idx.row(), idx.column()): idx for idx in sel}
        model = self._table_model
        lines: list[str] = []
        for r in rows:
            cells: list[str] = []
            for c in cols:
                if (r, c) in selected:
                    val = model.data(model.index(r, c),
                                     Qt.ItemDataRole.DisplayRole)
                    cells.append("" if val is None else str(val))
                else:
                    cells.append("")
            lines.append("\t".join(cells))
        QApplication.clipboard().setText("\n".join(lines))

    def _on_add_external(self) -> None:
        start = str(self._path.parent) if self._path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Add external spody binary", start,
            "spody binaries (*.bin);;All files (*)",
        )
        if not path:
            return
        p = Path(path)
        if p not in self._external:
            self._external.append(p)
        self._refresh_tree()
        # Convenience: also load the just-added file.
        self.load_file(p)

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        if self._loading_item:
            return
        raw = item.data(0, _PATH_ROLE)
        if not raw:
            return   # header item, ignore
        self.load_file(Path(raw))

    def _on_overlay_selected(self) -> None:
        """Overlay all selected files (matching the currently-loaded
        kind) using the active plot from the right-pane tree. Works
        for 2D and 3D depending on `spec.dim`; specs without an
        `overlay_fn` (e.g. per-component plots that draw 3 lines per
        file) trigger an explanatory message instead of an unreadable
        overlay."""
        if self._kind is None:
            QMessageBox.information(
                self, "Pick a file first",
                "Click a file in the tree to set the kind, then Ctrl/Shift-"
                "click the others you want to overlay."
            )
            return
        spec = self._active_spec
        if spec is None:
            QMessageBox.information(
                self, "Pick a plot first",
                "Click a plot in the plot tree, then press Overlay to "
                "stack the currently-selected files using that plot."
            )
            return
        if spec.mode == "diff":
            QMessageBox.information(
                self, "Overlay not applicable to a diff plot",
                f"'{spec.label}' is a diff plot -- click it directly with "
                "two files selected in the file tree; the diff dispatch "
                "fires automatically. Overlay is for single-file plots.")
            return
        if spec.overlay_fn is None:
            QMessageBox.information(
                self, "Overlay not supported",
                f"'{spec.label}' draws multiple lines per file, so an "
                "overlay would not be legible. Pick a single-series plot "
                "(e.g. |r|(t), |v|(t), an orbit projection, |a_total|, "
                "eclipse fraction, or '3D orbit + Moon') and try again."
            )
            return

        # Collect selected files that match the loaded kind; the
        # mismatch case (mixed kinds) is reported at the end so users
        # see exactly which files were skipped.
        paths: list[Path] = []
        skipped: list[str] = []
        for item in self._tree.selectedItems():
            raw = item.data(0, _PATH_ROLE)
            if not raw:
                continue
            p = Path(raw)
            kind = _detect_kind(p)
            if kind == self._kind:
                paths.append(p)
            else:
                skipped.append(f"{p.name} ({kind or 'unknown'})")
        if not paths:
            QMessageBox.information(
                self, "Nothing to overlay",
                f"Select one or more {self._kind} binaries in the tree first."
            )
            return

        # Read all selected files up front so a malformed one is
        # surfaced cleanly rather than mid-render.
        items: list[tuple[Path, np.ndarray]] = []
        reader = _READERS[self._kind]
        for p in paths:
            try:
                items.append((p, reader(p)))
            except (OSError, ValueError) as exc:
                skipped.append(f"{p.name} ({exc})")
        if not items:
            QMessageBox.critical(self, "Overlay failed",
                                 "None of the selected files could be read.")
            return

        try:
            if spec.dim == "2d":
                self._stack.setCurrentIndex(0)
                self._figure.clear()
                ax = self._figure.add_subplot(111)
                spec.overlay_fn(ax, items)
                self._figure.tight_layout()
                self._canvas.draw_idle()
            else:  # "3d"
                self._stack.setCurrentIndex(1)
                self._vtk.set_central_body_texture(self._configured_moon_texture())
                self._vtk.clear_scene()
                spec.overlay_fn(self._vtk, items)
                self._vtk.reset_camera()
                self._vtk.render()
        except Exception as exc:  # noqa: BLE001 -- surface anything to user
            QMessageBox.critical(self, "Overlay failed", repr(exc))
            return

        msg = (f"{spec.dim.upper()} overlay: {len(items)} files "
               f"({spec.label})\n" + "\n".join(str(p) for p, _ in items))
        if skipped:
            msg += "\n\nSkipped:\n" + "\n".join(skipped)
        self._info_label.setText(msg)
        self._info_label.setStyleSheet("")

    def load_file(self, path: Path) -> None:
        """Load a binary into the canvas. Auto-detects the kind from
        the file's magic; populates the plot tree accordingly and
        renders the first option immediately."""
        kind = _detect_kind(path)
        if kind is None:
            QMessageBox.warning(
                self, "Unknown file",
                f"{path.name} is not a spody binary "
                "(expected magic SPDYOUT_, SPDYACC_, or SPDYEVT_)."
            )
            return
        try:
            data = _READERS[kind](path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Read failed", str(exc))
            return

        self._path = path
        self._kind = kind
        self._data = data
        self._info_label.setText(
            f"{_KIND_LABEL[kind]} -- {len(data)} records\n{path}"
        )
        self._info_label.setStyleSheet("")

        # Always refresh the Table model so a tab switch later in the
        # session shows the right rows without re-reading the file.
        self._table_model.set_array(data, _FIELD_DISPLAY_RENAME.get(kind))

        # Rebuild the plot tree for the new kind. Auto-render the first
        # plot only when the Plot tab is currently active; if the user
        # is looking at the Table tab we leave the Plot view empty
        # until they switch back -- avoids spending I/O / VTK time on
        # an off-screen view.
        self._populate_plot_tree(kind)
        if self._right_tabs.currentIndex() == 0:
            first = self._first_plot_leaf()
            if first is not None:
                self._plot_tree.setCurrentItem(first)
                self._on_plot_tree_clicked(first, 0)

    # ------------------------------------------------------------------
    # Plot tree management
    # ------------------------------------------------------------------
    def _populate_plot_tree(self, kind: str) -> None:
        """Rebuild the right-pane tree for the given file kind. Specs
        with non-empty `category` get grouped under a bold folder; the
        rest live at root level. Registry order is preserved so the
        groups stack in a stable order."""
        self._plot_tree.clear()
        # Lazy import: avoid hardcoding a category-order list -- the
        # first time we encounter a category we create its folder, and
        # subsequent specs with the same string attach as children.
        folders: dict[str, QTreeWidgetItem] = {}
        for spec in PLOTS.get(kind, []):
            if not spec.category:
                # Root-level leaf.
                leaf = QTreeWidgetItem([spec.label])
                leaf.setData(0, _SPEC_ROLE, spec)
                self._plot_tree.addTopLevelItem(leaf)
                continue
            folder = folders.get(spec.category)
            if folder is None:
                folder = self._make_plot_folder(spec.category)
                folders[spec.category] = folder
                self._plot_tree.addTopLevelItem(folder)
            leaf = QTreeWidgetItem([spec.label])
            leaf.setData(0, _SPEC_ROLE, spec)
            folder.addChild(leaf)
        # Expand everything so the user sees the full menu at a glance.
        for folder in folders.values():
            folder.setExpanded(True)

    @staticmethod
    def _make_plot_folder(text: str) -> QTreeWidgetItem:
        """A bold, non-selectable folder header inside the plot tree."""
        item = QTreeWidgetItem([text])
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
        f = QFont()
        f.setBold(True)
        item.setFont(0, f)
        return item

    def _first_plot_leaf(self) -> QTreeWidgetItem | None:
        """First leaf (root-level or first child of the first folder)
        in the plot tree. Used by `load_file` to auto-plot something
        sensible on each new file."""
        for i in range(self._plot_tree.topLevelItemCount()):
            top = self._plot_tree.topLevelItem(i)
            if top.data(0, _SPEC_ROLE) is not None:
                return top
            if top.childCount() > 0:
                return top.child(0)
        return None

    def _on_plot_tree_clicked(self, item: QTreeWidgetItem, _col: int) -> None:
        """Leaf click -> dispatch the plot. Header clicks (no stored
        spec) are ignored (Qt still toggles their expansion state)."""
        spec = item.data(0, _SPEC_ROLE)
        if spec is None:
            return
        self._active_spec = spec
        # Sun-arrow row only makes sense once a 3D scene is up.
        self._sun_widget.setVisible(spec.dim == "3d")
        if spec.mode == "diff":
            self._plot_diff(spec)
        else:
            self._plot_active()

    def _refresh_tile_button(self) -> None:
        """Live counter + enable gate for the Tile button. The button
        accepts >= 2 selected leaves (tile mode is uninteresting with
        a single plot -- that's just the single-click path)."""
        n = sum(1 for it in self._plot_tree.selectedItems()
                if it.data(0, _SPEC_ROLE) is not None)
        self._btn_tile.setText(f"▦ Tile selected  ({n})")
        self._btn_tile.setEnabled(n >= 2)

    # Soft cap so the user doesn't accidentally render 30 subplots
    # into a 280-pixel canvas; we surface a friendly error instead.
    TILE_MAX_PLOTS = 12

    def _on_tile_clicked(self) -> None:
        """Render the multi-selected plot tree leaves as subplots in a
        single matplotlib figure. Grid is `ceil(sqrt(N)) x ceil(N/cols)`
        so 4 plots -> 2x2, 6 -> 2x3, 9 -> 3x3. All-single and all-diff
        selections are supported; mixed sets and 3D plots are rejected
        with a clear message."""
        if self._kind is None:
            QMessageBox.information(
                self, "Pick a file first",
                "Click a file in the tree, then Ctrl/Shift-click plots in "
                "the plot tree and press Tile.")
            return
        specs = [
            it.data(0, _SPEC_ROLE)
            for it in self._plot_tree.selectedItems()
            if it.data(0, _SPEC_ROLE) is not None
        ]
        # Drop 3D specs upfront so we can report them cleanly.
        excluded_3d = [s for s in specs if s.dim != "2d"]
        specs = [s for s in specs if s.dim == "2d"]
        if not specs:
            QMessageBox.information(
                self, "No 2D plots selected",
                "Tile mode works only with 2D plots. Ctrl/Shift-click "
                "two or more 2D plot leaves in the plot tree.")
            return
        if len(specs) > self.TILE_MAX_PLOTS:
            QMessageBox.warning(
                self, "Too many plots to tile",
                f"Capped at {self.TILE_MAX_PLOTS} subplots for legibility "
                f"({len(specs)} selected).")
            return

        # Decide dispatch mode from the selection. Mixed is rejected:
        # diff plots need 2 files, single plots need 1 -- their
        # canvases would compete for the same file-tree selection.
        modes = {s.mode for s in specs}
        if len(modes) > 1:
            QMessageBox.information(
                self, "Mixed plot modes",
                "Tile cannot mix single-file and diff plots in one figure "
                "(they read from the file tree differently). Pick from one "
                "category at a time.")
            return
        mode = modes.pop()

        # Resolve the data argument(s) once -- every subplot draws into
        # the same dataset(s) so we read disk once, not N times.
        if mode == "diff":
            paths, err = self._collect_two_diff_files()
            if err is not None:
                QMessageBox.information(self, "Diff tile", err)
                return
            reader = _READERS[self._kind]
            try:
                data_a = reader(paths[0])
                data_b = reader(paths[1])
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "Diff read failed", str(exc))
                return
            # Align once; every subplot then operates on identical
            # (possibly interpolated) arrays.
            try:
                data_a, data_b, was_interp, _note = _align_or_interp(
                    data_a, data_b)
            except ValueError as exc:
                QMessageBox.warning(self, "Tile incompatible", str(exc))
                return
            subtitle = f"A = {paths[0].name}    B = {paths[1].name}"
            if was_interp:
                subtitle += "    (B interpolated)"
        else:   # mode == "single"
            if self._data is None:
                QMessageBox.information(
                    self, "Pick a file first",
                    "Single-file tile mode needs a file loaded "
                    "(click one in the file tree).")
                return
            subtitle = self._path.name if self._path else ""

        n = len(specs)
        cols = math.ceil(math.sqrt(n))
        rows = math.ceil(n / cols)

        try:
            self._stack.setCurrentIndex(0)
            self._figure.clear()
            for i, spec in enumerate(specs):
                ax = self._figure.add_subplot(rows, cols, i + 1)
                if mode == "diff":
                    spec.fn(ax, data_a, data_b)
                else:
                    spec.fn(ax, self._data)
                # Shrink labels in tile mode -- matplotlib's default
                # sizes are calibrated for a single full-canvas plot.
                ax.title.set_size("small")
                ax.tick_params(labelsize="small")
                ax.xaxis.label.set_size("small")
                ax.yaxis.label.set_size("small")
                # Legends already use fontsize='small' or 'best'; nothing
                # to do for plots that don't add one.
            if subtitle:
                self._figure.suptitle(subtitle, fontsize="small")
            self._figure.tight_layout()
            self._canvas.draw_idle()
        except ValueError as exc:
            # Bubbled up from a plot fn (e.g. degenerate orbital
            # element math); kept as a clean message rather than a
            # raw traceback dialog.
            QMessageBox.warning(self, "Tile incompatible", str(exc))
            return
        except Exception as exc:  # noqa: BLE001 -- surface anything to user
            QMessageBox.critical(self, "Tile failed", repr(exc))
            return

        msg = f"Tile: {len(specs)} plots ({rows}x{cols})"
        if excluded_3d:
            msg += f"\n(skipped {len(excluded_3d)} 3D plot(s) -- tile is 2D-only)"
        self._info_label.setText(msg)
        self._info_label.setStyleSheet("")

    def _collect_two_diff_files(self) -> tuple[list[Path], str | None]:
        """Return the two file-tree-selected paths matching the active
        kind, or an error message string for the caller to surface.
        Extracted so `_plot_diff` and `_on_tile_clicked` share the
        selection-validation rule."""
        paths: list[Path] = []
        skipped: list[str] = []
        for it in self._tree.selectedItems():
            raw = it.data(0, _PATH_ROLE)
            if not raw:
                continue
            p = Path(raw)
            kind = _detect_kind(p)
            if kind == self._kind:
                paths.append(p)
            else:
                skipped.append(f"{p.name} ({kind or 'unknown'})")
        if len(paths) != 2:
            extra = ("\n\nSkipped (kind mismatch):\n" + "\n".join(skipped)
                     if skipped else "")
            return paths, (
                f"Diff needs exactly 2 {self._kind} files in the file "
                f"tree (Ctrl/Shift-click). Currently selected: "
                f"{len(paths)}.{extra}"
            )
        return paths, None

    # ------------------------------------------------------------------
    # Sun arrow
    # ------------------------------------------------------------------
    def _on_add_sun(self) -> None:
        """Compute the Sun direction at the typed epoch and add an
        arrow to the 3D scene. Requires the 3D canvas to be active
        and a valid numeric epoch in the field. Re-plotting (Plot /
        Overlay) clears the arrow as part of `clear_scene()`."""
        text = self._epoch_edit.text().strip()
        if not text:
            QMessageBox.information(
                self, "Sun arrow",
                "Type an epoch (TDB seconds past J2000) first; usually the "
                "same value as your TOML's simulation.et_start_s."
            )
            return
        try:
            et = float(text)
        except ValueError:
            QMessageBox.warning(self, "Sun arrow",
                                f"'{text}' is not a valid number.")
            return
        if self._stack.currentIndex() != 1:
            QMessageBox.information(
                self, "Sun arrow",
                "The Sun arrow is rendered in the 3D scene. Pick a 3D plot "
                "(e.g. '3D orbit + Moon' or '→ Overlay selected (3D)') first."
            )
            return
        d = sun_direction_j2000(et)
        self._vtk.add_sun_arrow(d)
        self._vtk.render()

    # ------------------------------------------------------------------
    # Picking (Ctrl+left-click on a trajectory in the 3D scene)
    # ------------------------------------------------------------------
    def _on_pick(self, path: Path | None) -> None:
        """Callback for VtkCanvas: the user Ctrl+left-clicked an
        overlaid trajectory. Update the info label and reflect the
        selection in the tree on the left so the user can immediately
        recognise which file the picked polyline came from."""
        if path is None:
            return
        self._info_label.setText(f"Picked: {path}")
        self._info_label.setStyleSheet("")
        self._highlight_path_in_tree(path)

    def _highlight_path_in_tree(self, path: Path) -> None:
        """Find the tree item whose UserRole matches `path` and make it
        the current item (without firing a load)."""
        target = str(path)
        for i in range(self._tree.topLevelItemCount()):
            header = self._tree.topLevelItem(i)
            for j in range(header.childCount()):
                child = header.child(j)
                if child.data(0, _PATH_ROLE) == target:
                    self._loading_item = True
                    try:
                        self._tree.setCurrentItem(child)
                    finally:
                        self._loading_item = False
                    return

    def _configured_moon_texture(self) -> Path | None:
        """Look up the Moon texture path from Settings on demand so
        edits via the Settings dialog take effect on the next 3D plot
        without restarting. Returns None when unset, so VtkCanvas
        falls back to the flat-grey sphere."""
        raw = self._store.moon_texture()
        return Path(raw) if raw else None

    def _plot_diff(self, spec: PlotSpec) -> None:
        """Render a mode='diff' plot: two trajectories selected in the
        file tree (sorted top-down = A then B) are read and passed to
        `spec.fn(ax, data_a, data_b)`. Mismatched kinds / wrong file
        count / read failures surface via message box so the user
        knows what to fix."""
        if self._kind is None:
            QMessageBox.information(
                self, "Pick a file first",
                "Click a file in the tree to set the kind, then pick a "
                "diff plot with two files selected.")
            return
        paths, err = self._collect_two_diff_files()
        if err is not None:
            QMessageBox.information(self, "Diff needs exactly 2 files", err)
            return

        reader = _READERS[self._kind]
        try:
            data_a = reader(paths[0])
            data_b = reader(paths[1])
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Diff read failed", str(exc))
            return

        # Align upfront so the plot fn just subtracts; same path
        # whether the grids match or B had to be interpolated.
        try:
            data_a, data_b, was_interp, note = _align_or_interp(data_a, data_b)
        except ValueError as exc:
            # Disjoint windows or fewer than 2 overlapping samples --
            # expected failure mode, surface as info not crash.
            QMessageBox.warning(self, "Diff incompatible", str(exc))
            return

        try:
            self._stack.setCurrentIndex(0)        # diff plots are 2D only
            self._figure.clear()
            ax = self._figure.add_subplot(111)
            spec.fn(ax, data_a, data_b)
            subtitle = f"A = {paths[0].name}    B = {paths[1].name}"
            if was_interp:
                subtitle += "    (B interpolated)"
            ax.set_title(f"{ax.get_title()}\n{subtitle}", fontsize="small")
            self._figure.tight_layout()
            self._canvas.draw_idle()
        except Exception as exc:  # noqa: BLE001 -- surface anything to user
            QMessageBox.critical(self, "Diff failed", repr(exc))
            return

        info = f"Diff: {spec.label}\nA = {paths[0]}\nB = {paths[1]}"
        if was_interp:
            info += f"\n{note}"
        self._info_label.setText(info)
        self._info_label.setStyleSheet("")

    def _plot_active(self) -> None:
        """Dispatch the active PlotSpec (last leaf clicked in the
        plot tree) to the right canvas. 2D plots are drawn into the
        matplotlib figure; 3D plots into the VTK scene. Each branch
        is fully responsible for its canvas lifecycle so individual
        plot functions stay tiny. Called by the tree's itemClicked
        handler -- re-clicking the active leaf re-plots."""
        if self._data is None or self._active_spec is None:
            return
        spec = self._active_spec
        try:
            if spec.dim == "2d":
                self._stack.setCurrentIndex(0)
                self._figure.clear()
                ax = self._figure.add_subplot(111)
                spec.fn(ax, self._data)
                self._figure.tight_layout()
                self._canvas.draw_idle()
            else:  # "3d"
                self._stack.setCurrentIndex(1)
                self._vtk.set_central_body_texture(self._configured_moon_texture())
                self._vtk.clear_scene()
                spec.fn(self._vtk, self._data)
                self._vtk.reset_camera()
                self._vtk.render()
        except Exception as exc:  # noqa: BLE001 -- surface anything to user
            QMessageBox.critical(self, "Plot failed", repr(exc))
