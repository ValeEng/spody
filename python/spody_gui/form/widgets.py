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

"""Widget factories: one `_add_<type>` per field family.

Each factory creates the widget, registers it under its dotted TOML
key, wires the change signal to `self._touch`, and adds it to the
given QFormLayout. A new field type gets its factory here.
"""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .catalog import DURATION_FACTORS, unit_suffix


class AssetCombo(QComboBox):
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


def hwrap(layout: QHBoxLayout) -> QWidget:
    """Wrap a layout in a transparent QWidget so QFormLayout.addRow
    accepts it as the field cell."""
    w = QWidget()
    w.setLayout(layout)
    return w


def hwrap_v(layout: QVBoxLayout) -> QWidget:
    w = QWidget()
    w.setLayout(layout)
    return w


def tidy_float(v: float) -> str:
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


class WidgetFactoriesMixin:
    """Widget-creation methods mixed into TomlForm. They only touch
    `self._widgets` / `self._touch` / `self._validate_field`, all
    defined by the concrete class."""

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
        layout.addRow(label + unit_suffix(key.split(".")[-1]), w)

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
        layout.addRow(label + unit_suffix(key.split(".")[-1]), w)

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
        for u in DURATION_FACTORS:
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
                seconds = v * DURATION_FACTORS[old_unit]
                w.blockSignals(True)
                w.setText(tidy_float(seconds / DURATION_FACTORS[new_unit]))
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
        layout.addRow(label, hwrap(row))

    def _add_int(self, layout: QFormLayout, key: str, label: str,
                 minimum: int = -2**31, maximum: int = 2**31 - 1,
                 hint: str = "") -> None:
        w = QLineEdit()
        w.setValidator(QIntValidator(minimum, maximum, w))
        w.textChanged.connect(self._touch)
        w.textChanged.connect(lambda _t, k=key: self._validate_field(k))
        self._widgets[key] = w
        full_label = label + unit_suffix(key.split(".")[-1])
        if hint:
            # Inline grey note next to the field (e.g. "(8 cores available)").
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(w, 1)
            hint_label = QLabel(hint)
            hint_label.setStyleSheet("color: gray;")
            row.addWidget(hint_label)
            layout.addRow(full_label, hwrap(row))
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
        combo = AssetCombo(category=category, body_key=body_key)
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
        layout.addRow(label, hwrap(row))
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
        if info is None or not isinstance(combo, AssetCombo):
            return
        from .. import assets
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
        field = hwrap(row)
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
        layout.addRow(label + unit_suffix(key.split(".")[-1]), hwrap(row))

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
            # The third-body list feeds the [[events.altitude_crossing]]
            # body combo's valid-name set; toggling a body has to refresh
            # every row so a now-checked body becomes pickable (and a
            # now-unchecked one shows up as a stale entry to fix). Safe
            # to call from the construction loop because the helper
            # short-circuits when the table doesn't exist yet.
            cb.toggled.connect(
                lambda _checked: self._refresh_altcross_body_options())
            boxes[name] = cb
            row_layouts[-1].addWidget(cb)
        # Pad the last partial row.
        if len(known) % cols:
            row_layouts[-1].addStretch(1)
        self._widgets[key] = boxes
        layout.addRow(label, hwrap_v(grid))
