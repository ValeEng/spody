"""Form widget that replaces the raw TOML editor.

Layout: a vertical stack of `QGroupBox` (one per TOML section) inside
a `QScrollArea`. The user fills the form and clicks **Generate TOML**;
the form serialises its state into a plain dict and the
`toml_io.write_toml` emitter writes the canonical TOML to disk. The
inverse path (**Load TOML**) parses an existing file with `tomli` and
populates the widgets.

Each field is stored in `self._widgets` keyed by its full dotted
section path (e.g. `spacecraft.srp.area_m2`) so to_dict / load can
walk a flat structure and let the emitter handle nesting.

Slice 1 covers the always-present sections plus the spacecraft XOR
debris object switch and the optional `[spacecraft.srp]` sub-table.
`[events]` / `[batch]` are deferred to slice 2 -- their conditional
UIs (batch.columns has user-defined keys, events has the eclipse
threshold) are handled in their own pass.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDoubleValidator, QIntValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


# ----------------------------------------------------------------------
# Catalogue of "known" enum / list values offered as drop-down or
# checkbox sets. Kept here (rather than in the autocomplete schema)
# because the form's needs are slightly different -- e.g. third bodies
# get individual checkboxes, not a combo list. Adding a new central
# body / integrator is a one-line edit to these tuples.
# ----------------------------------------------------------------------
CENTRAL_BODIES   = ("Moon",)
FRAMES           = ("central_inertial",)
INTEGRATORS      = ("rkdp45",)
OUTPUT_MODES     = ("fixed", "step")
THIRD_BODIES_ALL = ("Sun", "Mercury", "Venus", "Earth", "Moon",
                    "Mars", "Jupiter", "Saturn", "Uranus", "Neptune")

# Valid override-target paths for [batch.columns]. Mirrors the
# FIELD_TABLE in src/toml_input.c. Each entry is (path, mode_tag):
# mode_tag is None for shared targets, "spacecraft" or "debris" when
# the path only makes sense under that object schema. The form filters
# by the current object selection.
BATCH_TARGETS: tuple[tuple[str, str | None], ...] = (
    ("simulation.et_start_s",          None),
    ("simulation.duration_s",          None),
    ("spacecraft.mass_kg",             "spacecraft"),
    ("spacecraft.srp.area_m2",         "spacecraft"),
    ("spacecraft.srp.Cr",              "spacecraft"),
    ("debris.am_srp",                  "debris"),
    ("debris.Cr",                      "debris"),
    ("initial_state.position_km[0]",   None),
    ("initial_state.position_km[1]",   None),
    ("initial_state.position_km[2]",   None),
    ("initial_state.velocity_kms[0]",  None),
    ("initial_state.velocity_kms[1]",  None),
    ("initial_state.velocity_kms[2]",  None),
    ("force_model.srp",                None),
    ("integrator.rel_tol",             None),
    ("integrator.h_init_s",            None),
    ("integrator.h_min_s",             None),
    ("integrator.h_max_s",             None),
    ("output.interval_s",              None),
)

# Sentinel string shown when a column has no target assigned. We use
# a leading "(" so it sorts above real path names alphabetically and
# is visually distinct from a real target.
_UNASSIGNED = "(unassigned)"


# A short label appended to certain QLineEdits to remind the user of
# the unit; not parsed, purely cosmetic.
_UNIT = {
    "et_start_s":  "s (TDB past J2000)",
    "duration_s":  "s",
    "mass_kg":     "kg",
    "am_srp":      "m²/kg",
    "area_m2":     "m²",
    "rel_tol":     "",
    "h_init_s":    "s",
    "h_min_s":     "s",
    "h_max_s":     "s",
    "interval_s":  "s",
    "position_km": "km",
    "velocity_kms":"km/s",
}


def _unit_suffix(key: str) -> str:
    u = _UNIT.get(key, "")
    return f"  [{u}]" if u else ""


class TomlForm(QWidget):
    """Replaces the textarea editor in the Run tab. Exposes a small
    API compatible with what `MainWindow` used on the previous editor:

      * `load_path(path)` / `load_from_dict(data)`
      * `to_dict()` / `write_to(path)`
      * `is_modified()` / `clear_modified()`
      * `current_path() -> Path | None`
      * Signal `modificationChanged(bool)`
      * Signal `requestRunCheck()` -- emitted when the user presses
        the bottom "Generate TOML" button; main window catches this
        to refresh the Analysis working dir.
    """

    modificationChanged = Signal(bool)
    requestRunCheck     = Signal()       # emitted after a successful Generate
    runRequested        = Signal(str)    # subcommand to run ("propagate" / "batch")

    # Top-level sections this form owns directly (it has widgets for
    # every supported key inside). Anything else loaded from a TOML
    # (e.g. [events], [batch] in slice 1) is stashed in `_passthrough`
    # and re-emitted verbatim on write, so loading a file that has
    # sections the form does not yet render does NOT lose them.
    _FORM_OWNED_TOP = {
        "simulation", "spacecraft", "debris",
        "initial_state", "force_model", "ephemeris",
        "integrator", "output",
        "events", "batch",
    }

    def __init__(self) -> None:
        super().__init__()
        self._widgets: dict[str, QWidget] = {}   # dotted path -> widget
        self._current_path: Path | None = None
        self._modified = False
        self._loading = False                    # suppress modified flag
        # Top-level sections from the last load that the form does not
        # manage. Carried through to_dict so Generate does not destroy
        # data the form was never shown.
        self._passthrough: dict[str, Any] = {}

        outer = QVBoxLayout(self)

        # Top row: current file + Load / Generate / RUN.
        # RUN is the prominent green action: it routes to propagate or
        # batch depending on whether the form currently has a [batch]
        # section. Validate stays accessible via the menu / Ctrl+T.
        self._path_label = QLabel("(no file)")
        self._path_label.setStyleSheet("color: gray;")
        btn_load = QPushButton("Load...")
        btn_gen  = QPushButton("Generate")
        btn_run  = QPushButton("RUN")
        btn_run.setStyleSheet(
            "QPushButton { background-color: #2ea043; color: white; "
            "font-weight: bold; padding: 4px 16px; border-radius: 3px; }"
            "QPushButton:hover  { background-color: #3fb950; }"
            "QPushButton:pressed{ background-color: #238636; }"
        )
        btn_load.clicked.connect(self._on_load_clicked)
        btn_gen.clicked.connect(self._on_generate_clicked)
        btn_run.clicked.connect(self._on_run_clicked)
        top_row = QHBoxLayout()
        top_row.addWidget(self._path_label, 1)
        top_row.addWidget(btn_load)
        top_row.addWidget(btn_gen)
        top_row.addWidget(btn_run)
        outer.addLayout(top_row)

        # Scrollable body holding all the section groups.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.addWidget(self._build_simulation())
        body_lay.addWidget(self._build_object())
        body_lay.addWidget(self._build_initial_state())
        body_lay.addWidget(self._build_force_model())
        body_lay.addWidget(self._build_ephemeris())
        body_lay.addWidget(self._build_integrator())
        body_lay.addWidget(self._build_output())
        body_lay.addWidget(self._build_events())
        body_lay.addWidget(self._build_batch())
        body_lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

    # ==================================================================
    # Section builders
    # ==================================================================
    def _build_simulation(self) -> QGroupBox:
        g = QGroupBox("[simulation]")
        f = QFormLayout(g)
        self._add_string(f, "simulation.name",       "name")
        self._add_float (f, "simulation.et_start_s", "et_start_s")
        self._add_float (f, "simulation.duration_s", "duration_s")
        return g

    def _build_object(self) -> QGroupBox:
        """Spacecraft XOR debris. A pair of radios at the top swaps
        which sub-section is visible; only the active sub-section's
        fields contribute to the emitted TOML."""
        g = QGroupBox("Object  --  [spacecraft] XOR [debris]")
        v = QVBoxLayout(g)

        self._radio_spc = QRadioButton("Spacecraft  (mass + area)")
        self._radio_dbr = QRadioButton("Debris      (A/m only)")
        self._radio_spc.setChecked(True)
        radio_row = QHBoxLayout()
        radio_row.addWidget(self._radio_spc)
        radio_row.addWidget(self._radio_dbr)
        radio_row.addStretch(1)
        v.addLayout(radio_row)

        # ---- Spacecraft box ----------------------------------------
        self._spc_box = QWidget()
        spc_form = QFormLayout(self._spc_box)
        self._add_float(spc_form, "spacecraft.mass_kg", "mass_kg")

        # Optional [spacecraft.srp] sub-block, gated by a checkbox.
        self._srp_check = QCheckBox("Enable [spacecraft.srp]")
        self._srp_check.toggled.connect(self._on_srp_toggled)
        self._srp_check.toggled.connect(lambda _: self._touch())
        spc_form.addRow("", self._srp_check)

        self._srp_box = QWidget()
        srp_form = QFormLayout(self._srp_box)

        # Inside [spacecraft.srp], area_m2 XOR am_srp.
        self._srp_radio_area = QRadioButton("area_m2 (derive A/m)")
        self._srp_radio_am   = QRadioButton("am_srp (direct A/m)")
        self._srp_radio_area.setChecked(True)
        srp_radio_row = QHBoxLayout()
        srp_radio_row.addWidget(self._srp_radio_area)
        srp_radio_row.addWidget(self._srp_radio_am)
        srp_radio_row.addStretch(1)
        srp_form.addRow("Parameter", _hwrap(srp_radio_row))

        self._add_float(srp_form, "spacecraft.srp.area_m2", "area_m2")
        self._add_float(srp_form, "spacecraft.srp.am_srp",  "am_srp")
        self._add_float(srp_form, "spacecraft.srp.Cr",      "Cr")
        self._srp_radio_area.toggled.connect(self._on_srp_param_toggled)
        self._srp_radio_area.toggled.connect(lambda _: self._touch())
        spc_form.addRow(self._srp_box)
        self._srp_box.setVisible(False)
        self._on_srp_param_toggled()

        v.addWidget(self._spc_box)

        # ---- Debris box --------------------------------------------
        self._dbr_box = QWidget()
        dbr_form = QFormLayout(self._dbr_box)
        self._add_float(dbr_form, "debris.am_srp", "am_srp")
        self._add_float(dbr_form, "debris.Cr",     "Cr")
        v.addWidget(self._dbr_box)
        self._dbr_box.setVisible(False)

        self._radio_spc.toggled.connect(self._on_object_radio_toggled)
        self._radio_spc.toggled.connect(lambda _: self._touch())
        return g

    def _build_initial_state(self) -> QGroupBox:
        g = QGroupBox("[initial_state]")
        f = QFormLayout(g)
        self._add_enum(f, "initial_state.frame", "frame", FRAMES)
        self._add_vec3(f, "initial_state.position_km",  "position_km")
        self._add_vec3(f, "initial_state.velocity_kms", "velocity_kms")
        return g

    def _build_force_model(self) -> QGroupBox:
        g = QGroupBox("[force_model]")
        f = QFormLayout(g)
        self._add_enum (f, "force_model.central_body",     "central_body", CENTRAL_BODIES)
        self._add_path (f, "force_model.harmonics_file",   "harmonics_file",
                        "Harmonics (*.tab *.cof *.txt);;All files (*)")
        self._add_int  (f, "force_model.harmonics_degree", "harmonics_degree",
                        minimum=2, maximum=1200)
        self._add_strlist_checks(f, "force_model.third_bodies", "third_bodies",
                                 THIRD_BODIES_ALL)
        self._add_bool (f, "force_model.srp", "srp")
        return g

    def _build_ephemeris(self) -> QGroupBox:
        g = QGroupBox("[ephemeris]")
        f = QFormLayout(g)
        self._add_path(f, "ephemeris.file", "file",
                       "Ephemeris (*.spody *.bsp);;All files (*)")
        return g

    def _build_integrator(self) -> QGroupBox:
        g = QGroupBox("[integrator]")
        f = QFormLayout(g)
        self._add_enum (f, "integrator.type",     "type", INTEGRATORS)
        self._add_float(f, "integrator.rel_tol",  "rel_tol")
        self._add_float(f, "integrator.h_init_s", "h_init_s")
        self._add_float(f, "integrator.h_min_s",  "h_min_s")
        self._add_float(f, "integrator.h_max_s",  "h_max_s")
        return g

    def _build_output(self) -> QGroupBox:
        g = QGroupBox("[output]")
        f = QFormLayout(g)
        self._add_enum (f, "output.mode",               "mode", OUTPUT_MODES)
        self._add_float(f, "output.interval_s",         "interval_s")
        # Path fields below are optional -- empty string means the
        # corresponding output stream is not emitted by spody.
        self._add_path (f, "output.csv_file",           "csv_file",
                        "CSV (*.csv);;All files (*)", save=True)
        self._add_path (f, "output.bin_file",           "bin_file",
                        "Binary (*.bin);;All files (*)", save=True)
        self._add_path (f, "output.log_file",           "log_file",
                        "Log (*.log *.txt);;All files (*)", save=True)
        self._add_path (f, "output.accelerations_file", "accelerations_file",
                        "Binary (*.bin);;All files (*)", save=True)
        self._add_path (f, "output.events_log",         "events_log",
                        "Binary (*.bin);;All files (*)", save=True)
        return g

    def _build_events(self) -> QGroupBox:
        g = QGroupBox("[events]  (optional)")
        v = QVBoxLayout(g)

        self._events_check = QCheckBox("Enable [events]  (eclipse detection)")
        self._events_check.toggled.connect(self._on_events_toggled)
        self._events_check.toggled.connect(lambda _: self._touch())
        v.addWidget(self._events_check)

        self._events_box = QWidget()
        f = QFormLayout(self._events_box)
        self._add_float(f, "events.eclipse_threshold", "eclipse_threshold (0..1)")
        v.addWidget(self._events_box)
        self._events_box.setVisible(False)
        return g

    def _build_batch(self) -> QGroupBox:
        """Batch section + a CSV-aware [batch.columns] mapping table.

        The columns table is populated from the cases_file's CSV
        header (excluding the optional `id` column); each row gets a
        target dropdown (filtered by the current object schema) and a
        mode dropdown (override / delta). Heuristic pre-matching
        picks the obvious target if the column name matches the last
        segment of a known path (e.g. `mass_kg` -> `spacecraft.mass_kg`)."""
        g = QGroupBox("[batch]  (optional)")
        v = QVBoxLayout(g)

        self._batch_check = QCheckBox("Enable [batch]  (multi-case sweep)")
        self._batch_check.toggled.connect(self._on_batch_toggled)
        self._batch_check.toggled.connect(lambda _: self._touch())
        v.addWidget(self._batch_check)

        self._batch_box = QWidget()
        f = QFormLayout(self._batch_box)
        self._add_string(f, "batch.name",          "name")
        self._add_path  (f, "batch.output_dir",    "output_dir", "",
                         pick_dir=True)
        # os.cpu_count() can return None on exotic systems; default to 1
        # in that case so the hint is always something meaningful.
        cpu_n = os.cpu_count() or 1
        self._add_int   (f, "batch.thread_number", "thread_number",
                         minimum=1, maximum=64,
                         hint=f"({cpu_n} cores available)")

        # cases_file with a Browse that ALSO re-reads the CSV columns
        # immediately (and a separate Re-read button for manual edits
        # to the path).
        self._batch_cases_edit = QLineEdit()
        self._batch_cases_edit.textChanged.connect(self._touch)
        cases_browse = QPushButton("Browse...")
        cases_browse.clicked.connect(self._on_browse_cases_file)
        cases_reread = QPushButton("Re-read columns")
        cases_reread.clicked.connect(self._refresh_batch_columns)
        cases_row = QHBoxLayout()
        cases_row.setContentsMargins(0, 0, 0, 0)
        cases_row.addWidget(self._batch_cases_edit, 1)
        cases_row.addWidget(cases_browse)
        cases_row.addWidget(cases_reread)
        f.addRow("cases_file", _hwrap(cases_row))
        self._widgets["batch.cases_file"] = self._batch_cases_edit

        # Status line just under the cases_file row -- tells the user
        # whether the CSV was readable and how many columns came back.
        self._batch_cases_status = QLabel("")
        self._batch_cases_status.setStyleSheet("color: gray;")
        f.addRow("", self._batch_cases_status)

        # The column-mapping table.
        self._batch_columns_table = QTableWidget(0, 3)
        self._batch_columns_table.setHorizontalHeaderLabels(
            ["CSV column", "Target", "Mode"])
        self._batch_columns_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._batch_columns_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._batch_columns_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self._batch_columns_table.verticalHeader().setVisible(False)
        self._batch_columns_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._batch_columns_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._batch_columns_table.setMinimumHeight(120)
        f.addRow(QLabel("Column mapping  ([batch.columns]):"))
        f.addRow(self._batch_columns_table)

        v.addWidget(self._batch_box)
        self._batch_box.setVisible(False)
        return g

    # ==================================================================
    # Widget factories. Each one creates a widget, registers it under
    # the dotted key, wires the appropriate change signal to `_touch`,
    # and adds it to the given QFormLayout.
    # ==================================================================
    def _add_string(self, layout: QFormLayout, key: str, label: str) -> None:
        w = QLineEdit()
        w.textChanged.connect(self._touch)
        self._widgets[key] = w
        layout.addRow(label + _unit_suffix(key.split(".")[-1]), w)

    def _add_float(self, layout: QFormLayout, key: str, label: str) -> None:
        w = QLineEdit()
        # QDoubleValidator with a huge range accepts scientific notation;
        # we re-validate at to_dict time with float() to surface bad input.
        val = QDoubleValidator(-1.0e30, 1.0e30, 15, w)
        val.setNotation(QDoubleValidator.Notation.ScientificNotation)
        w.setValidator(val)
        w.textChanged.connect(self._touch)
        self._widgets[key] = w
        layout.addRow(label + _unit_suffix(key.split(".")[-1]), w)

    def _add_int(self, layout: QFormLayout, key: str, label: str,
                 minimum: int = -2**31, maximum: int = 2**31 - 1,
                 hint: str = "") -> None:
        w = QLineEdit()
        w.setValidator(QIntValidator(minimum, maximum, w))
        w.textChanged.connect(self._touch)
        self._widgets[key] = w
        full_label = label + _unit_suffix(key.split(".")[-1])
        if hint:
            # Inline grey note next to the field (e.g. "(8 cores available)").
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(w, 1)
            hint_label = QLabel(hint)
            hint_label.setStyleSheet("color: gray;")
            row.addWidget(hint_label)
            layout.addRow(full_label, _hwrap(row))
        else:
            layout.addRow(full_label, w)

    def _add_bool(self, layout: QFormLayout, key: str, label: str) -> None:
        w = QCheckBox()
        w.toggled.connect(self._touch)
        self._widgets[key] = w
        layout.addRow(label, w)

    def _add_enum(self, layout: QFormLayout, key: str, label: str,
                  values: tuple[str, ...]) -> None:
        w = QComboBox()
        for v in values:
            w.addItem(v)
        w.currentIndexChanged.connect(self._touch)
        self._widgets[key] = w
        layout.addRow(label, w)

    def _add_path(self, layout: QFormLayout, key: str, label: str,
                  filter_str: str, save: bool = False,
                  pick_dir: bool = False) -> None:
        edit = QLineEdit()
        edit.textChanged.connect(self._touch)
        btn = QPushButton("Browse...")
        def _browse() -> None:
            start = edit.text() or ""
            if pick_dir:
                path = QFileDialog.getExistingDirectory(
                    self, f"Choose {key}", start)
            elif save:
                path, _ = QFileDialog.getSaveFileName(
                    self, f"Choose {key}", start, filter_str)
            else:
                path, _ = QFileDialog.getOpenFileName(
                    self, f"Locate {key}", start, filter_str)
            if path:
                edit.setText(path)
        btn.clicked.connect(_browse)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        row.addWidget(btn)
        self._widgets[key] = edit
        layout.addRow(label, _hwrap(row))

    def _add_vec3(self, layout: QFormLayout, key: str, label: str) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        fields: list[QLineEdit] = []
        for _ in range(3):
            le = QLineEdit()
            val = QDoubleValidator(-1.0e30, 1.0e30, 15, le)
            val.setNotation(QDoubleValidator.Notation.ScientificNotation)
            le.setValidator(val)
            le.textChanged.connect(self._touch)
            row.addWidget(le, 1)
            fields.append(le)
        # Store as a tuple of the three line edits -- to_dict / load
        # detect this via isinstance.
        self._widgets[key] = tuple(fields)
        layout.addRow(label + _unit_suffix(key.split(".")[-1]), _hwrap(row))

    def _add_strlist_checks(self, layout: QFormLayout, key: str, label: str,
                            known: tuple[str, ...]) -> None:
        """One QCheckBox per known string value; the emitted list
        contains only those checked. Suitable for `third_bodies`."""
        boxes: dict[str, QCheckBox] = {}
        # Lay them out in two columns so 10 third-body options don't
        # take up a vertical wall.
        grid = QVBoxLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        cols = 2
        row_layouts: list[QHBoxLayout] = []
        for i, name in enumerate(known):
            if i % cols == 0:
                rl = QHBoxLayout()
                rl.setContentsMargins(0, 0, 0, 0)
                grid.addLayout(rl)
                row_layouts.append(rl)
            cb = QCheckBox(name)
            cb.toggled.connect(self._touch)
            boxes[name] = cb
            row_layouts[-1].addWidget(cb)
        # Pad the last partial row.
        if len(known) % cols:
            row_layouts[-1].addStretch(1)
        self._widgets[key] = boxes
        layout.addRow(label, _hwrap_v(grid))

    # ==================================================================
    # Conditional visibility (XOR groups)
    # ==================================================================
    def _on_object_radio_toggled(self, _checked: bool) -> None:
        on_spc = self._radio_spc.isChecked()
        self._spc_box.setVisible(on_spc)
        self._dbr_box.setVisible(not on_spc)
        # Batch column targets depend on the object schema; refresh
        # the per-row combos to drop now-invalid options.
        self._refresh_batch_column_target_combos()

    def _on_events_toggled(self, checked: bool) -> None:
        self._events_box.setVisible(checked)

    def _on_batch_toggled(self, checked: bool) -> None:
        self._batch_box.setVisible(checked)

    # ------------------------------------------------------------------
    # [batch.columns] helpers
    # ------------------------------------------------------------------
    def _available_batch_targets(self) -> list[str]:
        """Override-target paths valid under the current object mode."""
        mode = "spacecraft" if self._radio_spc.isChecked() else "debris"
        return [p for p, tag in BATCH_TARGETS if tag is None or tag == mode]

    def _on_browse_cases_file(self) -> None:
        start = self._batch_cases_edit.text() or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate cases CSV", start,
            "Cases (*.csv);;All files (*)")
        if path:
            self._batch_cases_edit.setText(path)
            self._refresh_batch_columns()

    def _refresh_batch_columns(self) -> None:
        """(Re-)read the CSV header at cases_file and rebuild the
        [batch.columns] table. Existing target/mode assignments are
        preserved across re-reads when the column name reappears."""
        path_str = self._batch_cases_edit.text().strip()
        if not path_str:
            self._batch_cases_status.setText("")
            self._batch_columns_table.setRowCount(0)
            return

        p = Path(path_str)
        if not p.is_absolute() and self._current_path is not None:
            # Same resolution rule spody uses internally: paths in the
            # TOML are relative to the TOML file's directory.
            p = self._current_path.parent / p

        if not p.is_file():
            self._batch_cases_status.setText(f"(not found: {p})")
            self._batch_columns_table.setRowCount(0)
            return

        try:
            columns = _read_csv_header(p)
        except OSError as exc:
            self._batch_cases_status.setText(f"(read failed: {exc})")
            self._batch_columns_table.setRowCount(0)
            return

        # Drop the special `id` column (used for case naming, not a
        # spody override target).
        columns = [c for c in columns if c.lower() != "id"]
        existing = self._snapshot_batch_columns()

        was_loading = self._loading
        self._loading = True
        try:
            self._batch_columns_table.setRowCount(0)
            for col in columns:
                self._add_batch_column_row(col, existing.get(col))
        finally:
            self._loading = was_loading

        self._batch_cases_status.setText(
            f"({len(columns)} non-id columns read from {p.name})")

    def _add_batch_column_row(self, col_name: str,
                               existing: tuple[str, str] | None = None) -> None:
        """Append one row to the column-mapping table. `existing` is a
        previous `(target, mode)` selection that survived a reload."""
        row = self._batch_columns_table.rowCount()
        self._batch_columns_table.insertRow(row)

        name_item = QTableWidgetItem(col_name)
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._batch_columns_table.setItem(row, 0, name_item)

        target_combo = QComboBox()
        target_combo.addItem(_UNASSIGNED)
        for path in self._available_batch_targets():
            target_combo.addItem(path)
        target_combo.currentIndexChanged.connect(self._touch)
        self._batch_columns_table.setCellWidget(row, 1, target_combo)

        mode_combo = QComboBox()
        mode_combo.addItems(["override", "delta"])
        mode_combo.currentIndexChanged.connect(self._touch)
        self._batch_columns_table.setCellWidget(row, 2, mode_combo)

        # Restore the previous selection if one survived a re-read;
        # otherwise try the obvious-name heuristic for a fresh row.
        if existing is not None:
            tgt, mode = existing
            idx = target_combo.findText(tgt)
            if idx >= 0:
                target_combo.setCurrentIndex(idx)
            mode_combo.setCurrentText(mode)
        else:
            tgt = _heuristic_target(col_name, self._available_batch_targets())
            if tgt is not None:
                target_combo.setCurrentText(tgt)

    def _snapshot_batch_columns(self) -> dict[str, tuple[str, str]]:
        """Capture the current (target, mode) per column. Used so a
        Re-read columns press doesn't blow away the user's work."""
        out: dict[str, tuple[str, str]] = {}
        for row in range(self._batch_columns_table.rowCount()):
            item = self._batch_columns_table.item(row, 0)
            if item is None:
                continue
            target = self._batch_columns_table.cellWidget(row, 1).currentText()
            mode   = self._batch_columns_table.cellWidget(row, 2).currentText()
            out[item.text()] = (target, mode)
        return out

    def _refresh_batch_column_target_combos(self) -> None:
        """Rebuild each row's Target combo for a new object mode,
        preserving any selection that survives the mode change."""
        if not hasattr(self, "_batch_columns_table"):
            return   # called too early during __init__
        new_targets = self._available_batch_targets()
        for row in range(self._batch_columns_table.rowCount()):
            combo = self._batch_columns_table.cellWidget(row, 1)
            if combo is None:
                continue
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(_UNASSIGNED)
            for t in new_targets:
                combo.addItem(t)
            idx = combo.findText(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _batch_columns_to_dict(self) -> dict[str, Any]:
        """Serialise the column-mapping table to a TOML-ready dict for
        emission inside `[batch.columns]`. Unassigned rows are
        silently dropped -- spody will error at validate time."""
        out: dict[str, Any] = {}
        for row in range(self._batch_columns_table.rowCount()):
            item = self._batch_columns_table.item(row, 0)
            if item is None:
                continue
            target = self._batch_columns_table.cellWidget(row, 1).currentText()
            mode   = self._batch_columns_table.cellWidget(row, 2).currentText()
            if target == _UNASSIGNED:
                continue
            if mode == "delta":
                out[item.text()] = {"target": target, "mode": "delta"}
            else:
                out[item.text()] = target
        return out

    def _apply_loaded_batch_columns(self, cols_data: dict[str, Any]) -> None:
        """After a TOML load, push the loaded `[batch.columns]` entries
        onto matching rows of the column-mapping table. Rows whose
        column name has no entry in the TOML keep their heuristic /
        unassigned default."""
        for row in range(self._batch_columns_table.rowCount()):
            item = self._batch_columns_table.item(row, 0)
            if item is None:
                continue
            name = item.text()
            entry = cols_data.get(name)
            if entry is None:
                continue
            if isinstance(entry, dict):
                target = entry.get("target", "")
                mode   = entry.get("mode", "override")
            else:
                target = str(entry)
                mode   = "override"
            target_combo = self._batch_columns_table.cellWidget(row, 1)
            mode_combo   = self._batch_columns_table.cellWidget(row, 2)
            idx = target_combo.findText(target)
            if idx >= 0:
                target_combo.setCurrentIndex(idx)
            mode_combo.setCurrentText(mode)

    def _on_srp_toggled(self, checked: bool) -> None:
        self._srp_box.setVisible(checked)

    def _on_srp_param_toggled(self) -> None:
        """Greys out whichever of area_m2 / am_srp is not the selected
        parameter -- so the user can't accidentally fill both and hit
        the XOR error from the parser."""
        use_area = self._srp_radio_area.isChecked()
        self._widgets["spacecraft.srp.area_m2"].setEnabled(use_area)
        self._widgets["spacecraft.srp.am_srp"].setEnabled(not use_area)

    # ==================================================================
    # Modification tracking
    # ==================================================================
    def _touch(self) -> None:
        if self._loading:
            return
        if not self._modified:
            self._modified = True
            self.modificationChanged.emit(True)

    def is_modified(self) -> bool:
        return self._modified

    def clear_modified(self) -> None:
        if self._modified:
            self._modified = False
            self.modificationChanged.emit(False)

    def current_path(self) -> Path | None:
        return self._current_path

    def set_current_path(self, path: Path | None) -> None:
        self._current_path = path
        self._path_label.setText(str(path) if path else "(no file)")
        self._path_label.setStyleSheet("" if path else "color: gray;")

    # ==================================================================
    # Round-trip: dict <-> widgets
    # ==================================================================
    def to_dict(self) -> dict[str, Any]:
        """Build the TOML-ready dict from the current widget values.
        Empty / disabled fields are omitted so the emitter does not
        write blank entries."""
        flat: dict[str, Any] = {}
        for key, w in self._widgets.items():
            v = self._widget_value(key, w)
            if v is None:
                continue
            flat[key] = v

        # Apply object XOR by stripping the inactive branch (so even if
        # both have stale data the emitted TOML is consistent).
        if self._radio_spc.isChecked():
            flat = {k: v for k, v in flat.items() if not k.startswith("debris.")}
            if not self._srp_check.isChecked():
                flat = {k: v for k, v in flat.items() if not k.startswith("spacecraft.srp.")}
            else:
                # XOR inside [spacecraft.srp]: drop whichever param is unselected.
                if self._srp_radio_area.isChecked():
                    flat.pop("spacecraft.srp.am_srp", None)
                else:
                    flat.pop("spacecraft.srp.area_m2", None)
        else:
            flat = {k: v for k, v in flat.items()
                    if not (k.startswith("spacecraft.") or k == "spacecraft.mass_kg")}

        # Optional sections: drop their fields entirely when the gating
        # checkbox is off so an unchecked block isn't emitted half-filled.
        if not self._events_check.isChecked():
            flat = {k: v for k, v in flat.items() if not k.startswith("events.")}
        if not self._batch_check.isChecked():
            flat = {k: v for k, v in flat.items() if not k.startswith("batch.")}

        result = _explode_dotted(flat)

        # [batch.columns] comes from the dynamic table, not from a flat
        # widget key; inject it only when batch is enabled and at least
        # one column has a target assigned.
        if self._batch_check.isChecked():
            cols = self._batch_columns_to_dict()
            if cols:
                result.setdefault("batch", {})["columns"] = cols

        # Pass-through for any top-level section we don't render at all.
        for k, v in self._passthrough.items():
            result.setdefault(k, v)
        return result

    def load_from_dict(self, data: dict[str, Any]) -> None:
        """Inverse of `to_dict`: takes a tomli-parsed dict and pushes
        the values back into the widgets, suppressing the modified
        flag while loading. Sections the form doesn't render are
        stashed in `_passthrough` so the next Generate preserves them."""
        self._passthrough = {
            k: v for k, v in data.items()
            if k not in self._FORM_OWNED_TOP and isinstance(v, dict)
        }
        self._loading = True
        try:
            self._reset_widgets()
            flat = _flatten_dotted(
                {k: v for k, v in data.items() if k in self._FORM_OWNED_TOP}
            )

            # Decide object mode FIRST so the XOR visibility is right
            # before fields populate.
            if "debris.am_srp" in flat:
                self._radio_dbr.setChecked(True)
            else:
                self._radio_spc.setChecked(True)
            self._on_object_radio_toggled(True)

            # SRP gate.
            has_srp = any(k.startswith("spacecraft.srp.") for k in flat)
            self._srp_check.setChecked(has_srp)
            self._on_srp_toggled(has_srp)
            if has_srp:
                # XOR area_m2 / am_srp.
                if "spacecraft.srp.am_srp" in flat:
                    self._srp_radio_am.setChecked(True)
                else:
                    self._srp_radio_area.setChecked(True)
                self._on_srp_param_toggled()

            # Optional-block gates: events, batch.
            has_events = any(k.startswith("events.") for k in flat)
            self._events_check.setChecked(has_events)
            self._on_events_toggled(has_events)

            has_batch = any(k.startswith("batch.") for k in flat)
            self._batch_check.setChecked(has_batch)
            self._on_batch_toggled(has_batch)

            # Now push field values.
            for key, value in flat.items():
                w = self._widgets.get(key)
                if w is None:
                    continue   # unknown key; emitter would round-trip via flatten too
                self._set_widget_value(key, w, value)

            # [batch.columns] is dynamic: first scan the freshly-loaded
            # cases_file to populate the rows, then apply the loaded
            # column->target mappings on top.
            if has_batch:
                self._refresh_batch_columns()
                cols_data = data.get("batch", {}).get("columns", {})
                if isinstance(cols_data, dict):
                    self._apply_loaded_batch_columns(cols_data)
        finally:
            self._loading = False
        self.clear_modified()

    def load_path(self, path: Path) -> bool:
        """Read a TOML from disk via tomli and populate the form."""
        from .toml_io import read_toml
        try:
            data = read_toml(path)
        except (OSError, Exception) as exc:
            QMessageBox.critical(self, "Load failed", f"{path}\n{exc}")
            return False
        # Set the current path FIRST so load_from_dict can resolve any
        # relative paths inside the TOML (notably batch.cases_file)
        # against the right base directory, matching what spody does.
        self.set_current_path(path)
        self.load_from_dict(data)
        return True

    def reset_to_blank(self) -> None:
        """Clear every field, restore the default XOR selections, drop
        the current path AND any pass-through sections from a previous
        load. Used by File > New."""
        self._passthrough = {}
        self._loading = True
        try:
            self._reset_widgets()
            self._radio_spc.setChecked(True)
            self._on_object_radio_toggled(True)
            self._srp_check.setChecked(False)
            self._on_srp_toggled(False)
            self._srp_radio_area.setChecked(True)
            self._on_srp_param_toggled()
            self._events_check.setChecked(False)
            self._on_events_toggled(False)
            self._batch_check.setChecked(False)
            self._on_batch_toggled(False)
            self._batch_columns_table.setRowCount(0)
            self._batch_cases_status.setText("")
        finally:
            self._loading = False
        self.set_current_path(None)
        self.clear_modified()

    def write_to(self, path: Path) -> bool:
        """Serialise the form via to_dict + write_toml. Returns True
        on success; surfaces I/O / value errors via a message box."""
        from .toml_io import write_toml
        try:
            data = self.to_dict()
            write_toml(path, data)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Generate failed", f"{path}\n{exc}")
            return False
        self.set_current_path(path)
        self.clear_modified()
        return True

    # ==================================================================
    # Bottom-bar handlers
    # ==================================================================
    def _on_load_clicked(self) -> None:
        start = str(self._current_path.parent) if self._current_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load TOML", start, "TOML files (*.toml);;All files (*)")
        if path:
            self.load_path(Path(path))

    def _on_generate_clicked(self) -> None:
        path = self._current_path
        if path is None:
            start = ""
            path, _ = QFileDialog.getSaveFileName(
                self, "Generate TOML", start,
                "TOML files (*.toml);;All files (*)")
            if not path:
                return
            path = Path(path)
        if self.write_to(path):
            self.requestRunCheck.emit()

    def _on_run_clicked(self) -> None:
        """Pick the right spody subcommand based on the form contents
        and ask MainWindow to launch it (save-before-run logic stays
        in MainWindow so this button shares it with the menu actions)."""
        subcommand = "batch" if "batch" in self.to_dict() else "propagate"
        self.runRequested.emit(subcommand)

    # ==================================================================
    # Internals
    # ==================================================================
    def _reset_widgets(self) -> None:
        """Restore every widget to a sensible blank state so a fresh
        load doesn't leave fields from the previous file behind."""
        for key, w in self._widgets.items():
            if isinstance(w, QLineEdit):
                w.clear()
            elif isinstance(w, QCheckBox):
                w.setChecked(False)
            elif isinstance(w, QComboBox):
                w.setCurrentIndex(0)
            elif isinstance(w, tuple):   # vec3
                for le in w:
                    le.clear()
            elif isinstance(w, dict):    # checkbox set
                for cb in w.values():
                    cb.setChecked(False)

    def _widget_value(self, key: str, w: Any) -> Any:
        if isinstance(w, QLineEdit):
            text = w.text().strip()
            if not text:
                return None
            # Floats / ints get coerced via the registered validator,
            # but we also try here so to_dict surfaces bad input.
            v = w.validator()
            if isinstance(v, QDoubleValidator):
                try:    return float(text)
                except ValueError:
                    raise ValueError(f"'{key}' is not a valid number: {text!r}")
            if isinstance(v, QIntValidator):
                try:    return int(text)
                except ValueError:
                    raise ValueError(f"'{key}' is not a valid integer: {text!r}")
            return text
        if isinstance(w, QCheckBox):
            return bool(w.isChecked())
        if isinstance(w, QComboBox):
            return w.currentText()
        if isinstance(w, tuple):   # vec3
            vals: list[float] = []
            for i, le in enumerate(w):
                t = le.text().strip()
                if not t:
                    return None
                try:    vals.append(float(t))
                except ValueError:
                    raise ValueError(
                        f"'{key}'[{i}] is not a valid number: {t!r}")
            return vals
        if isinstance(w, dict):    # checkbox set -> list of strings
            chosen = [name for name, cb in w.items() if cb.isChecked()]
            return chosen or None
        return None

    def _set_widget_value(self, key: str, w: Any, value: Any) -> None:
        if isinstance(w, QLineEdit):
            if isinstance(value, float):
                w.setText(repr(value))
            elif isinstance(value, int):
                w.setText(str(value))
            else:
                w.setText("" if value is None else str(value))
            return
        if isinstance(w, QCheckBox):
            w.setChecked(bool(value))
            return
        if isinstance(w, QComboBox):
            idx = w.findText(str(value))
            if idx >= 0:
                w.setCurrentIndex(idx)
            return
        if isinstance(w, tuple):   # vec3
            if isinstance(value, (list, tuple)) and len(value) == 3:
                for le, x in zip(w, value):
                    le.setText(repr(float(x)))
            return
        if isinstance(w, dict):    # checkbox set
            wanted = set(value) if isinstance(value, (list, tuple)) else set()
            for name, cb in w.items():
                cb.setChecked(name in wanted)
            return


# ----------------------------------------------------------------------
# Module helpers
# ----------------------------------------------------------------------
def _hwrap(layout: QHBoxLayout) -> QWidget:
    """Wrap a layout in a transparent QWidget so QFormLayout.addRow
    accepts it as the field cell."""
    w = QWidget()
    w.setLayout(layout)
    return w


def _hwrap_v(layout: QVBoxLayout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w


def _explode_dotted(flat: dict[str, Any]) -> dict[str, Any]:
    """Turn `{"spacecraft.srp.area_m2": 1.0}` into
    `{"spacecraft": {"srp": {"area_m2": 1.0}}}`. Used by `to_dict` so
    the emitter receives the same nested shape `tomli.load` produces."""
    out: dict[str, Any] = {}
    for key, val in flat.items():
        parts = key.split(".")
        cur = out
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val
    return out


def _flatten_dotted(nested: dict[str, Any]) -> dict[str, Any]:
    """Inverse of `_explode_dotted` -- used by `load_from_dict` so we
    can address widgets by the same dotted keys the form uses."""
    out: dict[str, Any] = {}
    def walk(prefix: str, d: dict[str, Any]) -> None:
        for k, v in d.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict) and not _is_inline_table(v):
                walk(path, v)
            else:
                out[path] = v
    walk("", nested)
    return out


def _is_inline_table(d: dict[str, Any]) -> bool:
    """Same heuristic the emitter uses: an inline table value (kept as
    a leaf) has the `target` key. spody only emits this inside
    `[batch.columns]`."""
    return "target" in d


def _read_csv_header(path: Path) -> list[str]:
    """First non-comment, non-blank line of the CSV, split on commas.
    Matches spody's own loose CSV reader: leading `#` lines are
    treated as comments, fields are trimmed."""
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            return [c.strip() for c in stripped.split(",")]
    return []


def _heuristic_target(col_name: str, available: list[str]) -> str | None:
    """Pre-match the column name to an available target when the last
    segment matches exactly. e.g. CSV column `mass_kg` ->
    `spacecraft.mass_kg`; `Cr` -> first match between `spacecraft.srp.Cr`
    and `debris.Cr` (filtered by mode at the caller). Returns None if
    nothing matches, leaving the row as (unassigned)."""
    for p in available:
        # Drop any [i] index suffix when comparing -- so a column
        # called `position_km` matches `position_km[0]` loosely. We
        # only use this as a hint; the user can always override.
        last = p.rsplit(".", 1)[-1].split("[", 1)[0]
        if last == col_name:
            return p
    return None
