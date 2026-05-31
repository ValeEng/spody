"""Analysis mode panel.

Lets the user pick a spody output binary, auto-detects its kind from
the 8-byte magic, then offers a list of 2D plots appropriate to that
kind. The plot itself is rendered into an embedded matplotlib canvas
with the standard zoom/pan/save toolbar attached.

Plot registry is a plain dict so adding a new plot is one entry: a
label and a function that takes (Axes, numpy.ndarray) and draws.
"""
from __future__ import annotations

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
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

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


# Plot function signature: (ax, data) -> None. Data shape depends on
# the file kind (TRAJ_DTYPE / ACCEL_DTYPE / EVENT_DTYPE). All plots are
# 2D for v0; 3D orbit view is deferred to a separate viewer.
PlotFn = Callable[[Axes, np.ndarray], None]


# ----------------------------------------------------------------------
# Trajectory plots
# ----------------------------------------------------------------------
def _plot_traj_r(ax: Axes, d: np.ndarray) -> None:
    r = np.sqrt(d["x"] ** 2 + d["y"] ** 2 + d["z"] ** 2)
    ax.plot(d["t"], r)
    ax.set_xlabel("t [s]")
    ax.set_ylabel("|r| [km]")
    ax.set_title("Radial distance")
    ax.grid(True, alpha=0.3)


def _plot_traj_v(ax: Axes, d: np.ndarray) -> None:
    v = np.sqrt(d["vx"] ** 2 + d["vy"] ** 2 + d["vz"] ** 2)
    ax.plot(d["t"], v)
    ax.set_xlabel("t [s]")
    ax.set_ylabel("|v| [km/s]")
    ax.set_title("Speed")
    ax.grid(True, alpha=0.3)


def _plot_traj_xyz(ax: Axes, d: np.ndarray) -> None:
    ax.plot(d["t"], d["x"], label="x")
    ax.plot(d["t"], d["y"], label="y")
    ax.plot(d["t"], d["z"], label="z")
    ax.set_xlabel("t [s]")
    ax.set_ylabel("position [km]")
    ax.set_title("Position components (inertial)")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)


def _plot_traj_vxyz(ax: Axes, d: np.ndarray) -> None:
    ax.plot(d["t"], d["vx"], label="vx")
    ax.plot(d["t"], d["vy"], label="vy")
    ax.plot(d["t"], d["vz"], label="vz")
    ax.set_xlabel("t [s]")
    ax.set_ylabel("velocity [km/s]")
    ax.set_title("Velocity components (inertial)")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)


def _plot_traj_projection(ax: Axes, d: np.ndarray, a: str, b: str) -> None:
    ax.plot(d[a], d[b], lw=0.8)
    ax.scatter([d[a][0]], [d[b][0]], color="green", s=30, zorder=3, label="t=0")
    ax.scatter([d[a][-1]], [d[b][-1]], color="red",   s=30, zorder=3, label="end")
    ax.set_xlabel(f"{a} [km]")
    ax.set_ylabel(f"{b} [km]")
    ax.set_aspect("equal", adjustable="datalim")
    ax.set_title(f"Orbit projection: {a.upper()}{b.upper()}")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)


# ----------------------------------------------------------------------
# Acceleration plots
# ----------------------------------------------------------------------
def _norm3(v: np.ndarray) -> np.ndarray:
    """|v| along the last axis for arrays of shape (..., 3)."""
    return np.sqrt(np.sum(v * v, axis=-1))


def _plot_acc_total(ax: Axes, d: np.ndarray) -> None:
    ax.semilogy(d["t"], _norm3(d["acc_total"]))
    ax.set_xlabel("t [s]")
    ax.set_ylabel("|a_total| [km/s²]")
    ax.set_title("Total acceleration magnitude")
    ax.grid(True, which="both", alpha=0.3)


def _plot_acc_breakdown(ax: Axes, d: np.ndarray) -> None:
    """Magnitude of each force component on one log-y plot. The forces
    that are inactive in the run (e.g. SRP disabled) appear as flat zero
    lines, which is informative in itself."""
    ax.semilogy(d["t"], _norm3(d["acc_2body"]),               label="2-body")
    ax.semilogy(d["t"], _norm3(d["acc_sphericalharmonics"]),  label="harmonics")
    ax.semilogy(d["t"], _norm3(d["acc_thirdbody_total"]),     label="3rd-body")
    ax.semilogy(d["t"], _norm3(d["acc_srp"]),                 label="SRP")
    ax.semilogy(d["t"], _norm3(d["acc_drag"]),                label="drag")
    ax.set_xlabel("t [s]")
    ax.set_ylabel("|a| [km/s²]")
    ax.set_title("Per-force acceleration magnitude")
    ax.legend(loc="best", fontsize="small")
    ax.grid(True, which="both", alpha=0.3)


def _plot_acc_eclipse(ax: Axes, d: np.ndarray) -> None:
    ax.plot(d["t"], d["eclipse_fraction"])
    ax.set_xlabel("t [s]")
    ax.set_ylabel("eclipse fraction")
    ax.set_title("Sunlight fraction (1 = full sun, 0 = full umbra)")
    ax.set_ylim(-0.05, 1.05)
    ax.grid(True, alpha=0.3)


# ----------------------------------------------------------------------
# Event plots
# ----------------------------------------------------------------------
def _plot_events_timeline(ax: Axes, d: np.ndarray) -> None:
    """One row per event kind, one marker per trigger; x = sim time."""
    if len(d) == 0:
        ax.set_title("No events recorded")
        ax.set_xlabel("t [s]")
        return
    kinds = np.unique(d["kind"])
    labels = {EVENT_KIND_IMPACT: "IMPACT", EVENT_KIND_ECLIPSE: "ECLIPSE"}
    colors = {EVENT_KIND_IMPACT: "tab:red", EVENT_KIND_ECLIPSE: "tab:blue"}
    for k in kinds:
        mask = d["kind"] == k
        ax.scatter(d["t"][mask], np.full(mask.sum(), int(k)),
                   color=colors.get(int(k), "tab:gray"),
                   label=labels.get(int(k), f"kind {int(k)}"),
                   marker="|", s=200)
    ax.set_yticks(sorted(int(k) for k in kinds))
    ax.set_yticklabels([labels.get(int(k), str(int(k))) for k in sorted(int(k) for k in kinds)])
    ax.set_xlabel("t [s]")
    ax.set_title(f"Event timeline ({len(d)} triggers)")
    ax.grid(True, axis="x", alpha=0.3)


# ----------------------------------------------------------------------
# Plot registry per file kind
# ----------------------------------------------------------------------
PLOTS: dict[str, list[tuple[str, PlotFn]]] = {
    "traj": [
        ("|r|(t) -- radial distance",       _plot_traj_r),
        ("|v|(t) -- speed",                 _plot_traj_v),
        ("x, y, z (t) -- position",         _plot_traj_xyz),
        ("vx, vy, vz (t) -- velocity",      _plot_traj_vxyz),
        ("orbit projection XY",             lambda ax, d: _plot_traj_projection(ax, d, "x", "y")),
        ("orbit projection XZ",             lambda ax, d: _plot_traj_projection(ax, d, "x", "z")),
        ("orbit projection YZ",             lambda ax, d: _plot_traj_projection(ax, d, "y", "z")),
    ],
    "accel": [
        ("|a_total|(t)",                    _plot_acc_total),
        ("per-force breakdown (log y)",     _plot_acc_breakdown),
        ("eclipse fraction (t)",            _plot_acc_eclipse),
    ],
    "events": [
        ("events timeline",                 _plot_events_timeline),
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


class AnalysisPanel(QWidget):
    """File picker + plot selector + matplotlib canvas. Stateless across
    files -- picking a new file clears the cached data and rebuilds the
    plot menu."""

    def __init__(self) -> None:
        super().__init__()
        self._kind: str | None = None
        self._data: np.ndarray | None = None
        self._path: Path | None = None

        # File row -----------------------------------------------------
        self._file_edit = QLineEdit()
        self._file_edit.setPlaceholderText(
            "Pick a spody output binary (.bin) -- traj / accel / events")
        self._file_edit.setReadOnly(True)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._on_browse)
        self._type_label = QLabel("(no file)")
        self._type_label.setStyleSheet("color: gray;")

        file_row = QHBoxLayout()
        file_row.addWidget(QLabel("File:"))
        file_row.addWidget(self._file_edit, 1)
        file_row.addWidget(browse)
        file_row.addWidget(self._type_label)

        # Plot row -----------------------------------------------------
        self._plot_combo = QComboBox()
        self._plot_combo.setEnabled(False)
        self._plot_btn = QPushButton("Plot")
        self._plot_btn.setEnabled(False)
        self._plot_btn.clicked.connect(self._on_plot)

        plot_row = QHBoxLayout()
        plot_row.addWidget(QLabel("Plot:"))
        plot_row.addWidget(self._plot_combo, 1)
        plot_row.addWidget(self._plot_btn)

        # Matplotlib canvas + toolbar ---------------------------------
        self._figure = Figure(figsize=(6, 4))
        self._canvas = FigureCanvasQTAgg(self._figure)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)

        layout = QVBoxLayout(self)
        layout.addLayout(file_row)
        layout.addLayout(plot_row)
        layout.addWidget(self._toolbar)
        layout.addWidget(self._canvas, 1)

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------
    def _on_browse(self) -> None:
        start = str(self._path.parent) if self._path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open spody output binary", start,
            "spody binaries (*.bin);;All files (*)",
        )
        if not path:
            return
        self.load_file(Path(path))

    def load_file(self, path: Path) -> None:
        """Public so the main window can later auto-load the last run's
        output without going through the Browse dialog."""
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
        self._file_edit.setText(str(path))
        self._type_label.setText(f"{_KIND_LABEL[kind]} -- {len(data)} records")
        self._type_label.setStyleSheet("")

        self._plot_combo.clear()
        for label, _fn in PLOTS.get(kind, []):
            self._plot_combo.addItem(label)
        has_plots = self._plot_combo.count() > 0
        self._plot_combo.setEnabled(has_plots)
        self._plot_btn.setEnabled(has_plots)

        # Re-plot the first option immediately so the canvas isn't blank
        # after loading -- saves a click for the common case.
        if has_plots:
            self._plot_combo.setCurrentIndex(0)
            self._on_plot()

    def _on_plot(self) -> None:
        if self._data is None or self._kind is None:
            return
        idx = self._plot_combo.currentIndex()
        if idx < 0:
            return
        _label, fn = PLOTS[self._kind][idx]
        self._figure.clear()
        ax = self._figure.add_subplot(111)
        try:
            fn(ax, self._data)
        except Exception as exc:  # noqa: BLE001 -- surface anything to user
            QMessageBox.critical(self, "Plot failed", repr(exc))
            return
        self._figure.tight_layout()
        self._canvas.draw_idle()
