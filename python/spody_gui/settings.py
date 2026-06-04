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
"""Persistent settings: path manager + Settings dialog.

QSettings persists to the OS-native location (registry on Windows, plist
on macOS, INI under ~/.config on Linux). The dialog stays intentionally
narrow:

  * spody binary  -- path to spody.exe (the C runner).
  * data dir      -- root for downloaded ephemeris / harmonics; the
                     Setup wizard manages the contents but the user
                     can point at an existing folder here.
  * Moon texture  -- equirectangular image for the 3D Analysis view.

The legacy `paths/harmonics_file`, `paths/ephemeris_file` and
`paths/output_dir` keys are still recognised so an existing config
doesn't get nuked, but they are no longer surfaced in the dialog --
the per-run harmonics / ephemeris paths live inside the TOML and are
filled by the Setup wizard.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import paths

# Names under which paths are stored in QSettings. Centralised so the
# dialog and the runner agree on the spelling. Legacy keys are kept
# for back-compat; the dialog no longer edits them.
KEY_SPODY_BIN     = "paths/spody_binary"
KEY_HARMONICS     = "paths/harmonics_file"      # legacy
KEY_EPHEMERIS     = "paths/ephemeris_file"      # legacy
KEY_OUTPUT_DIR    = "paths/output_dir"          # legacy
KEY_MOON_TEXTURE  = "paths/moon_texture"
KEY_RECENT        = "files/recent"


class SettingsStore:
    """Thin wrapper over QSettings with typed accessors for the keys
    we care about. Keeps every other module ignorant of QSettings."""

    def __init__(self) -> None:
        self._qs = QSettings()  # uses QApplication organisationName/applicationName

    # Paths ------------------------------------------------------------
    def spody_binary(self) -> str:    return self._qs.value(KEY_SPODY_BIN,     "", type=str)
    def moon_texture(self) -> str:    return self._qs.value(KEY_MOON_TEXTURE,  "", type=str)

    # Legacy accessors -- still read by older code but no longer the
    # primary source. The wizard's data dir is the canonical store.
    def harmonics_file(self) -> str:  return self._qs.value(KEY_HARMONICS, "", type=str)
    def ephemeris_file(self) -> str:  return self._qs.value(KEY_EPHEMERIS, "", type=str)
    def output_dir(self) -> str:      return self._qs.value(KEY_OUTPUT_DIR, "", type=str)

    def data_dir(self) -> Path:
        """Where the Setup wizard places downloaded data. Delegates to
        the `paths` module so the resolution rules live in one place."""
        return paths.data_dir()

    def set_paths(self, *, spody_bin: str, data_dir: str, moon_texture: str) -> None:
        self._qs.setValue(KEY_SPODY_BIN,    spody_bin)
        self._qs.setValue(KEY_MOON_TEXTURE, moon_texture)
        paths.set_data_dir(data_dir)

    # Recent files -----------------------------------------------------
    def recent_files(self) -> list[str]:
        val = self._qs.value(KEY_RECENT, [])
        # QSettings sometimes returns a single string instead of a list
        # when only one entry has been stored; normalise.
        if isinstance(val, str):
            return [val] if val else []
        return list(val) if val else []

    def add_recent_file(self, path: str, max_entries: int) -> None:
        recents = [p for p in self.recent_files() if p != path]
        recents.insert(0, path)
        del recents[max_entries:]
        self._qs.setValue(KEY_RECENT, recents)

    def clear_recent_files(self) -> None:
        self._qs.remove(KEY_RECENT)


def _path_picker_row(line: QLineEdit, dialog_title: str,
                     filter_str: str, pick_dir: bool = False) -> QWidget:
    """A QLineEdit + Browse... button laid out horizontally. The button
    opens a file (or directory) dialog and writes the chosen path back
    into the line edit."""
    row = QWidget()
    h = QHBoxLayout(row)
    h.setContentsMargins(0, 0, 0, 0)
    h.addWidget(line, 1)
    btn = QPushButton("Browse...")
    def _browse() -> None:
        start = line.text()
        if pick_dir:
            path = QFileDialog.getExistingDirectory(row, dialog_title, start)
        else:
            path, _ = QFileDialog.getOpenFileName(row, dialog_title, start, filter_str)
        if path:
            line.setText(path)
    btn.clicked.connect(_browse)
    h.addWidget(btn)
    return row


class SettingsDialog(QDialog):
    """Modal dialog for editing the persistent paths. Reads/writes the
    SettingsStore directly on OK; Cancel discards changes."""

    def __init__(self, store: SettingsStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SpOdy -- Paths")
        self.setMinimumWidth(640)
        self._store = store

        self._e_spody    = QLineEdit(store.spody_binary())
        self._e_data     = QLineEdit(str(store.data_dir()))
        self._e_moon     = QLineEdit(store.moon_texture())

        form = QFormLayout()
        form.addRow("spody binary",  _path_picker_row(self._e_spody,
                    "Locate spody.exe", "Executable (*.exe);;All files (*)"))
        form.addRow("data dir",      _path_picker_row(self._e_data,
                    "Choose data directory", "", pick_dir=True))
        # One-line note clarifying that the data dir is wizard-managed;
        # picking a different folder is supported but unusual.
        data_hint = QLabel(
            "Where the Setup wizard downloads ephemeris + harmonics into. "
            "Default sits next to the executable; change only to reuse an "
            "existing dataset."
        )
        data_hint.setStyleSheet("color: gray;")
        data_hint.setWordWrap(True)
        form.addRow("", data_hint)
        form.addRow("Moon texture (3D view)", _path_picker_row(self._e_moon,
                    "Locate Moon equirectangular texture",
                    "Images (*.jpg *.jpeg *.png);;All files (*)"))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _on_accept(self) -> None:
        self._store.set_paths(
            spody_bin=self._e_spody.text().strip(),
            data_dir=self._e_data.text().strip(),
            moon_texture=self._e_moon.text().strip(),
        )
        self.accept()
