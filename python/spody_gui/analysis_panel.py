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


@dataclass(frozen=True)
class PlotSpec:
    label: str
    dim:   str           # "2d" or "3d" -- selects which canvas page is shown
    fn:    Callable      # PlotFn2D for dim == "2d", PlotFn3D for dim == "3d"


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
    green/red start/end markers. Camera fitted to the trajectory."""
    canvas.add_central_body()
    pts = np.column_stack([d["x"], d["y"], d["z"]])
    canvas.add_trajectory(pts)


PLOTS: dict[str, list[PlotSpec]] = {
    "traj": [
        PlotSpec("|r|(t) -- radial distance",   "2d", _plot_traj_r),
        PlotSpec("|v|(t) -- speed",             "2d", _plot_traj_v),
        PlotSpec("x, y, z (t) -- position",     "2d", _plot_traj_xyz),
        PlotSpec("vx, vy, vz (t) -- velocity",  "2d", _plot_traj_vxyz),
        PlotSpec("orbit projection XY",         "2d", lambda ax, d: _plot_traj_projection(ax, d, "x", "y")),
        PlotSpec("orbit projection XZ",         "2d", lambda ax, d: _plot_traj_projection(ax, d, "x", "z")),
        PlotSpec("orbit projection YZ",         "2d", lambda ax, d: _plot_traj_projection(ax, d, "y", "z")),
        PlotSpec("3D orbit + Moon",             "3d", _plot_traj_3d_orbit),
    ],
    "accel": [
        PlotSpec("|a_total|(t)",                "2d", _plot_acc_total),
        PlotSpec("per-force breakdown (log y)", "2d", _plot_acc_breakdown),
        PlotSpec("eclipse fraction (t)",        "2d", _plot_acc_eclipse),
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

    def __init__(self) -> None:
        super().__init__()
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

        # Left pane: file tree + Add button ---------------------------
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(False)
        self._tree.itemClicked.connect(self._on_tree_item_clicked)

        btn_add = QPushButton("+ Add external file...")
        btn_add.clicked.connect(self._on_add_external)

        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.addWidget(self._tree, 1)
        left_lay.addWidget(btn_add)

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

        # 2D page: matplotlib canvas + toolbar in a sub-widget.
        self._figure  = Figure(figsize=(6, 4))
        self._canvas  = FigureCanvasQTAgg(self._figure)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)
        mpl_page = QWidget()
        mpl_lay = QVBoxLayout(mpl_page)
        mpl_lay.setContentsMargins(0, 0, 0, 0)
        mpl_lay.addWidget(self._toolbar)
        mpl_lay.addWidget(self._canvas, 1)

        # 3D page: VTK widget with its own built-in mouse controls.
        self._vtk = VtkCanvas()

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
                self._vtk.clear_scene()
                spec.fn(self._vtk, self._data)
                self._vtk.reset_camera()
                self._vtk.render()
        except Exception as exc:  # noqa: BLE001 -- surface anything to user
            QMessageBox.critical(self, "Plot failed", repr(exc))
