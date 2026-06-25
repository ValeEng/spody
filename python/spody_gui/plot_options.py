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
    QButtonGroup,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class PlotOptionsDialog(QDialog):
    """Non-modal plot-options panel attached to the AnalysisPanel.

    Mirrors `SceneOptionsDialog` in spirit -- a small always-on-top
    floating widget the user can leave open while flipping between
    plots. Today it exposes Export CSV and the frame selector
    (ICRF / body-fixed) for state-vector and Keplerian-angle plots."""

    exportCsvRequested = Signal()
    # Emitted when the user flips the frame radio. Payload is one of
    # "icrf" / "bf"; AnalysisPanel stores the choice and re-renders
    # the active plot.
    plotFrameChanged   = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plot options")
        self.setModal(False)
        self.setWindowFlag(Qt.WindowType.Tool, True)

        intro = QLabel(
            "Options for the currently displayed 2D plot.")
        intro.setWordWrap(True)

        # ---- Frame selector (ICRF / body-fixed) ----------------------
        # Affects state-vector plots in the first block (|r|, |v|, x/y/z,
        # vx/vy/vz, orbit XY/XZ/YZ projections) and the Keplerian
        # angle plots (RAAN, AOP, ν, e-vs-ω). Invariant quantities
        # (a, e, i, |r|, |v|) plot identically in both frames; only the
        # title suffix changes there. CR3BP runs and central bodies
        # without a registered body-fixed orientation get the BF radio
        # auto-disabled by `set_bf_available` so the choice gracefully
        # collapses to ICRF.
        frame_box = QGroupBox("Plot frame")
        self._rb_icrf = QRadioButton("ICRF (inertial)")
        self._rb_bf   = QRadioButton("Body-fixed")
        self._rb_icrf.setChecked(True)
        self._frame_group = QButtonGroup(self)
        self._frame_group.addButton(self._rb_icrf)
        self._frame_group.addButton(self._rb_bf)
        self._rb_icrf.toggled.connect(self._on_frame_radio_toggled)
        self._rb_bf.toggled.connect(self._on_frame_radio_toggled)
        frame_lay = QVBoxLayout(frame_box)
        frame_lay.addWidget(self._rb_icrf)
        frame_lay.addWidget(self._rb_bf)

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
        lay.addWidget(frame_box)
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

    # ------------------------------------------------------------------
    # Frame selector
    # ------------------------------------------------------------------
    def _on_frame_radio_toggled(self, _checked: bool) -> None:
        """Both radios fire toggled (one True, one False). Emit only
        once per user click by gating on the ICRF radio's checked
        state -- and only when the radios are user-facing (i.e. the
        BF option is enabled; otherwise the user did not actually
        flip anything intentionally)."""
        if not self._rb_bf.isEnabled() and not self._rb_icrf.isEnabled():
            return
        sender = self.sender()
        # Avoid double emit (one for the unchecked, one for the checked).
        if sender is not None and not sender.isChecked():
            return
        self.plotFrameChanged.emit(self.frame())

    def frame(self) -> str:
        """Current frame selection: "icrf" or "bf"."""
        return "bf" if self._rb_bf.isChecked() else "icrf"

    def set_frame(self, frame: str) -> None:
        """Set the radio without firing `plotFrameChanged` (the panel
        uses this to seed the dialog state to match the panel state
        on first open). Blocks signals around the radio writes."""
        target = self._rb_bf if frame == "bf" else self._rb_icrf
        # blockSignals on the buttons (NOT the group) so the toggled
        # signal does not bounce out and trigger a redundant render.
        for rb in (self._rb_icrf, self._rb_bf):
            rb.blockSignals(True)
        target.setChecked(True)
        for rb in (self._rb_icrf, self._rb_bf):
            rb.blockSignals(False)

    def set_bf_available(self, available: bool, bf_label: str = "") -> None:
        """Enable / disable the body-fixed radio based on whether the
        loaded run has a registered orientation provider for its
        central body. `bf_label` is the body-fixed frame name (e.g.
        "ITRS" / "PA"), shown as the BF radio's text so the user
        knows which BF they are picking. When disabled, the BF radio
        is greyed out and (if currently checked) flipped back to
        ICRF so the panel sees a consistent state."""
        text = f"Body-fixed ({bf_label})" if bf_label else "Body-fixed"
        self._rb_bf.setText(text)
        self._rb_bf.setEnabled(available)
        if not available and self._rb_bf.isChecked():
            self._rb_icrf.setChecked(True)
            self.plotFrameChanged.emit("icrf")
