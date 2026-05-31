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

import os

# QVTKRenderWindowInteractor sniffs the active Qt binding from QT_API;
# force PySide6 so it does not accidentally pull in PyQt5/PyQt6 if they
# happen to be on PYTHONPATH.
os.environ.setdefault("QT_API", "pyside6")

import numpy as np
from PySide6.QtWidgets import QVBoxLayout, QWidget
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import vtkCellArray, vtkPolyData, vtkPolyLine
from vtkmodules.vtkFiltersSources import vtkSphereSource
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkInteractionWidgets import vtkOrientationMarkerWidget
from vtkmodules.vtkRenderingAnnotation import vtkAxesActor
from vtkmodules.vtkRenderingCore import (
    vtkActor,
    vtkPolyDataMapper,
    vtkRenderer,
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
        (which lives on a separate marker widget) is preserved."""
        self._renderer.RemoveAllViewProps()

    def add_central_body(self, radius_km: float = MOON_RADIUS_KM,
                          color: tuple[float, float, float] = (0.55, 0.55, 0.58),
                          resolution: int = 64) -> None:
        """Add a solid sphere centred at the origin -- the central body
        the trajectories are referenced to. Resolution is the number of
        latitude/longitude bands; 64 is smooth-enough without being
        wasteful."""
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

    def add_trajectory(self, points_km: np.ndarray,
                        color: tuple[float, float, float] = (1.0, 0.85, 0.20),
                        line_width: float = 2.0,
                        endpoint_markers: bool = True) -> None:
        """Add a 3D polyline through `points_km` (Nx3, km in the
        central-body inertial frame). If `endpoint_markers` is true,
        a green sphere is placed at the first point and a red one at
        the last (sized to ~0.5% of the trajectory bounding diagonal)."""
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

        if endpoint_markers:
            diag = float(np.linalg.norm(points_km.max(axis=0) - points_km.min(axis=0)))
            marker_r = max(diag * 0.005, 1.0)   # ≥ 1 km even on tiny arcs
            self._add_marker_sphere(points_km[0],  marker_r, color=(0.0, 0.9, 0.0))
            self._add_marker_sphere(points_km[-1], marker_r, color=(0.95, 0.2, 0.2))

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
