"""Persistent settings: path manager + Settings dialog.

QSettings persists to the OS-native location (registry on Windows, plist
on macOS, INI under ~/.config on Linux). The keys are intentionally few
for v0 -- one path each for the spody binary, harmonics file, ephemeris
file, and a default output directory -- plus the Recent files list.
"""
from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

# Names under which paths are stored in QSettings. Centralised so the
# dialog and the runner agree on the spelling.
KEY_SPODY_BIN     = "paths/spody_binary"
KEY_HARMONICS     = "paths/harmonics_file"
KEY_EPHEMERIS     = "paths/ephemeris_file"
KEY_OUTPUT_DIR    = "paths/output_dir"
KEY_MOON_TEXTURE  = "paths/moon_texture"
KEY_RECENT        = "files/recent"


class SettingsStore:
    """Thin wrapper over QSettings with typed accessors for the keys
    we care about. Keeps every other module ignorant of QSettings."""

    def __init__(self) -> None:
        self._qs = QSettings()  # uses QApplication organisationName/applicationName

    # Paths ------------------------------------------------------------
    def spody_binary(self) -> str:    return self._qs.value(KEY_SPODY_BIN,     "", type=str)
    def harmonics_file(self) -> str:  return self._qs.value(KEY_HARMONICS,     "", type=str)
    def ephemeris_file(self) -> str:  return self._qs.value(KEY_EPHEMERIS,     "", type=str)
    def output_dir(self) -> str:      return self._qs.value(KEY_OUTPUT_DIR,    "", type=str)
    def moon_texture(self) -> str:    return self._qs.value(KEY_MOON_TEXTURE,  "", type=str)

    def set_paths(self, *, spody_bin: str, harmonics: str, ephemeris: str,
                  output_dir: str, moon_texture: str) -> None:
        self._qs.setValue(KEY_SPODY_BIN,    spody_bin)
        self._qs.setValue(KEY_HARMONICS,    harmonics)
        self._qs.setValue(KEY_EPHEMERIS,    ephemeris)
        self._qs.setValue(KEY_OUTPUT_DIR,   output_dir)
        self._qs.setValue(KEY_MOON_TEXTURE, moon_texture)

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

        self._e_spody     = QLineEdit(store.spody_binary())
        self._e_harmonics = QLineEdit(store.harmonics_file())
        self._e_ephemeris = QLineEdit(store.ephemeris_file())
        self._e_output    = QLineEdit(store.output_dir())
        self._e_moon      = QLineEdit(store.moon_texture())

        form = QFormLayout()
        form.addRow("spody binary",  _path_picker_row(self._e_spody,
                    "Locate spody.exe", "Executable (*.exe);;All files (*)"))
        form.addRow("harmonics file", _path_picker_row(self._e_harmonics,
                    "Locate harmonics file", "Harmonics (*.tab *.cof *.txt);;All files (*)"))
        form.addRow("ephemeris file", _path_picker_row(self._e_ephemeris,
                    "Locate ephemeris file", "Ephemeris (*.spody *.bsp);;All files (*)"))
        form.addRow("default output dir", _path_picker_row(self._e_output,
                    "Choose output directory", "", pick_dir=True))
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
            harmonics=self._e_harmonics.text().strip(),
            ephemeris=self._e_ephemeris.text().strip(),
            output_dir=self._e_output.text().strip(),
            moon_texture=self._e_moon.text().strip(),
        )
        self.accept()
