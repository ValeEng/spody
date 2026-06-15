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
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import SettingsStore

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QIntValidator
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
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSplitter,
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
class _AssetCombo(QComboBox):
    """QComboBox subclass used by `_add_asset_combo`. Each item's
    userData carries the absolute on-disk path of the file the entry
    refers to; the displayed text is the human-friendly Asset.name
    (or '<basename>  (custom)' for paths added via Browse...).

    Keeping `category` + `body_key` on the instance means the form's
    refresh routine can rebuild every combo's options uniformly
    without an external lookup table."""
    def __init__(self, category: str, body_key: str | None) -> None:
        super().__init__()
        self.category = category
        self.body_key = body_key

    def repopulate(self, entries, preserve_path: str | None = None) -> None:
        """Wipe + refill from `entries` ((display_name, Path) pairs).
        If `preserve_path` matches one of the new entries, select it;
        otherwise, if it doesn't match anything, add it as a one-off
        '(custom)' entry so the round-trip from a loaded TOML stays
        intact even after a refresh."""
        block = self.blockSignals(True)
        try:
            self.clear()
            for name, path in entries:
                self.addItem(name, str(path))
            if preserve_path:
                idx = self.findData(preserve_path)
                if idx >= 0:
                    self.setCurrentIndex(idx)
                else:
                    self.add_custom_path(preserve_path)
        finally:
            self.blockSignals(block)

    def add_custom_path(self, path: str) -> None:
        """Append an out-of-data-dir entry tagged '(custom)' and select
        it. Used both by the Browse... button and by `repopulate` to
        keep a TOML's pre-existing path visible when it doesn't match
        any wizard asset."""
        from pathlib import Path as _Path
        label = f"{_Path(path).name}  (custom)"
        # If an identical path is already there, just select it.
        existing = self.findData(path)
        if existing >= 0:
            self.setCurrentIndex(existing)
            return
        self.addItem(label, path)
        self.setCurrentIndex(self.count() - 1)


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


# Per-field tooltips. One line each, shown on hover (and amended with
# the validation error when a value is out of range). Keys are the
# dotted paths the form uses internally.
_TOOLTIPS: dict[str, str] = {
    "simulation.name":               "Human-readable scenario name. Drives single-run output names AND batch per-case file names (the form mirrors this into batch.name on emit).",
    "simulation.et_start_s":         "Start epoch in TDB seconds past J2000.",
    "simulation.duration_s":         "Propagation duration in seconds; > 0.",
    "spacecraft.mass_kg":            "Dry mass in kilograms; must be > 0.",
    "spacecraft.srp.area_m2":        "SRP cross-section in m²; A/m derived as area_m2 / mass_kg.",
    "spacecraft.srp.am_srp":         "A/m directly in m²/kg; alternative to area_m2 (XOR).",
    "spacecraft.srp.Cr":             "Reflectivity coefficient (1 = absorb, 2 = mirror).",
    "debris.am_srp":                 "Area-to-mass ratio in m²/kg; > 0.",
    "debris.Cr":                     "Reflectivity coefficient (used only when SRP is enabled).",
    "initial_state.frame":           "Inertial reference frame. v0 supports only 'central_inertial'.",
    "initial_state.position_km":     "[x, y, z] position in km, central-body inertial frame.",
    "initial_state.velocity_kms":    "[vx, vy, vz] velocity in km/s, same frame as position.",
    "force_model.central_body":      "Central body of the propagation. v0 supports only 'Moon'.",
    "force_model.harmonics_file":    "Spherical-harmonics coefficient file (e.g. GRGM1200B).",
    "force_model.harmonics_degree":  "Truncation degree; ≥ 2 and ≤ file maximum (1200 for GRGM1200B).",
    "force_model.third_bodies":      "Perturbing bodies; pick from the standard NAIF set.",
    "force_model.srp":               "Enable cannonball SRP. Requires [spacecraft.srp] in spacecraft mode.",
    "ephemeris.file":                "DE-series ephemeris in the .spody binary format.",
    "integrator.type":               "Integration scheme. v0 supports only RK Dormand-Prince 5(4).",
    "integrator.rel_tol":            "Relative tolerance per accepted step; > 0.",
    "integrator.h_init_s":           "Initial step size in seconds; > 0, normally in [h_min_s, h_max_s].",
    "integrator.h_min_s":            "Minimum step size in seconds; > 0.",
    "integrator.h_max_s":            "Maximum step size in seconds; > h_min_s.",
    "output.mode":                   "'fixed' = uniform interval_s sampling; 'step' = one record per accepted step.",
    "output.interval_s":             "Sample interval in seconds; required when mode = fixed.",
    "output.output_dir":             "Parent directory for the per-run timestamp folder spody.exe creates at launch (<output_dir>/<UTC-ISO8601>/). Each run is self-contained: a snapshot of this TOML lands there as input.toml alongside all output files. Applies to BOTH single-run propagation and batch (the form mirrors this into batch.output_dir on emit). Leave empty to write outputs to the TOML's own directory with no per-run folder.",
    "output.csv_file":               "State vector CSV stream. Auto-named '<sim_name>_state_icrf.csv' under output_dir.",
    "output.bin_file":               "State vector binary stream (SPDYOUT_). Auto-named '<sim_name>_state_icrf.bin'.",
    "output.log_file":               "Tee stdout/stderr to a file. Auto-named '<sim_name>.log'.",
    "output.accelerations_file":     "Per-force acceleration breakdown (SPDYACC_). Auto-named '<sim_name>_acc_icrf.bin'.",
    "output.events_log":             "Event triggers binary. Per-run SPDYEVT_ ('<sim_name>_events.bin') in single-propagate; aggregated SPDYEVTB ('<batch_name>_events.bin') in batch.",
    "events.eclipse_threshold":      "Sunlight-fraction crossing that fires the eclipse event; in [0, 1].",
    "batch.thread_number":           "1 = sequential. > 1 needs the OpenMP-enabled spody build.",
    "batch.cases_file":              "CSV (today) or .spody (future): one row per case, header = column names.",
}


# Per-field value validators. Each takes the coerced field value
# (float / int / str) and returns "" if acceptable, or a short error
# message used to populate the tooltip and turn the field red.
# Fields with no entry are not validated beyond the type-validator
# (QDoubleValidator / QIntValidator) attached to the widget.
def _pos    (v: float) -> str: return "" if v >  0.0 else "must be > 0"
def _nonneg (v: float) -> str: return "" if v >= 0.0 else "must be >= 0"
def _harm_deg(v: int)  -> str: return "" if 2 <= v <= 1200 else "must be in [2, 1200]"
def _frac01 (v: float) -> str: return "" if 0.0 <= v <= 1.0 else "must be in [0, 1]"

_VALIDATORS: dict[str, Any] = {
    "simulation.duration_s":          _pos,
    "spacecraft.mass_kg":             _pos,
    "spacecraft.srp.area_m2":         _pos,
    "spacecraft.srp.am_srp":          _pos,
    "spacecraft.srp.Cr":              _nonneg,
    "debris.am_srp":                  _pos,
    "debris.Cr":                      _nonneg,
    "force_model.harmonics_degree":   _harm_deg,
    "integrator.rel_tol":             _pos,
    "integrator.h_init_s":            _pos,
    "integrator.h_min_s":             _pos,
    "integrator.h_max_s":             _pos,
    "output.interval_s":              _pos,
    "events.eclipse_threshold":       _frac01,
    # batch.thread_number cap is already enforced by QIntValidator(1, cpu_n).
}

# QSS for an invalid field. Kept conservative so it works on light and
# dark Qt themes alike -- a thin red border + a tinted background.
_INVALID_QSS = (
    "QLineEdit { border: 1px solid #d04040; "
    "background-color: rgba(255, 200, 200, 60); }"
)


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


# Conversion factors for seconds-valued fields rendered with a unit
# combo (currently only `duration_s`). The TOML always carries seconds;
# the combo is purely a display/typing aid driven by user preference.
_DURATION_FACTORS: dict[str, float] = {
    "s":    1.0,
    "min":  60.0,
    "h":    3600.0,
    "days": 86400.0,
}
# Order tried by the auto-pick on load -- the first unit whose factor
# does not exceed |value| wins, with "s" as the fallback so sub-second
# durations stay rendered in seconds.
_DURATION_UNIT_AUTOPICK = ("days", "h", "min")


# Standard file-name suffixes for the auto-named output streams. Keys are
# the same dotted TOML paths as the form widgets; values are appended to
# the simulation's `[simulation].name` to form the basename. Pattern is
# `_<subject>_<frame>` for streams whose payload is in a specific frame
# (state, acc), bare suffix for the rest (events have no frame; log is
# plain text).
_OUTPUT_FILE_SUFFIX: dict[str, str] = {
    "output.csv_file":           "_state_icrf.csv",
    "output.bin_file":           "_state_icrf.bin",
    "output.accelerations_file": "_acc_icrf.bin",
    "output.events_log":         "_events.bin",
    "output.log_file":           ".log",
}

# Display label for each output checkbox: short + tells the user
# what the auto-name will look like once a `[simulation].name` is set.
_OUTPUT_CHECK_LABEL: dict[str, str] = {
    "output.csv_file":           "state vector CSV    (<sim_name>_state_icrf.csv)",
    "output.bin_file":           "state vector binary (<sim_name>_state_icrf.bin)",
    "output.accelerations_file": "accelerations       (<sim_name>_acc_icrf.bin)",
    "output.events_log":         "events              (<sim_name>_events.bin)",
    "output.log_file":           "log                 (<sim_name>.log)",
}


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

    # Style sheets for the Validate badge -- tiny, kept inline so the
    # button strip's visual language is self-contained in this file.
    _BADGE_OK  = ("color: #1a7f37; font-weight: bold;")
    _BADGE_BAD = ("color: #cf222e; font-weight: bold;")

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

    def __init__(self, store: "SettingsStore | None" = None) -> None:
        super().__init__()
        # Shared SettingsStore -- used by the Validate button to find
        # the spody binary. Optional so the form is still instantiable
        # for unit / smoke tests without a full MainWindow.
        self._store = store
        self._widgets: dict[str, QWidget] = {}   # dotted path -> widget
        # Asset combos registered separately so the dropdown can be
        # refreshed when the data dir or central_body change. Each entry
        # is { category: 'harmonics'|'ephemeris', body_key: str|None },
        # body_key being the dotted-path key of the widget whose value
        # filters the combo (None -> body-agnostic).
        self._asset_combos: dict[str, dict[str, Any]] = {}
        self._current_path: Path | None = None
        self._modified = False
        self._loading = False                    # suppress modified flag
        # Top-level sections from the last load that the form does not
        # manage. Carried through to_dict so Generate does not destroy
        # data the form was never shown.
        self._passthrough: dict[str, Any] = {}
        # Set of keys whose QLineEdit holds a float (no Qt validator is
        # attached so the user's typed text stays verbatim -- see
        # _add_float). Used by _widget_value to know when to parse the
        # text as float vs leave as string.
        self._float_keys: set[str] = set()

        # Float keys whose QLineEdit value is rendered in a user-picked
        # unit (the unit combo lives in _scaled_unit_combos[key]). The
        # TOML still carries the SI value; _widget_value multiplies by
        # the current factor on emit and _set_widget_value auto-picks
        # an appropriate unit on load. Currently only `duration_s` uses
        # this, but the machinery is keyed so other seconds-valued
        # fields can opt in with one call to _add_duration_seconds.
        self._scaled_unit_combos: dict[str, QComboBox] = {}
        # Remembers each combo's last selection so the combo-change
        # handler can reconvert the displayed value without storing the
        # underlying seconds in a separate field.
        self._scaled_unit_prev: dict[str, str] = {}

        outer = QVBoxLayout(self)

        # Top row: current file + Load / Generate / Validate / RUN
        # plus a small badge to the right showing the last validate
        # result (✓ OK / ✗ <error>) without going to the terminal.
        self._path_label = QLabel("(no file)")
        self._path_label.setStyleSheet("color: gray;")
        btn_load = QPushButton("Load...")
        btn_gen  = QPushButton("Generate")
        btn_val  = QPushButton("Validate")
        btn_run  = QPushButton("RUN")
        btn_run.setStyleSheet(
            "QPushButton { background-color: #2ea043; color: white; "
            "font-weight: bold; padding: 4px 16px; border-radius: 3px; }"
            "QPushButton:hover  { background-color: #3fb950; }"
            "QPushButton:pressed{ background-color: #238636; }"
        )
        btn_load.clicked.connect(self._on_load_clicked)
        btn_gen.clicked.connect(self._on_generate_clicked)
        btn_val.clicked.connect(self._on_validate_clicked)
        btn_run.clicked.connect(self._on_run_clicked)

        self._validate_badge = QLabel("")
        self._validate_badge.setMinimumWidth(160)

        top_row = QHBoxLayout()
        top_row.addWidget(self._path_label, 1)
        top_row.addWidget(btn_load)
        top_row.addWidget(btn_gen)
        top_row.addWidget(btn_val)
        top_row.addWidget(btn_run)
        outer.addLayout(top_row)

        badge_row = QHBoxLayout()
        badge_row.addStretch(1)
        badge_row.addWidget(self._validate_badge)
        outer.addLayout(badge_row)

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
        body_lay.addWidget(self._build_notes())
        body_lay.addStretch(1)
        scroll.setWidget(body)

        # Live TOML preview: read-only QPlainTextEdit fed by
        # _refresh_preview() on every form change. Wrapped in a small
        # widget with a header label so the user knows it's a preview
        # (not the editor!). The preview + form share a vertical
        # QSplitter the user can resize.
        preview_box = QWidget()
        preview_lay = QVBoxLayout(preview_box)
        preview_lay.setContentsMargins(0, 0, 0, 0)
        preview_header = QLabel("TOML preview  (read-only; reflects the form live):")
        preview_header.setStyleSheet("color: gray; padding-top: 4px;")
        self._preview = QPlainTextEdit()
        self._preview.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setPointSize(9)
        self._preview.setFont(mono)
        self._preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        preview_lay.addWidget(preview_header)
        preview_lay.addWidget(self._preview, 1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(scroll)
        splitter.addWidget(preview_box)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        splitter.setSizes([520, 280])
        outer.addWidget(splitter, 1)

        # Hook tooltips after every widget has been registered.
        self._apply_tooltips()
        # output preview needs simulation.name (built later than output);
        # wire it now that every section has registered its widgets.
        self._wire_output_preview_dependencies()
        # Seed the preview with the initial (mostly empty) form state.
        self._refresh_preview()

    # ==================================================================
    # Section builders
    # ==================================================================
    def _build_simulation(self) -> QGroupBox:
        g = QGroupBox("[simulation]")
        f = QFormLayout(g)
        self._add_string(f, "simulation.name",       "name")
        # ET stays the only thing the TOML carries; the UTC cell next
        # to it is a typing aid driven by `→` / `←` convert buttons.
        self._add_et_with_utc(f, "simulation.et_start_s", "et_start_s")
        self._add_duration_seconds(f, "simulation.duration_s", "duration_s")
        return g

    def _add_et_with_utc(self, layout: QFormLayout, key: str,
                          label: str) -> None:
        """Composite row: ET (the TOML-bound float) on the left, UTC
        ISO 8601 (a display/typing aid, NOT serialised) on the right,
        with a vertical stack of two convert buttons between them:

            ┌──────────┐   ┌─→┐   ┌──────────────────────┐
            │ ET sec   │   └──┘   │ UTC ISO 8601         │
            │          │   ┌─←┐   │                      │
            └──────────┘   └──┘   └──────────────────────┘

        `→` fills the UTC cell from the current ET value; `←` does
        the inverse. Conversion goes through `time_conv` which matches
        SPICE `str2et` to ~1 ns (see module docstring)."""
        et_edit = QLineEdit()
        et_edit.textChanged.connect(self._touch)
        self._widgets[key] = et_edit
        self._float_keys.add(key)

        utc_edit = QLineEdit()
        utc_edit.setPlaceholderText("YYYY-MM-DDThh:mm:ss[.fff]Z")
        # UTC is NOT registered in self._widgets -- the TOML carries
        # only `et_start_s`; the UTC text is a transient display.

        from . import time_conv

        def _et_to_utc() -> None:
            text = et_edit.text().strip()
            if not text:
                QMessageBox.information(
                    self, "ET → UTC", "Fill in et_start_s first.")
                return
            try:
                et = float(text)
            except ValueError:
                QMessageBox.warning(
                    self, "ET → UTC",
                    f"et_start_s is not a number: {text!r}")
                return
            utc_edit.setText(time_conv.format_utc_iso(time_conv.et_to_utc(et)))

        def _utc_to_et() -> None:
            text = utc_edit.text().strip()
            if not text:
                QMessageBox.information(
                    self, "UTC → ET", "Type a UTC ISO 8601 instant first "
                    "(e.g. 2009-09-18T12:00:00Z).")
                return
            try:
                dt = time_conv.parse_utc_iso(text)
                et = time_conv.utc_to_et(dt)
            except ValueError as exc:
                QMessageBox.warning(self, "UTC → ET", str(exc))
                return
            # repr() keeps every double-precision bit so a round-trip
            # UTC -> ET -> UTC stays exact at the cost of a long tail.
            et_edit.setText(repr(et))

        btn_to_utc = QPushButton("→")
        btn_to_utc.setToolTip("Compute UTC from this ET value (et_start_s → utc)")
        btn_to_utc.setMaximumWidth(28)
        btn_to_et  = QPushButton("←")
        btn_to_et.setToolTip("Compute ET from this UTC value (utc → et_start_s)")
        btn_to_et.setMaximumWidth(28)
        btn_to_utc.clicked.connect(_et_to_utc)
        btn_to_et.clicked.connect(_utc_to_et)

        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(2)
        btn_col.addWidget(btn_to_utc)
        btn_col.addWidget(btn_to_et)
        btn_wrap = QWidget()
        btn_wrap.setLayout(btn_col)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(et_edit, 1)
        row.addWidget(btn_wrap)
        row.addWidget(utc_edit, 1)
        layout.addRow(label + _unit_suffix("et_start_s"), _hwrap(row))

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
        # Harmonics combo: filtered by central_body so once Earth /
        # Mars / etc. land as supported central bodies, the dropdown
        # only ever shows the gravity models that apply to the
        # currently-selected body.
        self._add_asset_combo(
            f, "force_model.harmonics_file", "harmonics_file",
            category="harmonics", body_key="force_model.central_body",
        )
        self._add_int  (f, "force_model.harmonics_degree", "harmonics_degree",
                        minimum=2, maximum=1200)
        self._add_strlist_checks(f, "force_model.third_bodies", "third_bodies",
                                 THIRD_BODIES_ALL)
        self._add_bool (f, "force_model.srp", "srp")
        return g

    def _build_ephemeris(self) -> QGroupBox:
        g = QGroupBox("[ephemeris]")
        f = QFormLayout(g)
        # Ephemeris combo: body-agnostic. DE-series ephemerides cover
        # every planet at once, so the dropdown does not depend on
        # the central body.
        self._add_asset_combo(
            f, "ephemeris.file", "file",
            category="ephemeris", body_key=None,
        )
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
        # Stash the QFormLayout + the row index of interval_s so we can
        # toggle its visibility via QFormLayout.setRowVisible when the
        # mode combo changes (interval_s only applies to mode='fixed').
        self._output_form = QFormLayout(g)
        f = self._output_form
        self._add_enum (f, "output.mode",               "mode", OUTPUT_MODES)
        self._add_float(f, "output.interval_s",         "interval_s")
        self._output_interval_row = f.rowCount() - 1

        # output_dir: directory that holds all enabled streams. Empty =
        # the TOML's own folder. The 5 file paths below are auto-derived
        # from this + [simulation].name + a standard suffix, so the
        # user only ever picks ONE directory (not five paths).
        self._add_path(f, "output.output_dir", "output_dir", "",
                       pick_dir=True)

        # Five on/off toggles for the optional output streams. The file
        # path that spody.exe sees is composed at Generate time as
        #     <output_dir>/<sim_name><suffix>
        # using _OUTPUT_FILE_SUFFIX. Path fields are still emitted as
        # `output.csv_file` etc. -- the engine sees no schema change.
        for key in (
            "output.csv_file",
            "output.bin_file",
            "output.accelerations_file",
            "output.events_log",
            "output.log_file",
        ):
            cb = QCheckBox(_OUTPUT_CHECK_LABEL[key])
            cb.toggled.connect(self._touch)
            cb.toggled.connect(lambda _checked: self._refresh_output_preview())
            self._widgets[key] = cb
            f.addRow("", cb)

        # Live preview of the paths the next Generate will emit. Sits
        # below the checkboxes so the user can sanity-check the
        # combination of output_dir + sim_name + which streams are on.
        self._output_preview_label = QLabel("")
        self._output_preview_label.setStyleSheet("color: gray;")
        self._output_preview_label.setWordWrap(True)
        f.addRow("paths preview", self._output_preview_label)
        # Auto-refresh when output_dir or simulation.name change.
        self._widgets["output.output_dir"].textChanged.connect(
            lambda _t: self._refresh_output_preview())
        # `simulation.name` is wired later (when _build_simulation has
        # registered it); the deferred connection happens at the bottom
        # of __init__ via _wire_output_preview_dependencies.

        # Wire interval_s visibility to the mode combo. Note: the
        # field's value is also stripped from to_dict when mode='step'
        # so a stale value left over from a prior 'fixed' run does
        # not leak into the emitted TOML.
        mode_combo = self._widgets["output.mode"]
        mode_combo.currentTextChanged.connect(self._on_output_mode_changed)
        self._on_output_mode_changed(mode_combo.currentText())
        return g

    def _on_output_mode_changed(self, mode: str) -> None:
        if not hasattr(self, "_output_form"):
            return   # called too early during construction
        self._output_form.setRowVisible(self._output_interval_row,
                                        mode == "fixed")

    # ------------------------------------------------------------------
    # Output auto-naming
    # ------------------------------------------------------------------
    def _wire_output_preview_dependencies(self) -> None:
        """Connect [simulation].name -> output preview. Called once at
        the end of __init__ because the simulation widgets are built
        after the output ones."""
        sim_name_w = self._widgets.get("simulation.name")
        if isinstance(sim_name_w, QLineEdit):
            sim_name_w.textChanged.connect(
                lambda _t: self._refresh_output_preview())
        self._refresh_output_preview()

    def _resolved_output_paths(self) -> dict[str, str]:
        """Map each enabled `output.<stream>` key to the file path that
        the next Generate would emit. Disabled streams are absent from
        the returned dict. Used by both _refresh_output_preview (label
        text) and to_dict (TOML emission)."""
        sim_name_w = self._widgets.get("simulation.name")
        sim_name = sim_name_w.text().strip() if isinstance(sim_name_w, QLineEdit) else ""
        out_dir = self._widgets["output.output_dir"].text().strip()

        out: dict[str, str] = {}
        for key, suffix in _OUTPUT_FILE_SUFFIX.items():
            cb = self._widgets.get(key)
            if not isinstance(cb, QCheckBox) or not cb.isChecked():
                continue
            # Empty sim_name -> fall back to "output" so the file still
            # has a coherent name; the validator will surface the empty
            # simulation.name separately.
            stem = sim_name if sim_name else "output"
            name = f"{stem}{suffix}"
            out[key] = f"{out_dir}/{name}" if out_dir else name
        return out

    def _refresh_output_preview(self) -> None:
        if not hasattr(self, "_output_preview_label"):
            return
        paths = self._resolved_output_paths()
        if not paths:
            self._output_preview_label.setText("(no streams enabled)")
            return
        lines = [f"{key.rsplit('.', 1)[1]:>19}: {p}"
                 for key, p in paths.items()]
        self._output_preview_label.setText("\n".join(lines))

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
        # `batch.name` and `batch.output_dir` used to live here as
        # separate string / path widgets, doubling up the name and
        # output-folder fields the user had already filled under
        # [simulation] and [output]. They are now derived in to_dict
        # from `simulation.name` and `output.output_dir`, so the form
        # exposes exactly one name field and one output folder field
        # regardless of whether the user is running a single
        # propagation or a batch sweep.
        # Cap thread_number to the actual logical-CPU count of the
        # host. spody is CPU-bound numerical work, so oversubscribing
        # past the available cores is only ever slower; the validator
        # blocks input above the cap as the user types it.
        # os.cpu_count() can return None on exotic systems; default to
        # 1 in that case so the cap is always meaningful.
        cpu_n = os.cpu_count() or 1
        self._add_int   (f, "batch.thread_number", "thread_number",
                         minimum=1, maximum=cpu_n,
                         hint=f"(1..{cpu_n} cores available on this machine)")

        # The single user-facing path field. spody.exe natively only
        # accepts ICRF state, so the path's role depends on cases_frame:
        #   icrf -> passed verbatim to spody.exe as cases_file
        #   ric  -> the GUI rotates this file at Generate and writes
        #           <stem>_wrt_icrf.csv next to it; spody.exe reads the
        #           rotated copy.
        # Internally the widget is mapped to "batch.cases_source_file";
        # the actual `cases_file` key written to the TOML is computed in
        # to_dict() so the two paths can't drift out of sync.
        self._batch_cases_edit = QLineEdit()
        self._batch_cases_edit.textChanged.connect(self._touch)
        self._batch_cases_edit.textChanged.connect(
            lambda _: self._update_cases_frame_status())
        self._batch_cases_edit.textChanged.connect(
            lambda _: self._update_ric_preview())
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
        self._widgets["batch.cases_source_file"] = self._batch_cases_edit

        # Status line just under the cases_file row -- tells the user
        # whether the CSV was readable and how many columns came back.
        self._batch_cases_status = QLabel("")
        self._batch_cases_status.setStyleSheet("color: gray;")
        f.addRow("", self._batch_cases_status)

        # Frame selector for the state-vector columns in the cases file.
        # spody.exe only knows ICRF; this combo tells the GUI whether
        # the user's CSV is already in ICRF (used as-is) or in RIC
        # (rotated at Generate-TOML using [initial_state] as the
        # reference orbit). The cases_frame key is only emitted when
        # not the default.
        self._batch_cases_frame_combo = QComboBox()
        for frame_name in ("icrf", "ric", "lvlh"):
            self._batch_cases_frame_combo.addItem(frame_name)
        self._batch_cases_frame_combo.currentTextChanged.connect(
            self._on_cases_frame_changed)
        self._batch_cases_frame_combo.currentTextChanged.connect(
            lambda _: self._touch())
        self._widgets["batch.cases_frame"] = self._batch_cases_frame_combo
        f.addRow("cases_frame", self._batch_cases_frame_combo)

        # Status line under the frame combo: shows the path spody.exe
        # will actually read (== source for icrf, == rotated copy for ric).
        self._batch_frame_status = QLabel("")
        self._batch_frame_status.setStyleSheet("color: gray;")
        self._batch_frame_status.setWordWrap(True)
        f.addRow("", self._batch_frame_status)

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

        # CSV data preview: first N rows of the cases_file verbatim, so
        # the user can sanity-check the column-target mapping against
        # the actual numbers spody will see. Read-only, fixed-height
        # so it doesn't dominate the form even with many columns.
        self._batch_preview_status = QLabel("")
        self._batch_preview_status.setStyleSheet("color: gray;")
        f.addRow(self._batch_preview_status)

        self._batch_preview_table = QTableWidget(0, 0)
        self._batch_preview_table.verticalHeader().setDefaultSectionSize(20)
        self._batch_preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._batch_preview_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._batch_preview_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._batch_preview_table.setMinimumHeight(140)
        # Monospace cells so columns of numbers line up visually.
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._batch_preview_table.setFont(mono)
        f.addRow(self._batch_preview_table)

        # Rotated preview: visible only when cases_frame == "ric". Shows
        # the first N rows of what spody.exe would actually read once
        # the RIC->ICRF rotation is applied. Auto-refreshed on source /
        # frame / column-re-read; a Refresh button covers edits to the
        # column mapping or [initial_state].
        self._batch_rotated_preview_container = QWidget()
        rp_v = QVBoxLayout(self._batch_rotated_preview_container)
        rp_v.setContentsMargins(0, 0, 0, 0)

        rp_header = QHBoxLayout()
        rp_header.setContentsMargins(0, 0, 0, 0)
        # Label is a member so _update_ric_preview can patch it to the
        # actual frame ("post LVLH -> ICRF", "post RIC -> ICRF", ...).
        self._batch_rotated_preview_header = QLabel(
            "Rotated preview (post RIC -> ICRF):")
        rp_header.addWidget(self._batch_rotated_preview_header, 1)
        rp_refresh = QPushButton("Refresh preview")
        rp_refresh.clicked.connect(self._update_ric_preview)
        rp_header.addWidget(rp_refresh)
        rp_v.addLayout(rp_header)

        self._batch_rotated_preview_status = QLabel("")
        self._batch_rotated_preview_status.setStyleSheet("color: gray;")
        rp_v.addWidget(self._batch_rotated_preview_status)

        self._batch_rotated_preview_table = QTableWidget(0, 0)
        self._batch_rotated_preview_table.verticalHeader().setDefaultSectionSize(20)
        self._batch_rotated_preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._batch_rotated_preview_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._batch_rotated_preview_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._batch_rotated_preview_table.setMinimumHeight(140)
        self._batch_rotated_preview_table.setFont(mono)
        rp_v.addWidget(self._batch_rotated_preview_table)

        f.addRow(self._batch_rotated_preview_container)
        self._batch_rotated_preview_container.setVisible(False)

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
        w.textChanged.connect(lambda _t, k=key: self._validate_field(k))
        self._widgets[key] = w
        layout.addRow(label + _unit_suffix(key.split(".")[-1]), w)

    def _add_float(self, layout: QFormLayout, key: str, label: str) -> None:
        w = QLineEdit()
        # Intentionally no QDoubleValidator: it normalises the text on
        # editingFinished (locale-aware fixup, e.g. "1.0e-5" -> "1e-05"),
        # which surprises users -- they expect the value they typed to
        # stay verbatim. Range checking is done by _validate_field and
        # the float() parse in _widget_value.
        w.textChanged.connect(self._touch)
        w.textChanged.connect(lambda _t, k=key: self._validate_field(k))
        self._widgets[key] = w
        self._float_keys.add(key)
        layout.addRow(label + _unit_suffix(key.split(".")[-1]), w)

    def _add_duration_seconds(self, layout: QFormLayout, key: str,
                                label: str) -> None:
        """Float-in-seconds row with a unit combo (s | min | h | days).

        The QLineEdit displays the value in whichever unit the user
        picks; `_widget_value` multiplies by the current factor on
        emit so the TOML always carries seconds, regardless of what
        the user typed. On load, `_set_widget_value` auto-picks the
        largest unit that yields a magnitude >= 1 (with `s` as the
        fallback for sub-second values), then divides accordingly.
        Switching the combo reconverts the visible number so the
        underlying seconds-value stays invariant -- typing 3600 in
        seconds and flipping the combo to `h` shows 1.0 without
        touching the form-modified flag any more than the user's own
        edit would.

        Range validation runs on the displayed (scaled) value, which
        is fine for the only current consumer (`duration_s` uses
        `_pos`, invariant under positive scaling); if a future field
        wants a bound on the SI value, the validator needs to be
        applied post-scale instead.
        """
        w = QLineEdit()
        w.textChanged.connect(self._touch)
        w.textChanged.connect(lambda _t, k=key: self._validate_field(k))
        self._widgets[key] = w
        self._float_keys.add(key)

        combo = QComboBox()
        for u in _DURATION_FACTORS:
            combo.addItem(u)
        combo.setMaximumWidth(70)
        combo.setToolTip("Display unit for this duration. The TOML "
                         "always carries seconds; switching unit "
                         "rescales the visible number without changing "
                         "the underlying value.")

        def _on_unit_changed(idx: int) -> None:
            new_unit = combo.itemText(idx)
            old_unit = self._scaled_unit_prev.get(key, "s")
            if new_unit == old_unit:
                return
            text = w.text().strip()
            if text:
                try:
                    v = float(text)
                except ValueError:
                    self._scaled_unit_prev[key] = new_unit
                    return
                seconds = v * _DURATION_FACTORS[old_unit]
                w.blockSignals(True)
                w.setText(_tidy_float(seconds / _DURATION_FACTORS[new_unit]))
                w.blockSignals(False)
                # Re-run the validator on the new displayed value; the
                # blockSignals above suppressed textChanged.
                self._validate_field(key)
            self._scaled_unit_prev[key] = new_unit
            self._touch()

        combo.currentIndexChanged.connect(_on_unit_changed)
        self._scaled_unit_combos[key] = combo
        self._scaled_unit_prev[key] = combo.currentText()

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(w, 1)
        row.addWidget(combo)
        # No unit-suffix on the label: the combo IS the unit, and the
        # bracketed "[s]" tag would lie the moment the user picks min/h.
        layout.addRow(label, _hwrap(row))

    def _add_int(self, layout: QFormLayout, key: str, label: str,
                 minimum: int = -2**31, maximum: int = 2**31 - 1,
                 hint: str = "") -> None:
        w = QLineEdit()
        w.setValidator(QIntValidator(minimum, maximum, w))
        w.textChanged.connect(self._touch)
        w.textChanged.connect(lambda _t, k=key: self._validate_field(k))
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

    def _add_asset_combo(self, layout: QFormLayout, key: str, label: str,
                         category: str, body_key: str | None = None) -> None:
        """Dropdown of downloaded assets of a given category, with an
        escape hatch for legacy / external paths.

        - `category`: 'harmonics' or 'ephemeris' (matches Asset.category)
        - `body_key`: dotted-path widget key whose value filters by
                      body ('force_model.central_body'); None for
                      body-agnostic dropdowns like the ephemeris one.

        Each combo item carries the absolute path as its userData;
        `_widget_value` reads that out at Generate time. A 'Browse...'
        button next to the combo lets the user add an out-of-data-dir
        file as a one-off entry tagged '(custom)' so existing TOMLs
        with bespoke paths (e.g. the demos that point at
        external/spody-core) keep round-tripping.

        The combo is also re-populated when the user changes the
        central body (via the matching body_key's currentTextChanged
        signal -- wired here so the form stays self-contained)."""
        combo = _AssetCombo(category=category, body_key=body_key)
        combo.currentIndexChanged.connect(self._touch)

        btn = QPushButton("Browse...")
        def _browse() -> None:
            # Start in the data dir if available so a regular user lands
            # next to the things the wizard downloaded.
            start = ""
            if self._store is not None:
                start = str(self._store.data_dir())
            path, _ = QFileDialog.getOpenFileName(
                self, f"Locate {key}", start, "All files (*)")
            if not path:
                return
            combo.add_custom_path(path)
        btn.clicked.connect(_browse)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(combo, 1)
        row.addWidget(btn)
        self._widgets[key] = combo
        self._asset_combos[key] = {"category": category, "body_key": body_key}
        layout.addRow(label, _hwrap(row))
        self._refresh_asset_combo(key)
        # Re-populate when the central body changes (if applicable).
        if body_key is not None and body_key in self._widgets:
            body_widget = self._widgets[body_key]
            if isinstance(body_widget, QComboBox):
                body_widget.currentTextChanged.connect(
                    lambda _t, k=key: self._refresh_asset_combo(k))

    def _refresh_asset_combo(self, key: str) -> None:
        """Re-scan the data dir and rebuild this combo's options. The
        currently-selected path is preserved when possible (re-selected
        by data-equality) so the user doesn't lose their pick on a
        re-population."""
        info = self._asset_combos.get(key)
        combo = self._widgets.get(key)
        if info is None or not isinstance(combo, _AssetCombo):
            return
        from . import assets
        if self._store is None:
            return
        root = self._store.data_dir()
        body = None
        if info["body_key"] is not None:
            body_widget = self._widgets.get(info["body_key"])
            if isinstance(body_widget, QComboBox):
                body = body_widget.currentText().strip() or None
        prior_data = combo.currentData()
        combo.repopulate(
            assets.present_files_for(info["category"], root, body),
            preserve_path=str(prior_data) if prior_data else None,
        )

    def refresh_asset_combos(self) -> None:
        """Public entry point: re-scan every registered asset combo.
        Called by MainWindow after the Setup wizard closes so newly-
        downloaded files appear immediately."""
        for key in list(self._asset_combos):
            self._refresh_asset_combo(key)

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
            # No QDoubleValidator: see _add_float for the rationale.
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

    def _build_notes(self) -> QGroupBox:
        """Freeform notes attached to this TOML, emitted as a comment
        block at the end of the file.

        Purely metadata: spody.exe never reads the comments, and the
        TOML schema itself stays clean (no synthetic `notes` key
        polluting the document). The value is preserved verbatim in
        the per-run input.toml snapshot inside each run folder, so a
        user revisiting a result months later can still find what
        they wrote when they launched it -- 'tuned h_max down 10x to
        chase the LRO regression', 'sweep #3 with thread_number 16
        after the OpenMP fix', etc.

        The toml_io reader scans the file for the BEGIN / END marker
        pair (`_NOTES_BEGIN` / `_NOTES_END`) and lifts the comment
        body back into a `notes` string on the parsed dict; the
        emitter does the inverse on save. A TOML edited by hand
        keeps round-tripping as long as the markers stay intact.
        """
        g = QGroupBox("Notes  (optional, attached to this TOML)")
        v = QVBoxLayout(g)
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText(
            "Freeform notes about this scenario. Stored as a "
            "comment block at the end of the TOML; copied verbatim "
            "into the per-run input.toml snapshot.")
        self._notes_edit.setMinimumHeight(80)
        self._notes_edit.textChanged.connect(self._touch)
        v.addWidget(self._notes_edit)
        return g

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

    # ------------------------------------------------------------------
    # cases_frame (icrf | ric) handler + status label
    # ------------------------------------------------------------------
    def _on_cases_frame_changed(self, frame: str) -> None:
        """Refresh the status label + rotated preview after a frame
        switch. No other side effects -- the path field is always the
        user-picked source, and the actual cases_file written to the
        TOML is computed at Generate (to_dict)."""
        self._update_cases_frame_status()
        self._update_ric_preview()

    # Frames that require a GUI-side rotation to ICRF at Generate-TOML
    # (spody.exe only ingests ICRF state). Used by every dispatch helper
    # below as the single source of truth; add a new rotating frame here
    # + its (basis, rotation_fn) pair in `_rotation_helpers()` and the
    # rest plugs in automatically.
    _ROTATING_FRAMES = ("ric", "lvlh")

    @staticmethod
    def _rotation_helpers(frame: str):
        """Return `(basis_fn, rotate_csv_fn)` for a rotating frame.
        Both helpers come from `spody_gui.frames` and share the same
        contract; the dispatch maps the combo value to the right pair.
        Raises ValueError for an unrecognised frame."""
        from . import frames as _frames
        if frame == "ric":
            return _frames.ric_basis,  _frames.rotate_state_csv_ric_to_icrf
        if frame == "lvlh":
            return _frames.lvlh_basis, _frames.rotate_state_csv_lvlh_to_icrf
        raise ValueError(f"no rotation helpers for frame {frame!r}")

    def _resolved_cases_file(self) -> str:
        """Compute the cases_file value that spody.exe will see, from
        the current source path + frame combo. Returns an empty string
        when the source field is empty."""
        src = self._batch_cases_edit.text().strip()
        if not src:
            return ""
        frame = self._batch_cases_frame_combo.currentText()
        if frame not in self._ROTATING_FRAMES:
            # icrf or unset: the source IS the file spody.exe reads.
            return src
        # ric / lvlh: rotated copy alongside the source. Preserve
        # relative-ness (cases_file in the TOML is interpreted relative
        # to the TOML file's directory). Same '_wrt_icrf' suffix for
        # every rotating frame -- the destination is always ICRF.
        p = Path(src)
        return str(p.with_name(f"{p.stem}_wrt_icrf.csv"))

    def _update_cases_frame_status(self) -> None:
        """Single-line help text under the frame combo telling the user
        what spody.exe will actually read. spody.exe only accepts ICRF
        natively; this status keeps that explicit."""
        derived = self._resolved_cases_file()
        frame = self._batch_cases_frame_combo.currentText()
        if not derived:
            self._batch_frame_status.setText(
                "Pick a cases CSV above.")
            return
        if frame in self._ROTATING_FRAMES:
            self._batch_frame_status.setText(
                f"{frame.upper()} source. At Generate the GUI rotates "
                f"the state columns and writes '{Path(derived).name}'; "
                f"spody.exe reads THAT file.")
        else:
            self._batch_frame_status.setText(
                f"ICRF source. spody.exe reads it directly "
                f"('{Path(derived).name}').")

    # ------------------------------------------------------------------
    # Rotated preview (visible only when cases_frame == "ric")
    # ------------------------------------------------------------------
    def _clear_rotated_preview(self) -> None:
        self._batch_rotated_preview_table.setRowCount(0)
        self._batch_rotated_preview_table.setColumnCount(0)

    def _update_ric_preview(self) -> None:
        """Recompute the first 10 rotated rows of the source CSV and
        push them into the rotated-preview table. No-op (and hidden)
        when frame is not a rotating one or batch is disabled. Silent
        on errors -- only the status line shows the reason. Despite
        the historical name, this handles both RIC and LVLH sources."""
        frame = self._batch_cases_frame_combo.currentText()
        is_rotating = (self._batch_check.isChecked() and
                       frame in self._ROTATING_FRAMES)
        self._batch_rotated_preview_container.setVisible(is_rotating)
        if not is_rotating:
            return

        # Keep the header label in sync with the actually-selected
        # rotating frame so messages don't lie about the convention.
        self._batch_rotated_preview_header.setText(
            f"Rotated preview (post {frame.upper()} -> ICRF):")

        try:
            data = self.to_dict()
        except ValueError as exc:
            self._batch_rotated_preview_status.setText(
                f"(preview unavailable: form has invalid values -- {exc})")
            self._clear_rotated_preview()
            return

        resolved = self._resolve_ric_inputs(self._current_path, data)
        if not resolved or resolved[0] != "ok":
            msg = resolved[1] if resolved else "unknown"
            self._batch_rotated_preview_status.setText(
                f"(preview unavailable: {msg})")
            self._clear_rotated_preview()
            return
        _, src, _out, r_ref, v_ref, pos_cols, vel_cols = resolved

        # Build R once; bail with a clean status if the reference orbit
        # is degenerate.
        basis_fn, _ = self._rotation_helpers(frame)
        import csv as csv_mod
        import numpy as np
        try:
            R = basis_fn(r_ref, v_ref)
        except ValueError as exc:
            self._batch_rotated_preview_status.setText(
                f"(preview unavailable: {exc})")
            self._clear_rotated_preview()
            return

        # Read first 10 data rows of the source.
        PREVIEW_N = 10
        try:
            with src.open(encoding="utf-8", newline="") as fp:
                data_lines = [
                    ln for ln in fp
                    if ln.strip() and not ln.lstrip().startswith("#")
                ]
        except OSError as exc:
            self._batch_rotated_preview_status.setText(
                f"(preview unavailable: {exc})")
            self._clear_rotated_preview()
            return
        if not data_lines:
            self._batch_rotated_preview_status.setText(
                f"(preview: {src.name} has a header but no data rows)")
            self._clear_rotated_preview()
            return

        reader = csv_mod.DictReader(data_lines, skipinitialspace=True)
        header = [h.strip() for h in (reader.fieldnames or [])]
        reader.fieldnames = header
        rows: list[dict[str, str]] = []
        for row_idx, row in enumerate(reader, start=1):
            if len(rows) >= PREVIEW_N:
                break
            out_row = dict(row)
            try:
                if pos_cols is not None:
                    r_ric = np.array([float(row[c]) for c in pos_cols])
                    r_eci = R @ r_ric
                    for c, v in zip(pos_cols, r_eci):
                        out_row[c] = repr(float(v))
                if vel_cols is not None:
                    v_ric = np.array([float(row[c]) for c in vel_cols])
                    v_eci = R @ v_ric
                    for c, v in zip(vel_cols, v_eci):
                        out_row[c] = repr(float(v))
            except (KeyError, TypeError, ValueError) as exc:
                self._batch_rotated_preview_status.setText(
                    f"(preview: row {row_idx} skipped -- {exc})")
                continue
            rows.append(out_row)

        self._batch_rotated_preview_table.setRowCount(0)
        self._batch_rotated_preview_table.setColumnCount(len(header))
        self._batch_rotated_preview_table.setHorizontalHeaderLabels(header)
        for r_idx, row in enumerate(rows):
            self._batch_rotated_preview_table.insertRow(r_idx)
            for c_idx, col in enumerate(header):
                self._batch_rotated_preview_table.setItem(
                    r_idx, c_idx,
                    QTableWidgetItem(row.get(col, "")))

        # Help line: cite which columns were rotated.
        bits = []
        if pos_cols: bits.append(f"pos={list(pos_cols)}")
        if vel_cols: bits.append(f"vel={list(vel_cols)}")
        self._batch_rotated_preview_status.setText(
            f"(rotated preview: first {len(rows)} rows from {src.name}; "
            f"{', '.join(bits)})")

    def _refresh_batch_columns(self) -> None:
        """(Re-)read the CSV at cases_file and rebuild both the
        [batch.columns] mapping table and the data-preview table.
        Existing target/mode assignments survive a re-read when the
        column name reappears."""
        path_str = self._batch_cases_edit.text().strip()
        if not path_str:
            self._batch_cases_status.setText("")
            self._batch_columns_table.setRowCount(0)
            self._clear_preview()
            return

        p = Path(path_str)
        if not p.is_absolute() and self._current_path is not None:
            # Same resolution rule spody uses internally: paths in the
            # TOML are relative to the TOML file's directory.
            p = self._current_path.parent / p

        if not p.is_file():
            self._batch_cases_status.setText(f"(not found: {p})")
            self._batch_columns_table.setRowCount(0)
            self._clear_preview()
            return

        try:
            header, preview_rows, total_rows = _read_csv_preview(p)
        except OSError as exc:
            self._batch_cases_status.setText(f"(read failed: {exc})")
            self._batch_columns_table.setRowCount(0)
            self._clear_preview()
            return

        # Drop the special `id` column from the *mapping* table (it's
        # used for case naming, not a spody override target). The
        # preview keeps every column so the user sees the full row.
        mapping_columns = [c for c in header if c.lower() != "id"]
        existing = self._snapshot_batch_columns()

        was_loading = self._loading
        self._loading = True
        try:
            self._batch_columns_table.setRowCount(0)
            for col in mapping_columns:
                self._add_batch_column_row(col, existing.get(col))
        finally:
            self._loading = was_loading

        self._batch_cases_status.setText(
            f"({len(mapping_columns)} non-id columns read from {p.name})")
        self._populate_preview(header, preview_rows, total_rows, p)
        # New columns may have changed which targets are mapped to
        # initial_state -- the rotated preview depends on that.
        self._update_ric_preview()

    def _clear_preview(self) -> None:
        """Empty the preview table + clear the 'first N of M' label."""
        self._batch_preview_status.setText("")
        self._batch_preview_table.setRowCount(0)
        self._batch_preview_table.setColumnCount(0)

    def _populate_preview(self, header: list[str], rows: list[list[str]],
                          total: int, src: Path) -> None:
        """Push the CSV preview into the read-only table. Cells are
        rendered verbatim (no parsing), so float vs int vs string is
        whatever the file says."""
        n_shown = len(rows)
        if total == 0:
            self._batch_preview_status.setText(
                f"(preview: file has a header but no data rows: {src.name})")
        elif n_shown < total:
            self._batch_preview_status.setText(
                f"(preview: first {n_shown} of {total} rows from {src.name})")
        else:
            self._batch_preview_status.setText(
                f"(preview: {n_shown} rows from {src.name})")

        self._batch_preview_table.setRowCount(0)
        self._batch_preview_table.setColumnCount(len(header))
        self._batch_preview_table.setHorizontalHeaderLabels(header)
        for r, row_cells in enumerate(rows):
            self._batch_preview_table.insertRow(r)
            for c in range(len(header)):
                text = row_cells[c] if c < len(row_cells) else ""
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._batch_preview_table.setItem(r, c, item)

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
        emission inside `[batch.columns]`. Rows left at "(unassigned)"
        are emitted as `<col> = ""` -- the metadata-column sentinel the
        C parser recognises (target == empty string -> read out of the
        CSV but not applied to any field). This lets the user keep
        bookkeeping columns like L_char_m or fragment_id in the CSV
        without spody validate complaining about a missing entry."""
        out: dict[str, Any] = {}
        for row in range(self._batch_columns_table.rowCount()):
            item = self._batch_columns_table.item(row, 0)
            if item is None:
                continue
            target = self._batch_columns_table.cellWidget(row, 1).currentText()
            mode   = self._batch_columns_table.cellWidget(row, 2).currentText()
            if target == _UNASSIGNED:
                out[item.text()] = ""
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
            # Empty target == metadata-column sentinel (col = ""). Pin
            # the combo to (unassigned) so the round-trip preserves the
            # user's choice instead of letting the name-based heuristic
            # silently reassign it on load.
            if target == "":
                target_combo.setCurrentText(_UNASSIGNED)
            else:
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
        # Any edit invalidates a previous validate result; the badge
        # is cleared so the user is not misled into trusting a stale OK.
        if self._validate_badge.text():
            self._validate_badge.setText("")
        # Live preview is cheap (~30 fields, <1 ms format); we refresh
        # on every keystroke without debouncing.
        self._refresh_preview()

    def _validate_field(self, key: str) -> None:
        """Run the registered range check (if any) on a single field
        and turn the line edit red on failure. Empty fields are NOT
        an error: they are emitted-as-absent at `to_dict` time and
        spody catches genuinely missing required keys at validate.

        Numeric parsing is driven by what the registered validator
        function expects, not by the widget's QValidator (float
        fields no longer have one -- see `_add_float`)."""
        validator = _VALIDATORS.get(key)
        w = self._widgets.get(key)
        if validator is None or not isinstance(w, QLineEdit):
            return
        text = w.text().strip()
        base_tip = _TOOLTIPS.get(key, "")
        if not text:
            w.setStyleSheet("")
            w.setToolTip(base_tip)
            return
        try:
            # Integer field iff the widget has a QIntValidator (the
            # only Qt validator we still attach); everything else is a
            # float field.
            v = int(text) if isinstance(w.validator(), QIntValidator) else float(text)
            err = validator(v)
        except (ValueError, TypeError):
            err = "not a valid number"
        if err:
            w.setStyleSheet(_INVALID_QSS)
            w.setToolTip(f"{base_tip}\n\n⚠ {err}" if base_tip else f"⚠ {err}")
        else:
            w.setStyleSheet("")
            w.setToolTip(base_tip)

    def _apply_tooltips(self) -> None:
        """Push the per-field descriptions from `_TOOLTIPS` onto each
        registered widget. Called once at the end of __init__ so it
        covers every field built by the section builders."""
        for key, text in _TOOLTIPS.items():
            w = self._widgets.get(key)
            if w is None:
                continue
            if isinstance(w, tuple):       # vec3 (three QLineEdits)
                for le in w:
                    le.setToolTip(text)
            elif isinstance(w, dict):      # checkbox set
                for cb in w.values():
                    cb.setToolTip(text)
            else:
                w.setToolTip(text)

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

        # output.interval_s only applies to mode == "fixed"; in step
        # mode the field is hidden in the UI but the underlying widget
        # may still hold a stale value -- drop it from the emitted TOML
        # so the file matches what the user sees.
        if flat.get("output.mode") == "step":
            flat.pop("output.interval_s", None)

        # Output stream paths: the form uses checkbox + auto-naming, so
        # the five `output.<stream>` keys are stored as booleans in
        # `flat`. Replace each True with the resolved path
        # `<output_dir>/<sim_name><suffix>`; drop the falses. The
        # auxiliary `output.output_dir` key is kept so the GUI can
        # round-trip the user's choice on the next Load (spody.exe
        # ignores it).
        paths = self._resolved_output_paths()
        for key in _OUTPUT_FILE_SUFFIX:
            flat.pop(key, None)
            if key in paths:
                flat[key] = paths[key]

        # Resolve cases_file from the source path + frame combo. The
        # form has a SINGLE path widget (always showing the user-picked
        # source); the TOML carries three batch keys whose contract is:
        #
        #   cases_source_file = the path the user chose (= widget text)
        #   cases_frame       = "icrf" | "ric" (what frame the source is in)
        #   cases_file        = what spody.exe actually reads
        #                       == cases_source_file when icrf
        #                       == <stem>_wrt_icrf.csv when ric (the
        #                          rotated copy the GUI writes at Generate)
        #
        # The triple is emitted regardless of mode so loading is
        # symmetric and the schema is self-describing: any reader can
        # tell from cases_frame alone whether cases_file is a direct
        # copy or a derived rotation. spody.exe ignores cases_frame and
        # cases_source_file today (parser only reads keys it knows);
        # they also reserve the schema for a future engine-side RIC
        # handler that would no longer need the GUI-side rotation.
        source = flat.pop("batch.cases_source_file", None)
        frame  = flat.pop("batch.cases_frame", "icrf")
        if source:
            flat["batch.cases_source_file"] = source
            flat["batch.cases_frame"]       = frame
            if frame in self._ROTATING_FRAMES:
                p = Path(source)
                flat["batch.cases_file"] = str(
                    p.with_name(f"{p.stem}_wrt_icrf.csv"))
            else:
                flat["batch.cases_file"] = source

        result = _explode_dotted(flat)

        # [batch.columns] comes from the dynamic table, not from a flat
        # widget key; inject it only when batch is enabled and at least
        # one column has a target assigned.
        if self._batch_check.isChecked():
            cols = self._batch_columns_to_dict()
            if cols:
                result.setdefault("batch", {})["columns"] = cols
            # Mirror the form's single name + output-folder fields
            # into the batch section so spody.exe's batch dispatcher
            # finds the keys where it expects them. The form used to
            # show duplicate widgets here; the mirror keeps the on-
            # disk TOML schema unchanged while the UI surfaces one
            # field. Skip the mirror when the source field is empty
            # so a missing simulation.name surfaces as the same
            # validate error it always did, instead of being masked
            # by an empty string in batch.name.
            sim = result.get("simulation", {})
            if isinstance(sim, dict) and sim.get("name"):
                result.setdefault("batch", {})["name"] = sim["name"]
            out = result.get("output", {})
            if isinstance(out, dict) and out.get("output_dir"):
                result.setdefault("batch", {})["output_dir"] = out["output_dir"]

        # Pass-through for any top-level section we don't render at all.
        for k, v in self._passthrough.items():
            result.setdefault(k, v)

        # Notes: top-level string, not a table, so it sits at the
        # outermost scope rather than under any [section]. Emitted
        # only when non-empty -- an empty note would leave a
        # `notes = ""` artefact in every TOML the form produces.
        notes_text = self._notes_edit.toPlainText().strip()
        if notes_text:
            result["notes"] = notes_text
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

            # Normalise the [batch] schema for the load path: the form
            # has ONE path widget (`batch.cases_source_file`) backing
            # both modes. For ICRF / legacy TOMLs the user's chosen
            # path lives in `cases_file`; redirect it so the source
            # widget gets populated. RIC TOMLs already carry the path
            # in `cases_source_file`; their `cases_file` is the derived
            # copy (recomputed by to_dict) and we just drop it from the
            # flat view so it doesn't try to find a non-existent widget.
            #
            # Back-compat for the name/output-folder consolidation:
            # legacy TOMLs (and hand-written ones) may carry
            # `batch.name` / `batch.output_dir` without populating
            # the matching `simulation.name` / `output.output_dir`.
            # The form no longer renders separate batch widgets, so
            # we promote the batch values into the unified slots
            # when those are empty -- otherwise simulation.name
            # would silently stay blank after a load.
            batch_in = data.get("batch")
            if isinstance(batch_in, dict):
                batch_in = dict(batch_in)
                if batch_in.get("cases_frame") not in self._ROTATING_FRAMES:
                    if ("cases_file" in batch_in
                            and "cases_source_file" not in batch_in):
                        batch_in["cases_source_file"] = batch_in["cases_file"]
                batch_in.pop("cases_file", None)

                sim_in = dict(data.get("simulation") or {})
                if batch_in.get("name") and not sim_in.get("name"):
                    sim_in["name"] = batch_in["name"]
                    data = {**data, "simulation": sim_in}
                out_in = dict(data.get("output") or {})
                if batch_in.get("output_dir") and not out_in.get("output_dir"):
                    out_in["output_dir"] = batch_in["output_dir"]
                    data = {**data, "output": out_in}

                # Drop name/output_dir from the batch view so the
                # flat-load pass doesn't try to find a widget that
                # no longer exists; the values are now resolved
                # through simulation/output and will be re-emitted
                # under batch by to_dict.
                batch_in.pop("name", None)
                batch_in.pop("output_dir", None)
                data = {**data, "batch": batch_in}

            # Normalise [output]: the five stream paths in the TOML
            # become booleans (presence -> True). If the TOML doesn't
            # carry `output.output_dir`, derive a best-guess from the
            # first non-empty stream path; if all paths are bare names
            # the output_dir stays empty (= TOML's own dir).
            output_in = data.get("output")
            if isinstance(output_in, dict):
                output_in = dict(output_in)
                derived_dir = output_in.get("output_dir", "")
                for key in ("csv_file", "bin_file", "accelerations_file",
                            "events_log", "log_file"):
                    val = output_in.get(key, "")
                    if isinstance(val, str) and val:
                        output_in[key] = True
                        if not derived_dir:
                            d = os.path.dirname(val)
                            if d:
                                derived_dir = d
                    else:
                        output_in[key] = False
                output_in["output_dir"] = derived_dir
                data = {**data, "output": output_in}

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

            # Top-level `notes` string: optional, no section. Loaded
            # outside the flat-section pass since _flatten_dotted is
            # scoped to _FORM_OWNED_TOP sections and would lose it.
            notes_raw = data.get("notes", "")
            if isinstance(notes_raw, str):
                self._notes_edit.setPlainText(notes_raw)
        finally:
            self._loading = False
        self.clear_modified()
        # Preview was suppressed during _loading; sync it now.
        self._refresh_preview()
        if self._validate_badge.text():
            self._validate_badge.setText("")

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
            self._clear_preview()
        finally:
            self._loading = False
        self.set_current_path(None)
        self.clear_modified()
        self._refresh_preview()
        if self._validate_badge.text():
            self._validate_badge.setText("")

    def write_to(self, path: Path) -> bool:
        """Serialise the form via to_dict + write_toml. Returns True
        on success; surfaces I/O / value errors via a message box.

        When the cases_frame combo reads "ric", also performs the
        RIC -> ICRF rotation on the source CSV at this point: rotating
        at Generate (rather than at Run) keeps the side effect visible
        on disk and lets a downstream `spody batch <toml>` from the
        terminal work without re-opening the GUI."""
        from .toml_io import write_toml
        try:
            data = self.to_dict()
        except ValueError as exc:
            QMessageBox.critical(self, "Generate failed", f"{path}\n{exc}")
            return False

        # Rotation is driven by the live combo state, not by the TOML
        # dict (the GUI deliberately no longer persists cases_frame /
        # cases_source_file -- see to_dict). If rotation fails the TOML
        # is also not written so the user sees one atomic failure.
        if (self._batch_check.isChecked()
                and self._batch_cases_frame_combo.currentText()
                    in self._ROTATING_FRAMES):
            if not self._rotate_ric_cases(path, data):
                return False

        try:
            write_toml(path, data)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Generate failed", f"{path}\n{exc}")
            return False
        self.set_current_path(path)
        self.clear_modified()
        return True

    def _resolve_ric_inputs(self, toml_path: Path | None,
                            data: dict[str, Any]) -> tuple | None:
        """Shared resolver for the rotating-frame pipeline (used by
        both the Generate-time rotation and the live preview).

        Returns a tuple `(src_abs, out_abs, r_ref, v_ref, pos_cols,
        vel_cols)` on success, or `None` after a logged warning when
        any precondition fails. Warnings are surfaced through
        QMessageBox by `_rotate_ric_cases` (Generate path) and silenced
        in the preview path -- preview callers should branch on `None`
        and show a placeholder instead.

        `toml_path` may be None when the form has never been saved;
        in that case the cases path must already be absolute, otherwise
        we can't resolve it.

        Despite the historical name, this handles every entry in
        `_ROTATING_FRAMES` (RIC, LVLH, ...) -- error strings cite the
        actually-selected frame so the user isn't told "RIC" when the
        combo says LVLH.
        """
        import numpy as np

        frame_label = self._batch_cases_frame_combo.currentText().upper()

        src_raw = self._batch_cases_edit.text().strip()
        if not src_raw:
            return ("error", f"Pick a {frame_label}-frame source CSV in "
                             f"the cases_file field first.")
        src = Path(src_raw)
        if not src.is_absolute():
            if toml_path is None:
                return ("error",
                        "The cases_file path is relative but the TOML "
                        "has not been saved yet; save first or pick an "
                        "absolute path.")
            src = (toml_path.parent / src).resolve()
        if not src.is_file():
            return ("error", f"cases_file not found:\n  {src}")
        out = src.with_name(f"{src.stem}_wrt_icrf.csv")

        init = data.get("initial_state", {})
        r_ref = init.get("position_km")
        v_ref = init.get("velocity_kms")
        if not (isinstance(r_ref, list) and len(r_ref) == 3 and
                isinstance(v_ref, list) and len(v_ref) == 3):
            return ("error",
                    "[initial_state].position_km and velocity_kms must "
                    "be fully filled -- they define the reference orbit "
                    "whose axes the cases CSV uses.")

        cols_map: dict[str, Any] = data.get("batch", {}).get("columns", {})
        pos_by_idx: dict[int, str] = {}
        vel_by_idx: dict[int, str] = {}
        for csv_col, spec in cols_map.items():
            target = spec["target"] if isinstance(spec, dict) else spec
            for i in range(3):
                if target == f"initial_state.position_km[{i}]":
                    pos_by_idx[i] = csv_col
                elif target == f"initial_state.velocity_kms[{i}]":
                    vel_by_idx[i] = csv_col

        def _full_triplet(d: dict[int, str]) -> tuple[str, str, str] | None:
            if len(d) == 3 and all(i in d for i in range(3)):
                return (d[0], d[1], d[2])
            if not d:
                return None
            return ...   # partial sentinel

        pos_cols = _full_triplet(pos_by_idx)
        vel_cols = _full_triplet(vel_by_idx)
        if pos_cols is ... or vel_cols is ...:
            return ("error",
                    "Partial position or velocity triplet in "
                    "[batch.columns]: rotation needs all 3 components "
                    "of a vector or none.")
        if pos_cols is None and vel_cols is None:
            # Help the user see WHICH columns landed where: when a row
            # they thought was wired is actually emitted as the metadata
            # sentinel (target = ""), the listing makes the discrepancy
            # obvious without needing to inspect the generated TOML.
            mapped = []
            for csv_col, spec in cols_map.items():
                t = spec["target"] if isinstance(spec, dict) else spec
                mapped.append(f"{csv_col} -> {t!r}")
            mapped_str = ("\n  " + "\n  ".join(mapped)
                          if mapped else " (none)")
            return ("error",
                    f"No state columns wired in [batch.columns]. "
                    f"{frame_label} rotation has nothing to do -- "
                    f"either map position/velocity columns or switch "
                    f"frame to icrf.\nCurrent [batch.columns]:"
                    f"{mapped_str}")

        return ("ok", src, out,
                np.asarray(r_ref, dtype=float),
                np.asarray(v_ref, dtype=float),
                pos_cols, vel_cols)

    def _rotate_ric_cases(self, toml_path: Path, data: dict[str, Any]) -> bool:
        """Generate <stem>_wrt_icrf.csv from the user's source CSV in
        whichever rotating frame the combo currently shows (RIC or
        LVLH). Returns True on success or False after a message box.
        Reuses `_resolve_ric_inputs` for input validation -- the
        resolution rules (state-column triplets, reference orbit) are
        frame-agnostic; only the rotation kernel differs."""
        frame = self._batch_cases_frame_combo.currentText()
        try:
            _basis_fn, rotate_csv_fn = self._rotation_helpers(frame)
        except ValueError as exc:
            QMessageBox.warning(self, "Rotation",
                                f"unsupported cases_frame {frame!r}: {exc}")
            return False

        resolved = self._resolve_ric_inputs(toml_path, data)
        if resolved is None or resolved[0] == "error":
            QMessageBox.warning(self, f"{frame.upper()} rotation",
                                resolved[1] if resolved else "unknown error")
            return False
        _, src, out, r_ref, v_ref, pos_cols, vel_cols = resolved

        try:
            info = rotate_csv_fn(
                src, out,
                r_ref_km=r_ref, v_ref_kms=v_ref,
                pos_columns=pos_cols, vel_columns=vel_cols,
            )
        except (FileNotFoundError, ValueError) as exc:
            QMessageBox.critical(self, f"{frame.upper()} rotation failed",
                                 str(exc))
            return False

        # Brief on-the-fly status so the user gets immediate feedback
        # without an extra dialog.
        self._batch_cases_status.setText(
            f"({frame.upper()} -> ICRF: {info['n_rows']} rows written to "
            f"{out.name})")
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

    def _on_validate_clicked(self) -> None:
        """Write the current form to a temp TOML next to the current
        file (or to the OS temp dir if there is no current file) and
        run `spody validate` synchronously. Show the verdict on the
        badge -- green '✓ OK' or red '✗ <error>' with the full
        message in the tooltip. Does NOT touch the terminal pane;
        this is a quick check without committing to a Run."""
        if self._store is None:
            self._set_badge("(no SettingsStore wired)", ok=False)
            return
        spody_bin = self._store.spody_binary()
        if not spody_bin or not Path(spody_bin).exists():
            self._set_badge("(spody binary not set)", ok=False,
                            tip="Set Settings > Paths > spody binary first.")
            return
        # Hard guard: same check the menu run uses. spody validate is
        # tolerant of missing files in some edge cases, but most TOML
        # inputs reference the harmonics / ephemeris paths, and the
        # parser stats them eagerly -- safer to refuse outright and
        # offer the wizard.
        from .setup_wizard import require_data_ready
        if not require_data_ready(self._store, self, "Validate"):
            self._set_badge("(data not ready)", ok=False,
                            tip="Open Settings > Setup wizard...")
            return

        try:
            data = self.to_dict()
        except ValueError as exc:
            self._set_badge("✗ form has invalid values", ok=False,
                            tip=str(exc))
            return

        # Write next to the current file when possible so relative
        # paths inside the TOML (harmonics_file, ephemeris.file,
        # batch.cases_file) resolve the same way spody does at run time.
        if self._current_path is not None:
            tmp_dir = self._current_path.parent
            prefix  = ".spody_validate_"
        else:
            tmp_dir = Path(tempfile.gettempdir())
            prefix  = "spody_validate_"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", prefix=prefix,
            dir=str(tmp_dir), delete=False, encoding="utf-8",
        ) as fp:
            tmp_path = Path(fp.name)
            from .toml_io import format_toml
            fp.write(format_toml(data))

        try:
            r = subprocess.run(
                [spody_bin, "validate", str(tmp_path)],
                capture_output=True, text=True, timeout=30,
                cwd=str(tmp_dir),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._set_badge("✗ validate failed to launch", ok=False,
                            tip=str(exc))
            tmp_path.unlink(missing_ok=True)
            return
        finally:
            tmp_path.unlink(missing_ok=True)

        if r.returncode == 0:
            self._set_badge("✓ valid", ok=True,
                            tip=(r.stdout or "spody validate exit 0").strip())
        else:
            # spody writes one-line "error: ..." messages to stderr;
            # the last non-empty line is what we want as the short msg.
            err_lines = [
                ln for ln in (r.stderr or r.stdout).strip().splitlines() if ln
            ]
            short = err_lines[-1] if err_lines else f"exit {r.returncode}"
            # Strip a leading "error: <file>: " for the badge so it fits.
            badge_msg = short
            if ": " in badge_msg:
                badge_msg = "✗ " + badge_msg.split(": ", 2)[-1]
            else:
                badge_msg = "✗ " + badge_msg
            self._set_badge(badge_msg[:160], ok=False, tip=short)

    def _set_badge(self, text: str, *, ok: bool, tip: str = "") -> None:
        self._validate_badge.setText(text)
        self._validate_badge.setStyleSheet(self._BADGE_OK if ok else self._BADGE_BAD)
        self._validate_badge.setToolTip(tip)

    def _refresh_preview(self) -> None:
        """Update the read-only TOML preview to reflect the current
        form. Robust to in-progress invalid input: if to_dict raises,
        we show a one-line placeholder and the preview catches up on
        the next valid edit."""
        if not hasattr(self, "_preview"):
            return   # called during __init__ before the preview exists
        try:
            from .toml_io import format_toml
            text = format_toml(self.to_dict())
        except ValueError as exc:
            text = f"# (form has invalid values: {exc})"
        # Preserve the user's scroll position so the preview doesn't
        # jump to the top on every keystroke.
        scrollbar = self._preview.verticalScrollBar()
        pos = scrollbar.value()
        self._preview.setPlainText(text)
        scrollbar.setValue(pos)

    # ==================================================================
    # Internals
    # ==================================================================
    def _reset_widgets(self) -> None:
        """Restore every widget to a sensible blank state so a fresh
        load doesn't leave fields from the previous file behind."""
        # Reset unit combos first so they don't reconvert the value
        # we are about to clear from the QLineEdit they shadow.
        for key, combo in self._scaled_unit_combos.items():
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)
            self._scaled_unit_prev[key] = combo.currentText()
        for key, w in self._widgets.items():
            if isinstance(w, QLineEdit):
                w.clear()
            elif isinstance(w, QCheckBox):
                w.setChecked(False)
            elif isinstance(w, _AssetCombo):
                # Wipe any leftover '(custom)' entries from the prior
                # load, then re-scan the data dir so the dropdown is
                # current for the next file.
                self._refresh_asset_combo(key)
                w.setCurrentIndex(-1)
            elif isinstance(w, QComboBox):
                w.setCurrentIndex(0)
            elif isinstance(w, tuple):   # vec3
                for le in w:
                    le.clear()
            elif isinstance(w, dict):    # checkbox set
                for cb in w.values():
                    cb.setChecked(False)
        # Notes: standalone widget outside self._widgets (top-level
        # string, not a dotted-path field). Cleared here so a fresh
        # load doesn't inherit the previous TOML's notes.
        if hasattr(self, "_notes_edit"):
            self._notes_edit.clear()

    def _widget_value(self, key: str, w: Any) -> Any:
        if isinstance(w, QLineEdit):
            text = w.text().strip()
            if not text:
                return None
            # Type discrimination: int fields keep their QIntValidator
            # (we want the cap behaviour on thread_number); float fields
            # are tracked by name in self._float_keys; everything else
            # is a string.
            if isinstance(w.validator(), QIntValidator):
                try:    return int(text)
                except ValueError:
                    raise ValueError(f"'{key}' is not a valid integer: {text!r}")
            if key in self._float_keys:
                try:    v = float(text)
                except ValueError:
                    raise ValueError(f"'{key}' is not a valid number: {text!r}")
                # Unit-scaled fields (e.g. duration_s with a min/h/days
                # combo) multiply by the current combo factor so the
                # emitted TOML always carries SI.
                combo = self._scaled_unit_combos.get(key)
                if combo is not None:
                    v *= _DURATION_FACTORS[combo.currentText()]
                return v
            return text
        if isinstance(w, QCheckBox):
            return bool(w.isChecked())
        if isinstance(w, _AssetCombo):
            # Asset combos store the absolute path in item userData;
            # currentData() returns None when the combo is empty
            # (e.g. no assets downloaded yet for the current body).
            data = w.currentData()
            return str(data) if data else None
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
            # Unit-scaled fields auto-pick a display unit so a user who
            # types 86400 in seconds gets it back as 1.0 days on the
            # next load instead of a long second-count.
            combo = self._scaled_unit_combos.get(key)
            if combo is not None and isinstance(value, (int, float)):
                seconds = float(value)
                unit = "s"
                for u in _DURATION_UNIT_AUTOPICK:
                    if abs(seconds) >= _DURATION_FACTORS[u]:
                        unit = u
                        break
                combo.blockSignals(True)
                idx = combo.findText(unit)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                combo.blockSignals(False)
                self._scaled_unit_prev[key] = unit
                w.setText(_tidy_float(seconds / _DURATION_FACTORS[unit]))
                return
            if isinstance(value, float):
                w.setText(_tidy_float(value))
            elif isinstance(value, int):
                w.setText(str(value))
            else:
                w.setText("" if value is None else str(value))
            return
        if isinstance(w, QCheckBox):
            w.setChecked(bool(value))
            return
        if isinstance(w, _AssetCombo):
            # Loading a path: try to match by userData (absolute path);
            # if no entry matches, surface it as a '(custom)' entry so
            # the user sees their original path is still intact.
            target = "" if value is None else str(value)
            if not target:
                w.setCurrentIndex(-1)
                return
            # Resolve to an absolute path so it matches whatever the
            # asset combo would have stored. Relative paths from the
            # TOML are resolved against the TOML's own directory --
            # `self._current_path` was set by `load_from`.
            from pathlib import Path as _Path
            abs_target = target
            tp = _Path(target)
            if not tp.is_absolute() and self._current_path is not None:
                try:
                    abs_target = str((self._current_path.parent / tp).resolve())
                except OSError:
                    abs_target = target
            idx = w.findData(abs_target)
            if idx >= 0:
                w.setCurrentIndex(idx)
                return
            # Fall back to the original (unresolved) string so the user
            # sees exactly what was in the TOML.
            w.add_custom_path(target)
            return
        if isinstance(w, QComboBox):
            idx = w.findText(str(value))
            if idx >= 0:
                w.setCurrentIndex(idx)
            return
        if isinstance(w, tuple):   # vec3
            if isinstance(value, (list, tuple)) and len(value) == 3:
                for le, x in zip(w, value):
                    le.setText(_tidy_float(float(x)))
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


def _tidy_float(v: float) -> str:
    """Display-friendly form for a parsed float (used when populating a
    field from a loaded TOML).

    Python's `repr(float)` is the shortest round-trippable form but
    its exponent has a leading-zero quirk: `repr(1e-5) == '1e-05'`,
    `repr(2e8) == '200000000.0'`. The leading zero on the exponent is
    cosmetic noise people don't write in TOML files, so we strip it.
    Everything else is left exactly as `repr` produces it."""
    if v == 0.0:
        return "0.0"
    s = repr(v)
    # "1e-05" -> "1e-5", "1.5e+07" -> "1.5e+7"
    return re.sub(r"e([+-])0+(\d)", r"e\1\2", s)


def _read_csv_preview(path: Path, max_rows: int = 10
                       ) -> tuple[list[str], list[list[str]], int]:
    """Header + first `max_rows` data rows + total data-row count.

    Matches spody's own loose CSV reader: leading `#` lines are
    treated as comments, blank lines are skipped, fields are trimmed.
    The full file is scanned to count rows (needed for the
    'first N of M' status line); cases.csv files are typically
    small (~1000 rows max) so this is cheap."""
    header: list[str] = []
    rows:   list[list[str]] = []
    total = 0
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            cells = [c.strip() for c in stripped.split(",")]
            if not header:
                header = cells
                continue
            total += 1
            if len(rows) < max_rows:
                rows.append(cells)
    return header, rows, total


# Backward-compat shim: kept so any caller that only needed the header
# keeps working without an awkward signature change.
def _read_csv_header(path: Path) -> list[str]:
    header, _, _ = _read_csv_preview(path, max_rows=0)
    return header


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
