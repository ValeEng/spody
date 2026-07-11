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

"""In-scene UI chrome for Scene3D: playback bar + options panel.

Pure VTK (vtkButtonWidget / vtkSliderWidget / vtkTextActor) -- no Qt
anywhere, so a standalone window (`interactor.Start()`) becomes a
self-contained Cesium-style viewer: timeline slider, play/pause,
speed cycling, epoch readout, and a menu button dropping down
checkbox toggles.

Everything here is OPT-IN: nothing in Scene3D creates these widgets.
The spody GUI keeps its Qt animation bar and Scene-options dialog and
never enables them -- the target is scripted / quicklook windows.

Contract with the scene:

* construct AFTER the scene is populated -- `PlaybackBar` reads
  `scene.animation_time_range()` and both classes attach 2D props to
  the live renderer;
* `Scene3D.clear_scene()` wipes widget representations along with
  the data props: call `reinstall()` on each widget after a scene
  rebuild;
* time semantics stay with the host, as everywhere in spoviz: the
  bar shows whatever the `formatter` callable returns for a sim-time
  (spody would pass an `spopy.time.et_to_utc`-based formatter, a
  bare script can live with the default `t = ... s`).

The playback clock uses the interactor's repeating timer, which VTK
services both in a native `Start()` event loop and inside a Qt
embedding -- but again, the Qt host in spody drives the scene from
its own bar and never constructs a PlaybackBar.
"""

from __future__ import annotations

from typing import Any, Callable

import numpy as np
from vtkmodules.util.numpy_support import numpy_to_vtk
from vtkmodules.vtkCommonDataModel import vtkImageData
from vtkmodules.vtkInteractionWidgets import (
    vtkButtonWidget,
    vtkSliderRepresentation2D,
    vtkSliderWidget,
    vtkTexturedButtonRepresentation2D,
)
from vtkmodules.vtkRenderingCore import vtkTextActor

from .scene import Scene3D

# Shared chrome palette: dark pill background + near-white glyphs,
# matching the UTC overlay's styling so the whole bottom strip reads
# as one UI layer.
_ICON_BG = (45, 48, 58)
_ICON_FG = (235, 235, 240)
_ACCENT  = (1.0, 0.85, 0.20)     # slider handle: spody trajectory yellow


def icon_image(kind: str, size: int = 30) -> vtkImageData:
    """Render a small RGBA icon into a vtkImageData with numpy masks
    -- no font dependency (VTK's bundled faces miss most symbol
    glyphs) and no image assets to ship. Kinds: 'play', 'pause',
    'speed' (double chevron), 'menu' (3 bars), 'box_off', 'box_on'.

    vtkImageData's y axis points up while the mask math below uses
    plain array rows; every icon is drawn vertically symmetric so no
    flip is needed."""
    s = float(size)
    a = np.zeros((size, size, 4), dtype=np.uint8)
    a[..., 0] = _ICON_BG[0]
    a[..., 1] = _ICON_BG[1]
    a[..., 2] = _ICON_BG[2]
    a[..., 3] = 215
    yy, xx = np.mgrid[0:size, 0:size].astype(float)
    c = (s - 1.0) / 2.0

    def chevron(x0: float) -> "np.ndarray":
        x1 = x0 + 0.26 * s
        return ((xx >= x0) & (xx <= x1)
                & (np.abs(yy - c) <= 0.26 * s * (x1 - xx) / (x1 - x0)))

    if kind == "play":
        x0, x1 = 0.32 * s, 0.76 * s
        m = ((xx >= x0) & (xx <= x1)
             & (np.abs(yy - c) <= 0.30 * s * (x1 - xx) / (x1 - x0)))
    elif kind == "pause":
        m = (((np.abs(xx - 0.40 * s) <= 0.055 * s)
              | (np.abs(xx - 0.62 * s) <= 0.055 * s))
             & (np.abs(yy - c) <= 0.27 * s))
    elif kind == "speed":
        m = chevron(0.24 * s) | chevron(0.50 * s)
    elif kind == "menu":
        bars = ((np.abs(yy - 0.34 * s) <= 0.05 * s)
                | (np.abs(yy - c) <= 0.05 * s)
                | (np.abs(yy - 0.66 * s) <= 0.05 * s))
        m = (np.abs(xx - c) <= 0.30 * s) & bars
    elif kind in ("box_off", "box_on"):
        lo, hi, t = 0.24 * s, 0.76 * s, 0.07 * s
        outer = (xx >= lo) & (xx <= hi) & (yy >= lo) & (yy <= hi)
        inner = ((xx >= lo + t) & (xx <= hi - t)
                 & (yy >= lo + t) & (yy <= hi - t))
        m = outer & ~inner
        if kind == "box_on":
            m = m | ((xx >= lo + 2.4 * t) & (xx <= hi - 2.4 * t)
                     & (yy >= lo + 2.4 * t) & (yy <= hi - 2.4 * t))
    else:
        raise ValueError(f"unknown icon kind {kind!r}")

    a[m, 0] = _ICON_FG[0]
    a[m, 1] = _ICON_FG[1]
    a[m, 2] = _ICON_FG[2]
    a[m, 3] = 255

    img = vtkImageData()
    img.SetDimensions(size, size, 1)
    arr = numpy_to_vtk(np.ascontiguousarray(a.reshape(-1, 4)), deep=1)
    img.GetPointData().SetScalars(arr)
    return img


def _make_button(iren: Any, renderer: Any, icons: "list[vtkImageData]",
                  on_state_changed: Callable) -> vtkButtonWidget:
    """Textured 2D button wired to the interactor. With a single
    visual state pass the same image twice: vtkButtonWidget cycles
    states on click and fires StateChangedEvent each time, so two
    identical textures give a reliable 'clicked' signal. The explicit
    CurrentRenderer keeps the button's prop on the interactive top
    layer (the FAR decoration renderer is non-interactive)."""
    rep = vtkTexturedButtonRepresentation2D()
    rep.SetNumberOfStates(len(icons))
    for i, img in enumerate(icons):
        rep.SetButtonTexture(i, img)
    w = vtkButtonWidget()
    w.SetInteractor(iren)
    w.SetCurrentRenderer(renderer)
    w.SetRepresentation(rep)
    w.AddObserver("StateChangedEvent", on_state_changed)
    w.On()
    return w


def _fmt_speed(v: float) -> str:
    """Compact sim-seconds-per-wall-second readout for the speed
    label: '45 s/s', '12 min/s', '1.5 h/s'."""
    if v < 120.0:
        return f"{v:.0f} s/s"
    if v < 7200.0:
        return f"{v / 60.0:.0f} min/s"
    return f"{v / 3600.0:.1f} h/s"


class PlaybackBar:
    """Bottom-strip playback controls drawn inside the render window:
    [play/pause] [speed] [--- timeline slider ---] + epoch readout in
    the scene's existing UTC overlay pill (bottom-right).

    `formatter(t_sim_s) -> str` owns the readout text; None shows a
    bare `t = ... s`. `speeds` is the sim-seconds-per-wall-second
    cycle the speed button steps through; None derives a slow /
    medium / fast cycle from the animation range (full span in ~120 /
    30 / 8 wall-seconds). `loop=True` wraps at the end instead of
    pausing.

    The slider lives in normalized display coordinates (resize-proof
    by itself); the buttons are placed in raw display pixels and
    re-laid out on every ConfigureEvent (window resize)."""

    def __init__(self, scene: Scene3D,
                 formatter: "Callable[[float], str] | None" = None,
                 speeds: "tuple | list | None" = None,
                 interval_ms: int = 33,
                 loop: bool = False) -> None:
        rng = scene.animation_time_range()
        if rng is None:
            raise ValueError(
                "PlaybackBar needs an animated scene: construct it "
                "AFTER add_animated_* calls populated the timeline")
        self._scene = scene
        self._iren = scene._interactor
        self._t0, self._t1 = rng
        self._t = self._t0
        self._fmt = formatter if formatter is not None \
            else (lambda t: f"t = {t:,.1f} s")
        span = max(self._t1 - self._t0, 1.0e-9)
        if speeds is None:
            speeds = (span / 120.0, span / 30.0, span / 8.0)
        self._speeds = [float(v) for v in speeds]
        self._speed_idx = min(1, len(self._speeds) - 1)
        self._interval_ms = int(interval_ms)
        # Public knob: a host (or an OptionsPanel toggle) may flip it
        # at any time; it is only read at the end-of-range check.
        self.loop = bool(loop)
        self._playing = False
        self._timer_id: "int | None" = None
        # Guards the play-button observer while WE flip its state
        # programmatically (end-of-range auto-pause): SetState fires
        # StateChangedEvent just like a click would.
        self._syncing_button = False

        # --- timeline slider (normalized display coords) -----------
        srep = vtkSliderRepresentation2D()
        srep.SetMinimumValue(self._t0)
        srep.SetMaximumValue(self._t1)
        srep.SetValue(self._t0)
        srep.ShowSliderLabelOff()
        srep.SetTitleText("")
        p1 = srep.GetPoint1Coordinate()
        p1.SetCoordinateSystemToNormalizedDisplay()
        p1.SetValue(0.175, 0.055)
        p2 = srep.GetPoint2Coordinate()
        p2.SetCoordinateSystemToNormalizedDisplay()
        p2.SetValue(0.700, 0.055)
        srep.SetSliderLength(0.014)
        srep.SetSliderWidth(0.020)
        srep.SetTubeWidth(0.005)
        srep.SetEndCapLength(0.002)
        srep.GetSliderProperty().SetColor(*_ACCENT)
        srep.GetTubeProperty().SetColor(0.42, 0.45, 0.55)
        srep.GetCapProperty().SetColor(0.42, 0.45, 0.55)
        self._srep = srep
        self._slider = vtkSliderWidget()
        self._slider.SetInteractor(self._iren)
        self._slider.SetCurrentRenderer(scene._renderer)
        self._slider.SetRepresentation(srep)
        # Jump: clicking anywhere on the tube warps the handle there
        # (Cesium-timeline behaviour) instead of requiring a drag
        # that starts exactly on the handle.
        self._slider.SetAnimationModeToJump()
        self._slider.AddObserver("InteractionEvent", self._on_slider)
        self._slider.On()

        # --- buttons + speed label (display coords, re-laid out) ---
        self._btn_play = _make_button(
            self._iren, scene._renderer,
            [icon_image("play"), icon_image("pause")],
            self._on_play_clicked)
        self._btn_speed = _make_button(
            self._iren, scene._renderer,
            [icon_image("speed"), icon_image("speed")],
            self._on_speed_clicked)

        self._speed_label = vtkTextActor()
        prop = self._speed_label.GetTextProperty()
        prop.SetFontFamilyToCourier()
        prop.SetFontSize(13)
        prop.SetColor(0.92, 0.92, 0.94)
        prop.SetBackgroundColor(0.0, 0.0, 0.0)
        prop.SetBackgroundOpacity(0.55)
        prop.SetVerticalJustificationToCentered()
        self._speed_label.SetInput(_fmt_speed(self._speeds[self._speed_idx]))
        scene._renderer.AddActor2D(self._speed_label)

        self._iren.AddObserver("ConfigureEvent", self._on_resize)
        self._iren.AddObserver("TimerEvent", self._on_timer)
        self._layout()
        self._push_time()

    # -- geometry --------------------------------------------------
    def _layout(self) -> None:
        """Place the pixel-coordinate elements from the current window
        size. The slider re-places itself (normalized display)."""
        w, h = self._scene._render_window.GetSize()
        if w < 60 or h < 60:
            return  # window not realized yet; ConfigureEvent will come
        y = 0.055 * h
        r = max(11.0, 0.014 * max(w, h))
        self._btn_play.GetRepresentation().PlaceWidget(
            [0.040 * w - r, 0.040 * w + r, y - r, y + r, 0.0, 0.0])
        self._btn_speed.GetRepresentation().PlaceWidget(
            [0.085 * w - r, 0.085 * w + r, y - r, y + r, 0.0, 0.0])
        coord = self._speed_label.GetPositionCoordinate()
        coord.SetCoordinateSystemToDisplay()
        coord.SetValue(0.085 * w + r + 5.0, y)

    def _on_resize(self, *_args) -> None:
        self._layout()

    # -- state -----------------------------------------------------
    def _push_time(self) -> None:
        """Propagate self._t everywhere it shows: scene animation,
        slider handle, epoch readout. One render at the end."""
        self._scene.set_animation_time(self._t)
        self._srep.SetValue(self._t)
        self._scene.set_overlay_utc_text(self._fmt(self._t))
        self._scene.render()

    def _set_playing(self, playing: bool) -> None:
        if playing and self._t >= self._t1:
            self._t = self._t0          # replay from the start
        self._playing = playing
        rep = self._btn_play.GetRepresentation()
        want = 1 if playing else 0
        if rep.GetState() != want:
            self._syncing_button = True
            try:
                rep.SetState(want)
            finally:
                self._syncing_button = False
        if playing and self._timer_id is None:
            self._timer_id = self._iren.CreateRepeatingTimer(
                self._interval_ms)
        elif not playing and self._timer_id is not None:
            self._iren.DestroyTimer(self._timer_id)
            self._timer_id = None
        self._scene.render()

    # -- observers ---------------------------------------------------
    def _on_play_clicked(self, *_args) -> None:
        if self._syncing_button:
            return
        state = self._btn_play.GetRepresentation().GetState()
        self._set_playing(bool(state))

    def _on_speed_clicked(self, *_args) -> None:
        self._speed_idx = (self._speed_idx + 1) % len(self._speeds)
        self._speed_label.SetInput(
            _fmt_speed(self._speeds[self._speed_idx]))
        self._scene.render()

    def _on_slider(self, *_args) -> None:
        self._t = float(self._srep.GetValue())
        self._push_time()

    def _on_timer(self, *_args) -> None:
        # A repeating timer only exists while playing, but the
        # interactor may service other timers too -- gate on the flag
        # rather than on timer ids (the Python observer signature
        # does not expose the firing timer's id).
        if not self._playing:
            return
        t = self._t + self._speeds[self._speed_idx] \
            * (self._interval_ms / 1000.0)
        if t >= self._t1:
            if self.loop:
                t = self._t0 + (t - self._t1)
            else:
                t = self._t1
                self._set_playing(False)
        self._t = t
        self._push_time()

    # -- public ------------------------------------------------------
    def reinstall(self) -> None:
        """Re-attach everything after `Scene3D.clear_scene()` (which
        wipes the widget representations and the speed label along
        with the data props) and re-read the new animation range."""
        rng = self._scene.animation_time_range()
        if rng is not None:
            self._t0, self._t1 = rng
            self._srep.SetMinimumValue(self._t0)
            self._srep.SetMaximumValue(self._t1)
        self._t = min(max(self._t, self._t0), self._t1)
        self._set_playing(False)
        for w in (self._slider, self._btn_play, self._btn_speed):
            w.Off()
            w.On()
        self._scene._renderer.AddActor2D(self._speed_label)
        self._layout()
        self._push_time()


class OptionsPanel:
    """Menu button (top-right) that drops down a column of checkbox
    toggles -- the in-scene analogue of spody's Scene-options dialog.

    `options` is a declarative list of `(label, initially_on,
    callback)`; each click calls `callback(bool_new_state)` and
    re-renders. The panel owns only the chrome -- what a toggle MEANS
    (hide the trail, kill the skybox, ...) is entirely the host's
    callback, which keeps this reusable for any scene."""

    def __init__(self, scene: Scene3D,
                 options: "list[tuple[str, bool, Callable[[bool], None]]]",
                 ) -> None:
        self._scene = scene
        self._iren = scene._interactor
        self._open = False

        self._btn_menu = _make_button(
            self._iren, scene._renderer,
            [icon_image("menu"), icon_image("menu")],
            self._on_menu_clicked)

        self._rows: "list[tuple[vtkButtonWidget, vtkTextActor]]" = []
        for label, initial, callback in options:
            btn = _make_button(
                self._iren, scene._renderer,
                [icon_image("box_off"), icon_image("box_on")],
                self._make_row_observer(callback))
            btn.GetRepresentation().SetState(1 if initial else 0)
            btn.Off()                     # hidden until the menu opens
            txt = vtkTextActor()
            txt.SetInput(str(label))
            prop = txt.GetTextProperty()
            prop.SetFontFamilyToCourier()
            prop.SetFontSize(13)
            prop.SetColor(0.92, 0.92, 0.94)
            prop.SetBackgroundColor(0.0, 0.0, 0.0)
            prop.SetBackgroundOpacity(0.55)
            prop.SetJustificationToRight()
            prop.SetVerticalJustificationToCentered()
            txt.SetVisibility(False)
            scene._renderer.AddActor2D(txt)
            self._rows.append((btn, txt))

        self._iren.AddObserver("ConfigureEvent", self._on_resize)
        self._layout()

    def _make_row_observer(self, callback: Callable[[bool], None]):
        def observer(caller, _event) -> None:
            callback(bool(caller.GetRepresentation().GetState()))
            self._scene.render()
        return observer

    def _layout(self) -> None:
        w, h = self._scene._render_window.GetSize()
        if w < 60 or h < 60:
            return
        r = max(11.0, 0.014 * max(w, h))
        xc, yc = 0.955 * w, 0.935 * h
        self._btn_menu.GetRepresentation().PlaceWidget(
            [xc - r, xc + r, yc - r, yc + r, 0.0, 0.0])
        for i, (btn, txt) in enumerate(self._rows):
            y = yc - (i + 1) * 2.6 * r
            btn.GetRepresentation().PlaceWidget(
                [xc - r, xc + r, y - r, y + r, 0.0, 0.0])
            coord = txt.GetPositionCoordinate()
            coord.SetCoordinateSystemToDisplay()
            coord.SetValue(xc - r - 6.0, y)

    def _on_resize(self, *_args) -> None:
        self._layout()

    def _on_menu_clicked(self, *_args) -> None:
        self._open = not self._open
        for btn, txt in self._rows:
            if self._open:
                btn.On()
            else:
                btn.Off()
            txt.SetVisibility(self._open)
        # Re-place after On(): enabling a widget can reset its
        # representation's renderer assignment.
        self._layout()
        self._scene.render()

    def reinstall(self) -> None:
        """Re-attach after `Scene3D.clear_scene()`: re-enable the
        menu button, re-add the row labels, restore open state."""
        self._btn_menu.Off()
        self._btn_menu.On()
        for btn, txt in self._rows:
            self._scene._renderer.AddActor2D(txt)
            if self._open:
                btn.Off()
                btn.On()
            txt.SetVisibility(self._open)
        self._layout()
        self._scene.render()
