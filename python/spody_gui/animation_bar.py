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
"""Cesium-style playback bar for the 3D analysis canvas.

Layout (left to right):

    [ |< ]  [ > ]  [-------●-------]   T+ 00:42:15 / 01:00:00   [1x v]  [Trail]

  - |<     reset to t_min
  - >      play / pause toggle
  - slider 1000-tick mapping of [t_min, t_max] -> current time
  - label  hh:mm:ss elapsed / total
  - speed  multiplier applied to wall clock when playing (1x ... 10000x)
  - trail  toggles per-trajectory polyline clipping in the canvas

Two outbound signals:
  - `timeChanged(float)`   sim seconds, fired whenever the slider or
                            the play tick advances the time
  - `trailToggled(bool)`   the user clicked the Trail button

Time advance during play:
    every 33 ms a QTimer increments t by `dt_wall * speed_multiplier`.
    When t reaches t_max the playback auto-pauses (no looping; a Reset
    click sends t back to t_min if the user wants to replay).
"""
from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QWidget,
)


# Slider has 1000 integer ticks across [t_min, t_max]. Enough resolution
# for a 1-hour orbit to step in 3.6 s increments per tick on the slider.
_SLIDER_TICKS = 1000

# Wall-clock interval between play ticks. 33 ms ~= 30 fps; smooth enough
# for any orbit, cheap enough not to bottleneck on the trail rebuild.
_TICK_INTERVAL_MS = 33

# Speed multipliers offered in the combobox. The defaults span the
# orbital regimes spody runs on: 1x is real-time (useless for hour-long
# orbits), 100x replays an LRO orbit in ~70 seconds, 10000x scrubs a
# multi-day batch fast enough to scan visually.
_SPEEDS: tuple[tuple[str, float], ...] = (
    ("1x",     1.0),
    ("10x",    10.0),
    ("100x",   100.0),
    ("1000x",  1000.0),
    ("10000x", 10000.0),
)
_DEFAULT_SPEED_INDEX = 2   # 100x -- sane starting point for LRO-scale orbits


def _format_hms(seconds: float) -> str:
    """`123456` -> `34:17:36`. Negative or NaN clamps to `00:00:00`.
    Hours field grows past 99 unconstrained -- a multi-day batch
    legibly reads `48:00:00` rather than overflowing."""
    if not (seconds == seconds) or seconds < 0:   # NaN-safe
        seconds = 0.0
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s   = divmod(rem,   60)
    return f"{h:02d}:{m:02d}:{s:02d}"


class AnimationBar(QWidget):
    """Playback controls for `VtkCanvas`-hosted 3D animations.

    Emits time changes outward; the canvas owns the actual marker
    movement. Held disabled when the active plot has no animation
    handles, so it visually 'lights up' only on the 3D orbit views.
    """

    timeChanged          = Signal(float)   # sim seconds
    sceneOptionsRequested = Signal()       # user clicked the Scene button

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._t_min: float = 0.0
        self._t_max: float = 0.0
        self._t_cur: float = 0.0
        self._playing:  bool = False
        self._speed:    float = _SPEEDS[_DEFAULT_SPEED_INDEX][1]

        # --- Widgets --------------------------------------------------
        self._btn_reset = QPushButton("|<")
        self._btn_reset.setFixedWidth(36)
        self._btn_reset.setToolTip("Reset to t_min")
        self._btn_reset.clicked.connect(self._on_reset)

        self._btn_play = QPushButton(">")
        self._btn_play.setFixedWidth(36)
        self._btn_play.setCheckable(True)
        self._btn_play.setToolTip("Play / pause")
        self._btn_play.toggled.connect(self._on_play_toggled)

        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.setRange(0, _SLIDER_TICKS)
        self._slider.setValue(0)
        self._slider.setSingleStep(1)
        self._slider.setPageStep(10)
        self._slider.valueChanged.connect(self._on_slider_value_changed)

        self._lbl_time = QLabel("00:00:00 / 00:00:00")
        self._lbl_time.setMinimumWidth(140)
        self._lbl_time.setStyleSheet("font-family: Consolas, monospace;")

        self._speed_combo = QComboBox()
        for label, _ in _SPEEDS:
            self._speed_combo.addItem(label)
        self._speed_combo.setCurrentIndex(_DEFAULT_SPEED_INDEX)
        self._speed_combo.currentIndexChanged.connect(self._on_speed_changed)
        self._speed_combo.setToolTip("Playback speed (sim time / wall time)")

        self._btn_scene = QPushButton("Scene...")
        self._btn_scene.setToolTip(
            "Open Scene options (trail, triads, third bodies, ...)")
        self._btn_scene.clicked.connect(self.sceneOptionsRequested.emit)

        # --- Layout ---------------------------------------------------
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.addWidget(self._btn_reset)
        lay.addWidget(self._btn_play)
        lay.addWidget(self._slider, 1)
        lay.addWidget(self._lbl_time)
        lay.addWidget(self._speed_combo)
        lay.addWidget(self._btn_scene)

        # --- Play timer -----------------------------------------------
        self._timer = QTimer(self)
        self._timer.setInterval(_TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_tick)

        # Initially disabled; the panel calls set_time_range when a 3D
        # plot with animation handles is rendered.
        self.set_enabled(False)

    # ------------------------------------------------------------------
    # External API
    # ------------------------------------------------------------------
    def set_enabled(self, on: bool) -> None:
        """Enable/disable the PLAYBACK controls. The Scene options
        button stays enabled regardless: the user must always be able
        to re-open the dialog (e.g. to re-enable the spacecraft
        trajectory after toggling it off, which would otherwise leave
        no animation handles and disable the rest of the bar)."""
        for w in (self._btn_reset, self._btn_play, self._slider,
                  self._speed_combo):
            w.setEnabled(on)
        if not on and self._playing:
            # Don't leave the timer ticking on a disabled bar -- the
            # canvas may have been cleared underneath us.
            self._btn_play.setChecked(False)

    def set_time_range(self, t_min: float, t_max: float) -> None:
        """Bind the slider to [t_min, t_max] sim seconds. Resets the
        current time to t_min and re-enables the bar. Called by the
        analysis panel after every successful 3D plot/overlay render
        that left animation handles in the canvas."""
        if t_max <= t_min:
            self.set_enabled(False)
            return
        self._t_min = float(t_min)
        self._t_max = float(t_max)
        self._t_cur = float(t_min)
        # Push the slider without retriggering our own emit logic.
        self._slider.blockSignals(True)
        self._slider.setValue(0)
        self._slider.blockSignals(False)
        self._update_time_label()
        self.set_enabled(True)
        # Emit one initial time so the canvas paints the markers at
        # t_min straight away (otherwise they sit at whatever pose
        # add_animated_trajectory parked them at).
        self.timeChanged.emit(self._t_cur)

    def current_time(self) -> float:
        return self._t_cur

    def set_time(self, t: float) -> None:
        """Move the playhead to `t` (clamped to the bound range)
        without changing the t_min/t_max binding. Used by the analysis
        panel to preserve the playback position across re-renders
        triggered by Scene-options toggles."""
        self._set_time(t, emit=True, move_slider=True)

    # ------------------------------------------------------------------
    # Internal -- play loop
    # ------------------------------------------------------------------
    def _on_play_toggled(self, checked: bool) -> None:
        self._playing = checked
        self._btn_play.setText("||" if checked else ">")
        if checked:
            # If we were already at the end, rewind so a single click
            # starts a fresh playback instead of being a no-op.
            if self._t_cur >= self._t_max:
                self._set_time(self._t_min, emit=True, move_slider=True)
            self._timer.start()
        else:
            self._timer.stop()

    def _on_tick(self) -> None:
        dt = (_TICK_INTERVAL_MS / 1000.0) * self._speed
        t  = self._t_cur + dt
        if t >= self._t_max:
            t = self._t_max
            self._btn_play.setChecked(False)   # auto-pause at end
        self._set_time(t, emit=True, move_slider=True)

    # ------------------------------------------------------------------
    # Internal -- input handlers
    # ------------------------------------------------------------------
    def _on_reset(self) -> None:
        # Reset always pauses; clicking |< then > is a natural replay.
        self._btn_play.setChecked(False)
        self._set_time(self._t_min, emit=True, move_slider=True)

    def _on_slider_value_changed(self, v: int) -> None:
        # User dragged the slider directly. Map back to sim seconds
        # and re-emit; do NOT touch the slider (would loop).
        if self._t_max <= self._t_min:
            return
        frac = v / float(_SLIDER_TICKS)
        t = self._t_min + frac * (self._t_max - self._t_min)
        self._set_time(t, emit=True, move_slider=False)

    def _on_speed_changed(self, idx: int) -> None:
        self._speed = _SPEEDS[idx][1]

    # ------------------------------------------------------------------
    # Internal -- shared mutator
    # ------------------------------------------------------------------
    def _set_time(self, t: float, *, emit: bool, move_slider: bool) -> None:
        """Single point where `_t_cur` is updated. Keeps the slider
        position and the label in sync. `emit` controls whether the
        outward signal fires; the play tick and slider drag always
        emit, but the time-range setup uses its own emit to avoid
        double-firing."""
        t = max(self._t_min, min(self._t_max, float(t)))
        self._t_cur = t
        if move_slider and self._t_max > self._t_min:
            frac = (t - self._t_min) / (self._t_max - self._t_min)
            v = int(round(frac * _SLIDER_TICKS))
            self._slider.blockSignals(True)
            self._slider.setValue(v)
            self._slider.blockSignals(False)
        self._update_time_label()
        if emit:
            self.timeChanged.emit(t)

    def _update_time_label(self) -> None:
        elapsed = self._t_cur - self._t_min
        total   = self._t_max - self._t_min
        self._lbl_time.setText(f"{_format_hms(elapsed)} / {_format_hms(total)}")
