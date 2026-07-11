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

"""spoviz -- the SpOdy 3D astrodynamics visualization library.

Cesium-like time-dynamic 3D scenes (central bodies, trajectories,
reference-frame triads, third-body markers, sun illumination, star-map
skybox) built on VTK + numpy. The spody GUI is the first client; the
library itself is host-agnostic:

* `spoviz.scene.Scene3D` -- the scene engine. Pure VTK, no Qt: give
  it any (render window, interactor) pair, including an offscreen
  one, and drive it from scripts or notebooks.
* `spoviz.qt.SceneWidget` -- the Qt host widget. Deliberately NOT
  imported here so `import spoviz` stays PySide6-free; import it
  explicitly where a Qt embedding is wanted.
* `spoviz.decoration` -- ephemeris-driven scene decoration (third
  bodies, day/night sunlight, animated body-fixed triads). Takes an
  ephemeris object duck-typed on `spopy.Ephemeris.position` plus
  explicit callables/tables, so it carries no spody-app dependencies.
* `spoviz.bodies` -- visual catalog (NAIF ids, display colours,
  marker sizing, distance compression).
* `spoviz.textures` -- equirectangular texture fixups (meridian
  roll, ICRF-aligned skybox re-projection) with on-disk caching.

Positions are km; times are simulation seconds on whatever epoch the
caller's data uses; rotation-matrix sequences are (N, 3, 3) with
columns = local axes expressed in scene coordinates.
"""

from .scene import Scene3D
from . import bodies, decoration, textures

__all__ = ["Scene3D", "bodies", "decoration", "textures"]
