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

"""Uncertainty tab: edit a `<name>.uq.toml` (manual ch. 14).

An uncertainty file points at a propagate scenario and says what to
disperse: the initial state (standard deviations in RIC or ICRF axes,
with an optional correlation, or a full 6x6 covariance), physical
parameters (normal or lognormal) and process noise (Gauss-Markov
density and RIC accelerations along the run). The engine owns every check
(`spody_load_uq_input` / `spody_validate_uq_input`); the form only
refuses what it cannot write as TOML (an unparsable number, a lognormal
without `scenario_value_is`), so the GUI never drifts from the loader.

Reading goes through tomli, writing through `toml_io.format_uq_toml`.
The file's top comment block is kept on save; other comments are not.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import numpy as np
import tomli
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .form.catalog import BATCH_TARGETS, TOOLTIPS
from .terminal import TerminalView
from .toml_form import RUN_BUTTON_QSS, STOP_BUTTON_QSS
from .toml_io import (UQ_SUFFIX, find_toml_files, format_uq_toml,
                      is_uq_toml, uq_header_comment)

# Batch targets the Monte Carlo cannot disperse: the initial state has
# its own table, and epochs, integrator and output settings are not
# uncertainties (the engine refuses them, `uq_check_target`).
_NOT_DISPERSIBLE_PREFIXES = ("simulation.", "initial_state.", "integrator.", "output.")
_NOT_DISPERSIBLE = ("force_model.srp", "force_model.drag")

# (TOML key, label) of the ways a sigma can be given, per distribution.
_SIGMA_KINDS = {
    "normal":    (("sigma", "absolute"), ("sigma_percent", "% of scenario value")),
    "lognormal": (("sigma_percent", "% of scenario value"), ("sigma_ln", "σ of ln (log space)")),
}

# Run folders are named by their UTC start (2026-10-04T160914Z): files
# inside them are run copies, never edited in place.
_RUN_FOLDER_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{6}Z")

_NEW_HEADER = ("# SpOdy uncertainty file (manual ch. 14).\n#\n"
               "#   spody uncertainty montecarlo <this file>")

_TIPS = {
    "name": "Base name of the output files (<ts>_<name>_moments.uq.bin, ...).",
    "scenario": "The propagate scenario this Monte Carlo disperses: a high-fidelity "
                "TOML with output.mode = \"fixed\" and no [batch] section. Its "
                "propagation is case 0, the nominal every case is compared with.",
    "samples": "Number of dispersed cases (the nominal, case 0, comes on top). The "
               "error of a sigma estimate is about 1/sqrt(2(N-1)): 3 % with 500 cases.",
    "seed": "Random seed (integer >= 0). Same seed and same file give the same "
            "samples bit for bit, whatever the thread count.",
    "threads": "Cases propagated in parallel. The results do not depend on it.",
    "output_dir": "Folder of the run folders, relative to this file. Empty: the "
                  "scenario's output.output_dir.",
    "snapshots": "Times in seconds from the start, comma separated, where every case's "
                 "state is saved (the clouds file). Each must be an output time of "
                 "the scenario.",
    "case_outputs": "Also write every case's own trajectory file (SPDYOUT_). Large: "
                    "N times the scenario's output.",
    "axes": "RIC: radial / in-track / cross-track of the scenario's initial orbit "
            "(an orbit-determination error is usually given this way). ICRF: the "
            "inertial x / y / z axes.",
    "mode": "Standard deviations per component, optionally with a correlation "
            "matrix, or the full 6x6 covariance (km^2, km^2/s, km^2/s^2).",
    "vis": "Lognormal only, required: is the scenario's value the MEAN of the "
           "distribution (an estimate, e.g. a fitted Cd) or its MEDIAN (a typical "
           "factor, e.g. density_scale = 1)? They differ by e^(sigma_ln^2 / 2).",
    "pn": "Errors that change along the trajectory, a different history in every "
          "case (manual ch. 14). Each entry is optional; the nominal has no noise.",
    "pn_density": "Density multiplied by exp(eta(t)), eta a Gauss-Markov process. "
                  "Needs force_model.drag on.",
    "pn_sigma_ln": "Standard deviation of ln(rho_case / rho_model) at any instant "
                   "(0.08 = about 8 %). With ap_doubling: the value with no activity.",
    "pn_tau_s": "Correlation time [s]: two instants tau_s apart are correlated by "
                "e^-1 = 0.37.",
    "pn_interval_s": "Spacing of the noise nodes [s], at most tau_s (tau_s / 10 or "
                     "less recommended); linear interpolation in between.",
    "pn_vis": "Required: is the scenario's density the MEAN or the MEDIAN of the "
              "noisy one? With mean the factor is exp(eta - sigma_ln^2 / 2).",
    "pn_ap_doubling": "Optional, > 0: sigma becomes sigma_ln (1 + Ap(t) / ap_doubling), "
                      "Ap from the scenario's space-weather file. Empty: constant sigma.",
    "pn_accel": "Acceleration in the case's radial / in-track / cross-track axes, each "
                "axis its own Gauss-Markov process (forces the model leaves out).",
    "pn_accel_1rev": "Once-per-revolution part: on each axis A cos u + B sin u, u the "
                     "argument of latitude, A and B Gauss-Markov with that axis's sigma "
                     "and tau (thermospheric winds, unmodelled tides).",
    "pn_sigma_m_s2": "Stationary standard deviation per axis [m/s^2], >= 0; an axis "
                     "with 0 stays exact, at least one must be > 0.",
    "pn_tau_ric": "Correlation time [s]: only R filled = one value for the three axes, "
                  "or one per axis.",
    "pn_interval_ric": "Spacing of the nodes [s], at most the shortest tau_s of an axis "
                       "with sigma > 0 (a tenth of it or less recommended).",
}

# The process-noise entries with RIC axes: TOML key, group title, tooltip.
_PN_RIC = (("acceleration", "acceleration  (RIC)", "pn_accel"),
           ("acceleration_1rev", "acceleration_1rev  (once per revolution)", "pn_accel_1rev"))


def dispersible_targets(mode: str) -> list[str]:
    """Batch targets a Monte Carlo can disperse for a scenario of this
    object mode ("spacecraft" | "debris")."""
    return [p for p, tag in BATCH_TARGETS
            if (tag is None or tag == mode)
            and not p.startswith(_NOT_DISPERSIBLE_PREFIXES)
            and p not in _NOT_DISPERSIBLE]


def _num(x: float) -> str:
    """Full-precision number for an edit field, never in scientific
    notation (0.00005, not 5e-05)."""
    return np.format_float_positional(float(x), trim="-")


def _get_dotted(data: dict, path: str):
    cur = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


class SymMatrix6(QTableWidget):
    """Editable symmetric 6x6 matrix: typing (i, j) fills (j, i). With
    `unit_diag` the diagonal is fixed at 1 (a correlation matrix)."""

    edited = Signal()

    def __init__(self, unit_diag: bool) -> None:
        super().__init__(6, 6)
        self._unit_diag = unit_diag
        self._mirroring = False
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setMinimumHeight(190)
        self.set_values(np.eye(6) if unit_diag else np.zeros((6, 6)))
        self.itemChanged.connect(self._on_item_changed)

    def set_axes(self, ric: bool) -> None:
        names = (["R", "I", "C", "vR", "vI", "vC"] if ric
                 else ["x", "y", "z", "vx", "vy", "vz"])
        self.setHorizontalHeaderLabels(names)
        self.setVerticalHeaderLabels(names)

    def set_values(self, m) -> None:
        self._mirroring = True
        for i in range(6):
            for j in range(6):
                it = QTableWidgetItem(_num(m[i][j]))
                it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                if self._unit_diag and i == j:
                    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.setItem(i, j, it)
        self._mirroring = False

    def values(self) -> list[list[float]]:
        """The matrix; raises ValueError naming the first bad cell."""
        out = []
        for i in range(6):
            row = []
            for j in range(6):
                text = self.item(i, j).text().strip()
                try:
                    row.append(float(text))
                except ValueError:
                    raise ValueError(f"cell ({self.verticalHeaderItem(i).text()}, "
                                     f"{self.horizontalHeaderItem(j).text()}) = "
                                     f"'{text}' is not a number") from None
            out.append(row)
        return out

    def _on_item_changed(self, it: QTableWidgetItem) -> None:
        if self._mirroring:
            return
        i, j = it.row(), it.column()
        if i != j:
            self._mirroring = True
            self.item(j, i).setText(it.text())
            self._mirroring = False
        self.edited.emit()


class UncertaintyPanel(QWidget):
    """The Uncertainty tab: a `.uq.toml` picker with New / Load / Save /
    Save As, the form, and the run terminal."""

    fileLoadedOrSaved = Signal(Path)
    # Path or modified flag changed (window title).
    stateChanged = Signal()
    # Draw samples (True) / RUN (False); MainWindow owns the runner.
    runRequested = Signal(bool)
    stopRequested = Signal()
    # A finished run's moments file, to open in the Analysis tab.
    openResultsRequested = Signal(Path)

    def __init__(self, store) -> None:
        super().__init__()
        self._store = store
        self._working_dir: Path | None = None
        self._path: Path | None = None
        self._modified = False
        self._loading = True          # until _reset(): no edits during the build
        self._header = _NEW_HEADER
        self._scenario_data: dict = {}
        self._rows: list[dict] = []

        # Same layout as the Run tab: on the left the file row, the
        # path + launch buttons, the form and the live TOML preview; on
        # the right the terminal.
        column = QWidget()
        col_lay = QVBoxLayout(column)
        col_lay.setContentsMargins(0, 0, 0, 0)
        col_lay.setSpacing(4)
        col_lay.addWidget(self._build_file_row())

        self._path_label = QLabel("(no file)")
        self._path_label.setStyleSheet("color: gray;")
        self._samples_btn = QPushButton("Draw samples")
        self._samples_btn.setToolTip(
            "Check the file and draw the samples without propagating "
            "(spody uncertainty montecarlo --samples-only).")
        self._run_btn = QPushButton("RUN")
        self._run_btn.setStyleSheet(RUN_BUTTON_QSS)
        self._stop_btn = QPushButton("Stop")
        self._stop_btn.setStyleSheet(STOP_BUTTON_QSS)
        self._stop_btn.setEnabled(False)
        self._stop_btn.setToolTip("Kill the running spody process (Ctrl+.)")
        self._samples_btn.clicked.connect(lambda: self.runRequested.emit(True))
        self._run_btn.clicked.connect(lambda: self.runRequested.emit(False))
        self._stop_btn.clicked.connect(self.stopRequested)
        top_row = QHBoxLayout()
        top_row.addWidget(self._path_label, 1)
        top_row.addWidget(self._samples_btn)
        top_row.addWidget(self._run_btn)
        top_row.addWidget(self._stop_btn)
        col_lay.addLayout(top_row)
        self._badge = QLabel("")
        self._badge.setMinimumWidth(160)
        self._results: Path | None = None
        self._open_btn = QPushButton("Open results in Analysis")
        self._open_btn.setToolTip("Show the last run's moments file in the Analysis tab "
                                  "(the clouds file is next to it in the same folder).")
        self._open_btn.setEnabled(False)
        self._open_btn.clicked.connect(
            lambda: self._results and self.openResultsRequested.emit(self._results))
        badge_row = QHBoxLayout()
        badge_row.addWidget(self._open_btn)
        badge_row.addStretch(1)
        badge_row.addWidget(self._badge)
        col_lay.addLayout(badge_row)

        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.addWidget(self._build_run_group())
        body_lay.addWidget(self._build_state_group())
        body_lay.addWidget(self._build_params_group())
        body_lay.addWidget(self._build_noise_group())
        body_lay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)

        preview_box = QWidget()
        preview_lay = QVBoxLayout(preview_box)
        preview_lay.setContentsMargins(0, 0, 0, 0)
        header = QLabel("TOML preview  (read-only; reflects the form live):")
        header.setStyleSheet("color: gray; padding-top: 4px;")
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)
        self._preview.setFont(mono)
        self._preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        preview_lay.addWidget(header)
        preview_lay.addWidget(self._preview, 1)

        vsplit = QSplitter(Qt.Orientation.Vertical)
        vsplit.addWidget(scroll)
        vsplit.addWidget(preview_box)
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 2)
        vsplit.setSizes([520, 280])
        col_lay.addWidget(vsplit, 1)

        self.terminal = TerminalView()
        split = QSplitter(Qt.Orientation.Horizontal)
        split.addWidget(column)
        split.addWidget(self.terminal)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        split.setSizes([640, 640])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(split, 1)

        self._reset()

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def _build_file_row(self) -> QWidget:
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(QLabel("TOML:"))
        self._file_combo = QComboBox()
        self._file_combo.setMinimumWidth(240)
        self._file_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._file_combo.activated.connect(self._on_file_activated)
        lay.addWidget(self._file_combo, 1)
        # File > New / Save / Save As act on this tab while it is shown.
        for text, slot in (("Load TOML...", self.action_load),
                           ("Save", self.action_save), ("Save As...", self.action_save_as)):
            b = QPushButton(text)
            b.clicked.connect(lambda _c=False, f=slot: f())
            lay.addWidget(b)
        return row

    def _build_run_group(self) -> QGroupBox:
        box = QGroupBox("[montecarlo]")
        f = QFormLayout(box)

        self._name = QLineEdit()
        self._name.textEdited.connect(self._touch)

        self._scenario = QComboBox()
        self._scenario.activated.connect(lambda _i: self._on_scenario_changed())
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse_scenario)
        sc_row = QWidget()
        sc_lay = QHBoxLayout(sc_row)
        sc_lay.setContentsMargins(0, 0, 0, 0)
        sc_lay.addWidget(self._scenario, 1)
        sc_lay.addWidget(browse)
        self._scenario_info = QLabel("")
        self._scenario_info.setWordWrap(True)
        self._scenario_info.setStyleSheet("color: gray;")

        self._samples = QSpinBox()
        self._samples.setRange(2, 10_000_000)
        self._samples.valueChanged.connect(self._touch)
        self._seed = QLineEdit()
        self._seed.textEdited.connect(self._touch)
        self._threads = QSpinBox()
        self._threads.setRange(1, max(1, os.cpu_count() or 1))
        self._threads.valueChanged.connect(self._touch)
        self._output_dir = QLineEdit()
        self._output_dir.setPlaceholderText("(empty: the scenario's output_dir)")
        self._output_dir.textEdited.connect(self._touch)
        self._snapshots = QLineEdit()
        self._snapshots.setPlaceholderText("e.g. 86400, 172800 (seconds from the start)")
        self._snapshots.textEdited.connect(self._touch)
        self._case_outputs = QCheckBox("write every case's trajectory file (large)")
        self._case_outputs.toggled.connect(self._touch)

        for label, w, tip in (("name", self._name, "name"),
                              ("scenario", sc_row, "scenario"),
                              ("", self._scenario_info, "scenario"),
                              ("samples", self._samples, "samples"),
                              ("seed", self._seed, "seed"),
                              ("thread_number", self._threads, "threads"),
                              ("output_dir", self._output_dir, "output_dir"),
                              ("snapshots_s", self._snapshots, "snapshots"),
                              ("case_outputs", self._case_outputs, "case_outputs")):
            w.setToolTip(_TIPS[tip])
            f.addRow(label, w)
        return box

    def _build_state_group(self) -> QGroupBox:
        box = QGroupBox("[montecarlo.initial_state]  (optional)")
        box.setCheckable(True)
        box.toggled.connect(self._touch)
        self._state_box = box
        v = QVBoxLayout(box)

        top = QFormLayout()
        self._axes = QComboBox()
        self._axes.addItems(["RIC (radial, in-track, cross-track)", "ICRF (x, y, z)"])
        self._axes.setToolTip(_TIPS["axes"])
        self._axes.currentIndexChanged.connect(self._on_axes_changed)
        self._mode = QComboBox()
        self._mode.addItems(["standard deviations", "6x6 covariance"])
        self._mode.setToolTip(_TIPS["mode"])
        self._mode.currentIndexChanged.connect(self._on_mode_changed)
        top.addRow("axes", self._axes)
        top.addRow("given as", self._mode)
        v.addLayout(top)

        self._state_stack = QStackedWidget()
        sig = QWidget()
        g = QGridLayout(sig)
        g.setContentsMargins(0, 0, 0, 0)
        self._sig_edits: list[QLineEdit] = []
        self._sig_heads = [QLabel(), QLabel(), QLabel()]
        for k, h in enumerate(self._sig_heads):
            h.setAlignment(Qt.AlignmentFlag.AlignCenter)
            g.addWidget(h, 0, k + 1)
        for r, label in enumerate(("position_sigma_km", "velocity_sigma_kms")):
            g.addWidget(QLabel(label), r + 1, 0)
            for k in range(3):
                e = QLineEdit()
                e.textEdited.connect(self._touch)
                self._sig_edits.append(e)
                g.addWidget(e, r + 1, k + 1)
        self._use_corr = QCheckBox("correlation (6x6, optional)")
        self._use_corr.toggled.connect(self._on_corr_toggled)
        g.addWidget(self._use_corr, 3, 0, 1, 4)
        self._corr = SymMatrix6(unit_diag=True)
        self._corr.edited.connect(self._touch)
        g.addWidget(self._corr, 4, 0, 1, 4)
        self._state_stack.addWidget(sig)

        cov = QWidget()
        cv = QVBoxLayout(cov)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addWidget(QLabel("covariance [km², km²/s, km²/s²], symmetric: "
                            "typing one side fills the other"))
        self._cov = SymMatrix6(unit_diag=False)
        self._cov.edited.connect(self._touch)
        cv.addWidget(self._cov)
        self._state_stack.addWidget(cov)
        v.addWidget(self._state_stack)
        return box

    def _build_params_group(self) -> QGroupBox:
        box = QGroupBox("[montecarlo.parameters]  (optional)")
        v = QVBoxLayout(box)
        self._params = QTableWidget(0, 6)
        self._params.setHorizontalHeaderLabels(
            ["Target", "Scenario value", "Distribution", "σ given as", "σ", "Scenario value is"])
        self._params.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._params.horizontalHeader().setStretchLastSection(True)
        self._params.verticalHeader().setVisible(False)
        self._params.setMinimumHeight(140)
        v.addWidget(self._params)
        buttons = QHBoxLayout()
        add = QPushButton("Add parameter")
        add.clicked.connect(lambda: (self._add_row(), self._touch()))
        rem = QPushButton("Remove selected")
        rem.clicked.connect(self._remove_row)
        buttons.addWidget(add)
        buttons.addWidget(rem)
        buttons.addStretch(1)
        v.addLayout(buttons)
        return box

    def _build_noise_group(self) -> QGroupBox:
        """[montecarlo.process_noise]: three entries, each a checkable
        box (unchecked = the entry is not written)."""
        box = QGroupBox("[montecarlo.process_noise]  (optional)")
        box.setToolTip(_TIPS["pn"])
        v = QVBoxLayout(box)
        self._pn: dict[str, dict] = {}

        def edit(tip: str, hint: str = "") -> QLineEdit:
            e = QLineEdit()
            e.setToolTip(_TIPS[tip])
            e.setPlaceholderText(hint)
            e.textEdited.connect(self._touch)
            return e

        d = QGroupBox("density  (needs drag on)")
        d.setToolTip(_TIPS["pn_density"])
        d.setCheckable(True)
        d.toggled.connect(self._touch)
        f = QFormLayout(d)
        w: dict = {"box": d,
                   "sigma_ln": edit("pn_sigma_ln", "e.g. 0.063"),
                   "tau_s": edit("pn_tau_s", "e.g. 54600"),
                   "interval_s": edit("pn_interval_s", "e.g. 3600"),
                   "vis": QComboBox(),
                   "ap_doubling": edit("pn_ap_doubling", "empty: constant sigma_ln (e.g. 64.4)")}
        w["vis"].addItems(["(choose)", "mean", "median"])
        w["vis"].setToolTip(_TIPS["pn_vis"])
        w["vis"].currentIndexChanged.connect(self._touch)
        for key in ("sigma_ln", "tau_s", "interval_s"):
            f.addRow(key, w[key])
        f.addRow("scenario_value_is", w["vis"])
        f.addRow("ap_doubling", w["ap_doubling"])
        v.addWidget(d)
        self._pn["density"] = w

        for key, title, tip in _PN_RIC:
            b = QGroupBox(title)
            b.setToolTip(_TIPS[tip])
            b.setCheckable(True)
            b.toggled.connect(self._touch)
            g = QGridLayout(b)
            for k, name in enumerate(("R", "I", "C")):
                h = QLabel(name)
                h.setAlignment(Qt.AlignmentFlag.AlignCenter)
                g.addWidget(h, 0, k + 1)
            w = {"box": b,
                 "sigma_m_s2": [edit("pn_sigma_m_s2", "0") for _ in range(3)],
                 "tau_s": [edit("pn_tau_ric", "e.g. 1800")] + [edit("pn_tau_ric") for _ in range(2)],
                 "interval_s": edit("pn_interval_ric", "e.g. 60")}
            for r, row_key in enumerate(("sigma_m_s2", "tau_s")):
                g.addWidget(QLabel(row_key), r + 1, 0)
                for k in range(3):
                    g.addWidget(w[row_key][k], r + 1, k + 1)
            g.addWidget(QLabel("interval_s"), 3, 0)
            g.addWidget(w["interval_s"], 3, 1)
            v.addWidget(b)
            self._pn[key] = w
        return box

    # ------------------------------------------------------------------
    # Parameters table
    # ------------------------------------------------------------------
    def _object_mode(self) -> str:
        return "debris" if "debris" in self._scenario_data else "spacecraft"

    def _add_row(self, target: str | None = None, spec: dict | None = None) -> None:
        spec = spec or {}
        r = self._params.rowCount()
        self._params.insertRow(r)
        row = {"target": QComboBox(), "dist": QComboBox(), "kind": QComboBox(),
               "value": QLineEdit(), "vis": QComboBox()}
        targets = dispersible_targets(self._object_mode())
        if target and target not in targets:
            targets.append(target)       # keep what the file says; the engine judges
        row["target"].addItems(targets)
        if target:
            row["target"].setCurrentText(target)
        row["dist"].addItems(["normal", "lognormal"])
        row["dist"].setCurrentText(spec.get("distribution", "normal"))
        row["vis"].addItems(["(choose)", "mean", "median"])
        row["vis"].setToolTip(_TIPS["vis"])
        self._rows.append(row)
        self._fill_kinds(row)
        for key, _label in _SIGMA_KINDS[row["dist"].currentText()]:
            if key in spec:
                row["kind"].setCurrentIndex(
                    [k for k, _ in _SIGMA_KINDS[row["dist"].currentText()]].index(key))
                row["value"].setText(_num(spec[key]))
        if spec.get("scenario_value_is") in ("mean", "median"):
            row["vis"].setCurrentText(spec["scenario_value_is"])
        for col, key in ((0, "target"), (2, "dist"), (3, "kind"), (4, "value"), (5, "vis")):
            self._params.setCellWidget(r, col, row[key])
        item = QTableWidgetItem("")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._params.setItem(r, 1, item)

        row["target"].currentIndexChanged.connect(lambda _i, w=row: (self._refresh_row(w), self._touch()))
        row["dist"].currentIndexChanged.connect(lambda _i, w=row: (self._fill_kinds(w), self._refresh_row(w), self._touch()))
        row["kind"].currentIndexChanged.connect(self._touch)
        row["value"].textEdited.connect(self._touch)
        row["vis"].currentIndexChanged.connect(self._touch)
        self._refresh_row(row)

    def _fill_kinds(self, row: dict) -> None:
        row["kind"].blockSignals(True)
        row["kind"].clear()
        for key, label in _SIGMA_KINDS[row["dist"].currentText()]:
            row["kind"].addItem(label, key)
        row["kind"].blockSignals(False)

    def _refresh_row(self, row: dict) -> None:
        """Scenario value column and the lognormal-only combo."""
        r = self._rows.index(row)
        target = row["target"].currentText()
        val = _get_dotted(self._scenario_data, target)
        if val is None and target == "force_model.density_scale" and self._scenario_data:
            val = 1.0                       # the engine's default
        text = "-" if val is None else (_num(val) if isinstance(val, (int, float)) else str(val))
        self._params.item(r, 1).setText(text)
        tip = TOOLTIPS.get(target, "")
        row["target"].setToolTip(tip)
        row["vis"].setEnabled(row["dist"].currentText() == "lognormal")

    def _remove_row(self) -> None:
        r = self._params.currentRow()
        if r < 0:
            return
        self._params.removeRow(r)
        del self._rows[r]
        self._touch()

    # ------------------------------------------------------------------
    # Scenario
    # ------------------------------------------------------------------
    def _scenario_candidates(self) -> list[Path]:
        if self._working_dir is None or not self._working_dir.is_dir():
            return []
        return [p for p in find_toml_files(self._working_dir)
                if not is_uq_toml(p) and not _RUN_FOLDER_RE.fullmatch(p.parent.name)
                and not p.name.endswith(".wip.toml")]

    def _fill_scenario_combo(self, chosen: Path | None) -> None:
        self._scenario.blockSignals(True)
        self._scenario.clear()
        self._scenario.addItem("-- pick the scenario --", None)
        cands = self._scenario_candidates()
        if chosen is not None and chosen not in cands:
            cands.append(chosen)
        for p in cands:
            label = p.name if p.parent == self._working_dir else f"{p.parent.name}/{p.name}"
            self._scenario.addItem(label, str(p))
            self._scenario.setItemData(self._scenario.count() - 1, str(p),
                                       Qt.ItemDataRole.ToolTipRole)
            if chosen is not None and p == chosen:
                self._scenario.setCurrentIndex(self._scenario.count() - 1)
        self._scenario.blockSignals(False)

    def _scenario_path(self) -> Path | None:
        data = self._scenario.currentData()
        return Path(data) if data else None

    def _browse_scenario(self) -> None:
        start = str(self._path.parent if self._path else (self._working_dir or ""))
        path, _ = QFileDialog.getOpenFileName(self, "Scenario TOML", start,
                                              "TOML files (*.toml)")
        if not path:
            return
        if is_uq_toml(Path(path)):
            QMessageBox.warning(self, "Scenario",
                                "That is an uncertainty file: pick the propagate scenario it disperses.")
            return
        self._fill_scenario_combo(Path(path).resolve())
        self._on_scenario_changed()

    def _on_scenario_changed(self) -> None:
        """Re-read the scenario: object mode (spacecraft / debris) for the
        target lists, values for the table, grid for the snapshot hint."""
        p = self._scenario_path()
        self._scenario_data = {}
        info = ""
        if p is not None:
            try:
                self._scenario_data = tomli.loads(p.read_text(encoding="utf-8"))
            except (OSError, tomli.TOMLDecodeError) as exc:
                info = f"cannot read the scenario: {exc}"
        d = self._scenario_data
        if d:
            dur = _get_dotted(d, "simulation.duration_s")
            mode = _get_dotted(d, "output.mode")
            step = _get_dotted(d, "output.interval_s")
            parts = [f"{self._object_mode()}"]
            if isinstance(dur, (int, float)):
                parts.append(f"duration {_num(dur)} s")
            if mode == "fixed" and isinstance(step, (int, float)):
                parts.append(f"output every {_num(step)} s: snapshots must be multiples of it")
            elif mode is not None:
                parts.append(f"output.mode = \"{mode}\": the Monte Carlo needs \"fixed\"")
            info = ", ".join(parts)
        self._scenario_info.setText(info)
        targets = dispersible_targets(self._object_mode())
        for row in self._rows:
            cur = row["target"].currentText()
            row["target"].blockSignals(True)
            row["target"].clear()
            row["target"].addItems(targets + ([cur] if cur and cur not in targets else []))
            row["target"].setCurrentText(cur)
            row["target"].blockSignals(False)
            self._refresh_row(row)
        self._touch()

    # ------------------------------------------------------------------
    # State widgets
    # ------------------------------------------------------------------
    def _on_axes_changed(self, _i: int = 0) -> None:
        ric = self._axes.currentIndex() == 0
        for h, name in zip(self._sig_heads, ("R", "I", "C") if ric else ("x", "y", "z")):
            h.setText(name)
        self._corr.set_axes(ric)
        self._cov.set_axes(ric)
        self._touch()

    def _on_mode_changed(self, i: int) -> None:
        self._state_stack.setCurrentIndex(i)
        self._touch()

    def _on_corr_toggled(self, on: bool) -> None:
        self._corr.setVisible(on)
        self._touch()

    # ------------------------------------------------------------------
    # File state
    # ------------------------------------------------------------------
    def _touch(self, *_args) -> None:
        if self._loading:
            return
        if not self._modified:
            self._modified = True
            self._refresh_file_combo()
        self._badge.setText("")
        self._refresh_preview()

    def _refresh_preview(self) -> None:
        """What Save would write, live; the reasons it cannot yet,
        while the form is incomplete."""
        base = self._path.parent if self._path else (self._working_dir or Path.cwd())
        try:
            text = format_uq_toml(self._to_dict(base), self._header)
        except ValueError as exc:
            text = "# (cannot write the file yet:)\n" + "\n".join(
                f"#   {line}" for line in str(exc).splitlines())
        bar = self._preview.verticalScrollBar()
        pos = bar.value()
        self._preview.setPlainText(text)
        bar.setValue(pos)

    def is_modified(self) -> bool:
        return self._modified

    def current_path(self) -> Path | None:
        return self._path

    def set_working_dir(self, path: Path | None) -> None:
        self._working_dir = Path(path) if path is not None else None
        self._refresh_file_combo()
        self._fill_scenario_combo(self._scenario_path())

    def _refresh_file_combo(self) -> None:
        c = self._file_combo
        c.blockSignals(True)
        c.clear()
        c.addItem("-- pick an uncertainty file --", None)
        files = []
        if self._working_dir is not None and self._working_dir.is_dir():
            files = [p for p in find_toml_files(self._working_dir) if is_uq_toml(p)]
        if self._path is not None and self._path not in files:
            files.append(self._path)
        for p in files:
            label = p.name if p.parent == self._working_dir else f"{p.parent.name}/{p.name}"
            if p == self._path and self._modified:
                label += " *"
            c.addItem(label, str(p))
            c.setItemData(c.count() - 1, str(p), Qt.ItemDataRole.ToolTipRole)
            if p == self._path:
                c.setCurrentIndex(c.count() - 1)
        if self._path is None and self._modified:
            c.setItemText(0, "(new file, not saved) *")
        c.blockSignals(False)
        label = str(self._path) if self._path else "(unsaved)"
        self._path_label.setText(label + (" *" if self._modified else ""))
        self.stateChanged.emit()

    def _on_file_activated(self, idx: int) -> None:
        data = self._file_combo.itemData(idx)
        if not data or Path(data) == self._path:
            return
        if not self.maybe_save():
            self._refresh_file_combo()
            return
        self.load_path(Path(data))

    def maybe_save(self) -> bool:
        """Ask before throwing away edits. False = the user cancelled."""
        if not self._modified:
            return True
        resp = QMessageBox.question(
            self, "Unsaved changes",
            "The uncertainty file has unsaved edits. Save them before continuing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel)
        if resp == QMessageBox.StandardButton.Save:
            return self.action_save()
        return resp == QMessageBox.StandardButton.Discard

    # ------------------------------------------------------------------
    # dict <-> widgets
    # ------------------------------------------------------------------
    def _reset(self) -> None:
        self._loading = True
        self._path = None
        self._header = _NEW_HEADER
        self._name.setText("montecarlo")
        self._fill_scenario_combo(None)
        self._scenario_data = {}
        self._scenario_info.setText("")
        self._samples.setValue(500)
        self._seed.setText("1")
        self._threads.setValue(self._threads.maximum())
        self._output_dir.setText("")
        self._snapshots.setText("")
        self._case_outputs.setChecked(False)
        self._state_box.setChecked(True)
        self._axes.setCurrentIndex(0)
        self._mode.setCurrentIndex(0)
        for e in self._sig_edits:
            e.setText("0")
        self._use_corr.setChecked(False)
        self._corr.set_values(np.eye(6))
        self._cov.set_values(np.zeros((6, 6)))
        self._params.setRowCount(0)
        self._rows.clear()
        for w in self._pn.values():
            w["box"].setChecked(False)
            for e in w.values():
                for x in (e if isinstance(e, list) else [e]):
                    if isinstance(x, QLineEdit):
                        x.setText("")
        self._pn["density"]["vis"].setCurrentIndex(0)
        self._on_axes_changed()
        self._on_mode_changed(0)
        self._on_corr_toggled(False)
        self._loading = False
        self._modified = False
        self._refresh_file_combo()
        self._refresh_preview()

    def load_path(self, path: Path) -> bool:
        path = Path(path).resolve()
        try:
            raw = path.read_text(encoding="utf-8")
            mc = tomli.loads(raw).get("montecarlo")
        except (OSError, tomli.TOMLDecodeError) as exc:
            QMessageBox.critical(self, "Load uncertainty file", f"{path.name}: {exc}")
            return False
        if not isinstance(mc, dict):
            QMessageBox.critical(self, "Load uncertainty file",
                                 f"{path.name} has no [montecarlo] table.")
            return False
        self._reset()
        self._loading = True
        self._path = path
        self._header = uq_header_comment(raw)
        self._name.setText(str(mc.get("name", "")))
        scen = mc.get("scenario")
        self._fill_scenario_combo((path.parent / scen).resolve() if scen else None)
        if "samples" in mc:
            self._samples.setValue(int(mc["samples"]))
        if "seed" in mc:
            self._seed.setText(str(mc["seed"]))
        self._threads.setValue(int(mc.get("thread_number", 1)))
        self._output_dir.setText(str(mc.get("output_dir", "")))
        self._snapshots.setText(", ".join(_num(t) for t in mc.get("snapshots_s", [])))
        self._case_outputs.setChecked(bool(mc.get("case_outputs", False)))

        st = mc.get("initial_state")
        self._state_box.setChecked(isinstance(st, dict))
        if isinstance(st, dict):
            self._axes.setCurrentIndex(1 if st.get("axes") == "icrf" else 0)
            if "covariance" in st:
                self._mode.setCurrentIndex(1)
                self._cov.set_values(st["covariance"])
            else:
                vals = list(st.get("position_sigma_km", [0] * 3)) + list(st.get("velocity_sigma_kms", [0] * 3))
                for e, x in zip(self._sig_edits, vals):
                    e.setText(_num(x))
                if "correlation" in st:
                    self._use_corr.setChecked(True)
                    self._corr.set_values(st["correlation"])
        self._loading = False
        self._on_scenario_changed()        # object mode first: the rows' target lists
        self._loading = True
        for target, spec in (mc.get("parameters") or {}).items():
            self._add_row(target, spec if isinstance(spec, dict) else {})
        noise = mc.get("process_noise") or {}
        dens = noise.get("density")
        if isinstance(dens, dict):
            w = self._pn["density"]
            w["box"].setChecked(True)
            for key in ("sigma_ln", "tau_s", "interval_s", "ap_doubling"):
                if key in dens:
                    w[key].setText(_num(dens[key]))
            if dens.get("scenario_value_is") in ("mean", "median"):
                w["vis"].setCurrentText(dens["scenario_value_is"])
        for key, _title, _tip in _PN_RIC:
            spec = noise.get(key)
            if not isinstance(spec, dict):
                continue
            w = self._pn[key]
            w["box"].setChecked(True)
            for e, x in zip(w["sigma_m_s2"], spec.get("sigma_m_s2", [])):
                e.setText(_num(x))
            tau = spec.get("tau_s")
            for e, x in zip(w["tau_s"], tau if isinstance(tau, list) else ([tau] if tau is not None else [])):
                e.setText(_num(x))
            if "interval_s" in spec:
                w["interval_s"].setText(_num(spec["interval_s"]))
        self._loading = False
        self._modified = False
        self._refresh_file_combo()
        self._refresh_preview()
        self.fileLoadedOrSaved.emit(path)
        return True

    def _to_dict(self, save_dir: Path) -> dict:
        """The [montecarlo] table for a file saved in `save_dir`; raises
        ValueError with every problem found, one per line."""
        errors: list[str] = []

        def number(text: str, what: str) -> float:
            try:
                return float(text.strip())
            except ValueError:
                errors.append(f"{what}: '{text}' is not a number")
                return 0.0

        mc: dict = {"name": self._name.text().strip()}
        if not mc["name"]:
            errors.append("Name is empty")
        scen = self._scenario_path()
        if scen is None:
            errors.append("no scenario chosen")
        else:
            # Relative when the scenario sits in this folder or its parent
            # (the file then travels with its project), absolute otherwise.
            try:
                rel = os.path.relpath(scen, save_dir)
            except ValueError:               # another drive (Windows)
                rel = str(scen)
            if rel.replace("\\", "/").startswith("../.."):
                rel = str(scen)
            mc["scenario"] = rel.replace("\\", "/")
        mc["samples"] = self._samples.value()
        try:
            mc["seed"] = int(self._seed.text().strip())
            if mc["seed"] < 0:
                raise ValueError
        except ValueError:
            errors.append(f"Seed: '{self._seed.text()}' is not an integer >= 0")
        mc["thread_number"] = self._threads.value()
        if self._output_dir.text().strip():
            mc["output_dir"] = self._output_dir.text().strip().replace("\\", "/")
        snaps = [s for s in re.split(r"[,\s;]+", self._snapshots.text().strip()) if s]
        if snaps:
            mc["snapshots_s"] = [number(s, "Snapshots") for s in snaps]
        mc["case_outputs"] = self._case_outputs.isChecked()

        if self._state_box.isChecked():
            st: dict = {"axes": "ric" if self._axes.currentIndex() == 0 else "icrf"}
            try:
                if self._mode.currentIndex() == 1:
                    st["covariance"] = self._cov.values()
                else:
                    v = [number(e.text(), "Initial state σ") for e in self._sig_edits]
                    st["position_sigma_km"] = v[:3]
                    st["velocity_sigma_kms"] = v[3:]
                    if self._use_corr.isChecked():
                        st["correlation"] = self._corr.values()
            except ValueError as exc:
                errors.append(f"Initial state: {exc}")
            mc["initial_state"] = st

        params: dict = {}
        for row in self._rows:
            target = row["target"].currentText()
            if target in params:
                errors.append(f"{target} appears twice")
            dist = row["dist"].currentText()
            spec = {"distribution": dist,
                    row["kind"].currentData(): number(row["value"].text(), target)}
            if dist == "lognormal":
                vis = row["vis"].currentText()
                if vis not in ("mean", "median"):
                    errors.append(f"{target}: a lognormal needs 'Scenario value is' "
                                  "(mean or median)")
                spec["scenario_value_is"] = vis
            params[target] = spec
        if params:
            mc["parameters"] = params

        noise: dict = {}
        w = self._pn["density"]
        if w["box"].isChecked():
            spec = {k: number(w[k].text(), f"density {k}") for k in ("sigma_ln", "tau_s", "interval_s")}
            vis = w["vis"].currentText()
            if vis not in ("mean", "median"):
                errors.append("process noise density: choose scenario_value_is (mean or median)")
            spec["scenario_value_is"] = vis
            if w["ap_doubling"].text().strip():
                spec["ap_doubling"] = number(w["ap_doubling"].text(), "density ap_doubling")
            noise["density"] = spec
        for key, _title, _tip in _PN_RIC:
            w = self._pn[key]
            if not w["box"].isChecked():
                continue
            sig = [number(e.text() or "0", f"{key} sigma_m_s2") for e in w["sigma_m_s2"]]
            taus = [e.text().strip() for e in w["tau_s"]]
            if taus[0] and not taus[1] and not taus[2]:
                tau = number(taus[0], f"{key} tau_s")      # one value for the three axes
            else:
                tau = [number(t, f"{key} tau_s") for t in taus]
            noise[key] = {"sigma_m_s2": sig, "tau_s": tau,
                          "interval_s": number(w["interval_s"].text(), f"{key} interval_s")}
        if noise:
            mc["process_noise"] = noise
        if "initial_state" not in mc and not params and not noise:
            errors.append("nothing to disperse: turn on the initial state error, add a parameter "
                          "or a process noise")
        if errors:
            raise ValueError("\n".join(errors))
        return mc

    # ------------------------------------------------------------------
    # Run lifecycle (driven by MainWindow, which owns the runner)
    # ------------------------------------------------------------------
    def file_to_run(self) -> Path | None:
        """The saved file a run will read: unsaved edits are saved
        first (the engine reads the disk). None = the user cancelled."""
        if self._path is not None and not self._modified:
            return self._path
        resp = QMessageBox.question(
            self, "Save before running",
            "The engine runs the file on disk: save the uncertainty file now?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel)
        if resp != QMessageBox.StandardButton.Save or not self.action_save():
            return None
        return self._path

    def set_running(self, running: bool) -> None:
        self._samples_btn.setEnabled(not running)
        self._run_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)
        if running:
            self._open_btn.setEnabled(False)
            self._badge.setText("")

    def run_finished(self, exit_code: int, samples_only: bool,
                     run_folder: Path | None) -> None:
        """Badge with the outcome; on a full run, arm "Open results"
        with the run folder's moments file."""
        what = "samples drawn" if samples_only else "Monte Carlo done"
        if exit_code == 0:
            self._badge.setText(f"✓ {what}")
            self._badge.setStyleSheet("color: #1a7f37; font-weight: bold;")
        else:
            self._badge.setText(f"✗ exit {exit_code}: see the terminal")
            self._badge.setStyleSheet("color: #cf222e; font-weight: bold;")
        moments = (sorted(run_folder.glob("*_moments.uq.bin"))
                   if run_folder is not None and run_folder.is_dir() else [])
        self._results = moments[0] if (exit_code == 0 and moments) else None
        self._open_btn.setEnabled(self._results is not None)
        self._refresh_file_combo()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_new(self) -> None:
        if self.maybe_save():
            self._reset()

    def action_load(self) -> None:
        if not self.maybe_save():
            return
        start = str(self._path.parent if self._path else (self._working_dir or ""))
        path, _ = QFileDialog.getOpenFileName(self, "Open uncertainty file", start,
                                              f"Uncertainty files (*{UQ_SUFFIX})")
        if path:
            self.load_path(Path(path))

    def action_save(self) -> bool:
        # A file inside a run folder is the run's copy: never overwrite it.
        if self._path is None or _RUN_FOLDER_RE.fullmatch(self._path.parent.name):
            return self.action_save_as()
        return self._save_to(self._path)

    def action_save_as(self) -> bool:
        if self._path is not None and not _RUN_FOLDER_RE.fullmatch(self._path.parent.name):
            start = str(self._path)
        else:
            base = self._working_dir or Path.cwd()
            start = str(base / f"{self._name.text().strip() or 'montecarlo'}{UQ_SUFFIX}")
        path, _ = QFileDialog.getSaveFileName(self, "Save uncertainty file", start,
                                              f"Uncertainty files (*{UQ_SUFFIX})")
        if not path:
            return False
        p = Path(path)
        if not is_uq_toml(p):
            p = p.with_name(p.name[:-len(".toml")] if p.name.endswith(".toml") else p.name)
            p = p.with_name(p.name + UQ_SUFFIX)
        return self._save_to(p)

    def _save_to(self, path: Path) -> bool:
        path = path.resolve()
        try:
            mc = self._to_dict(path.parent)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot save", str(exc))
            return False
        try:
            path.write_text(format_uq_toml(mc, self._header), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Save uncertainty file", f"{path}: {exc}")
            return False
        self._path = path
        self._modified = False
        self._refresh_file_combo()
        self.fileLoadedOrSaved.emit(path)
        return True
