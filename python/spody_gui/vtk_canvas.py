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
"""Compatibility shim: the 3D engine now lives in the `spoviz`
library (python/spoviz/).

`VtkCanvas` is `spoviz.qt.SceneWidget` -- a QWidget hosting a
Qt-free `spoviz.scene.Scene3D` and forwarding the whole scene API
(add_central_body, add_animated_trajectory, set_animation_time,
...) through attribute delegation. Existing GUI code keeps importing
from here; NEW code should import from spoviz directly.
"""
from __future__ import annotations

from spoviz.qt import SceneWidget


# Mean radius of the Moon in km. Kept here for backwards-compat
# with callers that import it from this module (analysis/scene3d
# and historical scene_options documentation). Authoritative source
# is spody_const.h, parsed by `constants._load_spody_const`; if you
# change the value, change it there.
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


class VtkCanvas(SceneWidget):
    """The Analysis tab's 3D canvas. Same widget as
    `spoviz.qt.SceneWidget`; the subclass exists only so the GUI-side
    name (and its import path) survive the extraction of the 3D
    engine into spoviz."""
