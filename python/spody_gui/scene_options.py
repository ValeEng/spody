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
"""Per-scene UI options for the 3D orbit views.

`SceneOptions` is a lightweight dataclass passed through PlotContext
so every 3D plot function can ask "should I draw the PA triad? which
third bodies?" without reaching back into the panel.

`SceneOptionsDialog` is a non-modal QDialog that lets the user toggle
those options live: changing any checkbox emits `optionsChanged`,
which the analysis panel listens to and triggers a re-render of the
active 3D plot. The dialog stays open and movable -- the user can
keep tweaking visibility while watching the canvas update.

The body checkboxes are populated dynamically from whatever the
current run's TOML declared in `force_model.third_bodies`; the panel
calls `set_available_bodies` whenever a new file is loaded.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


@dataclass
class SceneOptions:
    """User preferences for what to render in a 3D orbit view.

    `show_bodies` is a set of body names (matching the TOML's
    `force_model.third_bodies` strings). An empty set means "show
    none"; the GUI defaults pre-check whatever the TOML declared.

    `scene_frame` is a placeholder for the future ICRF/PA toggle;
    today only "icrf" is actually implemented. The dialog disables
    the radio so users can't accidentally select an unimplemented
    mode.

    `cr3bp_elements_primary` (1 or 2) selects which of the two CR3BP
    primaries the osculating orbital-elements plots reference. The
    synodic-frame state is shifted by the primary's fixed position
    and the rotating-frame velocity is corrected by `omega x r_rel`
    to recover an inertial-frame velocity in the synodic basis, then
    Kepler's standard set is computed with that primary's `mu`.
    Ignored in HF mode."""
    show_trajectory:        bool = True
    show_third_bodies:      bool = True
    show_icrf_triad:        bool = True
    show_pa_triad:          bool = True   # also drives Moon body libration
    show_bodies:            set[str] = field(default_factory=set)
    trail_enabled:          bool = False  # polyline clipped behind the marker
    scene_frame:            str = "icrf"  # "icrf" | "pa" (pa = TODO)
    cr3bp_elements_primary: int = 1       # 1 (bigger) or 2 (smaller)


class SceneOptionsDialog(QDialog):
    """Non-modal scene-options panel attached to the AnalysisPanel.

    The user sees this as a small always-on-top floating widget;
    every checkbox flick fires `optionsChanged` so the parent panel
    can re-render the active 3D view. The dialog reads / writes a
    single `SceneOptions` instance owned by the panel, so closing
    and re-opening preserves the user's selections within a session."""

    optionsChanged = Signal()

    # Body display order in the dialog. Inner planets first, then
    # outer, then Sun on its own line (it tends to be the user's
    # primary interest for SRP / illumination so we keep it at the
    # top of the group).
    _BODY_DISPLAY_ORDER: tuple[str, ...] = (
        "Sun", "Mercury", "Venus", "Earth", "Mars",
        "Jupiter", "Saturn", "Uranus", "Neptune",
    )

    def __init__(self, options: SceneOptions,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Scene options (3D)")
        # Tool window stays on top of the main window without grabbing
        # the focus that a regular dialog would.
        self.setWindowFlag(Qt.WindowType.Tool, True)
        self.setModal(False)

        self._options = options
        # Per-body checkbox lookup, rebuilt whenever the available
        # body list changes (different TOML loaded -> different
        # third_bodies). Keys are the body name strings.
        self._body_boxes: dict[str, QCheckBox] = {}

        root = QVBoxLayout(self)

        # --- Scene contents -----------------------------------------
        self._gb_contents = QGroupBox("Scene contents")
        lay_contents = QVBoxLayout(self._gb_contents)
        self._cb_trajectory = self._make_checkbox(
            "Spacecraft trajectory + marker",
            options.show_trajectory,
            lambda v: self._set("show_trajectory", v))
        self._cb_third_bodies = self._make_checkbox(
            "Third bodies (sphere + arrow)",
            options.show_third_bodies,
            lambda v: self._set("show_third_bodies", v))
        self._cb_trail = self._make_checkbox(
            "Trail (clip polyline behind marker)",
            options.trail_enabled,
            lambda v: self._set("trail_enabled", v))
        lay_contents.addWidget(self._cb_trajectory)
        lay_contents.addWidget(self._cb_third_bodies)
        lay_contents.addWidget(self._cb_trail)
        root.addWidget(self._gb_contents)

        # --- Reference frames ---------------------------------------
        self._gb_frames = QGroupBox("Reference frames")
        lay_frames = QVBoxLayout(self._gb_frames)
        self._cb_icrf = self._make_checkbox(
            "ICRF triad (inertial, muted)",
            options.show_icrf_triad,
            lambda v: self._set("show_icrf_triad", v))
        # Label rewritten by `set_body_frame_label` whenever a new
        # run is loaded -- e.g. "PA triad + Moon libration" on the
        # Moon, "ITRF triad + Earth rotation" on Earth. Generic
        # text here is the fallback before the panel calls back.
        self._cb_pa = self._make_checkbox(
            "Body-fixed triad + central-body rotation (animated)",
            options.show_pa_triad,
            lambda v: self._set("show_pa_triad", v))
        lay_frames.addWidget(self._cb_icrf)
        lay_frames.addWidget(self._cb_pa)
        root.addWidget(self._gb_frames)

        # --- Third bodies (dynamic) ---------------------------------
        self._gb_bodies = QGroupBox("Third bodies (from TOML)")
        self._lay_bodies = QVBoxLayout(self._gb_bodies)
        self._lbl_bodies_empty = QLabel(
            "(no third bodies declared in this run's TOML)")
        self._lbl_bodies_empty.setStyleSheet("color: gray;")
        self._lay_bodies.addWidget(self._lbl_bodies_empty)
        root.addWidget(self._gb_bodies)

        # --- Scene frame (placeholder for the future ICRF/PA switch) -
        self._gb_frame_switch = QGroupBox("Scene frame")
        lay_fs = QHBoxLayout(self._gb_frame_switch)
        self._rb_icrf = QRadioButton("ICRF (inertial)")
        self._rb_pa   = QRadioButton("PA / body-fixed -- coming soon")
        self._rb_icrf.setChecked(options.scene_frame == "icrf")
        self._rb_pa.setChecked(options.scene_frame == "pa")
        self._rb_pa.setEnabled(False)   # placeholder
        group = QButtonGroup(self)
        group.addButton(self._rb_icrf)
        group.addButton(self._rb_pa)
        lay_fs.addWidget(self._rb_icrf)
        lay_fs.addWidget(self._rb_pa)
        lay_fs.addStretch(1)
        root.addWidget(self._gb_frame_switch)

        # --- CR3BP options (hidden in HF mode) ----------------------
        # Selector for which of the two synodic primaries the osculating
        # orbital-elements plots reference. The labels are filled in by
        # `set_dynamics_model` so the radios read the actual body names
        # ("primary 1: Earth" / "primary 2: Moon") instead of generic
        # placeholders.
        self._gb_cr3bp = QGroupBox("CR3BP options")
        lay_cr3bp = QVBoxLayout(self._gb_cr3bp)
        lay_cr3bp.addWidget(QLabel(
            "Orbital elements relative to:"))
        self._rb_primary_1 = QRadioButton("primary 1")
        self._rb_primary_2 = QRadioButton("primary 2")
        self._rb_primary_1.setChecked(options.cr3bp_elements_primary == 1)
        self._rb_primary_2.setChecked(options.cr3bp_elements_primary == 2)
        primary_group = QButtonGroup(self)
        primary_group.addButton(self._rb_primary_1, 1)
        primary_group.addButton(self._rb_primary_2, 2)
        primary_group.idToggled.connect(self._on_primary_changed)
        lay_cr3bp.addWidget(self._rb_primary_1)
        lay_cr3bp.addWidget(self._rb_primary_2)
        root.addWidget(self._gb_cr3bp)
        self._gb_cr3bp.setVisible(False)   # HF default; flipped on load

        # --- Close button (non-modal, no Apply needed; toggles are live) ---
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.close)
        bottom.addWidget(btn_close)
        root.addLayout(bottom)

    # ------------------------------------------------------------------
    # External API
    # ------------------------------------------------------------------
    def set_body_frame_label(self, body_name: str,
                              frame_tag: str) -> None:
        """Update the body-fixed triad checkbox label to reflect the
        run's central body. Called by the analysis panel whenever a
        new file is loaded (different TOML may have a different
        central body, so 'PA + Moon libration' may need to become
        'ITRF + Earth rotation')."""
        self._cb_pa.setText(
            f"{frame_tag} triad + {body_name} rotation "
            f"(body-fixed, animated)")

    def set_dynamics_model(self, model: str,
                            primary_names: tuple[str, str] | None = None
                            ) -> None:
        """Show / hide groups based on the loaded run's dynamics model
        and label the CR3BP primary radios with the actual body names.

        HF: scene-contents, reference-frames, third-bodies and scene-
        frame groups are all visible; the CR3BP group is hidden.

        CR3BP: only scene-contents (trajectory + trail) and the CR3BP
        group are visible. Third-bodies / ICRF / body-fixed triads are
        irrelevant in the synodic rotating frame and the scene-frame
        ICRF/PA switch is HF-only too -- all hidden so the dialog
        shows only knobs that actually do something."""
        is_cr3bp = (model == "cr3bp") and primary_names is not None
        self._gb_frames.setVisible(not is_cr3bp)
        self._gb_bodies.setVisible(not is_cr3bp)
        self._gb_frame_switch.setVisible(not is_cr3bp)
        self._gb_cr3bp.setVisible(is_cr3bp)
        # The third-bodies checkbox in 'Scene contents' is HF-only too
        # (CR3BP scenes have no third bodies -- the two primaries are
        # part of the dynamics, not decoration).
        self._cb_third_bodies.setVisible(not is_cr3bp)
        if is_cr3bp:
            self._rb_primary_1.setText(f"primary 1: {primary_names[0]}")
            self._rb_primary_2.setText(f"primary 2: {primary_names[1]}")

    def set_available_bodies(self, body_names: list[str]) -> None:
        """Repopulate the per-body checkbox section. Called by the
        panel when a new run is loaded (different TOML may have a
        different `third_bodies` list). Bodies already present in
        `self._options.show_bodies` stay checked; new ones default
        to checked (the user opted in via the TOML)."""
        # Wipe old widgets.
        for box in self._body_boxes.values():
            self._lay_bodies.removeWidget(box)
            box.deleteLater()
        self._body_boxes.clear()

        ordered = [b for b in self._BODY_DISPLAY_ORDER if b in body_names] \
                + [b for b in body_names
                   if b not in self._BODY_DISPLAY_ORDER]

        if not ordered:
            self._lbl_bodies_empty.show()
            self._options.show_bodies = set()
            return
        self._lbl_bodies_empty.hide()

        for name in ordered:
            checked = (name in self._options.show_bodies) \
                or not self._options.show_bodies  # first time: all on
            box = self._make_checkbox(
                name, checked,
                lambda v, n=name: self._toggle_body(n, v))
            self._lay_bodies.addWidget(box)
            self._body_boxes[name] = box
            if checked:
                self._options.show_bodies.add(name)
            else:
                self._options.show_bodies.discard(name)
        # No emit here: this is a programmatic refresh, not a user
        # action. The caller (panel) re-renders separately if needed.

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _make_checkbox(self, label: str, checked: bool,
                        on_toggle) -> QCheckBox:
        box = QCheckBox(label)
        box.setChecked(checked)
        box.toggled.connect(on_toggle)
        return box

    def _set(self, attr: str, value: bool) -> None:
        """Update one bool field on `self._options` and notify."""
        setattr(self._options, attr, value)
        self.optionsChanged.emit()

    def _toggle_body(self, name: str, value: bool) -> None:
        if value:
            self._options.show_bodies.add(name)
        else:
            self._options.show_bodies.discard(name)
        self.optionsChanged.emit()

    def _on_primary_changed(self, primary_id: int, checked: bool) -> None:
        """QButtonGroup.idToggled fires twice per click (old off, new on);
        we only act on the 'on' edge so optionsChanged is emitted once."""
        if not checked:
            return
        self._options.cr3bp_elements_primary = primary_id
        self.optionsChanged.emit()
