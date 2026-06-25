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

from .central_bodies import known_central_body_names

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
# get individual checkboxes, not a combo list. `CENTRAL_BODIES` is
# sourced from the registry in central_bodies.py so adding a body
# (Phase 2 = Earth) is one line there and shows up in the form
# automatically; the other tuples are short enough to extend in place.
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


CENTRAL_BODIES   = known_central_body_names()
DYNAMICS_MODELS  = ("high_fidelity", "cr3bp")
# Per-model valid frames -- the [initial_state] frame combo is filtered
# to the entries valid under the currently-selected dynamics_model.
# `central_body_fixed` is GUI-only: the user types the [initial_state]
# vectors (cart or kep angles) in the central body's body-fixed basis
# (Earth ITRS, Moon PA), and the form rotates them to ICRF at TOML
# emit so the engine sees plain `central_inertial`. The combo entry
# is auto-hidden for bodies without a registered orientation provider.
FRAMES_BY_MODEL: dict[str, tuple[str, ...]] = {
    "high_fidelity": ("central_inertial", "central_body_fixed"),
    "cr3bp":         ("synodic_rotating",),
}
# Curated CR3BP primary pairs. Mirror of CR3BP_PAIRS in
# src/toml_input.c; adding a pair on either side without the other is
# a load-time validate error from the engine, so the two lists must
# stay in lockstep.
CR3BP_PAIRS: tuple[tuple[str, str], ...] = (
    ("Earth", "Moon"),
)
# Primary-primary separation (km) for each curated pair. Needed by the
# Keplerian <-> Cartesian swap in [initial_state] under CR3BP so the
# GUI can call spopy.inertial_to_synodic without going back to the
# engine. Mirrors EARTH_MOON_DISTANCE_KM in spody-core's spody_const.h
# and the lookup_cr3bp_pair table in src/toml_input.c.
_CR3BP_L_KM: dict[tuple[str, str], float] = {
    ("Earth", "Moon"): 384400.0,
}
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
    "simulation.dynamics_model":     "Physics model. 'high_fidelity' = Cowell perturbations around a central body (needs spacecraft / force_model / ephemeris). 'cr3bp' = Circular Restricted 3-Body Problem in the synodic rotating frame (needs only [cr3bp] + [initial_state]).",
    "simulation.et_start_s":         "Start epoch in TDB seconds past J2000. Required by high_fidelity (anchors ephemeris / EOP lookups); ignored by cr3bp (autonomous).",
    "simulation.duration_s":         "Propagation duration in seconds; > 0.",
    "cr3bp.primary_1":               "Bigger primary (heavier body). GM looked up from the body table; primary-primary separation L from the curated CR3BP_PAIRS in spody-core.",
    "cr3bp.primary_2":               "Smaller primary. Must form a pair with primary_1 that is registered in the curated CR3BP table (today: Earth+Moon).",
    "spacecraft.mass_kg":            "Dry mass in kilograms; must be > 0.",
    "spacecraft.srp.area_m2":        "SRP cross-section in m²; A/m derived as area_m2 / mass_kg.",
    "spacecraft.srp.am_srp":         "A/m directly in m²/kg; alternative to area_m2 (XOR).",
    "spacecraft.srp.Cr":             "Reflectivity coefficient (1 = absorb, 2 = mirror).",
    "debris.am_srp":                 "Area-to-mass ratio in m²/kg; > 0.",
    "debris.Cr":                     "Reflectivity coefficient (used only when SRP is enabled).",
    "initial_state.frame":           "Inertial reference frame. v0 supports only 'central_inertial'.",
    "initial_state.kind":            "Cartesian (default) gives [x, y, z] and [vx, vy, vz] directly. Keplerian gives six classical orbital elements + a reference body; the engine (and the form's swap helper) converts to Cartesian on the fly.",
    "initial_state.reference_body":  "Which body the Keplerian elements reference. HF: 'central' (implicit). CR3BP: 'primary_1' (bigger) or 'primary_2' (smaller); required, no default.",
    "initial_state.semi_major_axis_km": "Semi-major axis a, in km; > 0 for elliptical orbits.",
    "initial_state.eccentricity":    "Eccentricity e in [0, 1). Hyperbolic / parabolic orbits are not supported via Keplerian input.",
    "initial_state.inclination_deg": "Inclination i, in degrees, in [0, 180].",
    "initial_state.raan_deg":        "Right ascension of the ascending node, in degrees. Folded into argument of periapsis when the orbit is equatorial.",
    "initial_state.arg_periapsis_deg": "Argument of periapsis omega, in degrees. Folded into 0 when the orbit is circular.",
    "initial_state.anomaly_deg":     "Anomaly value at t = 0, in degrees. Interpreted as true OR mean depending on anomaly_type below.",
    "initial_state.anomaly_type":    "Is anomaly_deg true anomaly (most natural) or mean anomaly (catalog convention; converted via Kepler's equation).",
    "initial_state.position_km":     "[x, y, z] position in km, central-body inertial frame.",
    "initial_state.velocity_kms":    "[vx, vy, vz] velocity in km/s, same frame as position.",
    "force_model.central_body":      "Central body of the propagation. Supported: " + ", ".join(f"'{n}'" for n in CENTRAL_BODIES) + ".",
    "force_model.harmonics_file":    "Spherical-harmonics coefficient file (e.g. GRGM1200B).",
    "force_model.harmonics_degree":  "Truncation degree; ≥ 2 and ≤ the N declared in the chosen harmonics file (1200 for GRGM1200B Moon, 2190 for EIGEN-6C4 Earth; schema cap is 2200).",
    "force_model.eop_file":          "IERS Earth Orientation Parameters file (finals2000A.all). Required when central_body = 'Earth'.",
    "force_model.iau2006_dir":       "Directory containing the IAU 2006/2000A series tables (tab5.2a.txt, tab5.2b.txt, tab5.2d.txt). Required when central_body = 'Earth'.",
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
def _harm_deg(v: int)  -> str: return "" if 2 <= v <= 2200 else "must be in [2, 2200] (schema cap; file maximum is the effective ceiling: 1200 for GRGM1200B, 2190 for EIGEN-6C4)"
def _frac01 (v: float) -> str: return "" if 0.0 <= v <= 1.0 else "must be in [0, 1]"

def _ecc01    (v: float) -> str: return "" if 0.0 <= v < 1.0 else "must be in [0, 1) (hyperbolic / parabolic not supported)"
def _inc_deg  (v: float) -> str: return "" if 0.0 <= v <= 180.0 else "must be in [0, 180] deg"

_VALIDATORS: dict[str, Any] = {
    "simulation.duration_s":          _pos,
    "spacecraft.mass_kg":             _pos,
    "spacecraft.srp.area_m2":         _pos,
    "spacecraft.srp.am_srp":          _pos,
    "spacecraft.srp.Cr":              _nonneg,
    "debris.am_srp":                  _pos,
    "debris.Cr":                      _nonneg,
    "force_model.harmonics_degree":   _harm_deg,
    "initial_state.semi_major_axis_km": _pos,
    "initial_state.eccentricity":     _ecc01,
    "initial_state.inclination_deg":  _inc_deg,
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
    "semi_major_axis_km": "km",
    "inclination_deg":    "deg",
    "raan_deg":           "deg",
    "arg_periapsis_deg":  "deg",
    "anomaly_deg":        "deg",
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
      * Signal `runRequested(str)` -- emitted by the RUN button with
        the subcommand the form thinks should run ("propagate" or
        "batch"); MainWindow does the save-before-run gating.
    """

    modificationChanged = Signal(bool)
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
        "initial_state", "cr3bp", "force_model", "ephemeris",
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

        # [initial_state] representation cache. Key: (kind, frame)
        # tuple covering the four (cartesian / keplerian) x (central_
        # inertial / central_body_fixed) combinations. Value: a dict
        # mapping widget keys to their values for that representation,
        # or absent when the corresponding representation has not been
        # computed (or failed to compute). Populated on every
        # `editingFinished` event of the IC widgets: the current
        # visible block is the ground truth, the other 3 are computed
        # from it via spopy (one conversion deep per representation),
        # cached, and re-used unchanged by kind / frame toggles --
        # avoiding the cascading ULP drift the old toggle-time
        # conversion suffered after a few back-and-forth flips.
        # Invalidated wholesale by changes to et_start_s,
        # central_body, dynamics_model, reference_body, anomaly_type.
        self._ic_cache: dict[tuple[str, str], dict[str, Any]] = {}

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

        # Top row: current file + Validate / RUN plus a small badge
        # showing the last validate result (✓ OK / ✗ <error>) without
        # going to the terminal. Load... and the old Generate button
        # both moved out: Load lives in the global top bar (MainWindow);
        # Generate's job is now done by the same top bar's Save / Save
        # As, which write through `write_to` -> `_on_form_loaded_or_saved`
        # the way Generate used to (recents update, working dir adopt,
        # analysis tree refresh).
        self._path_label = QLabel("(no file)")
        self._path_label.setStyleSheet("color: gray;")
        btn_val  = QPushButton("Validate")
        btn_run  = QPushButton("RUN")
        btn_run.setStyleSheet(
            "QPushButton { background-color: #2ea043; color: white; "
            "font-weight: bold; padding: 4px 16px; border-radius: 3px; }"
            "QPushButton:hover  { background-color: #3fb950; }"
            "QPushButton:pressed{ background-color: #238636; }"
        )
        btn_val.clicked.connect(self._on_validate_clicked)
        btn_run.clicked.connect(self._on_run_clicked)

        self._validate_badge = QLabel("")
        self._validate_badge.setMinimumWidth(160)

        top_row = QHBoxLayout()
        top_row.addWidget(self._path_label, 1)
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
        # Per-model section groups: HF needs object + force_model +
        # ephemeris; CR3BP needs just [cr3bp]. Both branches share
        # [initial_state] (with a model-filtered frame combo).
        # Kept on `self` so _on_dynamics_model_changed can flip
        # visibility without re-walking the layout.
        self._object_group     = self._build_object()
        body_lay.addWidget(self._object_group)
        body_lay.addWidget(self._build_initial_state())
        self._cr3bp_group      = self._build_cr3bp()
        body_lay.addWidget(self._cr3bp_group)
        self._force_model_group = self._build_force_model()
        body_lay.addWidget(self._force_model_group)
        self._ephemeris_group   = self._build_ephemeris()
        body_lay.addWidget(self._ephemeris_group)
        body_lay.addWidget(self._build_integrator())
        body_lay.addWidget(self._build_output())
        body_lay.addWidget(self._build_events())
        body_lay.addWidget(self._build_batch())
        body_lay.addWidget(self._build_notes())
        body_lay.addStretch(1)
        scroll.setWidget(body)

        # Sync HF/CR3BP visibility with the dynamics_model combo's
        # default selection (high_fidelity), so a freshly-opened form
        # starts in the legacy layout.
        self._on_dynamics_model_changed(
            self._widgets["simulation.dynamics_model"].currentText())

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
        self._sim_form = QFormLayout(g)
        f = self._sim_form
        self._add_string(f, "simulation.name",       "name")
        # dynamics_model selects which downstream sections are needed.
        # Switching this combo hides / shows [cr3bp] vs the HF stack
        # (object / force_model / ephemeris) -- see
        # _on_dynamics_model_changed below.
        self._add_enum(f, "simulation.dynamics_model", "dynamics_model",
                       DYNAMICS_MODELS)
        # ET stays the only thing the TOML carries; the UTC cell next
        # to it is a typing aid driven by `→` / `←` convert buttons.
        self._add_et_with_utc(f, "simulation.et_start_s", "et_start_s")
        # Remember the et_start_s row so we can mark it optional when
        # the model is autonomous (cr3bp).
        self._sim_et_row = f.rowCount() - 1
        self._add_duration_seconds(f, "simulation.duration_s", "duration_s")

        dm_combo = self._widgets["simulation.dynamics_model"]
        dm_combo.currentTextChanged.connect(self._on_dynamics_model_changed)
        # et_start_s drives R_icrf_to_bf; touching it invalidates the
        # cached BF / inertial cross-derivations so the next toggle
        # re-derives from the current visible block. dyn_model also
        # invalidates because cr3bp flips which mu / synodic chain
        # _ic_block_to_cart_inertial picks.
        et_w = self._widgets.get("simulation.et_start_s")
        if isinstance(et_w, QLineEdit):
            et_w.editingFinished.connect(self._invalidate_ic_cache)
        dm_combo.currentTextChanged.connect(
            lambda _t: self._invalidate_ic_cache())
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
        """Two-flavour [initial_state] form: a `kind` combo at the top
        swaps between the legacy Cartesian block (position_km +
        velocity_kms) and the Keplerian block (six classical elements
        + reference_body + anomaly_type). Switching `kind` after the
        user has typed values converts in place via spopy so nothing
        is lost; the swap is silent if any source field is empty /
        unparseable.

        The reference_body combo's items are filtered by the active
        dynamics_model -- HF defaults to 'central' (the only option);
        CR3BP exposes 'primary_1' and 'primary_2'."""
        g = QGroupBox("[initial_state]")
        v_outer = QVBoxLayout(g)
        v_outer.setContentsMargins(8, 6, 8, 6)

        # Top form row: frame + kind. Always visible.
        top = QFormLayout()
        top.setContentsMargins(0, 0, 0, 0)
        v_outer.addLayout(top)
        # Frame combo: items are filtered by the active dynamics model.
        # HF accepts `central_inertial` (engine-native) or
        # `central_body_fixed` (GUI-only: the form rotates the typed
        # values to ICRF at TOML emit so the engine sees
        # `central_inertial`). CR3BP only allows `synodic_rotating`.
        # Switching between `central_inertial` and `central_body_fixed`
        # does a live rotation of the currently typed cart / kep
        # values (same UX as the cartesian <-> keplerian swap).
        self._add_enum(top, "initial_state.frame", "frame",
                       FRAMES_BY_MODEL["high_fidelity"])
        self._add_enum(top, "initial_state.kind", "kind",
                       ("cartesian", "keplerian"))
        frame_combo = self._widgets["initial_state.frame"]
        frame_combo.currentTextChanged.connect(self._on_input_frame_changed)
        # Tracks the last frame so the converter knows which direction
        # to rotate. Synced to the combo on every successful swap.
        self._input_frame_prev: str = "central_inertial"

        # --- Cartesian block --------------------------------------------------
        self._init_cart_block = QWidget()
        cart_form = QFormLayout(self._init_cart_block)
        cart_form.setContentsMargins(0, 0, 0, 0)
        self._add_vec3(cart_form, "initial_state.position_km",  "position_km")
        self._add_vec3(cart_form, "initial_state.velocity_kms", "velocity_kms")
        v_outer.addWidget(self._init_cart_block)

        # --- Keplerian block --------------------------------------------------
        self._init_kep_block = QWidget()
        kep_form = QFormLayout(self._init_kep_block)
        kep_form.setContentsMargins(0, 0, 0, 0)
        # reference_body: cascade visibility / contents from dynamics_model.
        # Seed with the HF-only entry; _on_dynamics_model_changed reflows it.
        self._add_enum(kep_form, "initial_state.reference_body",
                       "reference_body", ("central",))
        self._add_float(kep_form, "initial_state.semi_major_axis_km",
                        "semi_major_axis_km")
        self._add_float(kep_form, "initial_state.eccentricity",
                        "eccentricity")
        self._add_float(kep_form, "initial_state.inclination_deg",
                        "inclination_deg")
        self._add_float(kep_form, "initial_state.raan_deg", "raan_deg")
        self._add_float(kep_form, "initial_state.arg_periapsis_deg",
                        "arg_periapsis_deg")
        self._add_float(kep_form, "initial_state.anomaly_deg", "anomaly_deg")
        self._add_enum(kep_form, "initial_state.anomaly_type",
                       "anomaly_type", ("true", "mean"))
        v_outer.addWidget(self._init_kep_block)

        # Default kind = cartesian -> hide kep block.
        self._init_kep_block.setVisible(False)

        # Wire the kind combo: on change, convert (best-effort) and swap.
        kind_combo = self._widgets["initial_state.kind"]
        kind_combo.currentTextChanged.connect(self._on_init_kind_changed)
        # Wire editingFinished on every cart / kep widget so each
        # finalised edit recomputes the four-representation cache.
        # Sub-table inside the form-level _ic_cache so toggles between
        # (kind, frame) become lossless lookups rather than chained
        # spopy conversions that drift after a few flips.
        for vec_key in ("initial_state.position_km",
                         "initial_state.velocity_kms"):
            trio = self._widgets.get(vec_key)
            if isinstance(trio, tuple):
                for le in trio:
                    le.editingFinished.connect(self._on_ic_field_finished)
        for k_key in ("initial_state.semi_major_axis_km",
                       "initial_state.eccentricity",
                       "initial_state.inclination_deg",
                       "initial_state.raan_deg",
                       "initial_state.arg_periapsis_deg",
                       "initial_state.anomaly_deg"):
            w = self._widgets.get(k_key)
            if isinstance(w, QLineEdit):
                w.editingFinished.connect(self._on_ic_field_finished)
        # Anomaly type and reference body re-interpret the cached
        # keplerian numbers, so flipping either invalidates the
        # cache wholesale (the user has to touch a field to repopulate
        # -- same UX as today, but consistent).
        for combo_key in ("initial_state.anomaly_type",
                           "initial_state.reference_body"):
            combo = self._widgets.get(combo_key)
            if isinstance(combo, QComboBox):
                combo.currentTextChanged.connect(
                    lambda _t: self._invalidate_ic_cache())
        return g

    def _build_cr3bp(self) -> QGroupBox:
        """[cr3bp]: pick a primary pair from the curated table. The
        combos cascade: primary_2 is filtered to the pairs registered
        with the currently-selected primary_1, so a user cannot pick a
        pair the engine would reject. Visible only when
        dynamics_model = 'cr3bp'."""
        g = QGroupBox("[cr3bp]")
        f = QFormLayout(g)

        primaries_1 = sorted({p1 for p1, _ in CR3BP_PAIRS})
        self._add_enum(f, "cr3bp.primary_1", "primary_1", tuple(primaries_1))
        # primary_2 starts wide; _on_cr3bp_primary_1_changed narrows it.
        self._add_enum(f, "cr3bp.primary_2", "primary_2",
                       tuple(sorted({p2 for _, p2 in CR3BP_PAIRS})))
        p1_combo = self._widgets["cr3bp.primary_1"]
        p1_combo.currentTextChanged.connect(self._on_cr3bp_primary_1_changed)
        self._on_cr3bp_primary_1_changed(p1_combo.currentText())
        return g

    def _on_cr3bp_primary_1_changed(self, p1: str) -> None:
        """Refresh primary_2 options to only those that form a
        registered pair with the just-selected primary_1."""
        valid = [p2 for q1, p2 in CR3BP_PAIRS if q1 == p1]
        combo = self._widgets.get("cr3bp.primary_2")
        if not isinstance(combo, QComboBox):
            return
        prev = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        for name in valid:
            combo.addItem(name)
        idx = combo.findText(prev)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)

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
        # Custom row: int field + a sticky 'max num / max model' info
        # label + a 'Suggest...' button that shells out to the new C
        # CLI `spody maxhgdegree`. The CLI reads the actual Cnm/Snm
        # from the harmonics file and walks degree-by-degree against
        # the ULP noise floor seeded by the J2 contribution -- way
        # more accurate than a Kaula-rule estimate (which was the
        # first cut and got bounced for predicting too low a degree
        # on rough fields like GRGM1200B over the Moon).
        hd_edit = QLineEdit()
        # Cap matches the engine's TOML-schema ceiling. The per-body
        # effective max (e.g. 1200 for GRGM1200B Moon, 2190 for
        # EIGEN-6C4 Earth) is enforced by the engine at load time
        # against the actual harmonics file. Suggest... helper queries
        # the file directly via `spody maxhgdegree`.
        hd_edit.setValidator(QIntValidator(2, 2200, hd_edit))
        hd_edit.textChanged.connect(self._touch)
        hd_edit.textChanged.connect(
            lambda _t: self._validate_field("force_model.harmonics_degree"))
        self._widgets["force_model.harmonics_degree"] = hd_edit
        # Sticky info label, populated on each Suggest run. Hidden
        # before the first run since the numbers depend on the chosen
        # harmonics file AND the current initial state -- showing a
        # value before the user has filled either would be misleading.
        self._hd_info = QLabel("")
        self._hd_info.setStyleSheet("color: gray;")
        self._hd_info.setVisible(False)
        hd_suggest = QPushButton("Suggest...")
        hd_suggest.setToolTip(
            "Run the C engine to compute the largest harmonics degree "
            "whose Cnm/Snm contribution at the current orbit altitude "
            "still rises above double-precision noise. Going beyond it "
            "costs CPU (per-step work is O(N^2)) with no measurable "
            "accuracy gain.")
        hd_suggest.clicked.connect(self._suggest_harmonics_degree)
        hd_row = QHBoxLayout()
        hd_row.setContentsMargins(0, 0, 0, 0)
        hd_row.addWidget(hd_edit, 1)
        hd_row.addWidget(self._hd_info)
        hd_row.addWidget(hd_suggest)
        f.addRow("harmonics_degree", _hwrap(hd_row))

        # Earth-only fields. Visible (and written to TOML) only when
        # central_body == "Earth"; for the Moon (and any other body
        # without an EOP / IAU 2006 dependency) the engine schema
        # forbids them. We keep them in [force_model] for proximity
        # with the rotation provider they configure.
        self._fm_eop_row = self._add_path(
            f, "force_model.eop_file", "eop_file",
            "IERS EOP (*.all *.dat *.txt);;All Files (*)",
        )
        self._fm_iau_row = self._add_path(
            f, "force_model.iau2006_dir", "iau2006_dir",
            "", pick_dir=True,
        )

        self._add_strlist_checks(f, "force_model.third_bodies", "third_bodies",
                                 THIRD_BODIES_ALL)
        self._add_bool (f, "force_model.srp", "srp")

        # Hook visibility of the Earth-only rows to the central_body
        # combo. Initial sync uses the combo's current value (set by
        # the enum widget's default selection -- Moon).
        self._fm_force_form = f
        cb_combo = self._widgets["force_model.central_body"]
        cb_combo.currentTextChanged.connect(self._on_central_body_changed)
        cb_combo.currentTextChanged.connect(
            lambda _t: self._invalidate_ic_cache())
        self._on_central_body_changed(cb_combo.currentText())
        return g

    def _on_central_body_changed(self, body: str) -> None:
        """Show / hide the Earth-only [force_model] fields, and
        auto-fill the path widgets with their canonical data-dir
        defaults the first time the user switches to Earth.

        When the engine sees `central_body = "Earth"` it requires
        `eop_file` and `iau2006_dir`; for any other body those fields
        must be absent. We mirror that by hiding the rows so the user
        never fills them for a non-Earth run.

        Defaults come from `<data_dir>/eop/finals2000A.all` and
        `<data_dir>/iau2006/` (the relpaths defined by the wizard's
        EOP_FILE + IAU2006_TAB_* assets). We only seed empty widgets:
        a user-typed override survives a Moon -> Earth -> Moon -> Earth
        round-trip.
        """
        if not hasattr(self, "_fm_force_form"):
            return  # called too early during construction
        is_earth = (body.strip().lower() == "earth")
        for row in (self._fm_eop_row, self._fm_iau_row):
            self._fm_force_form.setRowVisible(row, is_earth)

        if is_earth and self._store is not None:
            data_root = self._store.data_dir()
            eop_w = self._widgets.get("force_model.eop_file")
            iau_w = self._widgets.get("force_model.iau2006_dir")
            if isinstance(eop_w, QLineEdit) and not eop_w.text().strip():
                eop_w.setText(str(data_root / "eop" / "finals2000A.all"))
            if isinstance(iau_w, QLineEdit) and not iau_w.text().strip():
                iau_w.setText(str(data_root / "iau2006"))
        # Refresh the input_frame combo: the BF radio's label tracks the
        # central body's bf_frame_name (ITRS for Earth, PA for Moon).
        self._refresh_input_frame_availability()

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
        # Lunar BF rotation reads from this ephemeris; swapping
        # ephemerides invalidates the cached BF representations.
        eph_combo = self._widgets.get("ephemeris.file")
        if isinstance(eph_combo, QComboBox):
            eph_combo.currentIndexChanged.connect(
                lambda _i: self._invalidate_ic_cache())
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
    # [initial_state] kind swap (cartesian <-> keplerian)
    # ------------------------------------------------------------------
    def _on_init_kind_changed(self, kind: str) -> None:
        """Swap visibility between the cart / kep blocks. Tries the
        IC cache first (the four-representation snapshot kept in
        sync by `_on_ic_field_finished`): a cache hit writes the
        destination block verbatim, with NO spopy conversion at
        toggle time -- so back-and-forth flips no longer accumulate
        ULP drift. Falls back to the legacy on-the-fly conversion
        when the cache is empty for the destination view (first
        toggle of the session, or after an invalidating change).

        During load the conversion is skipped (the loader populates
        the destination block from the TOML directly); only the
        visibility toggle still runs so the right block is on screen."""
        is_kep = (kind == "keplerian")
        if not self._loading:
            # Toggle handlers NEVER re-seed the cache (re-seeding
            # here would re-derive the destination view through
            # cart_inertial every time, defeating the whole point
            # of the cache -- BF<->ICRF<->BF would still drift two
            # conversions per round-trip). The cache is filled by
            # editingFinished only; if there's no cached entry for
            # the destination view we fall back to legacy
            # conversion + repopulate the cache once from that.
            _, current_frame = self._ic_current_view()
            if not self._apply_ic_cache_to_widgets(kind, current_frame):
                if is_kep:
                    self._convert_cart_to_kep()
                else:
                    self._convert_kep_to_cart()
                # Capture the freshly-converted destination block
                # so subsequent toggles round-trip cleanly. The
                # visibility flip below has not happened yet -- we
                # use an explicit view rather than _ic_current_view
                # because the just-written widgets ARE the new
                # destination block.
                self._seed_ic_cache_from_view((kind, current_frame))
        self._init_cart_block.setVisible(not is_kep)
        self._init_kep_block.setVisible(is_kep)
        self._touch()

    # ------------------------------------------------------------------
    # [initial_state] four-representation cache
    # ------------------------------------------------------------------
    # Toggles between (cartesian / keplerian) x (central_inertial /
    # central_body_fixed) used to chain spopy conversions on every
    # click, which accumulated ULP noise after a few back-and-forth
    # flips. With the cache, every finalised edit re-derives all four
    # representations once and the toggle handlers just look up the
    # destination view -- no further conversions, no compounding drift.

    _IC_VARIANTS: tuple[tuple[str, str], ...] = (
        ("cartesian", "central_inertial"),
        ("cartesian", "central_body_fixed"),
        ("keplerian", "central_inertial"),
        ("keplerian", "central_body_fixed"),
    )

    _IC_CART_KEYS = (
        "initial_state.position_km",
        "initial_state.velocity_kms",
    )
    _IC_KEP_KEYS = (
        "initial_state.semi_major_axis_km",
        "initial_state.eccentricity",
        "initial_state.inclination_deg",
        "initial_state.raan_deg",
        "initial_state.arg_periapsis_deg",
        "initial_state.anomaly_deg",
    )

    def _invalidate_ic_cache(self) -> None:
        """Drop every cached IC representation. Triggered by changes
        to settings the cached values depend on (et_start_s,
        central_body, dynamics_model, anomaly_type, reference_body).
        The next finalised edit repopulates."""
        self._ic_cache.clear()

    def _on_ic_field_finished(self) -> None:
        """User finished editing a cart / kep field (focus loss or
        Enter): the currently-visible block is the ground truth, so
        re-derive every other representation from it and snapshot
        all four into `_ic_cache`. No-op during load (the legacy
        load path writes the widgets directly and we don't want a
        partial intermediate state to overwrite the loaded values
        in the cache)."""
        if self._loading:
            return
        self._seed_ic_cache_from_visible()

    def _ic_current_view(self) -> tuple[str, str]:
        """Return the (kind, frame) of the currently visible block,
        defaulting to the legacy ('cartesian', 'central_inertial')
        when the combos are not yet built."""
        kind_combo  = self._widgets.get("initial_state.kind")
        frame_combo = self._widgets.get("initial_state.frame")
        kind  = (kind_combo.currentText()
                 if isinstance(kind_combo, QComboBox) else "cartesian")
        frame = (frame_combo.currentText()
                 if isinstance(frame_combo, QComboBox)
                 else "central_inertial")
        return kind, frame

    def _snapshot_ic_block(self, kind: str) -> dict | None:
        """Read the kep / cart widgets for `kind` and return a dict
        of `{widget_key: parsed_value}`, or None when any required
        field is empty / unparseable (the cache stays as it was)."""
        if kind == "cartesian":
            r = self._read_vec3("initial_state.position_km")
            v = self._read_vec3("initial_state.velocity_kms")
            if r is None or v is None:
                return None
            return {
                "initial_state.position_km":  [float(x) for x in r],
                "initial_state.velocity_kms": [float(x) for x in v],
            }
        # Keplerian
        out: dict[str, float] = {}
        for k in self._IC_KEP_KEYS:
            v = self._read_float(k)
            if v is None:
                return None
            out[k] = float(v)
        return out

    def _ic_block_to_cart_inertial(self, block: dict, kind: str, frame: str
                                    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Convert `block` (in the given (kind, frame)) to canonical
        cartesian-inertial via spopy. Returns (r, v) or None on
        failure (missing mu, missing et_start, missing rotation
        provider, degenerate kep)."""
        import math
        import numpy as np
        if kind == "cartesian":
            r = np.asarray(block["initial_state.position_km"],  dtype=float)
            v = np.asarray(block["initial_state.velocity_kms"], dtype=float)
        else:
            ctx = self._resolve_kep_mu_and_synodic()
            if ctx is None:
                return None
            mu_ref, _synodic = ctx
            anom_combo = self._widgets.get("initial_state.anomaly_type")
            anom_type = (anom_combo.currentText()
                         if isinstance(anom_combo, QComboBox) else "true")
            try:
                from spopy import keplerian_to_cartesian, mean_to_true_anom
                nu = math.radians(block["initial_state.anomaly_deg"])
                if anom_type == "mean":
                    nu = mean_to_true_anom(nu, block["initial_state.eccentricity"])
                r, v = keplerian_to_cartesian(
                    block["initial_state.semi_major_axis_km"],
                    block["initial_state.eccentricity"],
                    math.radians(block["initial_state.inclination_deg"]),
                    math.radians(block["initial_state.raan_deg"]),
                    math.radians(block["initial_state.arg_periapsis_deg"]),
                    nu, mu_ref)
                r = np.asarray(r, dtype=float)
                v = np.asarray(v, dtype=float)
            except (ValueError, ZeroDivisionError):
                return None
        if frame == "central_body_fixed":
            et = self._form_et_start()
            if et is None:
                return None
            R_icrf_to_bf = self._resolve_bf_rotation(et)
            if R_icrf_to_bf is None:
                return None
            R_bf_to_icrf = R_icrf_to_bf.T
            r = R_bf_to_icrf @ r
            v = R_bf_to_icrf @ v
        return r, v

    def _ic_block_from_cart_inertial(self, r: "np.ndarray", v: "np.ndarray",
                                      kind: str, frame: str) -> dict | None:
        """Inverse of `_ic_block_to_cart_inertial`: build the
        (kind, frame) block dict from canonical cartesian-inertial.
        None when prerequisites are missing for the destination
        (mu / et_start / rotation provider) or kep extraction
        fails."""
        import math
        import numpy as np
        if frame == "central_body_fixed":
            et = self._form_et_start()
            if et is None:
                return None
            R_icrf_to_bf = self._resolve_bf_rotation(et)
            if R_icrf_to_bf is None:
                return None
            r_dst = R_icrf_to_bf @ r
            v_dst = R_icrf_to_bf @ v
        else:
            r_dst = np.asarray(r, dtype=float)
            v_dst = np.asarray(v, dtype=float)
        if kind == "cartesian":
            return {
                "initial_state.position_km":  [float(x) for x in r_dst],
                "initial_state.velocity_kms": [float(x) for x in v_dst],
            }
        ctx = self._resolve_kep_mu_and_synodic()
        if ctx is None:
            return None
        mu_ref, _synodic = ctx
        try:
            from spopy import cartesian_to_keplerian, true_to_mean_anom
            el = cartesian_to_keplerian(r_dst, v_dst, mu_ref)
        except (ValueError, ZeroDivisionError):
            return None
        anom_combo = self._widgets.get("initial_state.anomaly_type")
        anom_type = (anom_combo.currentText()
                     if isinstance(anom_combo, QComboBox) else "true")
        nu = float(el["true_anom_rad"])
        anom = (true_to_mean_anom(nu, float(el["ecc"]))
                if anom_type == "mean" else nu)
        return {
            "initial_state.semi_major_axis_km": float(el["sma_km"]),
            "initial_state.eccentricity":       float(el["ecc"]),
            "initial_state.inclination_deg":    math.degrees(float(el["inc_rad"])),
            "initial_state.raan_deg":           math.degrees(float(el["raan_rad"])),
            "initial_state.arg_periapsis_deg":  math.degrees(float(el["argp_rad"])),
            "initial_state.anomaly_deg":        math.degrees(anom),
        }

    def _seed_ic_cache_from_visible(self) -> None:
        """Re-derive all four IC representations from the currently
        visible block (defined by `_ic_current_view`) and store them
        in `_ic_cache`. Use the explicit-view variant
        `_seed_ic_cache_from_view` when the caller knows the source
        view is NOT what `_ic_current_view` would report (typically
        a toggle handler where the combo has already moved to the
        destination)."""
        self._seed_ic_cache_from_view(self._ic_current_view())

    def _seed_ic_cache_from_view(self, view: tuple[str, str]) -> None:
        """Snapshot the `view = (kind, frame)` block (visible or
        not -- we read the kind's widgets directly), then derive
        the other three representations from it via spopy and cache
        all four. Silent on incomplete / unparseable / degenerate
        source: the cache stays as it was."""
        kind, frame = view
        src = self._snapshot_ic_block(kind)
        if src is None:
            return
        # Wipe so a stale entry doesn't survive a re-seed where the
        # other-3 conversions fail.
        self._ic_cache.clear()
        self._ic_cache[(kind, frame)] = src
        ri_vi = self._ic_block_to_cart_inertial(src, kind, frame)
        if ri_vi is None:
            return
        ri, vi = ri_vi
        for (k, f) in self._IC_VARIANTS:
            if (k, f) == (kind, frame):
                continue
            block = self._ic_block_from_cart_inertial(ri, vi, k, f)
            if block is not None:
                self._ic_cache[(k, f)] = block

    def _apply_ic_cache_to_widgets(self, kind: str, frame: str) -> bool:
        """Write the cached (kind, frame) representation into the
        cart / kep widgets without going through spopy. Returns True
        on hit + write, False when the cache has no entry for that
        view (the caller then falls back to the legacy convert-on-
        toggle path). Signal-blocking is done at the widget level
        so the textChanged-driven `_touch` still fires (the user's
        intent IS modification)."""
        block = self._ic_cache.get((kind, frame))
        if block is None:
            return False
        if kind == "cartesian":
            r = block.get("initial_state.position_km")
            v = block.get("initial_state.velocity_kms")
            if r is None or v is None:
                return False
            self._write_vec3("initial_state.position_km",  r)
            self._write_vec3("initial_state.velocity_kms", v)
        else:
            for k in self._IC_KEP_KEYS:
                v = block.get(k)
                if v is None:
                    return False
                self._write_float(k, float(v))
        return True

    def _resolve_kep_mu_and_synodic(self) -> tuple[float, dict] | None:
        """Look up the reference-body mu (+ CR3BP geometry when needed)
        for the current dynamics_model + reference_body combo. Returns
        `(mu_km3_s2, synodic_ctx)` where synodic_ctx is either None
        (HF) or a dict {mu1, mu2, L, primary_index} that the two
        conversion helpers feed into spopy.synodic_to_inertial /
        inertial_to_synodic. Returns None when anything cannot be
        resolved (unknown body, missing CR3BP pair, ...)."""
        dm_combo = self._widgets.get("simulation.dynamics_model")
        dyn_model = (dm_combo.currentText()
                     if isinstance(dm_combo, QComboBox) else "high_fidelity")
        if dyn_model == "cr3bp":
            ref_combo = self._widgets.get("initial_state.reference_body")
            ref = ref_combo.currentText() if isinstance(ref_combo, QComboBox) else ""
            if ref not in ("primary_1", "primary_2"):
                return None
            p1_combo = self._widgets.get("cr3bp.primary_1")
            p2_combo = self._widgets.get("cr3bp.primary_2")
            p1 = p1_combo.currentText() if isinstance(p1_combo, QComboBox) else ""
            p2 = p2_combo.currentText() if isinstance(p2_combo, QComboBox) else ""
            from .central_bodies import resolve_central_body
            spec1 = resolve_central_body(p1)
            spec2 = resolve_central_body(p2)
            L = _CR3BP_L_KM.get((p1, p2))
            if spec1 is None or spec2 is None or L is None:
                return None
            primary_index = 1 if ref == "primary_1" else 2
            mu_ref = spec1.mu_km3_s2 if primary_index == 1 else spec2.mu_km3_s2
            return mu_ref, {
                "mu1": spec1.mu_km3_s2, "mu2": spec2.mu_km3_s2,
                "L":   L,               "primary_index": primary_index,
            }
        # HF path: mu = central body's GM.
        cb_combo = self._widgets.get("force_model.central_body")
        cb_name = cb_combo.currentText() if isinstance(cb_combo, QComboBox) else ""
        from .central_bodies import resolve_central_body
        spec = resolve_central_body(cb_name)
        if spec is None:
            return None
        return spec.mu_km3_s2, None

    def _read_vec3(self, key: str) -> "np.ndarray | None":
        import numpy as np  # local: avoid loading numpy at form-import time
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple) or len(widgets) != 3:
            return None
        out = []
        for w in widgets:
            txt = w.text().strip()
            if not txt:
                return None
            try:
                out.append(float(txt))
            except ValueError:
                return None
        return np.array(out)

    def _write_vec3(self, key: str, vals) -> None:
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple) or len(widgets) != 3:
            return
        for w, v in zip(widgets, vals):
            w.setText(repr(float(v)))

    def _read_float(self, key: str) -> float | None:
        w = self._widgets.get(key)
        if not isinstance(w, QLineEdit):
            return None
        txt = w.text().strip()
        if not txt:
            return None
        try:
            return float(txt)
        except ValueError:
            return None

    def _write_float(self, key: str, v: float) -> None:
        w = self._widgets.get(key)
        if isinstance(w, QLineEdit):
            w.setText(repr(float(v)))

    def _convert_cart_to_kep(self) -> None:
        """Cartesian -> Keplerian. For CR3BP this first rotates the
        synodic state back into the reference primary's inertial frame
        before extracting the elements."""
        import math
        r = self._read_vec3("initial_state.position_km")
        v = self._read_vec3("initial_state.velocity_kms")
        if r is None or v is None:
            return
        ctx = self._resolve_kep_mu_and_synodic()
        if ctx is None:
            return
        mu_ref, synodic_ctx = ctx
        try:
            from spopy import (cartesian_to_keplerian, synodic_to_inertial,
                               true_to_mean_anom)
            if synodic_ctx is not None:
                r_in, v_in = synodic_to_inertial(
                    r, v, synodic_ctx["mu1"], synodic_ctx["mu2"],
                    synodic_ctx["L"], synodic_ctx["primary_index"])
            else:
                r_in, v_in = r, v
            el = cartesian_to_keplerian(r_in, v_in, mu_ref)
        except (ValueError, ZeroDivisionError):
            return
        self._write_float("initial_state.semi_major_axis_km", el["sma_km"])
        self._write_float("initial_state.eccentricity",       el["ecc"])
        self._write_float("initial_state.inclination_deg",    math.degrees(el["inc_rad"]))
        self._write_float("initial_state.raan_deg",           math.degrees(el["raan_rad"]))
        self._write_float("initial_state.arg_periapsis_deg",  math.degrees(el["argp_rad"]))
        # Honour the current anomaly_type so a round-trip keplerian ->
        # cartesian -> keplerian preserves the user's choice between
        # true and mean.
        anom_combo = self._widgets.get("initial_state.anomaly_type")
        anom_type = (anom_combo.currentText()
                     if isinstance(anom_combo, QComboBox) else "true")
        nu_rad = el["true_anom_rad"]
        if anom_type == "mean":
            anom_rad = true_to_mean_anom(nu_rad, el["ecc"])
        else:
            anom_rad = nu_rad
        self._write_float("initial_state.anomaly_deg", math.degrees(anom_rad))

    def _convert_kep_to_cart(self) -> None:
        """Keplerian -> Cartesian. For CR3BP, the primary-centered
        inertial state is then mapped into the synodic frame at t = 0."""
        import math
        sma = self._read_float("initial_state.semi_major_axis_km")
        ecc = self._read_float("initial_state.eccentricity")
        inc = self._read_float("initial_state.inclination_deg")
        raan = self._read_float("initial_state.raan_deg")
        argp = self._read_float("initial_state.arg_periapsis_deg")
        anom = self._read_float("initial_state.anomaly_deg")
        if (sma is None or ecc is None or inc is None or raan is None
                or argp is None or anom is None):
            return
        ctx = self._resolve_kep_mu_and_synodic()
        if ctx is None:
            return
        mu_ref, synodic_ctx = ctx
        anom_combo = self._widgets.get("initial_state.anomaly_type")
        anom_type = (anom_combo.currentText()
                     if isinstance(anom_combo, QComboBox) else "true")
        try:
            from spopy import (keplerian_to_cartesian, inertial_to_synodic,
                               mean_to_true_anom)
            anom_rad = math.radians(anom)
            if anom_type == "mean":
                anom_rad = mean_to_true_anom(anom_rad, ecc)
            r_in, v_in = keplerian_to_cartesian(
                sma, ecc, math.radians(inc), math.radians(raan),
                math.radians(argp), anom_rad, mu_ref)
            if synodic_ctx is not None:
                r, v = inertial_to_synodic(
                    r_in, v_in, synodic_ctx["mu1"], synodic_ctx["mu2"],
                    synodic_ctx["L"], synodic_ctx["primary_index"])
            else:
                r, v = r_in, v_in
        except (ValueError, ZeroDivisionError):
            return
        self._write_vec3("initial_state.position_km",  r)
        self._write_vec3("initial_state.velocity_kms", v)

    def _resolve_bf_rotation(self, et_s: float) -> "np.ndarray | None":
        """Return R_icrf_to_bf at ET `et_s` for the form's currently
        selected central body, or None on any failure (missing EOP /
        ephemeris, unsupported body, CR3BP run). Mirrors the
        orientation pipeline `central_bodies._earth_orientation` /
        `_moon_orientation` use at scene-render time, so the BF
        defined here matches the one the analysis tab shows."""
        dm_combo = self._widgets.get("simulation.dynamics_model")
        dyn_model = (dm_combo.currentText()
                     if isinstance(dm_combo, QComboBox) else "high_fidelity")
        if dyn_model == "cr3bp":
            return None
        cb_combo = self._widgets.get("force_model.central_body")
        cb_name = (cb_combo.currentText()
                   if isinstance(cb_combo, QComboBox) else "")
        from .central_bodies import resolve_central_body
        spec = resolve_central_body(cb_name)
        if spec is None or spec.bf_orientation is None:
            return None
        # Earth orientation reads EOP from data_dir and ignores its
        # ephemeris argument; Moon orientation needs the ephemeris.
        # Build the latter lazily from the form's ephemeris.file
        # field; cache for subsequent calls in the same session so
        # switching frames many times does not re-load DE440 every
        # tick.
        eph = None
        if spec.name == "Moon":
            eph = self._cached_ephemeris_for_form()
            if eph is None:
                return None
        try:
            import numpy as np
            return np.asarray(spec.bf_orientation(float(et_s), eph), dtype=float)
        except Exception:
            return None

    def _cached_ephemeris_for_form(self):
        """Lazy + per-session cached spopy.Ephemeris built from the
        form's `ephemeris.file` widget value. Returns None when the
        path is empty or unreadable. Used only by the BF-rotation
        helper -- the engine loads its own copy at sim setup.

        `ephemeris.file` is an `_AssetCombo` (QComboBox subclass)
        whose visible text is just the basename of the picked file;
        the absolute path lives in the item's userData and is what
        the rest of the form (and the TOML emitter) reads via
        `_widget_value`. Querying `.text()` here used to return the
        basename and silently fail to find the file on disk."""
        eph_w = self._widgets.get("ephemeris.file")
        if isinstance(eph_w, QComboBox):
            data = eph_w.currentData()
            path = str(data).strip() if data else ""
        elif isinstance(eph_w, QLineEdit):
            path = eph_w.text().strip()
        else:
            path = ""
        if not path:
            return None
        # Cache key is the path string; switching ephemeris files in
        # the form invalidates the cache so the new one is loaded on
        # the next call.
        cached = getattr(self, "_form_ephemeris_cache", None)
        if cached is not None and cached[0] == path:
            return cached[1]
        try:
            from spopy import Ephemeris
            eph = Ephemeris(path)
        except (OSError, ValueError, ImportError):
            self._form_ephemeris_cache = (path, None)
            return None
        self._form_ephemeris_cache = (path, eph)
        return eph

    def _form_et_start(self) -> float | None:
        """Read simulation.et_start_s from the form, or None when it
        is empty / unparseable. Used by the BF conversion paths to
        evaluate R_icrf_to_bf at the run's start epoch."""
        et_w = self._widgets.get("simulation.et_start_s")
        if not isinstance(et_w, QLineEdit):
            return None
        txt = et_w.text().strip()
        if not txt:
            return None
        try:
            return float(txt)
        except ValueError:
            return None

    def _on_input_frame_changed(self, new_frame: str) -> None:
        """User flipped the [initial_state].frame combo: when the
        swap is between `central_inertial` and `central_body_fixed`,
        rotate the currently typed cart / kep values in place so the
        displayed numbers reflect the new basis. Other transitions
        (e.g. dynamics-model reflow that inserts `synodic_rotating`)
        do not rotate -- only the BF<->ICRF pair is a frame swap on
        the same physical state.

        When rotation prerequisites are missing (empty et_start_s,
        unreadable ephemeris for Moon, etc.) we still honour the
        user's combo flip and pop a one-shot warning explaining what
        was skipped -- the previous behaviour of silently reverting
        the combo to its old value made it look like the BF option
        was broken when actually et_start was just empty."""
        bf_pair = {"central_inertial", "central_body_fixed"}
        if (new_frame == self._input_frame_prev
                or new_frame not in bf_pair
                or self._input_frame_prev not in bf_pair):
            self._input_frame_prev = new_frame
            return
        # Toggle handlers NEVER re-seed the cache: each re-seed
        # would re-derive the destination via cart_inertial and
        # ICRF<->BF<->ICRF would still drift two conversions per
        # round-trip. The cache is filled by editingFinished (and
        # by the legacy-fallback path below); a cache hit here
        # writes the destination verbatim -- zero in-loop
        # conversions, lossless across repeated flips.
        kind, _ = self._ic_current_view()
        if self._apply_ic_cache_to_widgets(kind, new_frame):
            self._input_frame_prev = new_frame
            return
        # Cache miss: fall back to the legacy in-place rotation, and
        # if the prerequisites are missing surface a one-shot warning
        # so the user knows what to fix.
        et = self._form_et_start()
        if et is None:
            QMessageBox.warning(
                self, "Body-fixed rotation skipped",
                "Set simulation.et_start_s first so the form knows at "
                "which epoch to evaluate the body-fixed rotation. The "
                "frame selector was kept on '" + new_frame + "' but the "
                "values below have NOT been rotated.")
            self._input_frame_prev = new_frame
            return
        R_icrf_to_bf = self._resolve_bf_rotation(et)
        if R_icrf_to_bf is None:
            QMessageBox.warning(
                self, "Body-fixed rotation unavailable",
                "Could not evaluate the body-fixed rotation at the "
                "current et_start_s. For Moon: pick a valid DE-series "
                "ephemeris in [ephemeris].file. For Earth: make sure "
                "the EOP file exists under the wizard data dir. The "
                "frame selector was kept on '" + new_frame + "' but "
                "the values below have NOT been rotated.")
            self._input_frame_prev = new_frame
            return
        # icrf->bf when going inertial -> body_fixed; transpose for
        # the reverse. Pure rotation only (no omega x r correction);
        # matches the Slice-B plot semantics so what you see in BF
        # plots round-trips through this form unchanged.
        R = R_icrf_to_bf if new_frame == "central_body_fixed" else R_icrf_to_bf.T
        if kind == "cartesian":
            self._rotate_cart_inplace(R)
        else:
            self._rotate_kep_inplace(R)
        # The legacy fallback mutated the visible block; capture
        # the freshly-rotated destination view into the cache so a
        # subsequent toggle hits the lossless path. The combo
        # already moved to `new_frame`, so _ic_current_view() is
        # correct here.
        self._seed_ic_cache_from_visible()
        self._input_frame_prev = new_frame

    def _rotate_cart_inplace(self, R: "np.ndarray") -> None:
        """Rotate the cart widgets' (r, v) values by R. Silent if any
        component is empty / unparseable."""
        r = self._read_vec3("initial_state.position_km")
        v = self._read_vec3("initial_state.velocity_kms")
        if r is None or v is None:
            return
        self._write_vec3("initial_state.position_km",  R @ r)
        self._write_vec3("initial_state.velocity_kms", R @ v)

    def _rotate_kep_inplace(self, R: "np.ndarray") -> None:
        """Rotate Keplerian angles by R: kep -> cart -> rotate -> kep.
        Magnitudes (a, e, i) are basis-independent and round-trip
        unchanged; RAAN / AOP / ν are recomputed in the new basis.
        Silent on conversion failure."""
        import math
        sma = self._read_float("initial_state.semi_major_axis_km")
        ecc = self._read_float("initial_state.eccentricity")
        inc = self._read_float("initial_state.inclination_deg")
        raan = self._read_float("initial_state.raan_deg")
        argp = self._read_float("initial_state.arg_periapsis_deg")
        anom = self._read_float("initial_state.anomaly_deg")
        if (sma is None or ecc is None or inc is None or raan is None
                or argp is None or anom is None):
            return
        ctx = self._resolve_kep_mu_and_synodic()
        if ctx is None:
            return
        mu_ref, _synodic = ctx
        anom_combo = self._widgets.get("initial_state.anomaly_type")
        anom_type = (anom_combo.currentText()
                     if isinstance(anom_combo, QComboBox) else "true")
        try:
            from spopy import (cartesian_to_keplerian, keplerian_to_cartesian,
                               mean_to_true_anom, true_to_mean_anom)
            nu = math.radians(anom)
            if anom_type == "mean":
                nu = mean_to_true_anom(nu, ecc)
            r, v = keplerian_to_cartesian(
                sma, ecc, math.radians(inc), math.radians(raan),
                math.radians(argp), nu, mu_ref)
            r_new = R @ r
            v_new = R @ v
            el = cartesian_to_keplerian(r_new, v_new, mu_ref)
        except (ValueError, ZeroDivisionError):
            return
        self._write_float("initial_state.semi_major_axis_km", el["sma_km"])
        self._write_float("initial_state.eccentricity",       el["ecc"])
        self._write_float("initial_state.inclination_deg",
                          math.degrees(el["inc_rad"]))
        self._write_float("initial_state.raan_deg",
                          math.degrees(el["raan_rad"]))
        self._write_float("initial_state.arg_periapsis_deg",
                          math.degrees(el["argp_rad"]))
        nu_new = el["true_anom_rad"]
        anom_new = (true_to_mean_anom(nu_new, el["ecc"])
                    if anom_type == "mean" else nu_new)
        self._write_float("initial_state.anomaly_deg",
                          math.degrees(anom_new))

    def _refresh_input_frame_availability(self) -> None:
        """Single source of truth for the [initial_state].frame combo
        items. Rebuilds based on dynamics_model + central body:

          - CR3BP                          -> ("synodic_rotating",)
          - HF + body has BF orientation   -> ("central_inertial",
                                              "central_body_fixed")
          - HF + body without BF provider  -> ("central_inertial",)

        The previous selection survives the rebuild when it's still
        a valid item; otherwise the combo snaps to the first entry
        and `_input_frame_prev` is reset to match so a subsequent
        BF<->ICRF flip does not try to rotate from a stale state."""
        frame_combo = self._widgets.get("initial_state.frame")
        if not isinstance(frame_combo, QComboBox):
            return
        dm_combo = self._widgets.get("simulation.dynamics_model")
        dyn_model = (dm_combo.currentText()
                     if isinstance(dm_combo, QComboBox) else "high_fidelity")
        if dyn_model == "cr3bp":
            items: tuple[str, ...] = ("synodic_rotating",)
        else:
            cb_combo = self._widgets.get("force_model.central_body")
            cb_name = (cb_combo.currentText()
                       if isinstance(cb_combo, QComboBox) else "")
            from .central_bodies import resolve_central_body
            spec = resolve_central_body(cb_name)
            bf_available = (spec is not None
                            and spec.bf_orientation is not None)
            items = (("central_inertial", "central_body_fixed")
                     if bf_available else ("central_inertial",))
        prev = frame_combo.currentText()
        frame_combo.blockSignals(True)
        frame_combo.clear()
        for v in items:
            frame_combo.addItem(v)
        idx = frame_combo.findText(prev)
        if idx >= 0:
            frame_combo.setCurrentIndex(idx)
        else:
            frame_combo.setCurrentIndex(0)
            self._input_frame_prev = items[0]
        frame_combo.blockSignals(False)

    def _on_dynamics_model_changed(self, model: str) -> None:
        """Reflow the per-model sections + filter the frame combo + grey
        out HF-only optional toggles. HF shows the legacy stack (object
        / force_model / ephemeris) and hides [cr3bp]; CR3BP swaps the
        visibility and disables the output / event toggles whose engine
        path rejects CR3BP runs. Called once after construction (sync
        with the default selection) and on every user change of the
        dynamics_model combo."""
        if not hasattr(self, "_object_group"):
            return   # called too early during construction
        is_hf = (model == "high_fidelity")
        self._object_group.setVisible(is_hf)
        self._force_model_group.setVisible(is_hf)
        self._ephemeris_group.setVisible(is_hf)
        self._cr3bp_group.setVisible(not is_hf)
        # Frame combo is owned exclusively by
        # `_refresh_input_frame_availability` (called below) -- having
        # this method ALSO rebuild it caused races where the BF entry
        # would oscillate in / out of the list across consecutive
        # dynamics-model and kind swaps.

        # reference_body combo: HF only ever uses "central" (implicit
        # central body); CR3BP exposes primary_1 / primary_2 with no
        # default ("--" placeholder forces an explicit pick).
        ref_combo = self._widgets.get("initial_state.reference_body")
        if isinstance(ref_combo, QComboBox):
            ref_combo.blockSignals(True)
            prev = ref_combo.currentText()
            ref_combo.clear()
            if is_hf:
                ref_combo.addItem("central")
            else:
                ref_combo.addItem("primary_1")
                ref_combo.addItem("primary_2")
            idx = ref_combo.findText(prev)
            ref_combo.setCurrentIndex(idx if idx >= 0 else 0)
            ref_combo.blockSignals(False)

        # Optional knobs the engine rejects under CR3BP -- grey out and
        # uncheck so the user cannot enable them and so a stale toggle
        # from a prior HF scenario doesn't carry over.
        acc_cb = self._widgets.get("output.accelerations_file")
        if isinstance(acc_cb, QCheckBox):
            acc_cb.setEnabled(is_hf)
            if not is_hf and acc_cb.isChecked():
                acc_cb.setChecked(False)
            acc_cb.setToolTip(
                _TOOLTIPS.get("output.accelerations_file", "") if is_hf
                else "Disabled in CR3BP (per-force breakdown is HF-only)")
        if hasattr(self, "_events_check"):
            self._events_check.setEnabled(is_hf)
            if not is_hf and self._events_check.isChecked():
                self._events_check.setChecked(False)
            self._events_check.setToolTip(
                "" if is_hf
                else "Disabled in CR3BP (no Sun in this model -- "
                     "primary-impact events are wired automatically)")
        # Input-frame combo: BF only makes sense in HF with a registered
        # orientation provider; CR3BP runs and bare-central-body specs
        # silently fall back to inertial.
        self._refresh_input_frame_availability()

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
                  pick_dir: bool = False) -> QWidget:
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
        field = _hwrap(row)
        layout.addRow(label, field)
        return field

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

    def _suggest_harmonics_degree(self) -> None:
        """Shell out to `spody maxhgdegree <file> <x> <y> <z>` and
        populate the harmonics_degree row's info label with the
        result. Shows a modal progress dialog during the subprocess
        (the harmonics-file load takes ~1-2 s on GRGM1200B) and a
        Yes/No confirm dialog with the recommendation when the CLI
        finishes.

        Going via the C engine instead of a Python-side estimate
        means we read the *actual* Cnm/Snm coefficient magnitudes
        from the file rather than rely on a Kaula-rule bound. The
        Kaula estimate (first cut) under-predicted the useful degree
        on rough fields like GRGM1200B over the Moon and produced
        runs that diverged from SPICE; the file-driven walk is the
        right answer.
        """
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QProgressDialog

        store = self._store
        if store is None:
            QMessageBox.warning(
                self, "Suggest max harmonics degree",
                "Settings store not wired (running in a standalone form?).")
            return
        spody_bin = store.spody_binary()
        if not spody_bin or not Path(spody_bin).is_file():
            QMessageBox.warning(
                self, "Suggest max harmonics degree",
                "Set the path to spody.exe in Settings > Paths first.")
            return

        # Harmonics file path -- the asset combo stores the absolute
        # path as currentData(); empty when the user has not picked
        # anything yet.
        hfile_w = self._widgets.get("force_model.harmonics_file")
        hfile_path = hfile_w.currentData() if hfile_w is not None else None
        if not hfile_path:
            QMessageBox.warning(
                self, "Suggest max harmonics degree",
                "Pick a harmonics file in [force_model] first.")
            return

        # Initial-state position triplet from the vec3 widget.
        pos_w = self._widgets.get("initial_state.position_km")
        if not isinstance(pos_w, tuple) or len(pos_w) != 3:
            QMessageBox.warning(
                self, "Suggest max harmonics degree",
                "[initial_state].position_km is not a vec3 widget.")
            return
        try:
            pos = [float(le.text()) for le in pos_w]
        except ValueError:
            QMessageBox.warning(
                self, "Suggest max harmonics degree",
                "Fill in [initial_state].position_km first.")
            return

        # Modal indeterminate progress (range 0,0): the spody.exe load
        # is one opaque step from our side. No cancel -- aborting
        # mid-load offers no benefit over waiting the second out.
        dlg = QProgressDialog(
            "Loading harmonics file and walking degrees...",
            "", 0, 0, self)
        dlg.setWindowTitle("Suggest max harmonics degree")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setCancelButton(None)
        dlg.setValue(0)

        # QProcess kept on `self` for the duration so the lambda
        # closures see the right object; reset in on_finished.
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        out_buf = bytearray()

        def on_ready_read() -> None:
            out_buf.extend(bytes(proc.readAllStandardOutput()))

        def on_finished(exit_code: int, _exit_status) -> None:
            out_buf.extend(bytes(proc.readAllStandardOutput()))
            dlg.close()
            self._hd_suggest_proc = None
            text = out_buf.decode("utf-8", errors="replace")
            if exit_code != 0:
                QMessageBox.critical(
                    self, "Suggest max harmonics degree",
                    f"spody maxhgdegree exited with code {exit_code}.\n\n"
                    f"Output:\n{text}")
                return
            # Parse the `key: value` lines the CLI emits.
            parsed: dict[str, str] = {}
            for line in text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    parsed[k.strip()] = v.strip()
            num_max = parsed.get("numerical_max")
            mod_max = parsed.get("model_max")
            if not num_max or not mod_max:
                QMessageBox.warning(
                    self, "Suggest max harmonics degree",
                    f"Could not parse spody maxhgdegree output:\n\n{text}")
                return
            # Sticky info label sits between the field and the button.
            self._hd_info.setText(
                f"  max num: {num_max}  |  max model: {mod_max}  ")
            self._hd_info.setVisible(True)
            current = self._widgets["force_model.harmonics_degree"].text()
            r_km    = parsed.get("r_km", "?")
            R_body  = parsed.get("R_body_km", "?")
            reply = QMessageBox.question(
                self, "Suggest max harmonics degree",
                f"Largest harmonics degree whose Cnm/Snm contribution at\n"
                f"the current orbit altitude rises above double-precision\n"
                f"noise (computed from the actual coefficients in the\n"
                f"harmonics file, not a Kaula estimate):\n\n"
                f"  numerical max: {num_max}\n"
                f"  model max:     {mod_max}\n\n"
                f"r = {r_km} km, R_body = {R_body} km\n"
                f"Current harmonics_degree: {current or '(empty)'}\n\n"
                f"Apply {num_max} to harmonics_degree?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if reply == QMessageBox.StandardButton.Yes:
                self._widgets["force_model.harmonics_degree"].setText(num_max)

        def on_error(_err) -> None:
            dlg.close()
            self._hd_suggest_proc = None
            QMessageBox.critical(
                self, "Suggest max harmonics degree",
                f"Failed to launch spody.exe:\n{proc.errorString()}")

        proc.readyReadStandardOutput.connect(on_ready_read)
        proc.finished.connect(on_finished)
        proc.errorOccurred.connect(on_error)
        self._hd_suggest_proc = proc
        proc.start(spody_bin, [
            "maxhgdegree", str(hfile_path),
            repr(pos[0]), repr(pos[1]), repr(pos[2]),
        ])

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
        # Display: just the basename so a long absolute path doesn't
        # stretch the form column wider than its splitter slot. The
        # full path stays one hover away via the tooltip; the top-bar
        # working-dir field already shows the parent dir for context.
        self._path_label.setText(path.name if path else "(no file)")
        self._path_label.setToolTip(str(path) if path else "")
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

        # Model dispatch: CR3BP strips every HF-only section so a stale
        # widget value left over from a Moon scenario doesn't leak into
        # the emitted TOML. HF strips [cr3bp] for the same reason.
        dyn_model = flat.get("simulation.dynamics_model", "high_fidelity")
        if dyn_model == "cr3bp":
            flat = {k: v for k, v in flat.items()
                    if not (k.startswith("spacecraft.")
                            or k.startswith("debris.")
                            or k.startswith("force_model.")
                            or k.startswith("ephemeris."))}
        else:
            flat = {k: v for k, v in flat.items()
                    if not k.startswith("cr3bp.")}

            # Apply object XOR by stripping the inactive branch (so
            # even if both have stale data the emitted TOML is
            # consistent). HF-only -- under CR3BP both sub-branches
            # were already wiped above.
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

        # Earth-only [force_model] fields: present in the engine schema
        # ONLY when central_body == "Earth". The widgets are hidden in
        # the form for any other body but may still hold stale text from
        # a previous Earth run; drop them on emit so the validator
        # never rejects the TOML on round-trip.
        cb = flat.get("force_model.central_body", "")
        if str(cb).strip().lower() != "earth":
            flat.pop("force_model.eop_file",    None)
            flat.pop("force_model.iau2006_dir", None)

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

        # CR3BP-specific drops: the engine rejects the per-force
        # breakdown (no HF force model to break down) and the eclipse
        # event (no Sun in the model). Strip them regardless of the
        # checkbox state so a stale toggle from a HF scenario doesn't
        # turn into a validate error.
        if dyn_model == "cr3bp":
            flat.pop("output.accelerations_file", None)
            flat.pop("events.eclipse_threshold", None)

        # BF input: from spody 0.2.x the engine understands
        # `frame = "central_body_fixed"` natively and rotates the
        # parsed (position, velocity) into the integrator's
        # central_inertial frame at sim_setup via the central body's
        # bf_rotation provider. The GUI therefore writes the BF
        # values + the BF frame name unchanged -- no GUI-side
        # rotation, no frame override -- so the TOML preserves the
        # user's BF intent across save / load cycles.

        # [initial_state] kind dispatch: drop the inactive block's
        # keys so the emitted TOML matches what the user sees in the
        # form. 'cartesian' is the default; we also drop the `kind`
        # key itself in that case so legacy round-trips stay
        # byte-identical when the user never touched the new combo.
        init_kind = flat.get("initial_state.kind", "cartesian")
        if init_kind == "cartesian":
            for k in (
                "initial_state.kind",
                "initial_state.reference_body",
                "initial_state.semi_major_axis_km",
                "initial_state.eccentricity",
                "initial_state.inclination_deg",
                "initial_state.raan_deg",
                "initial_state.arg_periapsis_deg",
                "initial_state.anomaly_deg",
                "initial_state.anomaly_type",
            ):
                flat.pop(k, None)
        else:  # keplerian
            flat.pop("initial_state.position_km",  None)
            flat.pop("initial_state.velocity_kms", None)
            # HF: reference_body is implicit ("central"); omit so the
            # emitted TOML mirrors the schema docs (defaults are not
            # written).
            if dyn_model != "cr3bp":
                flat.pop("initial_state.reference_body", None)

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

            # Pick dynamics_model FIRST so HF/CR3BP visibility is right
            # before field values are pushed. Default to high_fidelity
            # when the TOML omits the key (legacy files).
            dm_value = flat.get("simulation.dynamics_model") or "high_fidelity"
            dm_combo = self._widgets.get("simulation.dynamics_model")
            if isinstance(dm_combo, QComboBox):
                idx = dm_combo.findText(str(dm_value))
                if idx >= 0:
                    dm_combo.setCurrentIndex(idx)
            self._on_dynamics_model_changed(str(dm_value))
            # TOML always stores inertial values so the loaded
            # `frame` will be central_inertial / synodic_rotating;
            # sync the rotation tracker accordingly so a subsequent
            # user-driven BF flip rotates from ICRF, not from a stale
            # in-memory state.
            self._input_frame_prev = (
                flat.get("initial_state.frame") or "central_inertial")

            # [initial_state].kind also drives visibility: pick it BEFORE
            # the field push so the right block is shown. Default to
            # cartesian (legacy TOMLs). The kind combo is wired through
            # _on_init_kind_changed which would otherwise try to convert
            # whatever is in the cartesian block right now (most likely
            # empty); _loading=True is already set so _touch is a no-op,
            # but we still want to skip the conversion attempt -- toggle
            # visibility manually here, _set_widget_value below will set
            # the combo to the right text.
            init_kind = (flat.get("initial_state.kind") or "cartesian")
            init_is_kep = (init_kind == "keplerian")
            if hasattr(self, "_init_cart_block"):
                self._init_cart_block.setVisible(not init_is_kep)
                self._init_kep_block.setVisible(init_is_kep)

            # Decide object mode FIRST so the XOR visibility is right
            # before fields populate. (No-op when dynamics_model = cr3bp
            # since the object group is hidden, but the radio still
            # tracks state for a later HF load.)
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
        # Seed the IC four-representation cache from the freshly-
        # loaded values so the first kind / frame toggle benefits
        # from the lossless lookup path (and we don't pay the spopy
        # conversion on a click that the user perceives as
        # navigation, not computation).
        self._invalidate_ic_cache()
        self._seed_ic_cache_from_visible()
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
            # Re-snap dynamics_model to the HF default so a previous
            # CR3BP session does not leak hidden HF widgets.
            dm_combo = self._widgets.get("simulation.dynamics_model")
            if isinstance(dm_combo, QComboBox):
                idx = dm_combo.findText("high_fidelity")
                if idx >= 0:
                    dm_combo.setCurrentIndex(idx)
            self._on_dynamics_model_changed("high_fidelity")
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
