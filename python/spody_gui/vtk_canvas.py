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
from pathlib import Path
from typing import Callable

# QVTKRenderWindowInteractor sniffs the active Qt binding from QT_API;
# force PySide6 so it does not accidentally pull in PyQt5/PyQt6 if they
# happen to be on PYTHONPATH.
os.environ.setdefault("QT_API", "pyside6")

import numpy as np
from PySide6.QtWidgets import QVBoxLayout, QWidget
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData, vtkPolyLine
from vtkmodules.vtkFiltersSources import (
    vtkArrowSource,
    vtkSphereSource,
    vtkTexturedSphereSource,
)
from vtkmodules.vtkIOImage import vtkJPEGReader, vtkPNGReader
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkBillboardTextActor3D,
    vtkPolyDataMapper,
    vtkPropPicker,
    vtkRenderer,
    vtkTextActor,
    vtkTexture,
)
# Importing the OpenGL rendering backend registers the implementation
# behind vtkRenderer / vtkRenderWindow; without it VTK silently fails
# to draw anything. Same story for the free-type text renderer used by
# the axes-actor labels.
import vtkmodules.vtkRenderingOpenGL2     # noqa: F401 -- side-effect import
import vtkmodules.vtkRenderingFreeType    # noqa: F401 -- side-effect import


# Mean radius of the Moon in km -- single source of truth for the
# central-body sphere in v0 (Moon-only).
MOON_RADIUS_KM = 1737.4


class VtkCanvas(QWidget):
    """Qt widget hosting a single VTK renderer. Stateless wrt to which
    file is currently shown -- `clear_scene` followed by add_*() calls
    rebuilds everything for the next plot."""

    def __init__(self) -> None:
        super().__init__()
        self._interactor = QVTKRenderWindowInteractor(self)
        self._render_window = self._interactor.GetRenderWindow()

        self._renderer = vtkRenderer()
        self._renderer.SetBackground(0.06, 0.07, 0.10)   # near-black; matches the terminal pane
        self._render_window.AddRenderer(self._renderer)

        style = vtkInteractorStyleTrackballCamera()
        self._interactor.SetInteractorStyle(style)

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
        self._interactor.AddObserver(
            "LeftButtonReleaseEvent", self._on_left_button_release
        )

        # Default equirectangular texture for the central body, applied
        # by add_central_body() when no explicit texture_path is given.
        # The panel sets this from Settings on every plot dispatch.
        self._default_central_texture: Path | None = None

        # The interactor must be initialised before the first render; it
        # is safe to call again later, so we do it once here.
        self._interactor.Initialize()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._interactor)

    # ------------------------------------------------------------------
    # Scene API
    # ------------------------------------------------------------------
    def clear_scene(self) -> None:
        """Remove every actor added via add_* methods. The corner triad
        (which lives on a separate marker widget) is preserved.
        Picking state is also cleared so stale actor refs don't leak."""
        self._renderer.RemoveAllViewProps()
        self._trajectory_actors.clear()
        self._highlighted_actor = None

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
        self._renderer.AddActor(actor)

    @staticmethod
    def _make_image_reader(path: Path):
        """Pick a vtk image reader based on the file extension. Returns
        None if the file is missing or the extension is unsupported --
        the caller then falls back to the flat-colour sphere.

        TIFFs (e.g. the NASA SVS CGI Moon Kit 2K/4K/8K) are routed
        through a Pillow-based PNG transcoder: vtkTIFFReader's bundled
        libtiff chokes on LZW-with-predictor and other variants the
        SVS files use ('Problem reading the row: 0' on the wire). The
        transcoded PNG is cached next to the TIFF, so the cost is paid
        once per texture file and subsequent loads hit the cache."""
        path = Path(path)
        if not path.is_file():
            return None
        ext = path.suffix.lower()
        if ext in {".jpg", ".jpeg"}:
            reader = vtkJPEGReader()
        elif ext == ".png":
            reader = vtkPNGReader()
        elif ext in {".tif", ".tiff"}:
            png_cache = VtkCanvas._ensure_png_cache(path)
            if png_cache is None:
                return None
            reader = vtkPNGReader()
            reader.SetFileName(str(png_cache))
            return reader
        else:
            return None
        reader.SetFileName(str(path))
        return reader

    @staticmethod
    def _ensure_png_cache(tiff_path: Path) -> Path | None:
        """Return the path of a VTK-ready PNG transcode of `tiff_path`,
        creating it via Pillow if missing or stale. Returns None if
        Pillow is unavailable or the conversion fails -- the caller
        then drops back to the flat-grey sphere with no further noise.

        The cache is NOT just a format change. NASA SVS (and most
        published lunar / planetary equirectangular maps) place the
        prime meridian at the *centre* of the image, i.e. column W/2
        is lon=0 and column 0 is lon=-180. vtkTexturedSphereSource on
        the other hand maps the texture's u=0 column onto theta=0 in
        scene coordinates -- the body's +X axis, which is where the
        prime meridian *should* land in the PA frame. Painting the
        SVS file unmodified rotates the surface by 180°.

        The fix is one np.roll by W/2 along the longitude axis at
        transcode time so the cached PNG has lon=0 at u=0, matching
        the VTK convention. The cache filename keeps a `_pa` suffix
        so the rotation it baked in is explicit (and so any earlier
        cache produced by the v1 code, sitting at `<stem>.png`, is
        bypassed)."""
        cache = tiff_path.with_name(tiff_path.stem + "_pa.png")
        if cache.is_file() and cache.stat().st_mtime >= tiff_path.stat().st_mtime:
            return cache
        try:
            from PIL import Image
        except ImportError:
            return None
        try:
            with Image.open(tiff_path) as img:
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
                        tip_length: float = 0.10) -> None:
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
        None no labels are drawn (handy for the bare-axes case)."""
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
            self._renderer.AddActor(self._make_arrow_actor(
                origin_km=tuple(origin),
                direction=tuple(direction),
                length_km=length_km,
                color=color,
                shaft_radius=shaft_radius,
                tip_radius=tip_radius,
                tip_length=tip_length,
            ))
            if labels_xyz is not None:
                tip = origin + direction * length_km * 1.05
                self._renderer.AddActor(self._make_text_label(
                    tip, labels_xyz[axis_idx], color, label_size,
                ))

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
        node-crossing waypoints) pass `radius_km` explicitly."""
        pos = np.asarray(position_km, dtype=float)
        self._add_marker_sphere(pos, radius_km, color)

    def add_legend(self, items: list[tuple[str, tuple[float, float, float]]],
                    max_label_chars: int = 36) -> None:
        """Multi-line legend in the top-left corner of the viewport.

        `items` is a list of `(label, (r, g, b))`. Each line is rendered
        in its own colour with a 2D text actor positioned in
        normalised-viewport coordinates so it stays put on resize.
        Long labels are middle-truncated to `max_label_chars` for
        readability."""
        if not items:
            return
        for i, (label, (r, g, b)) in enumerate(items):
            text = label
            if len(text) > max_label_chars:
                # Keep the last ~12 chars (usually the most informative
                # part of a filename) plus an ellipsis from the start.
                tail = max_label_chars - 4
                text = "..." + text[-tail:]
            actor = vtkTextActor()
            actor.SetInput(text)
            prop = actor.GetTextProperty()
            prop.SetColor(r, g, b)
            prop.SetFontSize(12)
            prop.SetFontFamilyToCourier()
            prop.SetBold(True)
            coord = actor.GetPositionCoordinate()
            coord.SetCoordinateSystemToNormalizedViewport()
            coord.SetValue(0.015, 0.97 - i * 0.035)
            self._renderer.AddActor2D(actor)

    # ------------------------------------------------------------------
    # Camera
    # ------------------------------------------------------------------
    def reset_camera(self) -> None:
        """Fit the camera to the bounding box of all currently added
        props. Call after the last add_* of a frame."""
        self._renderer.ResetCamera()

    def render(self) -> None:
        """Trigger a repaint. Call once after a batch of add_* calls."""
        self._render_window.Render()

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
