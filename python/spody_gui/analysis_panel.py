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

This module hosts only the AnalysisPanel widget and its file-browser
plumbing; every plot / info / table building block lives in the
`analysis` package (see spody_gui/analysis/__init__.py for the
layout and for where a new view or file kind is added).
"""

from __future__ import annotations

import contextlib
import math
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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .settings import SettingsStore
from .animation_bar import AnimationBar
from .central_bodies import (
    CentralBodySpec,
    default_central_body,
    resolve_central_body,
)
from .plot_options import PlotOptionsDialog
from .scene_options import SceneOptions, SceneOptionsDialog
from .toml_io import read_toml
from .vtk_canvas import VtkCanvas
# All plot/table/info machinery lives in the analysis package; this
# module keeps only the AnalysisPanel widget and its file plumbing.
from . import constants
from .analysis import (
    CR3BPPrimary,
    KIND_LABEL,
    NumpyTableModel,
    PLOTS,
    PlotContext,
    PlotSpec,
    READERS,
    detect_kind,
    resolve_run_context,
)
from .analysis.info import (
    SECTION,
    info_rows_accel,
    info_rows_diff,
    info_rows_events,
    info_rows_run_summary,
    info_rows_traj,
)
from .analysis.plots_diff import align_or_interp
from .analysis.table_model import FIELD_DISPLAY_RENAME


# Folder names skipped during the working-dir .bin scan. Same
# build / VCS / venv pruning the TOML combo uses in MainWindow,
# so picking a project root in either tab gives consistent visibility.
# `output/` is INTENTIONALLY NOT in the skip set -- per-run bins
# live exactly there.
_BIN_SCAN_SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__", ".git", ".venv", "venv",
    "build", "dist", "node_modules",
})

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


def _serialize_axes_to_csv(axes) -> str:
    """Dump every Line2D on every axis as CSV text.

    One section per axis (separated by a blank line + comment header
    carrying the subplot title); within a section, lines that share an
    identical x-array collapse to `x, y1, y2, ...`, otherwise each
    line gets `x_<lbl>, y_<lbl>` pairs padded with empty cells to the
    longest length. Lines whose matplotlib label starts with `_`
    (auto-generated legend-hidden labels) get a generic `y<j>` name."""
    chunks: list[str] = []
    for i, ax in enumerate(axes):
        lines = ax.get_lines()
        if not lines:
            continue
        title = ax.get_title() or f"axis {i + 1}"
        chunks.append(f"# Axis {i + 1}: {title}")
        chunks.append(
            f"# xlabel: {ax.get_xlabel()}   ylabel: {ax.get_ylabel()}")
        xs = [np.asarray(ln.get_xdata(), dtype=float) for ln in lines]
        ys = [np.asarray(ln.get_ydata(), dtype=float) for ln in lines]
        labels = []
        for j, ln in enumerate(lines):
            lab = ln.get_label() or ""
            labels.append(lab if (lab and not lab.startswith("_")) else f"y{j}")
        same_x = all(
            x.shape == xs[0].shape and np.array_equal(x, xs[0]) for x in xs)
        if same_x:
            chunks.append("x," + ",".join(labels))
            for k in range(xs[0].size):
                row = [repr(float(xs[0][k]))]
                row.extend(repr(float(y[k])) for y in ys)
                chunks.append(",".join(row))
        else:
            chunks.append(
                ",".join(f"x_{lab},y_{lab}" for lab in labels))
            max_len = max(x.size for x in xs)
            for k in range(max_len):
                cells: list[str] = []
                for x, y in zip(xs, ys):
                    if k < x.size:
                        cells.append(repr(float(x[k])))
                        cells.append(repr(float(y[k])))
                    else:
                        cells.append("")
                        cells.append("")
                chunks.append(",".join(cells))
        chunks.append("")
    return "\n".join(chunks) + "\n"


def _scan_bin_files(root: Path) -> list[Path]:
    """Return all *.bin files under `root`, scanning fully recursively
    so deep run-folder layouts (`output/<ts>/...`, nested case dirs)
    surface in the tree no matter how deep they sit. Subtrees listed
    in `_BIN_SCAN_SKIP_DIRS` (build / VCS / venv noise) are pruned
    upfront so a huge venv pointed at by accident doesn't lock the
    UI. Silently skips anything we can't traverse."""
    out: list[Path] = []
    if not root.is_dir():
        return out
    stack: list[Path] = [root]
    while stack:
        cur = stack.pop()
        try:
            entries = list(cur.iterdir())
        except OSError:
            continue
        for p in entries:
            try:
                is_dir  = p.is_dir()
                is_file = p.is_file()
            except OSError:
                continue
            if is_dir:
                if p.name in _BIN_SCAN_SKIP_DIRS:
                    continue
                stack.append(p)
            elif is_file and p.suffix.lower() == ".bin":
                out.append(p)
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
        # Last successful diff (aligned A/B + paths) cached by
        # `_plot_diff` so the Info tab can show plot-aware diff stats
        # (|Δr|, RIC, growth) without re-reading the files. Cleared
        # whenever the active plot leaves a diff spec.
        self._last_diff: "tuple[list[Path], np.ndarray, np.ndarray, bool] | None" = None

        # Working-dir display: the dedicated row used to live here, but
        # the working-dir field + Browse button now sit in the top bar
        # owned by MainWindow (it's a single concept shared with the
        # Form tab). `set_working_dir` remains the public hook the
        # main window calls; a small Refresh button stays beside the
        # file tree so the user can re-scan after dropping new bins
        # in by hand.

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
        btn_refresh = QPushButton("⟳ Refresh")
        btn_refresh.clicked.connect(self._refresh_tree)
        # Overlay button uses the active plot (set by the plot tree
        # below the splitter): produces a 2D overlay when a 2D plot is
        # active and a 3D overlay otherwise (subject to spec.overlay_fn).
        btn_overlay = QPushButton("→ Overlay selected")
        btn_overlay.clicked.connect(self._on_overlay_selected)

        files_box = QWidget()
        files_lay = QVBoxLayout(files_box)
        files_lay.setContentsMargins(0, 0, 0, 0)
        files_lay.addWidget(self._tree, 1)
        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 0, 0, 0)
        add_row.addWidget(btn_add, 1)
        add_row.addWidget(btn_refresh)
        files_lay.addLayout(add_row)
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

        # Plot frame: "icrf" (default) or "bf"; threaded into the
        # PlotContext on every dispatch so state-vector / projection
        # / Keplerian-angle plots can rotate their data into the
        # central body's body-fixed basis when the user picks BF in
        # the Plot-options dialog.
        self._plot_frame: str = "icrf"

        # Scene-options state lives on the panel; the dialog mutates
        # it in place. Every toggle ends up driving a re-render via
        # _on_scene_options_changed. The dialog itself is opened from
        # the animation bar's "Scene..." button (see wiring below).
        self._scene_options = SceneOptions()
        # Tracks the file path the 3D canvas was last rendered against.
        # When a re-render targets the same file (Scene-options toggle,
        # animation restart, ...), we preserve the user's camera pan /
        # zoom; only a switch to a different file triggers the
        # ResetCamera auto-fit again.
        self._last_3d_path: "Path | None" = None
        # Restore persistent toggles from Settings. show_starfield is
        # the only one persisted today; the rest stay at dataclass
        # defaults until the user touches them in the dialog.
        try:
            self._scene_options.show_starfield = self._store.show_starfield()
        except Exception:
            pass
        self._scene_dialog: SceneOptionsDialog | None = None
        # Central body resolved from the loaded run's snapshot
        # TOML's `force_model.central_body`. Defaults to Moon so
        # opening a bare .bin without a snapshot still renders
        # something (the legacy assumption).
        self._central_body: CentralBodySpec = default_central_body()
        # Dynamics-model side-channel state populated by load_file from
        # the run's snapshot. Defaults match the legacy HF behaviour so
        # opening a bare .bin without a snapshot keeps rendering the
        # single-central-body scene.
        self._dynamics_model: str = "high_fidelity"
        self._cr3bp_primaries: tuple[CR3BPPrimary, ...] = ()
        # Backwards-compat shim: set_default_epoch still gets called
        # from MainWindow when a TOML is loaded. We no longer need
        # the epoch in the panel (the third-body markers compute
        # everything from the snapshot), but keep the field alive as
        # a hidden no-op widget so the slot below doesn't crash.
        self._epoch_edit = QLineEdit()
        self._epoch_edit.hide()
        # Old per-3D toolbar slot: keep an empty hidden widget around
        # so the existing show/hide call sites referring to
        # `_sun_widget` don't have to be torn out -- the animation
        # bar now hosts every 3D-only control (playback + Scene).
        self._sun_widget = QWidget()
        self._sun_widget.setVisible(False)

        # 2D page: matplotlib canvas + toolbar + Plot-options button.
        # The Options button rides on the toolbar row (right-aligned),
        # mirroring how the 3D AnimationBar exposes its "Scene..."
        # button. It only lives on the 2D page so 3D plots never see
        # it, which is the whole point of having a separate 3D control.
        self._figure  = Figure(figsize=(6, 4))
        self._canvas  = FigureCanvasQTAgg(self._figure)
        self._toolbar = NavigationToolbar2QT(self._canvas, self)
        self._btn_plot_options = QPushButton("Plot options...")
        self._btn_plot_options.setToolTip(
            "Open plot options (export CSV, ...)")
        self._btn_plot_options.clicked.connect(self._on_open_plot_options)
        self._plot_options_dialog: PlotOptionsDialog | None = None
        toolbar_row = QWidget()
        toolbar_row_lay = QHBoxLayout(toolbar_row)
        toolbar_row_lay.setContentsMargins(0, 0, 0, 0)
        toolbar_row_lay.addWidget(self._toolbar, 1)
        toolbar_row_lay.addWidget(self._btn_plot_options)
        mpl_page = QWidget()
        mpl_lay = QVBoxLayout(mpl_page)
        mpl_lay.setContentsMargins(0, 0, 0, 0)
        mpl_lay.addWidget(toolbar_row)
        mpl_lay.addWidget(self._canvas, 1)

        # 3D page: VTK widget with its own built-in mouse controls,
        # plus Ctrl+left-click picking wired to highlight the source
        # file in the tree and the info label.
        self._vtk = VtkCanvas()
        self._vtk.set_pick_callback(self._on_pick)

        # Cesium-style playback bar shown above the 3D canvas. Lights
        # up only when the active plot dropped animated trajectories
        # into the canvas (single + overlay 3D orbit views); stays
        # disabled / hidden otherwise. Signals are forwarded into the
        # canvas's animation API.
        self._anim_bar = AnimationBar()
        self._anim_bar.timeChanged.connect(self._on_anim_time_changed)
        self._anim_bar.sceneOptionsRequested.connect(
            self._on_open_scene_options)
        self._anim_bar.setVisible(False)

        # Stack switched by the dispatcher in `_on_plot` based on
        # PlotSpec.dim. Index 0 = 2D, index 1 = 3D.
        self._stack = QStackedWidget()
        self._stack.addWidget(mpl_page)
        self._stack.addWidget(self._vtk)

        self._info_label = QLabel("(no file loaded)")
        self._info_label.setStyleSheet("color: gray;")
        self._info_label.setWordWrap(True)

        # Plot tab content: sun bar + animation bar + 2D/3D stack.
        # Both extra bars hide for 2D plots; the dispatcher in
        # `_plot_active` / `_plot_overlay` flips their visibility.
        plot_tab = QWidget()
        plot_lay = QVBoxLayout(plot_tab)
        plot_lay.setContentsMargins(0, 0, 0, 0)
        plot_lay.addWidget(self._sun_widget)
        plot_lay.addWidget(self._anim_bar)
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

        # Info tab content: kind-specific summary table populated by
        # `_refresh_info_tab` on every file load and (when the active
        # plot is a diff) on every plot-tree click. Two columns
        # (Field, Value); section headers are rendered as bold rows
        # spanning both columns.
        self._info_table = QTableWidget(0, 2)
        self._info_table.setHorizontalHeaderLabels(["Field", "Value"])
        self._info_table.verticalHeader().setVisible(False)
        self._info_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._info_table.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection)
        self._info_table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectItems)
        self._info_table.setAlternatingRowColors(True)
        self._info_table.setWordWrap(True)
        info_h = self._info_table.horizontalHeader()
        info_h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        info_h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._info_table.setFont(mono)
        info_tab = QWidget()
        info_lay = QVBoxLayout(info_tab)
        info_lay.setContentsMargins(0, 0, 0, 0)
        info_lay.addWidget(self._info_table, 1)

        # Top-level tabs: clicking a file populates whichever tab is
        # active right now; switching tab on an already-loaded file
        # repopulates the new view from the cached array (no re-read).
        self._right_tabs = QTabWidget()
        self._right_tabs.addTab(plot_tab,  "Plot")
        self._right_tabs.addTab(table_tab, "Table")
        self._right_tabs.addTab(info_tab,  "Info")
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
        layout.addWidget(splitter, 1)

        self._refresh_tree()

    # ------------------------------------------------------------------
    # Public API (used by MainWindow)
    # ------------------------------------------------------------------
    def set_working_dir(self, path: Path | None) -> None:
        """Set the directory that the 'In folder' section scans. Called
        by the main window whenever the shared top-bar working dir
        changes (TOML opened/saved/Run finished/folder browsed).
        The visible field for this path lives in the top bar -- the
        panel only consumes it."""
        self._working_dir = Path(path) if path is not None else None
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
            files = _scan_bin_files(self._working_dir)
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
    def _on_right_tab_changed(self, idx: int) -> None:
        """Switching to the Plot tab on an already-loaded file that has
        no current plot triggers the default render -- otherwise the
        canvas would stay blank until the user clicked something in the
        plot tree. The Table model is always in sync with `self._data`
        so its tab needs no extra work. The Info tab repopulates on
        every switch so a plot-tree click in the background propagates
        immediately when the user comes back to it."""
        if idx == 0 and self._data is not None and self._kind is not None:
            current = self._plot_tree.currentItem()
            if current is None or current.data(0, _SPEC_ROLE) is None:
                first = self._first_plot_leaf()
                if first is not None:
                    self._plot_tree.setCurrentItem(first)
                    self._on_plot_tree_clicked(first, 0)
        elif idx == 2:
            self._refresh_info_tab()

    def _refresh_info_tab(self) -> None:
        """Rebuild the Info-tab table from `self._data` + the loaded
        run's snapshot + the currently active plot spec. Cheap (a
        handful of rows, no plotting); called on every load_file,
        every right-tab change to Info, and every plot-tree click
        whose spec is a diff -- the cost is bounded by the per-kind
        builders, which all run in O(N) numpy."""
        self._info_table.setRowCount(0)
        if self._kind is None or self._data is None or self._path is None:
            self._info_table.setRowCount(1)
            self._info_table.setSpan(0, 0, 1, 2)
            placeholder = QTableWidgetItem("(no file loaded)")
            placeholder.setForeground(Qt.GlobalColor.gray)
            self._info_table.setItem(0, 0, placeholder)
            return
        snapshot = resolve_run_context(self._path)
        rows = info_rows_run_summary(
            self._path, self._kind, len(self._data), snapshot,
            self._central_body, self._dynamics_model,
            self._cr3bp_primaries)
        if self._kind == "traj":
            rows += info_rows_traj(
                self._data, self._central_body, self._dynamics_model)
        elif self._kind == "accel":
            rows += info_rows_accel(self._data)
        elif self._kind in ("events", "events_batch"):
            rows += info_rows_events(self._data, snapshot)
        # Diff overlay: only meaningful when the active plot is a diff
        # spec AND we have a cached aligned pair from `_plot_diff`.
        if (self._active_spec is not None
                and self._active_spec.mode == "diff"
                and self._last_diff is not None):
            paths, data_a, data_b, was_interp = self._last_diff
            rows += info_rows_diff(
                data_a, data_b, self._active_spec, paths, was_interp)
        self._populate_info_table(rows)

    def _populate_info_table(self,
                             rows: "list[tuple[str, str | None]]") -> None:
        """Render the (label, value) rows into the QTableWidget. A
        `value is None` row is a section header: spanned across both
        columns, bold font, alt-row colouring suppressed via the
        widget-level alternating-row setting (which still applies to
        regular rows for readability)."""
        self._info_table.setRowCount(len(rows))
        header_font = QFont(self._info_table.font())
        header_font.setBold(True)
        for r, (label, value) in enumerate(rows):
            if value is SECTION:
                item = QTableWidgetItem(label)
                item.setFont(header_font)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self._info_table.setItem(r, 0, item)
                self._info_table.setSpan(r, 0, 1, 2)
            else:
                label_item = QTableWidgetItem(label)
                value_item = QTableWidgetItem(value)
                self._info_table.setItem(r, 0, label_item)
                self._info_table.setItem(r, 1, value_item)
        self._info_table.resizeRowsToContents()

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
                "eclipse fraction, or '3D orbit + central body') and try again."
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
            kind = detect_kind(p)
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
        reader = READERS[self._kind]
        for p in paths:
            try:
                items.append((p, reader(p)))
            except (OSError, ValueError) as exc:
                skipped.append(f"{p.name} ({exc})")
        if not items:
            QMessageBox.critical(self, "Overlay failed",
                                 "None of the selected files could be read.")
            return

        # Same context for 2D and 3D overlays: the central body
        # drives orbital-element mu (2D) AND triad/body/marker
        # scaling (3D). Built once per dispatch.
        ovl_ctx = (self._build_plot_context(items[0][0])
                   if items else None)
        try:
            with self._busy(f"overlaying {len(items)} files ({spec.label})"):
                if spec.dim == "2d":
                    self._stack.setCurrentIndex(0)
                    self._figure.clear()
                    ax = self._figure.add_subplot(111)
                    spec.overlay_fn(ax, items, ovl_ctx)
                    self._figure.tight_layout()
                    self._canvas.draw_idle()
                else:  # "3d"
                    self._stack.setCurrentIndex(1)
                    self._vtk.set_central_body_texture(self._configured_central_body_texture())
                    self._apply_skybox_to_canvas()
                    self._vtk.clear_scene()
                    spec.overlay_fn(self._vtk, items, ovl_ctx)
                    # Pin the rotation pivot on the central body so
                    # mouse-drag rotation keeps the Moon centred even
                    # when the auto-fit bbox is pulled off-axis by the
                    # third-body markers (Sun ~50000 km off to one
                    # side, etc.).
                    self._vtk.reset_camera_on_origin()
                    self._vtk.render()
                self._sync_anim_bar_to_canvas()
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
        kind = detect_kind(path)
        if kind is None:
            QMessageBox.warning(
                self, "Unknown file",
                f"{path.name} is not a spody binary "
                "(expected magic SPDYOUT_, SPDYACC_, or SPDYEVT_)."
            )
            return
        with self._busy(f"loading {path.name}"):
            try:
                data = READERS[kind](path)
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "Read failed", str(exc))
                return

        self._path = path
        self._kind = kind
        self._data = data
        self._info_label.setText(
            f"{KIND_LABEL[kind]} -- {len(data)} records\n{path}"
        )
        self._info_label.setStyleSheet("")

        # Always refresh the Table model so a tab switch later in the
        # session shows the right rows without re-reading the file.
        self._table_model.set_array(data, FIELD_DISPLAY_RENAME.get(kind))

        # Resolve the central body from the run's snapshot TOML
        # (force_model.central_body) so every 3D plot reads radius
        # + frame name + orientation from one place. Falls back to
        # the Moon spec when no snapshot is available.
        self._central_body = self._resolve_central_body_from_snapshot()
        # CR3BP plumbing: dispatch table on simulation.dynamics_model
        # (high_fidelity by default). The two primaries' synodic
        # positions, radii and GM come from a tiny hardcoded table
        # mirroring CR3BP_PAIRS in src/toml_input.c.
        self._dynamics_model   = self._resolve_dynamics_model_from_snapshot()
        self._cr3bp_primaries  = self._resolve_cr3bp_primaries_from_snapshot()
        # Seed the per-body visibility set from the snapshot's
        # third_bodies BEFORE the first 3D render, so Sun arrow /
        # Moon marker / etc. show up without needing the user to
        # open the Scene-options dialog first.
        self._seed_show_bodies_from_snapshot()
        # Refresh the Scene-options dialog's body list with whatever
        # `force_model.third_bodies` is declared in this run's TOML
        # (silently no-op when the snapshot is missing or the dialog
        # has never been opened).
        self._refresh_scene_dialog_bodies()
        # Update the Plot-options dialog's BF radio: enables / disables
        # based on whether the new run's central body has an
        # orientation provider, and updates the BF label ("ITRS" /
        # "PA" / ...). No-op when the dialog hasn't been opened yet.
        self._refresh_plot_options_bf_availability()

        # Switching file invalidates the cached diff payload: the
        # `_last_diff` arrays were aligned against a different pair
        # and would surface stale rows in the Info tab.
        self._last_diff = None

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
        # Refresh the Info tab from the new file's data + snapshot.
        # Cheap (no plotting), so we do it unconditionally even when
        # the Info tab is currently in the background.
        self._refresh_info_tab()

    # ------------------------------------------------------------------
    # Plot tree management
    # ------------------------------------------------------------------
    def _populate_plot_tree(self, kind: str) -> None:
        """Rebuild the right-pane tree for the given file kind. Specs
        with non-empty `category` get grouped under a bold folder; the
        rest live at root level. Registry order is preserved so the
        groups stack in a stable order.

        Specs whose `models` tuple does NOT include the loaded run's
        `dynamics_model` are filtered out before grouping -- so an HF
        run never sees the Jacobi plot and a CR3BP run never sees the
        body-fixed impact lat/lon views."""
        self._plot_tree.clear()
        # Lazy import: avoid hardcoding a category-order list -- the
        # first time we encounter a category we create its folder, and
        # subsequent specs with the same string attach as children.
        folders: dict[str, QTreeWidgetItem] = {}
        for spec in PLOTS.get(kind, []):
            if self._dynamics_model not in spec.models:
                continue
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
        # Leaving a diff spec invalidates the cached pair -- the next
        # diff click will repopulate. Done up front so the Info tab
        # refresh below sees a consistent (spec, diff-cache) pair.
        if spec.mode != "diff":
            self._last_diff = None
        # Every dispatch is wrapped in a busy context so 3D scene
        # builds with many third bodies and large-batch diffs paint
        # the wait cursor + status message instead of letting Windows
        # flip the title bar to 'Not Responding'.
        with self._busy(f"rendering '{spec.label}'"):
            if spec.mode == "diff":
                self._plot_diff(spec)
            else:
                self._plot_active()
        # Info tab: refresh whenever the active spec changes so the
        # diff overlay rows appear / disappear with the plot selection.
        self._refresh_info_tab()

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
        # diff plots need 2 files, single+context plots need 1 -- but
        # single and context are file-tree-compatible (both consume the
        # one loaded file), so we collapse them for tiling purposes and
        # let the per-spec branch below pass `ctx` where needed.
        raw_modes = {s.mode for s in specs}
        effective_modes = {("single" if m in ("single", "context") else m)
                           for m in raw_modes}
        if len(effective_modes) > 1:
            QMessageBox.information(
                self, "Mixed plot modes",
                "Tile cannot mix single-file and diff plots in one figure "
                "(they read from the file tree differently). Pick from one "
                "category at a time.")
            return
        mode = effective_modes.pop()

        # Resolve the data argument(s) once -- every subplot draws into
        # the same dataset(s) so we read disk once, not N times.
        if mode == "diff":
            paths, err = self._collect_two_diff_files()
            if err is not None:
                QMessageBox.information(self, "Diff tile", err)
                return
            reader = READERS[self._kind]
            try:
                data_a = reader(paths[0])
                data_b = reader(paths[1])
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "Diff read failed", str(exc))
                return
            # Align once; every subplot then operates on identical
            # (possibly interpolated) arrays.
            try:
                data_a, data_b, was_interp, _note = align_or_interp(
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

        # Built once: context specs all consume the same PlotContext
        # (one file = one path) -- no need to re-build per subplot.
        ctx = (self._build_plot_context(self._path)
               if mode == "single" and self._path is not None
               else None)

        try:
            with self._busy(f"tiling {n} plots"):
                self._stack.setCurrentIndex(0)
                self._figure.clear()
                for i, spec in enumerate(specs):
                    ax = self._figure.add_subplot(rows, cols, i + 1)
                    if mode == "diff":
                        spec.fn(ax, data_a, data_b)
                    elif spec.mode == "context":
                        spec.fn(ax, self._data, ctx)
                    else:
                        spec.fn(ax, self._data)
                    # Shrink labels in tile mode -- matplotlib's default
                    # sizes are calibrated for a single full-canvas plot.
                    ax.title.set_size("small")
                    ax.tick_params(labelsize="small")
                    ax.xaxis.label.set_size("small")
                    ax.yaxis.label.set_size("small")
                    # Legends already use fontsize='small' or 'best';
                    # nothing to do for plots that don't add one.
                    # Pump the event loop between subplots so a slow
                    # tile (e.g. 12 batch plots) still updates the
                    # cursor / message without freezing the title bar.
                    QApplication.processEvents()
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
            kind = detect_kind(p)
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
    # ------------------------------------------------------------------
    # 3D animation -- the bar above the canvas owns the timeline; the
    # canvas owns the per-frame marker / trail update. These slots glue
    # the two together. `_sync_anim_bar_to_canvas` is the helper the
    # plot dispatcher calls after each 3D render so the bar's t-range
    # matches the freshly-loaded handles.
    # ------------------------------------------------------------------
    def _on_anim_time_changed(self, t_s: float) -> None:
        # Cheap fast path: if the active plot isn't 3D the canvas has
        # no animation handles and set_animation_time is a no-op; we
        # still skip the render call to avoid waking the GL context.
        if self._stack.currentIndex() != 1:
            return
        self._vtk.set_animation_time(t_s)
        # Push the UTC corresponding to (et_start + t_s) into the
        # bottom-right overlay so the user can spot-check what wall-
        # clock instant the marker is at. Snapshot lookup is cheap
        # (cached in `resolve_run_context`), but degrade gracefully
        # when no snapshot is present (bare .bin loaded ad-hoc).
        self._refresh_utc_overlay(t_s)
        self._vtk.render()

    @contextlib.contextmanager
    def _busy(self, message: str):
        """Wait-cursor + info-label progress note around a slow op.
        Quick-win against Windows' 'Not Responding' label: any
        operation > ~1 s that runs on the main thread (file loads,
        3D scene builds, batch tile renders, third-body ephemeris
        loops) gets visible feedback. `processEvents()` is called on
        entry so the cursor + message paint before the work starts;
        on exit the cursor is unconditionally restored even when the
        wrapped block raises. If the wrapped block did NOT overwrite
        the info-label (e.g. a 3D scene render that has nothing
        post-success to say), the "Working" string is cleared
        instead of being left as visual debris."""
        prev_text       = self._info_label.text()
        prev_stylesheet = self._info_label.styleSheet()
        busy_text       = f"Working: {message}…"
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        self._info_label.setText(busy_text)
        self._info_label.setStyleSheet("color: #888;")
        QApplication.processEvents()
        try:
            yield
        finally:
            QApplication.restoreOverrideCursor()
            # Only sweep the label if nobody else touched it — the
            # "swept" state is the pre-busy text + style, so a
            # dispatcher that printed its own status (e.g. "Diff:
            # A=... B=...") sees its message survive.
            if self._info_label.text() == busy_text:
                self._info_label.setText(prev_text)
                self._info_label.setStyleSheet(prev_stylesheet)
            QApplication.processEvents()

    def _refresh_utc_overlay(self, t_s: float) -> None:
        """Convert (et_start + t_s) to a UTC ISO string and push it
        into the VtkCanvas overlay. No-op (clears the overlay) when
        no snapshot / et_start is available."""
        if self._path is None:
            self._vtk.set_overlay_utc_text("")
            return
        info = resolve_run_context(self._path)
        if info is None:
            self._vtk.set_overlay_utc_text("")
            return
        try:
            from .time_conv import et_to_utc, format_utc_iso
            dt = et_to_utc(float(info["et_start_s"]) + float(t_s))
            # Three fractional digits = millisecond resolution: enough
            # for any orbital regime spody propagates, and short
            # enough that the overlay box stays small.
            self._vtk.set_overlay_utc_text(
                "UTC " + format_utc_iso(dt, fractional_digits=3))
        except Exception:
            self._vtk.set_overlay_utc_text("")

    def _sync_anim_bar_to_canvas(self) -> None:
        """Show / hide / range the playback bar after every 3D render.

        Visibility: the bar IS the 3D toolbar -- always visible while
        the canvas is on the 3D page so the Scene-options button
        stays reachable. Playback controls (play / slider / speed)
        get enabled only when the canvas actually has animation
        handles; otherwise they're greyed out but the Scene button
        still works (user can re-enable the spacecraft trajectory
        from the dialog and re-render)."""
        # Sync the Plot-options dialog's Export CSV state whenever a
        # new figure is rendered, so the user doesn't see a stale
        # enabled/disabled state if the dialog was left open between
        # plot clicks.
        if self._plot_options_dialog is not None:
            self._plot_options_dialog.set_export_enabled(
                self._can_export_active_plot_csv())
        if self._stack.currentIndex() != 1:
            self._anim_bar.setVisible(False)
            # Drop the UTC overlay too: it's a 3D-only widget and a
            # leftover string on a 2D switch would mislead.
            self._vtk.set_overlay_utc_text("")
            return
        self._anim_bar.setVisible(True)
        rng = self._vtk.animation_time_range()
        if rng is None:
            self._anim_bar.set_enabled(False)
            self._vtk.set_overlay_utc_text("")
        else:
            self._anim_bar.set_time_range(*rng)
            # Seed the overlay at t_min so the UTC is visible before
            # the user has nudged the slider for the first time.
            self._refresh_utc_overlay(rng[0])

    # ------------------------------------------------------------------
    # Scene options dialog -- non-modal, mutates self._scene_options
    # in place, and triggers a re-render of the active 3D plot on
    # every toggle.
    # ------------------------------------------------------------------
    def _on_open_scene_options(self) -> None:
        if self._scene_dialog is None:
            self._scene_dialog = SceneOptionsDialog(
                self._scene_options, parent=self)
            self._scene_dialog.optionsChanged.connect(
                self._on_scene_options_changed)
            self._refresh_scene_dialog_bodies()
        # Re-gate the starfield checkbox every time the dialog opens:
        # the user may have just edited Settings > Paths to point at a
        # newly-downloaded star map (or cleared it).
        self._scene_dialog.set_starfield_available(
            self._configured_star_texture() is not None)
        self._scene_dialog.show()
        self._scene_dialog.raise_()
        self._scene_dialog.activateWindow()

    def _on_scene_options_changed(self) -> None:
        """A Scene-options toggle fired; re-render whatever the user
        is currently viewing so the change shows up live without a
        manual Plot click. Trail state is a canvas-level flag that
        survives clear_scene; we push it BEFORE the re-render so the
        newly-added trajectories inherit the correct mode.

        Both 2D and 3D plots are re-rendered: 3D plots react to the
        triad / third-body / trail toggles, and 2D orbital-element
        plots react to the CR3BP primary-selector radio. Re-render is
        cheap for 2D (single matplotlib redraw) and harmless when the
        active spec doesn't actually consume the changed option."""
        # Persist the starfield toggle to QSettings so the choice
        # survives session restarts. Other toggles stay session-local
        # for now (matches the existing dataclass-default behaviour).
        try:
            self._store.set_show_starfield(self._scene_options.show_starfield)
        except Exception:
            pass
        if self._active_spec is None:
            return
        is_3d = (self._stack.currentIndex() == 1)
        if is_3d:
            # Trail mode is a canvas flag, not a per-handle property.
            # Push it now so the about-to-be-added handles see the right
            # state inside add_animated_trajectory.
            self._vtk.set_trail_enabled(self._scene_options.trail_enabled)
            # Skybox state: pushed BEFORE the rebuild so clear_scene
            # reinstalls the right (or no) actor instead of relying on
            # a stale cache from the previous toggle state.
            self._apply_skybox_to_canvas()
            # Preserve the playhead position across the re-plot. The
            # rebuild fires set_time_range which would otherwise snap
            # the animation back to t_min, wiping out wherever the
            # user was scrubbed to when they opened the dialog.
            saved_t = self._anim_bar.current_time()
        self._plot_active()
        if is_3d:
            rng = self._vtk.animation_time_range()
            if rng is not None and rng[0] <= saved_t <= rng[1]:
                self._anim_bar.set_time(saved_t)

    # ------------------------------------------------------------------
    # Plot options dialog (2D canvas) -- non-modal, hosts Export CSV
    # and any future per-plot toggles. Counterpart to the 3D Scene
    # options dialog above.
    # ------------------------------------------------------------------
    def _on_open_plot_options(self) -> None:
        if self._plot_options_dialog is None:
            self._plot_options_dialog = PlotOptionsDialog(parent=self)
            self._plot_options_dialog.exportCsvRequested.connect(
                self._export_active_plot_csv)
            self._plot_options_dialog.plotFrameChanged.connect(
                self._on_plot_frame_changed)
        self._plot_options_dialog.set_export_enabled(
            self._can_export_active_plot_csv())
        self._plot_options_dialog.set_frame(self._plot_frame)
        self._refresh_plot_options_bf_availability()
        self._plot_options_dialog.clear_status()
        self._plot_options_dialog.show()
        self._plot_options_dialog.raise_()
        self._plot_options_dialog.activateWindow()

    def _on_plot_frame_changed(self, frame: str) -> None:
        """User flipped the frame radio: persist on the panel and
        re-render the active plot so the new frame takes effect
        immediately without a manual file-tree click."""
        if frame not in ("icrf", "bf"):
            return
        if self._plot_frame == frame:
            return
        self._plot_frame = frame
        # The Info tab does not consume the plot frame today, so just
        # re-render the canvas. _on_plot_tree_clicked rebuilds the
        # PlotContext (which reads self._plot_frame) so the choice
        # threads through every context-mode plot in the registry.
        current = self._plot_tree.currentItem()
        if current is not None and current.data(0, _SPEC_ROLE) is not None:
            self._on_plot_tree_clicked(current, 0)

    def _refresh_plot_options_bf_availability(self) -> None:
        """Push the BF-availability state into the Plot-options
        dialog: BF is available only for HF runs whose central body
        has a registered `bf_orientation` (Earth / Moon today). The
        dialog greys out the BF radio and falls back to ICRF when
        unavailable."""
        if self._plot_options_dialog is None:
            return
        available = (
            self._dynamics_model != "cr3bp"
            and self._central_body.bf_orientation is not None)
        bf_label = self._central_body.bf_frame_name if available else ""
        self._plot_options_dialog.set_bf_available(available, bf_label)

    def _can_export_active_plot_csv(self) -> bool:
        """True iff the matplotlib figure currently shows a 2D plot
        with at least one Line2D somewhere on it. Tile mode counts as
        long as one subplot has lines."""
        if self._stack.currentIndex() != 0:
            return False
        for ax in self._figure.axes:
            if ax.get_lines():
                return True
        return False

    def _export_active_plot_csv(self) -> None:
        """Dump every Line2D on the current matplotlib figure to a CSV
        file. Tile mode produces one section per subplot, separated by
        a blank line and a comment header carrying the subplot title.
        Lines that share an identical x-array collapse to `x, y1, y2,
        ...`; otherwise each line gets its own `x_<lbl>, y_<lbl>`
        pair, padded with empty cells to the longest length.

        Scatter / fill / collection-based plots (impact lat/lon, batch
        density heatmaps, ...) carry no Line2D and are skipped with a
        message instead of writing an empty file."""
        if not self._can_export_active_plot_csv():
            QMessageBox.information(
                self, "Nothing to export",
                "The current plot has no line data to export "
                "(scatter, fill, and 3D plots aren't supported yet).")
            return
        stem = self._path.stem if self._path is not None else "plot"
        label = (self._active_spec.label
                 if self._active_spec is not None else "plot")
        safe_label = "".join(c if c.isalnum() or c in "-_" else "_"
                             for c in label)
        suggested = f"{stem}_{safe_label}.csv"
        start_dir = (str(self._working_dir / suggested)
                     if self._working_dir is not None else suggested)
        dest, _ = QFileDialog.getSaveFileName(
            self, "Export CSV", start_dir, "CSV files (*.csv);;All files (*)")
        if not dest:
            return
        dest_path = Path(dest)
        dlg = self._plot_options_dialog
        # Visible progress on a fast op: wait cursor + status label in
        # the dialog. processEvents() flushes the paint so the user
        # actually sees the message even when the write completes in a
        # few ms. The cursor is restored unconditionally in `finally`.
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        if dlg is not None:
            dlg.set_status(f"Saving to {dest_path.name}...")
        QApplication.processEvents()
        try:
            csv_text = _serialize_axes_to_csv(self._figure.axes)
            dest_path.write_text(csv_text, encoding="utf-8")
        except OSError as exc:
            if dlg is not None:
                dlg.set_status(f"Export failed: {exc}", ok=False)
            QMessageBox.critical(self, "Export failed", str(exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        # Success: confirm in the panel info label (stays visible after
        # the dialog auto-closes) and dismiss the dialog so the user
        # is back at the canvas.
        size_kb = dest_path.stat().st_size / 1024.0
        self._info_label.setText(
            f"Exported CSV: {dest_path}  ({size_kb:.1f} kB)")
        if dlg is not None:
            dlg.set_status(f"Saved {size_kb:.1f} kB")
            dlg.hide()

    def _resolve_central_body_from_snapshot(self) -> CentralBodySpec:
        """Read `force_model.central_body` from the loaded run's
        snapshot TOML and resolve to a `CentralBodySpec`. Falls back
        to `default_central_body()` (Moon) whenever the snapshot is
        missing, the TOML is unreadable, or the body name is not
        registered (legacy `.bin` files without a snapshot keep
        rendering as before)."""
        if self._path is None:
            return default_central_body()
        info = resolve_run_context(self._path)
        if info is None:
            return default_central_body()
        try:
            cfg = read_toml(info["toml_path"])
        except (OSError, ValueError):
            return default_central_body()
        name = cfg.get("force_model", {}).get("central_body", "")
        spec = resolve_central_body(name) if isinstance(name, str) else None
        return spec if spec is not None else default_central_body()

    def _resolve_dynamics_model_from_snapshot(self) -> str:
        """Look up `simulation.dynamics_model` from the run's snapshot
        TOML. Returns "high_fidelity" when the key is absent, when the
        snapshot is missing, or when the file is unreadable -- legacy
        runs and bare .bin files therefore always look HF to the
        downstream plots, matching pre-CR3BP behaviour."""
        if self._path is None:
            return "high_fidelity"
        info = resolve_run_context(self._path)
        if info is None:
            return "high_fidelity"
        try:
            cfg = read_toml(info["toml_path"])
        except (OSError, ValueError):
            return "high_fidelity"
        model = cfg.get("simulation", {}).get("dynamics_model", "")
        return str(model) if model else "high_fidelity"

    def _resolve_cr3bp_primaries_from_snapshot(
        self) -> tuple[CR3BPPrimary, ...]:
        """Build the two CR3BP primary descriptors from the snapshot's
        [cr3bp] section. Returns an empty tuple for non-CR3BP runs (or
        when [cr3bp] is missing / unrecognised), so downstream plots
        can `if ctx.cr3bp_primaries:` to branch."""
        if self._path is None:
            return ()
        info = resolve_run_context(self._path)
        if info is None:
            return ()
        try:
            cfg = read_toml(info["toml_path"])
        except (OSError, ValueError):
            return ()
        if cfg.get("simulation", {}).get("dynamics_model", "") != "cr3bp":
            return ()
        cr = cfg.get("cr3bp", {})
        name1 = str(cr.get("primary_1", ""))
        name2 = str(cr.get("primary_2", ""))
        spec1 = resolve_central_body(name1)
        spec2 = resolve_central_body(name2)
        # The curated CR3BP_PAIRS in the engine + the Python form
        # mirror it; Earth-Moon is the only pair today and both bodies
        # are in the central-body registry. Future pairs (Sun-Earth,
        # ...) will need the Sun registered there too -- gating on
        # `spec is not None` keeps the GUI graceful in the interim.
        if spec1 is None or spec2 is None:
            return ()
        # L comes from the same constant the engine uses
        # (EARTH_MOON_DISTANCE_KM in spody_const.h, via constants.py).
        # Gated to the single registered pair until more pairs land.
        if {name1, name2} != {"Earth", "Moon"}:
            return ()
        L_km = constants.EARTH_MOON_DISTANCE_KM
        mu1 = spec1.mu_km3_s2
        mu2 = spec2.mu_km3_s2
        mu_tot = mu1 + mu2
        x1 = -(mu2 / mu_tot) * L_km
        x2 = +(mu1 / mu_tot) * L_km
        return (
            CR3BPPrimary(name=spec1.name,
                         position_km=(x1, 0.0, 0.0),
                         radius_km=spec1.radius_km,
                         mu_km3_s2=mu1),
            CR3BPPrimary(name=spec2.name,
                         position_km=(x2, 0.0, 0.0),
                         radius_km=spec2.radius_km,
                         mu_km3_s2=mu2),
        )

    def _build_plot_context(self, path: Path) -> PlotContext:
        """Single source of truth for the PlotContext fed to every
        context-aware plot fn. Centralised here so that adding a new
        side-channel (dynamics_model, cr3bp_primaries, ...) is one
        place, not four. Caller is responsible for passing a non-None
        path; we trust load_file to have populated `self._path` and
        the model-specific resolved state already."""
        return PlotContext(
            path=path,
            central_body_texture=self._configured_central_body_texture(),
            scene_options=self._scene_options,
            central_body=self._central_body,
            dynamics_model=self._dynamics_model,
            cr3bp_primaries=self._cr3bp_primaries,
            plot_frame=self._plot_frame,
        )

    def _third_bodies_from_snapshot(self) -> list[str]:
        """Pull `force_model.third_bodies` out of the loaded run's
        snapshot TOML. Empty list when no snapshot, no [force_model]
        section, or no [force_model].third_bodies entry. Used both to
        seed `scene_options.show_bodies` on every load (so the first
        3D render shows arrows / markers without waiting for the user
        to open the Scene-options dialog) and to feed the dialog's
        per-body checkbox list when it does open."""
        if self._path is None:
            return []
        info = resolve_run_context(self._path)
        if info is None:
            return []
        try:
            cfg = read_toml(info["toml_path"])
        except (OSError, ValueError):
            return []
        bodies = cfg.get("force_model", {}).get("third_bodies", [])
        if not isinstance(bodies, list):
            return []
        return [b for b in bodies if isinstance(b, str)]

    def _seed_show_bodies_from_snapshot(self) -> None:
        """When `scene_options.show_bodies` is still empty (fresh
        session, or a dataclass-default panel that has never had its
        body list populated), pre-fill it with every third body the
        snapshot TOML declares. `add_third_bodies` treats an empty
        set as 'hide every body' (the user explicitly unchecked them
        all in the dialog); without this seed, the first 3D render
        of a new file drops the Sun arrow / Moon marker / etc. even
        though their checkboxes in the dialog appear ticked the next
        time the dialog opens. We deliberately do NOT overwrite a
        non-empty set: the user's explicit hide-all-but-X choice
        from a previous session must survive."""
        if self._scene_options.show_bodies:
            return
        self._scene_options.show_bodies = set(self._third_bodies_from_snapshot())

    def _refresh_scene_dialog_bodies(self) -> None:
        """Push the current run's `force_model.third_bodies` into
        the Scene-options dialog so the per-body checkboxes match,
        and update the body-fixed triad label so it reads "PA + Moon
        libration" / "ITRF + Earth rotation" / ... per the resolved
        central body. Also reconfigures the dialog for the run's
        dynamics model: CR3BP runs collapse HF-only groups and reveal
        the primary-selector for the osculating orbital elements.
        No-op when the dialog hasn't been opened yet or the snapshot
        is missing."""
        if self._scene_dialog is None:
            return
        # Body-fixed triad label always reflects the resolved central
        # body, even before bodies are loaded.
        self._scene_dialog.set_body_frame_label(
            self._central_body.name, self._central_body.bf_frame_name)
        # Dynamics-model switch: triggers HF/CR3BP group visibility
        # inside the dialog. Done up here so CR3BP runs end up showing
        # the primary radios even when no snapshot is available beyond
        # the [cr3bp] section.
        primary_names: tuple[str, str] | None = (
            (self._cr3bp_primaries[0].name, self._cr3bp_primaries[1].name)
            if self._cr3bp_primaries else None)
        self._scene_dialog.set_dynamics_model(
            self._dynamics_model, primary_names)
        self._scene_dialog.set_available_bodies(
            self._third_bodies_from_snapshot())

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

    def _configured_star_texture(self) -> Path | None:
        """Resolve the equirectangular star map for the 3D skybox via
        the wizard-managed data dir (same flow as the Moon / Earth
        textures). Returns None when the user has not downloaded the
        asset yet, which the caller turns into a disabled 'Show
        starfield' checkbox + a no-op canvas state."""
        from . import assets
        return assets.star_texture_path(self._store.data_dir())

    def _apply_skybox_to_canvas(self) -> None:
        """Push the skybox state to VtkCanvas based on the current
        SceneOptions toggle + the configured star texture path. Pulled
        out so every render path (initial plot, re-render after a
        scene-options toggle, scene rebuild via clear_scene) can
        share one call."""
        if self._scene_options.show_starfield:
            tex = self._configured_star_texture()
            self._vtk.set_skybox_texture(tex)
        else:
            self._vtk.set_skybox_texture(None)

    def _configured_central_body_texture(self) -> Path | None:
        """Resolve the equirectangular texture for the currently loaded
        run's central body. Looked up on demand so edits via the
        Settings dialog take effect on the next 3D plot without
        restarting.

        Resolution order:
          1. Legacy Moon path: when the run's central body is Moon AND
             the user has a Settings > Paths override (`moon_texture`),
             honour the override. Kept so existing user setups keep
             working bit-for-bit.
          2. Body-aware wizard fallback: walk the asset registry for
             entries tagged `category='texture', body=<body name>`
             and return the first one present under the data dir.
             Adding Earth in Phase 2 is one Asset entry + nothing
             here -- this path picks it up automatically.

        Returns None when neither yields a file -- VtkCanvas / the
        2D map then fall back to the flat-grey sphere / no background."""
        from . import assets
        body_name = self._central_body.name
        if body_name == "Moon":
            raw = self._store.moon_texture()
            if raw and Path(raw).is_file():
                return Path(raw)
        return assets.central_body_texture_path(
            self._store.data_dir(), body_name)

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

        reader = READERS[self._kind]
        try:
            data_a = reader(paths[0])
            data_b = reader(paths[1])
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Diff read failed", str(exc))
            return

        # Align upfront so the plot fn just subtracts; same path
        # whether the grids match or B had to be interpolated.
        try:
            data_a, data_b, was_interp, note = align_or_interp(data_a, data_b)
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
        # Cache the aligned pair so `_refresh_info_tab` can compute
        # |Δr| / |Δv| / RIC stats without re-reading the files. The
        # caller (`_on_plot_tree_clicked`) refreshes the Info tab
        # after dispatch, so we only set the payload here.
        self._last_diff = (paths, data_a, data_b, was_interp)

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
        # Context-mode plots receive a PlotContext alongside the data
        # array so they can resolve sibling files (input.toml, cases CSV,
        # ephemeris .spody) and pick up the Moon texture without
        # touching QSettings. Built once here so each plot fn stays a
        # pure (ax, data, ctx) call. self._path is guaranteed non-None
        # whenever self._data is (set together in load_file).
        ctx = (self._build_plot_context(self._path)
               if spec.mode == "context" and self._path is not None
               else None)
        try:
            if spec.dim == "2d":
                self._stack.setCurrentIndex(0)
                self._figure.clear()
                ax_kwargs = {"projection": spec.projection} if spec.projection else {}
                ax = self._figure.add_subplot(111, **ax_kwargs)
                if ctx is not None:
                    spec.fn(ax, self._data, ctx)
                else:
                    spec.fn(ax, self._data)
                self._figure.tight_layout()
                self._canvas.draw_idle()
            else:  # "3d"
                # Preserve the user's camera pan / zoom across
                # re-renders of the SAME file (Scene-options toggle,
                # animation refresh). Only a fresh file load (or
                # never-loaded) takes the ResetCamera auto-fit branch.
                preserve = (self._last_3d_path == self._path
                            and self._stack.currentIndex() == 1)
                saved_pose = self._vtk.capture_camera_pose() if preserve else None
                self._stack.setCurrentIndex(1)
                self._vtk.set_central_body_texture(self._configured_central_body_texture())
                self._apply_skybox_to_canvas()
                self._vtk.clear_scene()
                if ctx is not None:
                    spec.fn(self._vtk, self._data, ctx)
                else:
                    spec.fn(self._vtk, self._data)
                if saved_pose is not None:
                    self._vtk.restore_camera_pose(saved_pose)
                else:
                    # See _on_overlay_selected for why we lock the focal
                    # point at the origin instead of using vanilla
                    # reset_camera() here.
                    self._vtk.reset_camera_on_origin()
                self._vtk.render()
                self._last_3d_path = self._path
            self._sync_anim_bar_to_canvas()
        except Exception as exc:  # noqa: BLE001 -- surface anything to user
            QMessageBox.critical(self, "Plot failed", repr(exc))
