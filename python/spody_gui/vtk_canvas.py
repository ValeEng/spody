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
"""3D scene widget built on VTK + QVTKRenderWindowInteractor.

Embeds a VTK render window in a Qt widget and exposes a small,
spody-specific API:

    canvas.clear_scene()                          # remove all data props
    canvas.add_central_body(radius_km, color)     # solid sphere
    canvas.add_trajectory(points_km, color, lw)   # polyline + endpoint markers
    canvas.reset_camera()                         # fit-to-scene
    canvas.render()                               # repaint

Default mouse controls (VTK's built-in `vtkInteractorStyleTrackballCamera`):
    left-drag   : rotate
    middle-drag : pan
    right-drag  : zoom
    scroll      : zoom
    r           : reset camera

A corner triad shows the ICRF-aligned axes (X red, Y green, Z blue).
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# QVTKRenderWindowInteractor sniffs the active Qt binding from QT_API;
# force PySide6 so it does not accidentally pull in PyQt5/PyQt6 if they
# happen to be on PYTHONPATH.
os.environ.setdefault("QT_API", "pyside6")

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout, QWidget
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.vtkCommonCore import vtkPoints, vtkUnsignedCharArray
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData, vtkPolyLine
from vtkmodules.vtkCommonMath import vtkMatrix4x4
from vtkmodules.vtkFiltersHybrid import vtkPolyDataSilhouette
from vtkmodules.vtkFiltersSources import (
    vtkArrowSource,
    vtkRegularPolygonSource,
    vtkSphereSource,
    vtkTexturedSphereSource,
)
from vtkmodules.vtkIOImage import vtkPNGReader
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkActor2D,
    vtkBillboardTextActor3D,
    vtkCoordinate,
    vtkGlyph3DMapper,
    vtkPolyDataMapper,
    vtkPolyDataMapper2D,
    vtkPropPicker,
    vtkRenderer,
    vtkSkybox,
    vtkTextActor,
    vtkTexture,
)
# Importing the OpenGL rendering backend registers the implementation
# behind vtkRenderer / vtkRenderWindow; without it VTK silently fails
# to draw anything. Same story for the free-type text renderer used by
# the axes-actor labels.
import vtkmodules.vtkRenderingOpenGL2     # noqa: F401 -- side-effect import
import vtkmodules.vtkRenderingFreeType    # noqa: F401 -- side-effect import


# Mean radius of the Moon in km. Kept here for backwards-compat
# with callers that imported it (analysis_panel, scene_options
# documentation). Authoritative source is now spody_const.h,
# parsed by `constants._load_spody_const`; if you change the
# value, change it there.
#
# Local import to avoid a hard analysis_panel -> vtk_canvas
# circular at module load: VtkCanvas itself doesn't need the
# constant, only re-exports it.
def _moon_radius_km_fallback() -> float:
    try:
        from .central_bodies import MOON_RADIUS_KM as _R
        return _R
    except Exception:  # noqa: BLE001 -- circular / missing module
        return 1737.4
MOON_RADIUS_KM = _moon_radius_km_fallback()


@dataclass
class _AnimHandle:
    """One animated trajectory: the static geometry (full polyline +
    moving marker sphere) plus the (time, point) arrays
    set_animation_time interpolates on at every tick.

    `points` is Nx3 (km, scene frame); `times` is N entries (sim
    seconds, monotonically increasing). `marker_actor` is the sphere
    whose Position is rewritten each frame. `line_actor` is the full
    polyline; when the trail mode is enabled we swap its mapper's
    input data for a progressively-extended truncation, so toggling
    trail on/off doesn't require rebuilding the renderer."""
    times:            "np.ndarray"     # noqa: F821 -- forward-ref to numpy
    points:           "np.ndarray"     # noqa: F821
    line_actor:       vtkActor
    full_poly:        vtkPolyData      # mapper input when trail is off (whole orbit)
    marker_actor:     vtkActor
    silhouette_actor: vtkActor | None  # None for textured markers (e.g. Moon 3rd-body) -- the body's surface is self-identifying so a white outline would only halo it.
    color:            tuple[float, float, float] = (1.0, 0.85, 0.20)
    # Optional per-time body-fixed -> scene rotation for the marker
    # actor. When non-None, set_animation_time installs the rotation
    # as the marker's UserMatrix so e.g. Earth's ITRS texture rotates
    # under the continents even when Earth is shown as a third body
    # in a Moon-centred scene. Shape (N, 3, 3); same length as `times`.
    marker_R_sequence: "np.ndarray | None" = None    # noqa: F821


@dataclass
class _AnimTriadHandle:
    """One animated reference-frame triad: three arrow actors + three
    optional billboard label actors, all anchored at `origin` and
    rotated each tick so their axes align with the columns of
    `R_sequence[idx]` (nearest-or-interpolated index of `times`).

    `R_sequence` is (N, 3, 3); columns are the frame's local axes
    expressed in scene coordinates. The API stays agnostic about
    WHICH frame this is -- today we use it for the PA triad in an
    ICRF scene (R = R_pa_to_icrf), but a future "switch scene frame"
    mode would feed the ICRF triad's R_icrf_to_pa here without any
    other code change."""
    times:        "np.ndarray"        # noqa: F821
    R_sequence:   "np.ndarray"        # noqa: F821, (N, 3, 3)
    arrow_actors: list                # exactly 3 vtkActor (or None per axis)
    label_actors: list                # 3 vtkBillboardTextActor3D or None each
    length_km:    float
    origin:       "np.ndarray"        # noqa: F821, (3,)
    has_labels:   bool


@dataclass
class _AnimBodyHandle:
    """The central body, with a time-varying orientation. Each tick
    rewrites the actor's UserMatrix from `R_sequence[idx]` so the
    texture (lunar mascons, prime meridian, ...) tracks the body's
    physical attitude.

    Same design rationale as `_AnimTriadHandle`: the R sequence is
    "this body's axes in scene coordinates". In ICRF scene with a
    librating Moon, R = R_pa_to_icrf. In a future PA scene the body
    would be identity (R = I) and the satellite trajectory would
    instead be rotated per-tick."""
    times:      "np.ndarray"          # noqa: F821
    R_sequence: "np.ndarray"          # noqa: F821, (N, 3, 3)
    actor:      vtkActor


@dataclass
class _AnimArrowHandle:
    """One animated direction arrow: an arrow actor anchored at the
    scene origin with a fixed length, whose orientation is rewritten
    each tick to point toward the (interpolated) body position. Used
    for third-body indicators (Sun, Earth, planets) so the user
    always sees WHICH WAY the body is, even when the body itself
    sits 150M km out of frame at the default zoom-on-Moon view.

    `positions` is the body's full physical km position over time;
    only the direction is consumed when re-orienting the arrow.
    Length is baked into the actor's scale at creation, so it stays
    constant during animation."""
    times:       "np.ndarray"      # noqa: F821
    positions:   "np.ndarray"      # noqa: F821, Nx3 km in scene frame
    arrow_actor: vtkActor


def _ensure_icrf_aligned_skybox(src: Path) -> Path:
    """Re-project an equirectangular star map so that vtkSkybox.Sphere
    samples it in ICRF orientation: looking toward +X_ICRF shows the
    RA=0 patch (vernal equinox), looking toward -Y_ICRF shows the
    Milky Way bulge (Sgr A* at RA~270), looking toward +Z_ICRF shows
    the North Celestial Pole (Polaris).

    vtkSkybox.Sphere's fragment shader hard-codes a (pole=+Y, RA=0=-Z)
    convention and ignores both SetUserTransform (uses model-space
    vertex positions, not world) and SetFloorPlane / SetFloorRight
    (those only affect Floor projection). The only clean fix is to
    rotate the pixels themselves so the shader's sampling
    convention, when interpreted in ICRF world coords, lands on the
    right star.

    The rotated copy is cached on disk next to the source as
    `<stem>_icrf<ext>`; subsequent calls return the cache directly
    when it is newer than the source. ~1-2 s the first time for an
    8K image, instant after that. Pillow + numpy do all the work;
    no VTK calls here so the function is import-cheap.

    Falls back to returning the source path on any failure (PIL
    missing, source unreadable, write permission denied) so the
    skybox still renders, just misaligned."""
    src = Path(src)
    if not src.is_file():
        return src
    dst = src.with_name(f"{src.stem}_icrf{src.suffix}")
    try:
        if dst.is_file() and dst.stat().st_mtime >= src.stat().st_mtime:
            return dst
    except OSError:
        return src

    try:
        from PIL import Image
    except ImportError:
        return src

    try:
        img = np.asarray(Image.open(src).convert("RGB"))
    except (OSError, ValueError):
        return src
    H, W = img.shape[:2]

    # Output pixel grids (u_new in [0,1) horizontal, v_new in [0,1) vertical).
    u_new = (np.arange(W) + 0.5) / W
    v_new = (np.arange(H) + 0.5) / H

    # vtkSkybox shader: u = atan2(d.x, d.z)/2pi + 0.5
    #                   v = 0.5 - asin(d.y)/pi
    # Invert to find the world direction d the shader is sampling for
    # each (u_new, v_new):
    #   d.y = sin((0.5 - v) * pi)  = cos(v * pi)
    #   phi = atan2(d.x, d.z)      = (u - 0.5) * 2pi
    #   d.x = cos(asin(d.y)) * sin(phi),  d.z = cos(asin(d.y)) * cos(phi)
    phi    = (u_new - 0.5) * 2.0 * np.pi          # (W,)
    dy     = np.cos(v_new * np.pi)                # (H,) -- sin((0.5-v)*pi)
    cos_lat = np.sqrt(np.maximum(0.0, 1.0 - dy * dy))  # (H,)
    sin_p  = np.sin(phi)                          # (W,)
    cos_p  = np.cos(phi)                          # (W,)
    dx     = cos_lat[:, None] * sin_p[None, :]    # (H, W)
    dz     = cos_lat[:, None] * cos_p[None, :]    # (H, W)
    dy2    = np.broadcast_to(dy[:, None], (H, W))

    # The Solar System Scope (and most photographic Milky Way) star
    # maps are stored in GALACTIC coordinates: the bulge sits at the
    # IMAGE CENTRE (u=0.5, v=0.5), NOT at RA=270 / Dec=-29. So we
    # rotate the shader's world direction (ICRF) into galactic coords
    # before computing the image lookup.
    #
    # R_icrf_to_gal is the standard J2000 ICRS -> Galactic rotation
    # (Liu et al. 2011, derived from Hipparcos pole / centre):
    #   d_gal = R @ d_icrf
    R_ICRF_TO_GAL = np.array([
        [-0.0548755604, -0.8734370902, -0.4838350155],
        [+0.4941094279, -0.4448296300, +0.7469822445],
        [-0.8676661490, -0.1980763734, +0.4559837762],
    ])
    # Apply to every pixel direction (we have d as three (H,W) arrays).
    gx = (R_ICRF_TO_GAL[0, 0] * dx +
          R_ICRF_TO_GAL[0, 1] * dy2 +
          R_ICRF_TO_GAL[0, 2] * dz)
    gy = (R_ICRF_TO_GAL[1, 0] * dx +
          R_ICRF_TO_GAL[1, 1] * dy2 +
          R_ICRF_TO_GAL[1, 2] * dz)
    gz = (R_ICRF_TO_GAL[2, 0] * dx +
          R_ICRF_TO_GAL[2, 1] * dy2 +
          R_ICRF_TO_GAL[2, 2] * dz)
    # Galactic (l, b) from the rotated direction.
    l_gal = np.arctan2(gy, gx)        # [-pi, pi]
    b_gal = np.arcsin(np.clip(gz, -1.0, 1.0))

    # Image convention (Solar System Scope style, l=0 at image centre):
    #   u_src = l / (2pi) + 0.5     (so u=0.5 = bulge)
    #   v_src = 0.5 - b / pi
    u_src = ((l_gal / (2.0 * np.pi)) + 0.5) % 1.0
    v_src = 0.5 - b_gal / np.pi

    src_x = np.clip((u_src * W).astype(np.int32), 0, W - 1)
    src_y = np.clip((v_src * H).astype(np.int32), 0, H - 1)
    new_img = img[src_y, src_x]

    try:
        Image.fromarray(new_img).save(
            dst, quality=92 if src.suffix.lower() in (".jpg", ".jpeg") else None)
    except OSError:
        return src
    return dst


class VtkCanvas(QWidget):
    """Qt widget hosting a single VTK renderer. Stateless wrt to which
    file is currently shown -- `clear_scene` followed by add_*() calls
    rebuilds everything for the next plot."""

    def __init__(self) -> None:
        super().__init__()
        self._interactor = QVTKRenderWindowInteractor(self)
        self._render_window = self._interactor.GetRenderWindow()

        # Two layered renderers, Cesium-multi-frustum style. The depth
        # buffer's 24 bits can't span 1737 km (Moon) and 150,000,000
        # km (Sun) at once without z-fighting -- so each layer keeps
        # its own depth scope, tight on what IT contains, and the two
        # layers are composited (layer 0 first, then layer 1 on top
        # with EraseOff so layer 0's pixels survive where layer 1 has
        # no geometry).
        #
        #   layer 0 (back)  -- decoration: third-body spheres &
        #                      polylines at TRUE physical scale.
        #                      Wide clipping; bad depth precision
        #                      doesn't matter (bodies don't intersect).
        #   layer 1 (front) -- primary: Moon, spacecraft trajectories,
        #                      direction arrows, frame triads.
        #                      Tight clipping; full depth precision
        #                      where it matters.
        #
        # The interactor talks to layer 1 only; layer 0's camera is
        # slaved to it via the ModifiedEvent observer below so the
        # two stay in the same pose.
        self._render_window.SetNumberOfLayers(2)

        self._renderer_far = vtkRenderer()
        self._renderer_far.SetLayer(0)
        self._renderer_far.SetBackground(0.06, 0.07, 0.10)
        self._renderer_far.SetInteractive(0)
        self._render_window.AddRenderer(self._renderer_far)

        self._renderer = vtkRenderer()
        self._renderer.SetLayer(1)
        # Preserve the color buffer between layer 0 and layer 1 so
        # the body sphere pixels from layer 0 survive in regions
        # where layer 1 has no geometry. The depth buffer, on the
        # other hand, MUST be cleared (PreserveDepthBufferOff = the
        # default): layer 0's depth values are in a much wider
        # frustum (~150M km), and reusing them would make every
        # layer-1 fragment fail the depth test against random
        # leftover values. With fresh depth, layer 1 just composes
        # in front of layer 0's color -- exactly the "primary always
        # on top" semantic we want.
        self._renderer.SetPreserveColorBuffer(1)
        self._render_window.AddRenderer(self._renderer)

        style = vtkInteractorStyleTrackballCamera()
        style.SetDefaultRenderer(self._renderer)
        self._interactor.SetInteractorStyle(style)

        # Share ONE camera object between FRONT (layer 1) and FAR
        # (layer 0). Both renderers see exactly the same pose at the
        # same instant -- no sync lag, no separate ModifiedEvent
        # observer to keep them in step. The previous design used two
        # cameras with a Modified-driven copy, but that one-frame lag
        # showed up as a wobbling / scaling skybox on FAR when only
        # the skybox lived there (no third-body markers to "anchor"
        # the bbox-driven clipping reset). Sharing the camera makes
        # the FAR view always coherent with the FRONT view, which is
        # what the user perceives anyway.
        self._renderer_far.SetActiveCamera(
            self._renderer.GetActiveCamera())
        # Trackball interactions (dolly / zoom) implicitly recompute
        # the camera's clipping range via VTK's resetCameraClippingRange
        # hooks, which use FRONT's bbox (Earth + trajectory ~10000 km).
        # That narrow far plane clips out the FAR layer's far-away
        # decoration markers (Sun at ~150M km). We re-widen the far
        # plane on every camera Modified to ~5e8 km, so zooming in does
        # not make the body spheres disappear. The flag guards against
        # the recursion of writing to the very camera we observe.
        self._syncing_cameras = False
        self._renderer.GetActiveCamera().AddObserver(
            "ModifiedEvent", self._on_top_camera_modified)

        # Corner triad: independent of scene scale, always visible.
        self._axes_actor = vtkAxesActor()
        self._axes_widget = vtkOrientationMarkerWidget()
        self._axes_widget.SetOrientationMarker(self._axes_actor)
        self._axes_widget.SetInteractor(self._interactor)
        self._axes_widget.SetViewport(0.0, 0.0, 0.2, 0.2)
        self._axes_widget.SetEnabled(1)
        self._axes_widget.InteractiveOff()

        # Picking state. Trajectories registered via add_trajectory
        # (with a source_path) become pickable on Ctrl+left-click; the
        # callback fires with the matching path or None on a miss.
        self._trajectory_actors: list[tuple[vtkActor, Path]] = []
        self._highlighted_actor: vtkActor | None = None
        self._highlight_extra_lw = 4.0
        self._pick_callback: Callable[[Path | None], None] | None = None

        # Animation handles registered by add_animated_trajectory. Each
        # entry carries everything set_animation_time needs to advance
        # one moving marker + (optionally) clip its trail polyline. The
        # `_trail_enabled` flag below switches every handle on or off in
        # a single call from the animation bar above the canvas.
        self._anim_handles: list[_AnimHandle]       = []
        self._anim_arrows:  list[_AnimArrowHandle]  = []
        self._anim_triads:  list[_AnimTriadHandle]  = []
        self._anim_body:    _AnimBodyHandle | None  = None
        # Central body actor stash so set_central_body_animated_
        # orientation can find it without the caller passing it back.
        # add_central_body sets this; clear_scene resets it.
        self._central_body_actor: vtkActor | None = None
        self._trail_enabled: bool = False
        self._interactor.AddObserver(
            "LeftButtonReleaseEvent", self._on_left_button_release
        )

        # Default equirectangular texture for the central body, applied
        # by add_central_body() when no explicit texture_path is given.
        # The panel sets this from Settings on every plot dispatch.
        self._default_central_texture: Path | None = None

        # Skybox texture (equirectangular star map). Lives on the FAR
        # renderer behind every other actor; clear_scene reinstalls it
        # from this cached path so a scene rebuild does not lose the
        # background. None = no skybox (the default dark colour shows).
        self._skybox_texture: Path | None = None
        self._skybox_actor:   vtkSkybox | None = None

        # The interactor must be initialised before the first render; it
        # is safe to call again later, so we do it once here.
        self._interactor.Initialize()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._interactor)

        # UTC overlay lives INSIDE the VTK scene (vtkTextActor 2D, on
        # the FRONT renderer) rather than as a Qt child widget on top
        # of QVTKRenderWindowInteractor. The Qt-on-top approach left
        # two artefacts on Windows: a residual 1-px frame around the
        # pill from the QLabel/QFrame native paint, and a "paint
        # hole" where leftover pixels from a previous render bled
        # through the QLabel's screen region during scene swaps. A
        # vtkTextActor composes through VTK's own pipeline, so it
        # gets cleared and repainted in lockstep with the rest of the
        # scene -- no overlay artefacts.
        # The actor is permanently allocated (text="" initially); the
        # public API set_overlay_utc_text() flips its visibility +
        # text in place rather than adding / removing per call.
        self._utc_overlay_actor = vtkTextActor()
        self._utc_overlay_actor.SetInput("")
        utc_prop = self._utc_overlay_actor.GetTextProperty()
        utc_prop.SetFontFamilyToCourier()
        utc_prop.SetFontSize(14)
        utc_prop.SetColor(1.0, 1.0, 1.0)
        utc_prop.SetBackgroundColor(0.0, 0.0, 0.0)
        utc_prop.SetBackgroundOpacity(0.55)
        utc_prop.SetFrame(False)
        utc_prop.SetJustificationToRight()
        utc_prop.SetVerticalJustificationToBottom()
        coord = self._utc_overlay_actor.GetPositionCoordinate()
        coord.SetCoordinateSystemToNormalizedViewport()
        # ~10 px inset from the bottom-right corner at typical canvas
        # sizes. Normalised viewport keeps it pinned across resizes.
        coord.SetValue(0.99, 0.012)
        self._utc_overlay_actor.SetVisibility(False)
        self._renderer.AddActor2D(self._utc_overlay_actor)

    # ------------------------------------------------------------------
    # UTC overlay (bottom-right of the canvas)
    # ------------------------------------------------------------------
    def set_overlay_utc_text(self, text: str) -> None:
        """Set the UTC string shown in the bottom-right overlay. An
        empty string hides the overlay. Caller is responsible for
        formatting (the canvas knows nothing about ET / leap seconds);
        AnalysisPanel converts `et_start + t_anim_s` via
        `spody_gui.time_conv.et_to_utc` and pushes the result here on
        every animation tick. The actor lives inside the VTK scene
        (top-layer renderer) so it composes through the same pipeline
        as the rest of the scene -- no native-widget overlay glitches."""
        if not text:
            self._utc_overlay_actor.SetVisibility(False)
            return
        self._utc_overlay_actor.SetInput(text)
        self._utc_overlay_actor.SetVisibility(True)

    # ------------------------------------------------------------------
    # Scene API
    # ------------------------------------------------------------------
    def clear_scene(self) -> None:
        """Remove every actor added via add_* methods. The corner triad
        (which lives on a separate marker widget) is preserved.
        Picking state and animation handles are also cleared so stale
        actor refs don't leak.

        The skybox is the one persistent FAR-renderer prop: it's
        re-installed after the wipe from the cached `_skybox_texture`
        path so a scene rebuild doesn't kill the star background."""
        self._renderer.RemoveAllViewProps()
        self._renderer_far.RemoveAllViewProps()
        self._skybox_actor = None
        self._trajectory_actors.clear()
        self._highlighted_actor = None
        self._anim_handles.clear()
        self._anim_arrows.clear()
        self._anim_triads.clear()
        self._anim_body = None
        self._central_body_actor = None
        self._install_skybox_actor()
        # UTC overlay survives scene wipes -- it is a viewport
        # decoration, not part of the scene. Re-added (idempotent
        # call; AddActor2D ignores duplicates is NOT true, so we
        # only re-add when it actually got removed by the wipe
        # above). Visibility is preserved across the wipe by
        # re-attaching the same actor.
        self._utc_overlay_actor.SetVisibility(False)
        self._renderer.AddActor2D(self._utc_overlay_actor)

    # ------------------------------------------------------------------
    # Skybox
    # ------------------------------------------------------------------
    def set_skybox_texture(self, path: Path | None) -> None:
        """Install an equirectangular star-map texture as the background
        of the FAR renderer (behind every other actor). `path = None`
        removes the skybox and falls back to the flat dark colour.

        The skybox uses `vtkSkybox.Sphere` projection so a single image
        suffices -- no cubemap juggling. clear_scene reinstalls the
        actor from the cached path, so this setter is sticky across
        scene rebuilds (typical: load a new .bin -> the skybox
        survives the rebuild).

        Idempotent: a call with the same path the canvas already has
        installed is a no-op (no re-read, no extra render). The
        analysis panel relies on this -- it calls into here on every
        3D dispatch to pick up Settings edits, so duplicate work
        would mean re-decoding a multi-MB image on every plot
        click."""
        new_path = Path(path) if path is not None else None
        if new_path == self._skybox_texture and (
                (new_path is None) == (self._skybox_actor is None)):
            return
        self._skybox_texture = new_path
        if self._skybox_actor is not None:
            self._renderer_far.RemoveViewProp(self._skybox_actor)
            self._skybox_actor = None
        self._install_skybox_actor()
        self._render_window.Render()

    def _install_skybox_actor(self) -> None:
        """Build the vtkSkybox actor from `_skybox_texture` and add it
        to the FAR renderer. No-op when the texture path is None or
        the file is unreadable (the scene degrades silently to the
        flat dark background)."""
        if self._skybox_texture is None or self._skybox_actor is not None:
            return
        # Re-project the equirectangular star map into vtkSkybox.Sphere's
        # native convention so RA=0 lines up with ICRF +X and the
        # north celestial pole lines up with ICRF +Z. vtkSkybox bakes
        # its own (pole=+Y, RA=0=-Z) convention into the shader and
        # ignores both SetUserTransform and SetFloorPlane/Right for the
        # Sphere projection, so the only clean fix is to physically
        # rotate the texture pixels and feed the rotated image in.
        # One-time cost (~1-2 s for an 8K image), cached on disk.
        aligned = _ensure_icrf_aligned_skybox(self._skybox_texture)
        reader = self._make_image_reader(aligned)
        if reader is None:
            return
        reader.Update()
        texture = vtkTexture()
        texture.SetInputConnection(reader.GetOutputPort())
        texture.InterpolateOn()
        texture.MipmapOn()
        skybox = vtkSkybox()
        skybox.SetTexture(texture)
        skybox.SetProjection(vtkSkybox.Sphere)
        # NOTE: SetFloorPlane / SetFloorRight are deliberately NOT set
        # here. vtkSkybox's Sphere projection ignores them (they only
        # affect Floor projection mode); the ICRF alignment is done by
        # rotating the texture pixels themselves in
        # `_ensure_icrf_aligned_skybox` above.
        # Exclude the skybox geometry from the FAR renderer's bbox so
        # ResetCameraClippingRange (called from _sync_far_camera on
        # every camera nudge) does not clamp the near/far planes around
        # the unit cube the skybox uses internally. Without this the
        # skybox starts clipping into streaks as soon as the user
        # rotates the trackball -- the camera position quickly leaves
        # the tight box and the renderer drops the now-out-of-range
        # background fragments. Skybox shaders force depth = 1 anyway,
        # so they never need the renderer's frustum to "contain" them.
        skybox.SetUseBounds(False)
        # We add to the FAR renderer so the actor sits behind the
        # bodies / trajectories on layer 1.
        self._renderer_far.AddActor(skybox)
        self._skybox_actor = skybox

    def set_central_body_texture(self, path: Path | None) -> None:
        """Default equirectangular texture used by subsequent
        add_central_body() calls when no explicit `texture_path` is
        passed. None reverts to the flat-grey sphere."""
        self._default_central_texture = path

    def add_central_body(self, radius_km: float = MOON_RADIUS_KM,
                          color: tuple[float, float, float] = (0.55, 0.55, 0.58),
                          resolution: int = 64,
                          texture_path: Path | None = None) -> None:
        """Add a sphere centred at the origin. If `texture_path`
        resolves to a readable JPEG / PNG (or the default set via
        `set_central_body_texture` does), the sphere is uv-mapped with
        that equirectangular image; otherwise a flat-colour sphere is
        drawn. Resolution is the number of latitude / longitude bands.

        `vtkTexturedSphereSource` is used for the textured case
        because it already generates the equirectangular UV coords
        spody-friendly Moon mosaics expect (longitude 0..360 mapped
        to u 0..1, latitude -90..+90 to v 0..1)."""
        chosen = texture_path if texture_path is not None else self._default_central_texture
        reader = self._make_image_reader(chosen) if chosen else None

        if reader is not None:
            sphere = vtkTexturedSphereSource()
            sphere.SetRadius(radius_km)
            sphere.SetThetaResolution(resolution)
            sphere.SetPhiResolution(resolution // 2)

            texture = vtkTexture()
            texture.SetInputConnection(reader.GetOutputPort())
            texture.InterpolateOn()

            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(sphere.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.SetTexture(texture)
            actor.GetProperty().SetAmbient(0.50)
            actor.GetProperty().SetDiffuse(0.55)
        else:
            sphere = vtkSphereSource()
            sphere.SetRadius(radius_km)
            sphere.SetThetaResolution(resolution)
            sphere.SetPhiResolution(resolution // 2)
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(sphere.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(*color)
            actor.GetProperty().SetAmbient(0.30)
            actor.GetProperty().SetDiffuse(0.70)
        # Central body is primary by definition -- it lives on the
        # top (sharp) layer so its silhouette never gets z-fought by
        # a far-away body sphere.
        self._renderer.AddActor(actor)
        # Stash so set_central_body_animated_orientation can later
        # bind a libration animation onto it.
        self._central_body_actor = actor

    def add_secondary_body(self, position_km: tuple[float, float, float],
                            radius_km: float,
                            color: tuple[float, float, float] = (0.55, 0.55, 0.58),
                            resolution: int = 64,
                            texture_path: Path | None = None,
                            label: str | None = None) -> None:
        """Static body sphere placed at an arbitrary position. Used by
        the CR3BP 3D scene where the two primaries sit at fixed synodic
        coordinates and neither is "central" in the HF sense (the
        barycenter is at the scene origin instead).
        Unlike `add_central_body`, this method does NOT stash the actor
        for animated orientation: CR3BP primaries are fixed in the
        synodic frame by construction. `label`, if given, attaches a
        small billboard text actor next to the body so the user knows
        which primary is which without checking the scene table."""
        chosen = texture_path
        reader = self._make_image_reader(chosen) if chosen else None

        if reader is not None:
            sphere = vtkTexturedSphereSource()
            sphere.SetRadius(radius_km)
            sphere.SetThetaResolution(resolution)
            sphere.SetPhiResolution(resolution // 2)
            texture = vtkTexture()
            texture.SetInputConnection(reader.GetOutputPort())
            texture.InterpolateOn()
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(sphere.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.SetTexture(texture)
            actor.GetProperty().SetAmbient(0.50)
            actor.GetProperty().SetDiffuse(0.55)
        else:
            sphere = vtkSphereSource()
            sphere.SetRadius(radius_km)
            sphere.SetThetaResolution(resolution)
            sphere.SetPhiResolution(resolution // 2)
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(sphere.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(*color)
            actor.GetProperty().SetAmbient(0.30)
            actor.GetProperty().SetDiffuse(0.70)
        actor.SetPosition(float(position_km[0]),
                          float(position_km[1]),
                          float(position_km[2]))
        self._renderer.AddActor(actor)

        if label:
            billboard = vtkBillboardTextActor3D()
            billboard.SetInput(str(label))
            # Offset the label by 1.5 radii along +z so it sits clearly
            # above the body in the default view; VTK billboards keep
            # the text facing the camera regardless of orbit.
            billboard.SetPosition(float(position_km[0]),
                                  float(position_km[1]),
                                  float(position_km[2]) + 1.5 * radius_km)
            billboard.GetTextProperty().SetFontSize(14)
            billboard.GetTextProperty().SetColor(1.0, 1.0, 1.0)
            self._renderer.AddActor(billboard)

    @staticmethod
    def _make_image_reader(path: Path):
        """Pick a vtk image reader for an equirectangular body texture.
        Returns None if the file is missing, the extension is
        unsupported, or the meridian-alignment cache can't be built --
        the caller then falls back to the flat-colour sphere.

        Every input (JPEG / PNG / TIFF) is routed through the same
        Pillow-based transcoder that bakes in the W/2 longitude roll
        (see `_ensure_uv0_meridian_cache`): published equirectangular
        body maps (NASA SVS, Solar System Scope, ...) place the prime
        meridian at the *centre* column, while
        `vtkTexturedSphereSource` maps texture u=0 to the body's
        local +X. Without the pre-roll the surface lands 180° off,
        which only became visually obvious once the 3rd-body markers
        started spinning. TIFF inputs additionally avoid
        vtkTIFFReader's libtiff variant pitfalls."""
        path = Path(path)
        if not path.is_file():
            return None
        ext = path.suffix.lower()
        if ext not in {".jpg", ".jpeg", ".png", ".tif", ".tiff"}:
            return None
        png_cache = VtkCanvas._ensure_uv0_meridian_cache(path)
        if png_cache is None:
            return None
        reader = vtkPNGReader()
        reader.SetFileName(str(png_cache))
        return reader

    @staticmethod
    def _ensure_uv0_meridian_cache(src_path: Path) -> Path | None:
        """Return the path of a VTK-ready PNG transcode of `src_path`,
        with the prime meridian rolled from the image centre (u=0.5)
        to the left edge (u=0). Creates the cache via Pillow if
        missing or stale. Returns None if Pillow is unavailable or
        the conversion fails -- the caller then drops back to the
        flat-grey sphere with no further noise.

        Why the roll: NASA SVS, Solar System Scope, and most published
        planetary / lunar equirectangular maps place the prime
        meridian at the *centre* of the image (column W/2 is lon=0,
        column 0 is lon=-180). vtkTexturedSphereSource on the other
        hand maps the texture's u=0 column onto theta=0 in scene
        coordinates -- the body's local +X axis, which is where the
        prime meridian *should* land in the body-fixed frame. Without
        the pre-roll the surface lands 180° off, which the spinning
        3rd-body markers expose as a "lit Australia at 14 UTC" sort
        of bug.

        Cache filename suffix `_uv0` advertises what's baked in (also
        bypasses any earlier `<stem>.png` cache the v1 code may have
        left behind, and the lunar `<stem>_pa.png` rename predecessor)."""
        cache = src_path.with_name(src_path.stem + "_uv0.png")
        if cache.is_file() and cache.stat().st_mtime >= src_path.stat().st_mtime:
            return cache
        try:
            from PIL import Image
        except ImportError:
            return None
        try:
            with Image.open(src_path) as img:
                arr = np.asarray(img.convert("RGB"))
            arr = np.roll(arr, arr.shape[1] // 2, axis=1)
            Image.fromarray(arr).save(cache, format="PNG", optimize=False)
        except (OSError, ValueError):
            try:
                cache.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        return cache

    def add_trajectory(self, points_km: np.ndarray,
                        color: tuple[float, float, float] = (1.0, 0.85, 0.20),
                        line_width: float = 2.0,
                        endpoint_markers: bool = True,
                        source_path: Path | None = None) -> None:
        """Add a 3D polyline through `points_km` (Nx3, km in the
        central-body inertial frame). If `endpoint_markers` is true,
        a green sphere is placed at the first point and a red one at
        the last (sized to ~0.5% of the trajectory bounding diagonal).

        `source_path` (optional) registers the polyline actor as a
        pickable target. On Ctrl+left-click the registered pick
        callback receives this path."""
        n = len(points_km)
        if n < 2:
            return

        # Polyline geometry: one vtkPoints with N entries, plus a
        # single vtkPolyLine cell referencing 0..N-1.
        vpoints = vtkPoints()
        vpoints.SetNumberOfPoints(n)
        for i, (x, y, z) in enumerate(points_km):
            vpoints.SetPoint(i, x, y, z)

        polyline = vtkPolyLine()
        polyline.GetPointIds().SetNumberOfIds(n)
        for i in range(n):
            polyline.GetPointIds().SetId(i, i)

        cells = vtkCellArray()
        cells.InsertNextCell(polyline)

        poly = vtkPolyData()
        poly.SetPoints(vpoints)
        poly.SetLines(cells)

        mapper = vtkPolyDataMapper()
        mapper.SetInputData(poly)

        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetLineWidth(line_width)
        self._renderer.AddActor(actor)

        if source_path is not None:
            self._trajectory_actors.append((actor, source_path))

        if endpoint_markers:
            diag = float(np.linalg.norm(points_km.max(axis=0) - points_km.min(axis=0)))
            marker_r = max(diag * 0.005, 1.0)   # ≥ 1 km even on tiny arcs
            self._add_marker_sphere(points_km[0],  marker_r, color=(0.0, 0.9, 0.0))
            self._add_marker_sphere(points_km[-1], marker_r, color=(0.95, 0.2, 0.2))

    def add_animated_trajectory(self, points_km: np.ndarray,
                                 times_s: np.ndarray,
                                 color: tuple[float, float, float] = (1.0, 0.85, 0.20),
                                 line_width: float = 2.0,
                                 source_path: Path | None = None,
                                 marker_radius_km: float | None = None,
                                 marker_texture_path: Path | None = None,
                                 marker_R_bf_to_scene_sequence: np.ndarray | None = None,
                                 is_decoration: bool = False) -> None:
        """Like `add_trajectory` but also registers an animation
        handle: a coloured sphere marker (sized to ~1% of the scene
        diagonal, ≥2 km) that `set_animation_time` slides along the
        polyline, and a record of the (times, points) arrays so the
        trail clipping mode can rebuild a partial polyline at each
        frame.

        `times_s` is the sim time at each sample (monotonic, seconds);
        the same length as `points_km`. The animation bar above the
        canvas exposes the (t_min, t_max) of this and every other
        animated trajectory via `animation_time_range`.

        Endpoint marker spheres are NOT drawn here: the moving marker
        replaces the end marker visually, and the start of the orbit
        is implied by the trail's growing tip when trail mode is on.
        Source-path picking is still wired through `_trajectory_actors`
        when `source_path` is given."""
        n = len(points_km)
        if n < 2 or len(times_s) != n:
            return
        pts = np.asarray(points_km, dtype=float)
        ts  = np.asarray(times_s,   dtype=float)

        # --- Static polyline (mapper input mutates when trail is on) ---
        vpoints = vtkPoints()
        vpoints.SetNumberOfPoints(n)
        for i, (x, y, z) in enumerate(pts):
            vpoints.SetPoint(i, x, y, z)
        polyline = vtkPolyLine()
        polyline.GetPointIds().SetNumberOfIds(n)
        for i in range(n):
            polyline.GetPointIds().SetId(i, i)
        cells = vtkCellArray()
        cells.InsertNextCell(polyline)
        poly = vtkPolyData()
        poly.SetPoints(vpoints)
        poly.SetLines(cells)

        mapper = vtkPolyDataMapper()
        mapper.SetInputData(poly)
        line_actor = vtkActor()
        line_actor.SetMapper(mapper)
        line_actor.GetProperty().SetColor(*color)
        line_actor.GetProperty().SetLineWidth(line_width)
        target_renderer = self._renderer_far if is_decoration else self._renderer
        target_renderer.AddActor(line_actor)

        # --- Moving marker sphere -------------------------------------
        # The marker is intentionally oversized vs the trajectory's
        # bounding diagonal (~3 %) and lit "from inside" via
        # Ambient=1 + LightingOff -- it reads as a glowing puck in the
        # otherwise scene-lit-from-the-Sun rendering, so it stands out
        # against both the dark background and the Moon surface.
        if marker_radius_km is not None:
            marker_r = float(marker_radius_km)
        else:
            diag = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))
            marker_r = max(diag * 0.030, 8.0)
        # Textured marker (used for 3rd-body planets/Moon so they read
        # as themselves even when rendered far away at real scale).
        # Falls back to the flat-color glowing puck path on any image-
        # read failure so we never lose the marker entirely.
        texture_reader = (self._make_image_reader(marker_texture_path)
                          if marker_texture_path is not None else None)
        marker = vtkActor()
        if texture_reader is not None:
            # vtkTexturedSphereSource is always origin-centered (no
            # SetCenter); the marker is moved via actor.SetPosition()
            # like for the flat-colour path. Higher angular resolution
            # here (vs the colored sphere) makes the texture's seams /
            # poles look right when the camera zooms in.
            sphere = vtkTexturedSphereSource()
            sphere.SetRadius(marker_r)
            sphere.SetThetaResolution(48)
            sphere.SetPhiResolution(24)
            m_mapper = vtkPolyDataMapper()
            m_mapper.SetInputConnection(sphere.GetOutputPort())
            marker.SetMapper(m_mapper)
            texture = vtkTexture()
            texture.SetInputConnection(texture_reader.GetOutputPort())
            texture.InterpolateOn()
            marker.SetTexture(texture)
            # Same lighting recipe as add_central_body so the textured
            # 3rd-body marker reads identically whether it's central
            # (LRO Moon-scene) or distant (GLONASS Earth-scene).
            marker.GetProperty().SetAmbient(0.50)
            marker.GetProperty().SetDiffuse(0.55)
        else:
            sphere = vtkSphereSource()
            sphere.SetRadius(marker_r)
            sphere.SetThetaResolution(24)
            sphere.SetPhiResolution(16)
            sphere.SetCenter(0.0, 0.0, 0.0)    # geometry centred; pose via SetPosition
            m_mapper = vtkPolyDataMapper()
            m_mapper.SetInputConnection(sphere.GetOutputPort())
            marker.SetMapper(m_mapper)
            marker.GetProperty().SetColor(*color)
            marker.GetProperty().SetAmbient(1.0)
            marker.GetProperty().SetDiffuse(0.0)
            marker.GetProperty().LightingOff()
        marker.SetPosition(*pts[0])            # park at start until first tick
        target_renderer.AddActor(marker)

        # --- White silhouette outline ---------------------------------
        # vtkPolyDataSilhouette emits the apparent-contour edges of the
        # sphere from the active camera's POV (recomputed every frame
        # by VTK itself when the camera moves). A white outline pops
        # the marker against the Moon's grey surface AND against the
        # near-black background. The silhouette actor is moved in
        # lockstep with the sphere -- one SetPosition call per tick.
        #
        # Skipped for textured markers (the Moon/planet body texture
        # is self-identifying and a hard white edge would only look
        # like an undesired halo at distance).
        silhouette_actor = None
        if texture_reader is None:
            silhouette = vtkPolyDataSilhouette()
            silhouette.SetInputConnection(sphere.GetOutputPort())
            # Silhouette uses the TOP camera's POV; far renderer's camera
            # is slaved to it so the contour is consistent regardless of
            # which layer the actor lives on.
            silhouette.SetCamera(self._renderer.GetActiveCamera())
            silhouette.SetEnableFeatureAngle(0)
            s_mapper = vtkPolyDataMapper()
            s_mapper.SetInputConnection(silhouette.GetOutputPort())
            silhouette_actor = vtkActor()
            silhouette_actor.SetMapper(s_mapper)
            silhouette_actor.GetProperty().SetColor(1.0, 1.0, 1.0)
            silhouette_actor.GetProperty().SetLineWidth(2.5)
            silhouette_actor.GetProperty().LightingOff()
            silhouette_actor.SetPosition(*pts[0])
            target_renderer.AddActor(silhouette_actor)

        if source_path is not None:
            self._trajectory_actors.append((line_actor, source_path))

        # Marker attitude animation: required for bodies that have a
        # body-fixed frame (Earth's ITRF, Moon's PA) so the texture
        # rotates under the surface features over the run. Shape gate
        # is loud — silently dropping a wrong shape would mean the
        # texture freezes with no visible error.
        R_marker: np.ndarray | None = None
        if marker_R_bf_to_scene_sequence is not None:
            R_marker = np.asarray(marker_R_bf_to_scene_sequence,
                                   dtype=float)
            if R_marker.shape != (n, 3, 3):
                raise ValueError(
                    "marker_R_bf_to_scene_sequence must have shape "
                    f"({n}, 3, 3); got {R_marker.shape}")
        self._anim_handles.append(_AnimHandle(
            times=ts, points=pts, line_actor=line_actor,
            full_poly=poly, marker_actor=marker,
            silhouette_actor=silhouette_actor, color=color,
            marker_R_sequence=R_marker))

        # If the user already turned the trail mode on for the previous
        # scene, mirror that on the new handle so all polylines start
        # from the same display mode.
        if self._trail_enabled:
            self._refresh_trail_poly(self._anim_handles[-1], ts[0])

    def add_animated_arrow(self, times_s: np.ndarray,
                            positions_km: np.ndarray,
                            color: tuple[float, float, float],
                            length_km: float,
                            is_decoration: bool = False) -> None:
        """Add a fixed-length direction arrow anchored at the origin
        that, on every `set_animation_time(t)` call, re-orients to
        point toward the (linearly interpolated) `positions_km` value
        at time `t`. Used for third-body indicators: even when Earth
        sits at 384k km and Sun at 150M km (far outside the
        Moon-zoom default viewport), the arrows tell the user where
        each body is relative to the central body.

        `length_km` is the arrow's scene-units length and stays
        constant; only orientation animates. Arrows default to
        `is_decoration=False`: they belong on the sharp top layer
        with the Moon and the orbit (UI indicators sharing the same
        tight clip range), even when they POINT at far-away decoration
        bodies. The animation bar's [t_min, t_max] is the union
        across handles + arrows, so arrows alone (no animated
        trajectory) still light up the bar."""
        n = len(times_s)
        if n < 1 or len(positions_km) != n:
            return
        ts  = np.asarray(times_s,     dtype=float)
        pos = np.asarray(positions_km, dtype=float)
        # Initial orientation toward positions[0]. _make_arrow_actor
        # bakes scale + rotation + translation into the actor's
        # transform; we then RESET orientation on every tick before
        # applying the new rotation so it doesn't accumulate.
        actor = self._make_arrow_actor(
            origin_km=(0.0, 0.0, 0.0),
            direction=tuple(pos[0]),
            length_km=float(length_km),
            color=color,
            shaft_radius=0.015,
            tip_radius=0.045,
            tip_length=0.18,
        )
        # Make the arrow read against the dark background without
        # needing a scene light to hit it.
        actor.GetProperty().SetAmbient(1.0)
        actor.GetProperty().SetDiffuse(0.0)
        actor.GetProperty().LightingOff()
        target_renderer = self._renderer_far if is_decoration else self._renderer
        target_renderer.AddActor(actor)
        self._anim_arrows.append(_AnimArrowHandle(
            times=ts, positions=pos, arrow_actor=actor))

    def add_animated_frame_triad(self, times_s: np.ndarray,
                                   R_sequence: np.ndarray,
                                   origin_km: tuple[float, float, float] = (0.0, 0.0, 0.0),
                                   length_km: float = 1.4 * MOON_RADIUS_KM,
                                   colors_xyz: tuple[tuple[float, float, float], ...] = (
                                       (1.0, 0.25, 0.25),
                                       (0.30, 0.95, 0.35),
                                       (0.35, 0.55, 1.00)),
                                   labels_xyz: tuple[str, str, str] | None = None,
                                   label_size: int = 16,
                                   shaft_radius: float = 0.006,
                                   tip_radius: float = 0.022,
                                   tip_length: float = 0.10,
                                   opacity: float = 1.0,
                                   is_decoration: bool = False) -> None:
        """Time-varying analogue of `add_frame_triad`. `R_sequence`
        is (N, 3, 3) with columns = local axes expressed in scene
        coordinates at each `times_s[i]`. On every `set_animation_time`
        call we interpolate the rotation matrix and re-orient the
        three arrow actors (and reposition the three labels).

        The frame-switch agnostic design: today this draws the PA
        triad in an ICRF scene with R = R_pa_to_icrf; a future PA
        scene would pass R = R_icrf_to_pa for an animated ICRF triad,
        no other code change needed."""
        n = len(times_s)
        if n < 1 or len(R_sequence) != n:
            return
        ts  = np.asarray(times_s,    dtype=float)
        Rs  = np.asarray(R_sequence, dtype=float)
        if Rs.shape != (n, 3, 3):
            raise ValueError(
                f"R_sequence must be (N, 3, 3), got {Rs.shape}")
        origin = np.asarray(origin_km, dtype=float)
        target_renderer = self._renderer_far if is_decoration else self._renderer

        # Build the three arrows from the initial pose. Each arrow's
        # orientation is rewritten on every tick; only the scale and
        # the per-axis color stay constant.
        arrow_actors: list[vtkActor | None] = []
        label_actors: list = []
        R0 = Rs[0]
        for axis_idx in range(3):
            direction = R0[:, axis_idx]
            norm = float(np.linalg.norm(direction))
            if norm < 1.0e-12:
                arrow_actors.append(None)
                label_actors.append(None)
                continue
            direction = direction / norm
            color = colors_xyz[axis_idx]
            arrow = self._make_arrow_actor(
                origin_km=tuple(origin),
                direction=tuple(direction),
                length_km=length_km,
                color=color,
                shaft_radius=shaft_radius,
                tip_radius=tip_radius,
                tip_length=tip_length,
            )
            if opacity < 1.0:
                arrow.GetProperty().SetOpacity(opacity)
            target_renderer.AddActor(arrow)
            arrow_actors.append(arrow)

            if labels_xyz is not None:
                tip = origin + direction * length_km * 1.05
                label = self._make_text_label(
                    tip, labels_xyz[axis_idx], color, label_size)
                if opacity < 1.0:
                    label.GetTextProperty().SetOpacity(opacity)
                target_renderer.AddActor(label)
                label_actors.append(label)
            else:
                label_actors.append(None)

        self._anim_triads.append(_AnimTriadHandle(
            times=ts, R_sequence=Rs,
            arrow_actors=arrow_actors,
            label_actors=label_actors,
            length_km=length_km,
            origin=origin,
            has_labels=labels_xyz is not None,
        ))

    def set_central_body_animated_orientation(self, times_s: np.ndarray,
                                                R_sequence: np.ndarray
                                                ) -> None:
        """Bind a time-varying orientation onto the central body
        actor previously installed by `add_central_body`. Each tick
        rewrites the actor's UserMatrix from `R_sequence[idx]`, so
        the texture (Moon's prime meridian, mascons, mares) tracks
        the body's physical attitude alongside the PA triad.

        Without this, animating the PA triad would visibly desync
        from the lunar surface features -- the axes would spin over a
        frozen surface. Calling this from the same plot function that
        adds the animated triad keeps the two perfectly in lockstep.

        No-op when add_central_body wasn't called first."""
        if self._central_body_actor is None:
            return
        n = len(times_s)
        if n < 1 or len(R_sequence) != n:
            return
        Rs = np.asarray(R_sequence, dtype=float)
        if Rs.shape != (n, 3, 3):
            raise ValueError(
                f"R_sequence must be (N, 3, 3), got {Rs.shape}")
        self._anim_body = _AnimBodyHandle(
            times=np.asarray(times_s, dtype=float),
            R_sequence=Rs,
            actor=self._central_body_actor,
        )

    def has_animations(self) -> bool:
        return (bool(self._anim_handles) or bool(self._anim_arrows)
                or bool(self._anim_triads) or self._anim_body is not None)

    def animation_time_range(self) -> tuple[float, float] | None:
        """(t_min, t_max) across every registered handle, or None when
        nothing animated is in the scene. The slider above the canvas
        uses this to map its 0..1 position to sim-time, and the play
        button stops when t reaches t_max."""
        if not self.has_animations():
            return None
        all_times: list[np.ndarray] = [h.times for h in self._anim_handles] \
                                    + [a.times for a in self._anim_arrows] \
                                    + [t.times for t in self._anim_triads]
        if self._anim_body is not None:
            all_times.append(self._anim_body.times)
        t_min = min(float(ts[0])  for ts in all_times if len(ts))
        t_max = max(float(ts[-1]) for ts in all_times if len(ts))
        return t_min, t_max

    def set_trail_enabled(self, enabled: bool) -> None:
        """Toggle trail-clipping for every animated trajectory. When
        enabled, each polyline shows only the segment from its first
        sample up to the current animation time (set by the most
        recent `set_animation_time` call). When disabled (default),
        the full orbit polyline is always visible and only the marker
        moves."""
        if self._trail_enabled == enabled:
            return
        self._trail_enabled = enabled
        if not enabled:
            # Restore the full polyline as each mapper's input.
            for h in self._anim_handles:
                h.line_actor.GetMapper().SetInputData(h.full_poly)
        # When enabled, the next set_animation_time call paints the
        # right partial polyline; until then leave the full one in
        # place so a render before the first tick is not blank.

    def set_animation_time(self, t_s: float) -> None:
        """Move every marker to the interpolated position at sim time
        `t_s`. When trail mode is on, also rebuild each polyline to
        cover only [t_min_handle, t_s] (with the trailing tip exactly
        at the interpolated point so the marker visually 'pulls' the
        trail). t values outside a handle's range clamp to its
        endpoints -- so on an overlay where one orbit ends before
        another, the shorter one freezes at its last sample."""
        for h in self._anim_handles:
            pos = self._interp_point(h, t_s)
            if h.marker_R_sequence is not None:
                # Marker attitude AND position baked into a single
                # UserMatrix. Two reasons we do not just call
                # SetUserMatrix(R) + SetPosition(p): (a) VTK's
                # ComputeMatrix composes UserMatrix INSIDE the
                # position translation, which is right in theory but
                # surprising to debug; (b) leaving a stale
                # SetPosition from a previous rotation-less pass on
                # the same actor would silently double-translate.
                # The 4x4 we build is M = T(p) * R, so applying it
                # to a body-frame vertex v gives R*v + p directly.
                # SetPosition is cleared to (0,0,0) for this actor so
                # the rotated-position pipeline owns the placement.
                R_now = self._interp_R(h.times, h.marker_R_sequence, t_s)
                self._apply_pose(h.marker_actor, R_now, pos)
            else:
                h.marker_actor.SetPosition(*pos)
            if h.silhouette_actor is not None:
                h.silhouette_actor.SetPosition(*pos)
            if self._trail_enabled:
                self._refresh_trail_poly(h, t_s, tip_point=pos)
        for a in self._anim_arrows:
            # Interpolated body position; arrows only consume direction.
            d = self._interp_xyz(a.times, a.positions, t_s)
            self._orient_arrow_to(a.arrow_actor, d)
        for tr in self._anim_triads:
            R_now = self._interp_R(tr.times, tr.R_sequence, t_s)
            for axis_idx in range(3):
                arrow = tr.arrow_actors[axis_idx]
                if arrow is None:
                    continue
                axis = R_now[:, axis_idx]
                norm = float(np.linalg.norm(axis))
                if norm < 1.0e-12:
                    continue
                axis_unit = axis / norm
                self._orient_arrow_to(arrow, axis_unit)
                if tr.has_labels and tr.label_actors[axis_idx] is not None:
                    tip = tr.origin + axis_unit * tr.length_km * 1.05
                    tr.label_actors[axis_idx].SetPosition(*tip)
        if self._anim_body is not None:
            R_now = self._interp_R(self._anim_body.times,
                                    self._anim_body.R_sequence, t_s)
            self._apply_rotation_matrix(self._anim_body.actor, R_now)

    @staticmethod
    def _interp_xyz(times: np.ndarray, points: np.ndarray,
                     t: float) -> np.ndarray:
        """Linear interpolation of `points` at sim time `t`. Clamps
        to endpoints outside `times`. The output stride from
        spody.exe is typically 60 s, far smaller than anything
        visually relevant -- linear is enough; spline would just
        chew CPU. Used by both trajectory markers and direction
        arrows."""
        if t <= times[0]:
            return points[0]
        if t >= times[-1]:
            return points[-1]
        idx = int(np.searchsorted(times, t)) - 1
        t0, t1 = float(times[idx]), float(times[idx + 1])
        alpha = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        return points[idx] + alpha * (points[idx + 1] - points[idx])

    @classmethod
    def _interp_point(cls, h: _AnimHandle, t: float) -> np.ndarray:
        return cls._interp_xyz(h.times, h.points, t)

    @staticmethod
    def _interp_R(times: np.ndarray, R_sequence: np.ndarray,
                   t: float) -> np.ndarray:
        """Interpolated rotation matrix at sim time `t`. Linear
        component-wise interp between adjacent samples followed by
        Gram-Schmidt re-orthonormalisation -- proper slerp would
        cost more for no visible difference at the libration's slow
        rate (~0.5 deg per minute). Clamps to endpoints outside the
        sampled range."""
        if t <= times[0]:
            return R_sequence[0]
        if t >= times[-1]:
            return R_sequence[-1]
        idx = int(np.searchsorted(times, t)) - 1
        t0, t1 = float(times[idx]), float(times[idx + 1])
        alpha = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        R = R_sequence[idx] + alpha * (R_sequence[idx + 1] - R_sequence[idx])
        # Re-orthonormalise columns; a linearly-interpolated rotation
        # matrix is not strictly orthogonal, and feeding it to
        # _orient_arrow_to / SetUserMatrix would amplify a tiny
        # scaling error after many ticks.
        c0 = R[:, 0]; n0 = np.linalg.norm(c0)
        if n0 < 1.0e-12:
            return R_sequence[idx]
        c0 = c0 / n0
        c1 = R[:, 1] - np.dot(R[:, 1], c0) * c0
        n1 = np.linalg.norm(c1)
        if n1 < 1.0e-12:
            return R_sequence[idx]
        c1 = c1 / n1
        c2 = np.cross(c0, c1)
        return np.column_stack((c0, c1, c2))

    @staticmethod
    def _apply_rotation_matrix(actor: vtkActor,
                                 R: np.ndarray) -> None:
        """Set `actor`'s UserMatrix to a pure rotation (R, no
        translation, no scale). Replaces any previous UserMatrix on
        the actor. Used by the central-body libration animation."""
        m = vtkMatrix4x4()
        for i in range(3):
            for j in range(3):
                m.SetElement(i, j, float(R[i, j]))
            m.SetElement(i, 3, 0.0)
        m.SetElement(3, 0, 0.0); m.SetElement(3, 1, 0.0)
        m.SetElement(3, 2, 0.0); m.SetElement(3, 3, 1.0)
        actor.SetUserMatrix(m)

    @staticmethod
    def _apply_pose(actor: vtkActor,
                     R: np.ndarray,
                     position_km: np.ndarray) -> None:
        """Bake (R, position) into a single 4x4 UserMatrix and clear
        the actor's SetPosition. The matrix is M = T(p) * R so
        world = M @ model_bf = R @ model_bf + p directly. Avoids the
        VTK composition order surprise where UserMatrix is applied
        INSIDE Position translation, and dodges leaving a stale
        SetPosition value behind from a previous tick where the
        rotation-less path called SetPosition explicitly."""
        m = vtkMatrix4x4()
        for i in range(3):
            for j in range(3):
                m.SetElement(i, j, float(R[i, j]))
            m.SetElement(i, 3, float(position_km[i]))
        m.SetElement(3, 0, 0.0); m.SetElement(3, 1, 0.0)
        m.SetElement(3, 2, 0.0); m.SetElement(3, 3, 1.0)
        actor.SetPosition(0.0, 0.0, 0.0)
        actor.SetUserMatrix(m)

    @staticmethod
    def _orient_arrow_to(actor: vtkActor,
                          direction: np.ndarray) -> None:
        """Reset `actor`'s rotation and re-orient so its local +X
        (the natural axis of `vtkArrowSource`) points along
        `direction`. The actor's position and scale are left alone --
        only rotation is rewritten, so this is cheap to call per
        frame. Identical math to `_make_arrow_actor`; centralised
        here so the per-tick update and the initial pose stay in
        sync."""
        dx, dy, dz = float(direction[0]), float(direction[1]), float(direction[2])
        dn = math.sqrt(dx * dx + dy * dy + dz * dz)
        actor.SetOrientation(0.0, 0.0, 0.0)
        if dn < 1.0e-12:
            return
        dx, dy, dz = dx / dn, dy / dn, dz / dn
        axis_y, axis_z = -dz, dy
        axis_len = math.hypot(axis_y, axis_z)
        if axis_len < 1.0e-12:
            if dx < 0.0:
                actor.RotateWXYZ(180.0, 0.0, 1.0, 0.0)
        else:
            angle = math.degrees(math.acos(max(-1.0, min(1.0, dx))))
            actor.RotateWXYZ(angle, 0.0, axis_y, axis_z)

    def _refresh_trail_poly(self, h: _AnimHandle, t: float,
                             tip_point: np.ndarray | None = None) -> None:
        """Rebuild a vtkPolyData containing only the samples in
        `h.points` whose `h.times` value is ≤ t, with an extra final
        point at `tip_point` (default = interp at t) so the trail
        ends exactly under the marker. Swaps the new poly into the
        line actor's mapper.

        Cost is O(N) per call but N is the sample count of one
        trajectory (typically <2000); rebuilding is cheaper than
        clipping in OpenGL via per-vertex visibility, and avoids
        keeping a duplicate vtkPoints around per frame."""
        ts = h.times
        # Number of samples in [t_start, t]; this is the count BEFORE
        # we append the interpolated tip.
        cut = int(np.searchsorted(ts, t, side="right"))
        if cut < 1:
            # Pre-start: draw an empty cell (one isolated point at t_min
            # so the actor is valid but invisible).
            cut = 1
        tip = tip_point if tip_point is not None else self._interp_point(h, t)
        # Build new geometry: cut points from the file + 1 interp tip.
        # When t is past the last sample, cut == N and the tip equals
        # the last sample -- the duplicate is harmless visually.
        n_out = cut + 1
        vpts = vtkPoints()
        vpts.SetNumberOfPoints(n_out)
        for i in range(cut):
            x, y, z = h.points[i]
            vpts.SetPoint(i, x, y, z)
        vpts.SetPoint(cut, *tip)

        polyline = vtkPolyLine()
        polyline.GetPointIds().SetNumberOfIds(n_out)
        for i in range(n_out):
            polyline.GetPointIds().SetId(i, i)
        cells = vtkCellArray()
        cells.InsertNextCell(polyline)
        poly = vtkPolyData()
        poly.SetPoints(vpts)
        poly.SetLines(cells)
        h.line_actor.GetMapper().SetInputData(poly)

    def add_sun_arrow(self, direction: tuple[float, float, float],
                       length_km: float = 5.0 * MOON_RADIUS_KM,
                       color: tuple[float, float, float] = (1.0, 0.85, 0.20)) -> None:
        """Add an arrow originating at the scene origin and pointing
        toward `direction` (unit vector). The arrow length is fixed in
        km (default ~5 Moon radii) so it stays visible regardless of
        the trajectory scale. Shaft/tip dimensions match the frame-
        triad defaults so the Sun arrow reads as one more axis in the
        scene without dominating it."""
        self._renderer.AddActor(self._make_arrow_actor(
            origin_km=(0.0, 0.0, 0.0),
            direction=direction,
            length_km=length_km,
            color=color,
            shaft_radius=0.006,
            tip_radius=0.022,
            tip_length=0.10,
        ))

    def add_frame_triad(self, origin_km: tuple[float, float, float] = (0.0, 0.0, 0.0),
                        basis_in_scene=None,
                        length_km: float = 1.4 * MOON_RADIUS_KM,
                        colors_xyz: tuple[tuple[float, float, float], ...] = (
                            (1.0, 0.25, 0.25),
                            (0.30, 0.95, 0.35),
                            (0.35, 0.55, 1.00)),
                        labels_xyz: tuple[str, str, str] | None = None,
                        label_size: int = 16,
                        shaft_radius: float = 0.006,
                        tip_radius: float = 0.022,
                        tip_length: float = 0.10,
                        opacity: float = 1.0) -> None:
        """Draw a coloured X/Y/Z arrow triad rooted at `origin_km`.

        `basis_in_scene` is a 3x3 matrix whose columns are the three
        unit vectors of the local frame expressed in scene
        coordinates: column 0 is the local X axis direction, etc.
        Pass numpy.eye(3) (or None) for an axis-aligned triad. For
        e.g. ICRF axes in a PA-aligned scene, pass the
        ICRF-to-scene rotation matrix straight in -- it transports
        ICRF basis vectors into the scene frame.

        Labels (3-tuple) are rendered as billboard text at each
        arrow tip and always face the camera. When `labels_xyz` is
        None no labels are drawn (handy for the bare-axes case).

        `opacity` (0..1) applies to both the arrow actors and the
        labels. Useful for the 'secondary' triad in a scene where a
        primary frame already carries the bright RGB triplet -- the
        secondary stays readable but stops competing for attention."""
        if basis_in_scene is None:
            basis = np.eye(3)
        else:
            basis = np.asarray(basis_in_scene, dtype=float)
        if basis.shape != (3, 3):
            raise ValueError(
                f"basis_in_scene must be 3x3, got {basis.shape}")
        origin = np.asarray(origin_km, dtype=float)
        for axis_idx in range(3):
            direction = basis[:, axis_idx]
            norm = float(np.linalg.norm(direction))
            if norm < 1.0e-12:
                continue
            direction = direction / norm
            color = colors_xyz[axis_idx]
            arrow_actor = self._make_arrow_actor(
                origin_km=tuple(origin),
                direction=tuple(direction),
                length_km=length_km,
                color=color,
                shaft_radius=shaft_radius,
                tip_radius=tip_radius,
                tip_length=tip_length,
            )
            if opacity < 1.0:
                arrow_actor.GetProperty().SetOpacity(opacity)
            self._renderer.AddActor(arrow_actor)
            if labels_xyz is not None:
                tip = origin + direction * length_km * 1.05
                label_actor = self._make_text_label(
                    tip, labels_xyz[axis_idx], color, label_size,
                )
                # vtkBillboardTextActor3D draws via its text property's
                # frame opacity, not the actor's GetProperty().Opacity.
                if opacity < 1.0:
                    label_actor.GetTextProperty().SetOpacity(opacity)
                self._renderer.AddActor(label_actor)

    @staticmethod
    def _make_arrow_actor(origin_km: tuple[float, float, float],
                           direction: tuple[float, float, float],
                           length_km: float,
                           color: tuple[float, float, float],
                           shaft_radius: float = 0.015,
                           tip_radius: float = 0.045,
                           tip_length: float = 0.18) -> vtkActor:
        """Build a vtkArrowSource actor pointing from `origin_km` toward
        `direction` (need not be unit-length; normalised inside),
        scaled to `length_km`. Shared by add_sun_arrow and the frame
        triad so both render with identical stylings.

        `vtkArrowSource` produces a unit arrow along +X; we rotate
        with `RotateWXYZ(angle, axis)` where axis = (1,0,0) × dir,
        then translate to the origin. The transform order matters --
        scale and rotation must be set before SetPosition so VTK
        composes them right-to-left as (T R S)."""
        arrow = vtkArrowSource()
        arrow.SetTipResolution(24)
        arrow.SetShaftResolution(24)
        arrow.SetShaftRadius(shaft_radius)
        arrow.SetTipRadius(tip_radius)
        arrow.SetTipLength(tip_length)

        mapper = vtkPolyDataMapper()
        mapper.SetInputConnection(arrow.GetOutputPort())

        actor = vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetAmbient(0.5)

        # Normalise the direction so the axis = (1,0,0) × d formula
        # below produces a unit rotation axis -- VTK's RotateWXYZ
        # internally normalises but doing it here avoids ambiguity on
        # tiny inputs.
        dx, dy, dz = direction
        dn = math.sqrt(dx * dx + dy * dy + dz * dz)
        if dn < 1.0e-12:
            return actor
        dx, dy, dz = dx / dn, dy / dn, dz / dn

        # Rotation from +X to `direction`. Axis = (1,0,0) × d = (0, -d_z, d_y).
        axis_y, axis_z = -dz, dy
        axis_len = math.hypot(axis_y, axis_z)
        if axis_len < 1.0e-12:
            if dx < 0.0:
                actor.RotateWXYZ(180.0, 0.0, 1.0, 0.0)
        else:
            angle = math.degrees(math.acos(max(-1.0, min(1.0, dx))))
            actor.RotateWXYZ(angle, 0.0, axis_y, axis_z)

        actor.SetScale(length_km, length_km, length_km)
        actor.SetPosition(float(origin_km[0]),
                          float(origin_km[1]),
                          float(origin_km[2]))
        return actor

    @staticmethod
    def _make_text_label(position_km, text: str,
                          color: tuple[float, float, float],
                          font_size: int) -> vtkBillboardTextActor3D:
        """Camera-facing 3D text anchored at `position_km`. Used by
        add_frame_triad to label each axis tip with e.g. 'X_pa' /
        'Y_icrf'. vtkBillboardTextActor3D auto-orients to the camera
        on every render so the label stays readable through the
        trackball rotation."""
        actor = vtkBillboardTextActor3D()
        actor.SetPosition(float(position_km[0]),
                          float(position_km[1]),
                          float(position_km[2]))
        actor.SetInput(text)
        prop = actor.GetTextProperty()
        prop.SetColor(*color)
        prop.SetFontSize(font_size)
        prop.SetBold(True)
        prop.SetFontFamilyToCourier()
        prop.SetJustificationToCentered()
        prop.SetVerticalJustificationToCentered()
        return actor

    def add_point(self, position_km,
                   radius_km: float = 30.0,
                   color: tuple[float, float, float] = (1.0, 0.25, 0.25)) -> None:
        """Drop a small solid sphere at `position_km` (3-vector in the
        central-body inertial frame). Used by the impact-3D view to
        place one marker per impact location on the Moon. Default
        radius (30 km, ~1.7% of MOON_RADIUS_KM) is visible without
        crowding the surface at typical 1-Moon-radius zoom levels;
        callers that need a different scale (debris cloud sweep,
        node-crossing waypoints) pass `radius_km` explicitly.

        For more than a handful of markers prefer `add_points` -- a
        single GPU-instanced actor scales to tens of thousands of
        spheres, while one-call-per-marker via `add_point` is
        CPU-bound on per-actor state changes."""
        pos = np.asarray(position_km, dtype=float)
        self._add_marker_sphere(pos, radius_km, color)

    def add_points(self, positions_km, colors_rgb,
                    radius_km: float = 30.0) -> None:
        """Batch-render N marker spheres as a single GPU-instanced
        actor (vtkGlyph3DMapper). One draw call regardless of N --
        scales to tens of thousands of impact points without the
        per-actor overhead that brings the per-marker `add_point`
        path to its knees around 1k+ markers.

        `positions_km` is an (N, 3) float array in scene coordinates.
        `colors_rgb` is an (N, 3) array of floats in [0..1] (typical
        matplotlib colormap output) -- internally converted to a
        per-point uchar RGB scalar array so the mapper colours each
        instance from its own value rather than the actor's flat
        colour. `radius_km` applies uniformly to every instance.

        Empty inputs are a no-op (no actor added) so the caller does
        not need a separate length guard."""
        pts_arr = np.asarray(positions_km, dtype=float)
        cols_arr = np.asarray(colors_rgb, dtype=float)
        if pts_arr.ndim != 2 or pts_arr.shape[1] != 3:
            raise ValueError(
                f"positions_km must be (N, 3), got shape {pts_arr.shape}")
        if cols_arr.shape != pts_arr.shape:
            raise ValueError(
                f"colors_rgb must match positions_km shape; got "
                f"{cols_arr.shape} vs {pts_arr.shape}")
        n = pts_arr.shape[0]
        if n == 0:
            return

        vpts = vtkPoints()
        vpts.SetNumberOfPoints(n)
        for i in range(n):
            vpts.SetPoint(i,
                          float(pts_arr[i, 0]),
                          float(pts_arr[i, 1]),
                          float(pts_arr[i, 2]))

        # Per-point RGB as a 3-component uchar scalar array. The mapper
        # reads this through SelectColorArray; without ScalarVisibility
        # on, every instance would inherit the actor's flat colour.
        rgb = (np.clip(cols_arr, 0.0, 1.0) * 255.0).astype(np.uint8)
        c_arr = vtkUnsignedCharArray()
        c_arr.SetName("colors")
        c_arr.SetNumberOfComponents(3)
        c_arr.SetNumberOfTuples(n)
        for i in range(n):
            c_arr.SetTuple3(i, int(rgb[i, 0]), int(rgb[i, 1]), int(rgb[i, 2]))

        pd = vtkPolyData()
        pd.SetPoints(vpts)
        pd.GetPointData().SetScalars(c_arr)

        # Modest geometric fidelity per glyph: 16x8 keeps the surface
        # round at the typical zoom-out and stays cheap to instance.
        sphere = vtkSphereSource()
        sphere.SetRadius(radius_km)
        sphere.SetThetaResolution(16)
        sphere.SetPhiResolution(8)

        mapper = vtkGlyph3DMapper()
        mapper.SetInputData(pd)
        mapper.SetSourceConnection(sphere.GetOutputPort())
        mapper.SetScalarVisibility(True)
        mapper.SetScalarModeToUsePointFieldData()
        mapper.SelectColorArray("colors")

        actor = vtkActor()
        actor.SetMapper(mapper)
        self._renderer.AddActor(actor)

    def add_legend(self, items: list[tuple[str, tuple[float, float, float]]],
                    max_label_chars: int = 36) -> None:
        """Multi-line legend in the top-left corner of the viewport.

        Each item produces a coloured 16-sided 2D disk swatch followed
        by the label text on the same line, both in `items[i][1]`'s
        colour. The disk is drawn as a `vtkActor2D` with a regular-
        polygon source in display coordinates -- guaranteed visible
        regardless of the bundled font's Unicode coverage (VTK's
        Courier and Arial faces do not include U+25CF / U+2022, so
        text-only bullet prefixes render as blanks).

        Long labels are middle-truncated to `max_label_chars` for
        readability.
        """
        if not items:
            return
        for i, (label, color) in enumerate(items):
            r, g, b = color
            text = label
            if len(text) > max_label_chars:
                # Keep the last ~12 chars (usually the most informative
                # part of a filename) plus an ellipsis from the start.
                tail = max_label_chars - 4
                text = "..." + text[-tail:]
            y_norm = 0.97 - i * 0.035

            # Coloured disk swatch on the left.
            self._add_legend_dot(0.020, y_norm, color)

            # Text label to the right of the swatch.
            actor = vtkTextActor()
            actor.SetInput(text)
            prop = actor.GetTextProperty()
            prop.SetColor(r, g, b)
            prop.SetFontSize(12)
            prop.SetFontFamilyToArial()
            prop.SetBold(True)
            coord = actor.GetPositionCoordinate()
            coord.SetCoordinateSystemToNormalizedViewport()
            coord.SetValue(0.030, y_norm)
            self._renderer.AddActor2D(actor)

    def _add_legend_dot(self, x_norm: float, y_norm: float,
                          color: tuple[float, float, float],
                          radius_px: float = 5.0) -> None:
        """Filled 16-sided coloured disk used as a legend swatch.

        Built with `vtkRegularPolygonSource` + `vtkPolyDataMapper2D`
        / `vtkActor2D`. The mapper's transform coordinate is set to
        Display, so the polygon's `radius` is interpreted as pixels;
        the actor's position coordinate is normalised viewport, so
        the swatch sticks to the legend's left edge across resizes.
        """
        poly = vtkRegularPolygonSource()
        poly.SetNumberOfSides(16)
        poly.SetRadius(radius_px)
        poly.SetCenter(0.0, 0.0, 0.0)
        poly.GeneratePolygonOn()

        mapper = vtkPolyDataMapper2D()
        mapper.SetInputConnection(poly.GetOutputPort())
        # Without this the mapper interprets the polygon's vertex
        # coordinates as world units, scaling the swatch with the
        # camera (it disappears when the user zooms out the trajectory
        # scene). Display = raw pixels, which is what we want for a
        # fixed-size 2D overlay.
        disp = vtkCoordinate()
        disp.SetCoordinateSystemToDisplay()
        mapper.SetTransformCoordinate(disp)

        actor = vtkActor2D()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color)
        pos = actor.GetPositionCoordinate()
        pos.SetCoordinateSystemToNormalizedViewport()
        # Nudge the dot down a touch so its centre sits on the text
        # baseline rather than at the top of the line.
        pos.SetValue(x_norm, y_norm + 0.008)
        self._renderer.AddActor2D(actor)

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------
    def reset_camera(self) -> None:
        """Fit the camera to the bounding box of all currently added
        props. Call after the last add_* of a frame."""
        self._renderer.ResetCamera()

    def reset_camera_on_origin(self) -> None:
        """Auto-fit the top renderer on its own actors (Moon +
        spacecraft + arrows), pin the focal point on the scene
        origin, and propagate the resulting pose to the far renderer
        so the body sphere layer renders from the same POV.

        Each renderer keeps its OWN clipping range computed from
        only its own actors (the default behaviour of
        ResetCameraClippingRange). That is the whole point of the
        layered setup: top is tight (Moon-scale precision), far is
        wide (covers 150M-km Sun) without bleeding into each other.

        The interactor's trackball rotates around the focal point;
        pinning it at origin keeps the central body visually centred
        across rotations."""
        self._renderer.ResetCamera()
        cam = self._renderer.GetActiveCamera()
        cam.SetFocalPoint(0.0, 0.0, 0.0)
        self._renderer.ResetCameraClippingRange()
        # The far camera follows via the ModifiedEvent observer; we
        # also call it directly here so the first render shows the
        # bodies in the right place even before the user touches the
        # mouse (the observer would fire from the SetFocalPoint above
        # already, but being explicit is cheap and reads better).
        self._sync_far_camera()

    def _on_top_camera_modified(self, _caller, _event) -> None:
        """vtkCamera ModifiedEvent handler. Forwards the top
        renderer's camera pose to the far renderer and refreshes its
        own clipping range. Guarded against re-entry by
        `_syncing_cameras`: the writes we make on the far camera fire
        ITS ModifiedEvent, but our observer is on the TOP camera so
        the cascade stops there; the flag is belt-and-braces in case
        a future refactor adds a top-camera write inside sync."""
        if self._syncing_cameras:
            return
        self._syncing_cameras = True
        try:
            self._sync_far_camera()
        finally:
            self._syncing_cameras = False

    def _sync_far_camera(self) -> None:
        """Legacy shim: FAR and FRONT now share one vtkCamera object
        (see __init__). There is nothing left to copy across cameras;
        we just widen the shared clipping range so the FAR layer's
        decoration markers (Sun arrow ~150M km, etc.) survive the
        FRONT renderer's tight ResetCameraClippingRange. The near
        plane stays at whatever the FRONT bbox produced so
        Earth-surface z-precision is preserved."""
        cam = self._renderer.GetActiveCamera()
        near, far = cam.GetClippingRange()
        if far < 5.0e8:
            cam.SetClippingRange(near, 5.0e8)

    def render(self) -> None:
        """Trigger a repaint. Call once after a batch of add_* calls."""
        self._render_window.Render()

    def capture_camera_pose(self) -> dict:
        """Snapshot the current camera pose into a plain-Python dict.
        Used by the analysis panel to preserve the user's zoom / pan
        across scene rebuilds that are NOT a fresh file load (toggling
        Scene options, animation re-renders, ...)."""
        cam = self._renderer.GetActiveCamera()
        return {
            "position":   cam.GetPosition(),
            "focal":      cam.GetFocalPoint(),
            "up":         cam.GetViewUp(),
            "view_angle": cam.GetViewAngle(),
            "parallel":   cam.GetParallelScale(),
        }

    def restore_camera_pose(self, pose: dict) -> None:
        """Push a `capture_camera_pose` snapshot back onto the camera.
        Bypasses ResetCamera, so a re-render after a scene-options
        toggle keeps the user wherever they had panned/zoomed to."""
        cam = self._renderer.GetActiveCamera()
        cam.SetPosition(*pose["position"])
        cam.SetFocalPoint(*pose["focal"])
        cam.SetViewUp(*pose["up"])
        cam.SetViewAngle(pose["view_angle"])
        cam.SetParallelScale(pose["parallel"])

    # ------------------------------------------------------------------
    # Picking
    # ------------------------------------------------------------------
    def set_pick_callback(self, cb: Callable[[Path | None], None] | None) -> None:
        """Register a function invoked when the user Ctrl+left-clicks
        on a pickable trajectory. The argument is the source path of
        the picked trajectory, or `None` if the click missed all
        registered actors."""
        self._pick_callback = cb

    def _on_left_button_release(self, caller, _event) -> None:
        """VTK observer: Ctrl+left-click runs a prop pick and notifies
        the registered callback. Without Ctrl this is a no-op so the
        trackball camera keeps its normal rotate-on-drag behaviour."""
        if not caller.GetControlKey():
            return
        x, y = caller.GetEventPosition()
        picker = vtkPropPicker()
        picker.Pick(x, y, 0, self._renderer)
        prop = picker.GetViewProp()
        found_path: Path | None = None
        found_actor: vtkActor | None = None
        for actor, p in self._trajectory_actors:
            if actor is prop:
                found_actor = actor
                found_path = p
                break
        self._set_highlighted(found_actor)
        if self._pick_callback is not None:
            self._pick_callback(found_path)

    def _set_highlighted(self, actor: vtkActor | None) -> None:
        """Bump the line width of the picked actor and restore the
        previously highlighted one."""
        if self._highlighted_actor is not None:
            prop = self._highlighted_actor.GetProperty()
            prop.SetLineWidth(prop.GetLineWidth() - self._highlight_extra_lw)
        self._highlighted_actor = actor
        if actor is not None:
            prop = actor.GetProperty()
            prop.SetLineWidth(prop.GetLineWidth() + self._highlight_extra_lw)
        self.render()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _add_marker_sphere(self, center: np.ndarray, radius: float,
                            color: tuple[float, float, float]) -> None:
        s = vtkSphereSource()
        s.SetRadius(radius)
        s.SetCenter(float(center[0]), float(center[1]), float(center[2]))
        s.SetThetaResolution(16)
        s.SetPhiResolution(8)
        m = vtkPolyDataMapper()
        m.SetInputConnection(s.GetOutputPort())
        a = vtkActor()
        a.SetMapper(m)
        a.GetProperty().SetColor(*color)
        self._renderer.AddActor(a)
