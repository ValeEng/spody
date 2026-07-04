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

from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import SettingsStore

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

# The form's building blocks live in the form package; this module
# keeps only the TomlForm composition: state, signals, modification
# tracking and internals.
from .form import (
    HandlersMixin,
    RoundTripMixin,
    SectionBuildersMixin,
    VisibilityMixin,
    WidgetFactoriesMixin,
)
from .form.catalog import (
    DURATION_FACTORS,
    DURATION_UNIT_AUTOPICK,
    INVALID_QSS,
    TOOLTIPS,
    VALIDATORS,
)
from .form.widgets import AssetCombo, tidy_float


class TomlForm(SectionBuildersMixin, WidgetFactoriesMixin,
               VisibilityMixin, RoundTripMixin, HandlersMixin,
               QWidget):
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
        validator = VALIDATORS.get(key)
        w = self._widgets.get(key)
        if validator is None or not isinstance(w, QLineEdit):
            return
        text = w.text().strip()
        base_tip = TOOLTIPS.get(key, "")
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
            w.setStyleSheet(INVALID_QSS)
            w.setToolTip(f"{base_tip}\n\n⚠ {err}" if base_tip else f"⚠ {err}")
        else:
            w.setStyleSheet("")
            w.setToolTip(base_tip)

    def _apply_tooltips(self) -> None:
        """Push the per-field descriptions from `TOOLTIPS` onto each
        registered widget. Called once at the end of __init__ so it
        covers every field built by the section builders."""
        for key, text in TOOLTIPS.items():
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
            elif isinstance(w, AssetCombo):
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
                    v *= DURATION_FACTORS[combo.currentText()]
                return v
            return text
        if isinstance(w, QCheckBox):
            return bool(w.isChecked())
        if isinstance(w, AssetCombo):
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
                for u in DURATION_UNIT_AUTOPICK:
                    if abs(seconds) >= DURATION_FACTORS[u]:
                        unit = u
                        break
                combo.blockSignals(True)
                idx = combo.findText(unit)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                combo.blockSignals(False)
                self._scaled_unit_prev[key] = unit
                w.setText(tidy_float(seconds / DURATION_FACTORS[unit]))
                return
            if isinstance(value, float):
                w.setText(tidy_float(value))
            elif isinstance(value, int):
                w.setText(str(value))
            else:
                w.setText("" if value is None else str(value))
            return
        if isinstance(w, QCheckBox):
            w.setChecked(bool(value))
            return
        if isinstance(w, AssetCombo):
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
                    le.setText(tidy_float(float(x)))
            return
        if isinstance(w, dict):    # checkbox set
            wanted = set(value) if isinstance(value, (list, tuple)) else set()
            for name, cb in w.items():
                cb.setChecked(name in wanted)
            return
