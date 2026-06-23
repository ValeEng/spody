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
"""Per-plot UI options for the 2D analysis canvas.

Counterpart of `scene_options.py` for matplotlib (2D) plots. A small
non-modal dialog hosted by AnalysisPanel and opened from the `Plot
options...` button on the matplotlib toolbar row; today it exposes a
single Export-CSV action, with room to grow as new per-plot toggles
arrive.

The dialog itself owns no data: it emits signals (`exportCsvRequested`)
that the analysis panel handles against whatever figure is currently
shown.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class PlotOptionsDialog(QDialog):
    """Non-modal plot-options panel attached to the AnalysisPanel.

    Mirrors `SceneOptionsDialog` in spirit -- a small always-on-top
    floating widget the user can leave open while flipping between
    plots. Today it exposes Export CSV only; future per-plot toggles
    (axis scaling, marker on/off, downsampling, ...) land here too."""

    exportCsvRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plot options")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Tool, True)

        intro = QLabel(
            "Options for the currently displayed 2D plot.")
        intro.setWordWrap(True)

        self._btn_export_csv = QPushButton("Export CSV...")
        self._btn_export_csv.setToolTip(
            "Save every line drawn on the current figure as a CSV file. "
            "Tile and overlay views are supported; scatter / heat-map "
            "layers (impact maps, density plots) are not exported.")
        self._btn_export_csv.clicked.connect(self.exportCsvRequested.emit)

        row = QHBoxLayout()
        row.addWidget(self._btn_export_csv)
        row.addStretch(1)

        # In-dialog status line: shows "Saving to <path>...", "Saved
        # (...)" on success (briefly, before the dialog auto-closes),
        # or an error message that keeps the dialog open.
        self._status = QLabel("")
        self._status.setStyleSheet("color: gray;")
        self._status.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addLayout(row)
        lay.addWidget(self._status)
        lay.addStretch(1)

    def set_export_enabled(self, on: bool) -> None:
        """Toggle the Export CSV button. Called by the panel whenever
        the active plot changes -- disabled when there is no figure
        loaded yet, or when the active plot is 3D (the dialog is
        meant for the matplotlib canvas only)."""
        self._btn_export_csv.setEnabled(on)

    def set_status(self, text: str, ok: bool = True) -> None:
        """Write `text` into the status line; `ok=False` paints it red
        so an export error stands out without a separate dialog."""
        self._status.setText(text)
        self._status.setStyleSheet(
            "color: gray;" if ok else "color: #b00020;")

    def clear_status(self) -> None:
        self._status.setText("")
