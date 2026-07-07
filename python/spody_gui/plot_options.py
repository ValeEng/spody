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
options...` button on the matplotlib toolbar row; it exposes the plot
frame selector and a CSV-export box (a radio list of export types +
one Export button), with room to grow as new per-plot toggles arrive.

The dialog itself owns no data: it emits signals (`exportRequested`
with the selected type id, `plotFrameChanged`) that the analysis panel
handles against whatever figure / file is currently shown. Which
export types are available is pushed in via `set_export_availability`.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QGroupBox,
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

    # Emitted when the user clicks Export. Payload is the id of the
    # selected export type ("lines" / "bands"); AnalysisPanel routes it
    # to the matching writer. The set of types and which are currently
    # available is pushed in via `set_export_availability`.
    exportRequested = Signal(str)
    # Emitted when the user flips the frame radio. Payload is one of
    # "icrf" / "bf"; AnalysisPanel stores the choice and re-renders
    # the active plot.
    plotFrameChanged   = Signal(str)

    # The CSV export types the dialog offers, in display order:
    # (id, radio label, tooltip). Availability per type is data-driven
    # (pushed by the panel); the id is what `exportRequested` carries.
    _EXPORT_TYPES = (
        ("lines", "Plot lines (as drawn)",
         "Every line on the current figure as CSV (tile and overlay "
         "views supported). Scatter / bar / heat-map plots -- impact "
         "maps, altitude-band views -- carry no line data."),
        ("bands", "Altitude bands (per batch element)",
         "The altitude-band occupancy table: one row per batch element "
         "(ascending case id), a time + entries pair per band. Needs an "
         "events file with central-body altitude crossings."),
        ("impacts", "Impact points (lat/lon + time of flight)",
         "One row per IMPACT: case id, body-fixed latitude / longitude, "
         "and time of flight. Needs an events file with at least one "
         "impact on a body that has a body-fixed frame."),
    )

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

        # ---- CSV export ---------------------------------------------
        # A box that lists the available CSV export types as radios and
        # one Export button acting on the selected one. Types grey out
        # individually when they don't apply (a bar / heat-map plot has
        # no line data; a non-events file has no altitude bands), so the
        # user sees WHICH exports exist and why one is unavailable,
        # instead of a lone button that mysteriously greys.
        export_box = QGroupBox("Export CSV")
        export_lay = QVBoxLayout(export_box)
        self._export_group = QButtonGroup(self)
        self._export_radios: dict[str, QRadioButton] = {}
        for tid, label, tip in self._EXPORT_TYPES:
            rb = QRadioButton(label)
            rb.setToolTip(tip)
            self._export_group.addButton(rb)
            self._export_radios[tid] = rb
            export_lay.addWidget(rb)
        self._btn_export = QPushButton("Export selected...")
        self._btn_export.clicked.connect(self._on_export_clicked)
        self._btn_export.setEnabled(False)
        export_lay.addWidget(self._btn_export)

        # In-dialog status line: shows "Saving to <path>...", "Saved
        # (...)" on success (briefly, before the dialog auto-closes),
        # or an error message that keeps the dialog open.
        self._status = QLabel("")
        self._status.setStyleSheet("color: gray;")
        self._status.setWordWrap(True)

        lay = QVBoxLayout(self)
        lay.addWidget(intro)
        lay.addWidget(frame_box)
        lay.addWidget(export_box)
        lay.addWidget(self._status)
        lay.addStretch(1)

    def _checked_export_id(self) -> str | None:
        for tid, rb in self._export_radios.items():
            if rb.isChecked():
                return tid
        return None

    def _on_export_clicked(self) -> None:
        tid = self._checked_export_id()
        if tid is not None and self._export_radios[tid].isEnabled():
            self.exportRequested.emit(tid)

    def set_export_availability(self, avail: "dict[str, bool]") -> None:
        """Enable/disable each export-type radio from `avail`
        (id -> bool). If the currently-selected type is unavailable,
        the selection moves to the first available one; the Export
        button is enabled iff at least one type is available. Called by
        the panel on every render and on dialog open."""
        first_ok: str | None = None
        for tid, rb in self._export_radios.items():
            ok = bool(avail.get(tid, False))
            rb.setEnabled(ok)
            if ok and first_ok is None:
                first_ok = tid
        checked = self._checked_export_id()
        if (checked is None or not avail.get(checked, False)) \
                and first_ok is not None:
            self._export_radios[first_ok].setChecked(True)
        self._btn_export.setEnabled(first_ok is not None)

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
