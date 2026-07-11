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

"""Qt host for `spoviz.scene.Scene3D`.

The ONLY spoviz module that imports PySide6 -- everything else in the
package stays importable in headless / offscreen environments. Import
it explicitly (`from spoviz.qt import SceneWidget`); `spoviz`'s
`__init__` deliberately does not pull it in.

`SceneWidget` embeds a QVTKRenderWindowInteractor in a QWidget,
builds a `Scene3D` on its render window, and forwards the whole
scene API through `__getattr__` -- so `widget.add_trajectory(...)`
and `widget.scene.add_trajectory(...)` are the same call. The one
name that needs an explicit override is `render`: QWidget already
has a `render()` method, so attribute lookup would never reach the
delegation fallback for it.
"""

from __future__ import annotations

import os

# QVTKRenderWindowInteractor sniffs the active Qt binding from QT_API;
# force PySide6 so it does not accidentally pull in PyQt5/PyQt6 if they
# happen to be on PYTHONPATH.
os.environ.setdefault("QT_API", "pyside6")

from PySide6.QtWidgets import QVBoxLayout, QWidget
from vtkmodules.qt.QVTKRenderWindowInteractor import QVTKRenderWindowInteractor

from .scene import Scene3D


class SceneWidget(QWidget):
    """Qt widget hosting a `Scene3D`. The scene object is reachable
    both through plain delegation (`widget.add_central_body(...)`)
    and explicitly as `widget.scene` for callers that want to be
    unambiguous about which layer they talk to."""

    def __init__(self) -> None:
        super().__init__()
        self._interactor = QVTKRenderWindowInteractor(self)
        self.scene = Scene3D(self._interactor.GetRenderWindow(),
                             self._interactor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._interactor)

    def __getattr__(self, name: str):
        # Called only when normal lookup fails, i.e. for Scene3D API
        # names that QWidget doesn't define. The `scene` guard stops
        # the recursion that would otherwise happen if anything reads
        # an attribute before __init__ assigns self.scene.
        if name == "scene":
            raise AttributeError(name)
        return getattr(self.scene, name)

    def render(self) -> None:
        """Repaint the VTK scene. Shadowed explicitly because
        QWidget.render(QPaintDevice, ...) exists and would win the
        attribute lookup over the `__getattr__` delegation."""
        self.scene.render()
