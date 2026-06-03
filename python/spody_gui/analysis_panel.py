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
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
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
from .astronomy import sun_direction_j2000
from .settings import SettingsStore
from .vtk_canvas import VtkCanvas

# Recurse this many levels under the working dir when scanning for
# *.bin files. 3 covers the common `output/batch/<case>.bin` pattern
# without crawling huge data trees by accident.
SCAN_MAX_DEPTH = 3

# Roles used to store the per-item full path on tree items, so we
# don't have to re-resolve from display text.
_PATH_ROLE = Qt.ItemDataRole.UserRole


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


PLOTS: dict[str, list[PlotSpec]] = {
    "traj": [
        PlotSpec("|r|(t) -- radial distance",   "2d", _plot_traj_r,
                 overlay_fn=_make_2d_overlay(_plot_traj_r)),
        PlotSpec("|v|(t) -- speed",             "2d", _plot_traj_v,
                 overlay_fn=_make_2d_overlay(_plot_traj_v)),
        # XYZ / VxVyVz draw 3 lines per file: not overlay-safe (would
        # produce 3N illegible lines). Same for the accel breakdown.
        PlotSpec("x, y, z (t) -- position",     "2d", _plot_traj_xyz),
        PlotSpec("vx, vy, vz (t) -- velocity",  "2d", _plot_traj_vxyz),
        PlotSpec("orbit projection XY",         "2d", _p_xy,
                 overlay_fn=_make_2d_overlay(_p_xy)),
        PlotSpec("orbit projection XZ",         "2d", _p_xz,
                 overlay_fn=_make_2d_overlay(_p_xz)),
        PlotSpec("orbit projection YZ",         "2d", _p_yz,
                 overlay_fn=_make_2d_overlay(_p_yz)),
        PlotSpec("3D orbit + Moon",             "3d", _plot_traj_3d_orbit,
                 overlay_fn=_overlay_3d_orbit),
        # Classical orbital elements derived from r, v. All single-line
        # so overlay-safe out of the box. See _orbital_elements for
        # the degenerate-case handling (equatorial / circular).
        PlotSpec("a(t) -- semi-major axis",     "2d", _plot_traj_a,
                 overlay_fn=_make_2d_overlay(_plot_traj_a)),
        PlotSpec("e(t) -- eccentricity",        "2d", _plot_traj_e,
                 overlay_fn=_make_2d_overlay(_plot_traj_e)),
        PlotSpec("i(t) -- inclination",         "2d", _plot_traj_i,
                 overlay_fn=_make_2d_overlay(_plot_traj_i)),
        PlotSpec("RAAN Ω(t)",                   "2d", _plot_traj_raan,
                 overlay_fn=_make_2d_overlay(_plot_traj_raan)),
        PlotSpec("arg. periapsis ω(t)",         "2d", _plot_traj_aop,
                 overlay_fn=_make_2d_overlay(_plot_traj_aop)),
        PlotSpec("true anomaly ν(t)",           "2d", _plot_traj_nu,
                 overlay_fn=_make_2d_overlay(_plot_traj_nu)),
    ],
    "accel": [
        PlotSpec("|a_total|(t)",                "2d", _plot_acc_total,
                 overlay_fn=_make_2d_overlay(_plot_acc_total)),
        PlotSpec("per-force breakdown (log y)", "2d", _plot_acc_breakdown),
        PlotSpec("eclipse fraction (t)",        "2d", _plot_acc_eclipse,
                 overlay_fn=_make_2d_overlay(_plot_acc_eclipse)),
    ],
    "events": [
        PlotSpec("events timeline",             "2d", _plot_events_timeline),
    ],
}


# Friendly names for the kind tag shown in the type label.
_KIND_LABEL = {
    "traj":   "trajectory  (SPDYOUT_)",
    "accel":  "accelerations  (SPDYACC_)",
    "events": "events log  (SPDYEVT_)",
}

_READERS = {
    "traj":   read_trajectory,
    "accel":  read_accelerations,
    "events": read_events,
}


def _detect_kind(path: Path) -> str | None:
    """Read the first 8 bytes and match against the three known magics."""
    try:
        with path.open("rb") as fp:
            m = fp.read(8)
    except OSError:
        return None
    if m == SPODY_BIN_MAGIC: return "traj"
    if m == SPODY_ACC_MAGIC: return "accel"
    if m == SPODY_EVT_MAGIC: return "events"
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
        # Overlay button works on the active plot in the combo: it
        # produces a 2D overlay when a 2D plot is selected and a 3D
        # overlay when a 3D plot is selected (subject to spec.overlay_fn).
        btn_overlay = QPushButton("→ Overlay selected")
        btn_overlay.clicked.connect(self._on_overlay_selected)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(self._tree, 1)
        left_lay.addWidget(btn_add)
        left_lay.addWidget(btn_overlay)

        # Right pane: plot controls + stacked 2D/3D canvas + info ----
        self._plot_combo = QComboBox()
        self._plot_combo.setEnabled(False)
        self._plot_btn = QPushButton("Plot")
        self._plot_btn.setEnabled(False)
        self._plot_btn.clicked.connect(self._on_plot)

        plot_row = QHBoxLayout()
        plot_row.addWidget(QLabel("Plot:"))
        plot_row.addWidget(self._plot_combo, 1)
        plot_row.addWidget(self._plot_btn)

        # Sun-arrow controls (3D only). The epoch field auto-fills from
        # the TOML currently open in the Run tab; user can override.
        self._epoch_edit = QLineEdit()
        self._epoch_edit.setPlaceholderText("et_start_s (TDB sec past J2000)")
        btn_sun = QPushButton("+ Sun arrow")
        btn_sun.clicked.connect(self._on_add_sun)
        sun_row = QHBoxLayout()
        sun_row.addWidget(QLabel("Epoch:"))
        sun_row.addWidget(self._epoch_edit, 1)
        sun_row.addWidget(btn_sun)

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

        right = QWidget()
        right_lay = QVBoxLayout(right)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.addLayout(plot_row)
        right_lay.addLayout(sun_row)
        right_lay.addWidget(self._stack, 1)
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
        Selection is dropped (no auto-load); the user picks an item to
        load explicitly."""
        self._tree.clear()

        folder_header = self._make_header(
            f"In folder ({self._working_dir})" if self._working_dir
            else "In folder (none)"
        )
        self._tree.addTopLevelItem(folder_header)
        if self._working_dir is not None:
            for p in _scan_bin_files(self._working_dir, SCAN_MAX_DEPTH):
                rel = p.relative_to(self._working_dir)
                child = QTreeWidgetItem([str(rel).replace("\\", "/")])
                child.setData(0, _PATH_ROLE, str(p))
                child.setToolTip(0, str(p))
                folder_header.addChild(child)
        folder_header.setExpanded(True)

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
        kind) using the plot picked in the combo. Works for 2D and 3D
        depending on `spec.dim`; specs without an `overlay_fn` (e.g.
        per-component plots that draw 3 lines per file) trigger an
        explanatory message instead of an unreadable overlay."""
        if self._kind is None:
            QMessageBox.information(
                self, "Pick a file first",
                "Click a file in the tree to set the kind, then Ctrl/Shift-"
                "click the others you want to overlay."
            )
            return
        idx = self._plot_combo.currentIndex()
        if idx < 0:
            return
        spec = PLOTS[self._kind][idx]
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
        the file's magic; populates the plot menu accordingly and
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

        # Repopulate the plot menu; auto-plot the first option.
        self._plot_combo.blockSignals(True)
        self._plot_combo.clear()
        for spec in PLOTS.get(kind, []):
            self._plot_combo.addItem(spec.label)
        self._plot_combo.blockSignals(False)
        has_plots = self._plot_combo.count() > 0
        self._plot_combo.setEnabled(has_plots)
        self._plot_btn.setEnabled(has_plots)
        if has_plots:
            self._plot_combo.setCurrentIndex(0)
            self._on_plot()

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

    def _on_plot(self) -> None:
        """Dispatch the selected plot to the right canvas. 2D plots are
        drawn into the matplotlib figure; 3D plots into the VTK scene.
        Each branch is fully responsible for its canvas lifecycle
        (clear, draw, render) so individual plot functions stay tiny."""
        if self._data is None or self._kind is None:
            return
        idx = self._plot_combo.currentIndex()
        if idx < 0:
            return
        spec = PLOTS[self._kind][idx]
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
