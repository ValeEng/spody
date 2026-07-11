# spoviz — SpOdy 3D astrodynamics visualization library

Cesium-like, time-dynamic 3D scenes (central bodies, orbits,
reference-frame triads, third-body markers, day/night sunlight,
star-map skybox) built on **VTK + numpy**. The spody GUI is the
first client; the library itself is host-agnostic and renders with
**no Qt in the process** when you want it to.

```
spoviz/
  scene.py       Scene3D        the scene engine (pure VTK, no Qt)
  decoration.py  add_*          ephemeris-driven garnish (third bodies, sun, BF frame)
  bodies.py      catalogs       NAIF ids, display colours, marker sizing knobs
  textures.py    ensure_*       equirectangular pixel fixups (cached on disk)
  widgets.py     PlaybackBar    opt-in in-scene UI chrome (VTK widgets, no Qt)
  qt.py          SceneWidget    the ONLY module that imports PySide6
```

Dependencies: `vtk`, `numpy` (always); `Pillow` (only for textured
bodies / skybox); `PySide6` (only if you import `spoviz.qt`).
`import spoviz` never pulls Qt in.

## Conventions (everywhere in the API)

- Positions and lengths are **km**, in whatever inertial/synodic
  frame your data uses — the scene does not reinterpret them.
- Times are **simulation seconds** on the caller's epoch. The
  library never converts time scales: readouts go through formatter
  callables you provide (spody passes `spopy.time.et_to_utc`).
- Rotation sequences are `(N, 3, 3)` arrays; **columns = the local
  frame's axes expressed in scene coordinates** at each sample.
- Physical constants are **arguments, not defaults**: spoviz cannot
  read `spody_const.h`, so body radii & co. always come from the
  caller.
- Decoration functions are silent on failure (bad ephemeris, ET out
  of coverage): the scene degrades to fewer props, it never raises.

## Hosting contract

`Scene3D` never creates a window. You hand it a bound
`(vtkRenderWindow, interactor)` pair; it installs its two layered
renderers (Cesium-style multi-frustum: tight depth scope for the
primary scene, wide scope for far decoration), the trackball style
and its observers.

| host        | how |
|-------------|-----|
| offscreen   | `vtkRenderWindow` + `SetOffScreenRendering(1)` + `vtkRenderWindowInteractor` |
| native window | same, without offscreen; call `interactor.Start()` for the event loop |
| Qt          | `from spoviz.qt import SceneWidget` — done (see below) |

---

## `spoviz.Scene3D`

`Scene3D(render_window, interactor)`

### Scene building

| call | what it does |
|------|--------------|
| `add_central_body(radius_km, color=(0.55,0.55,0.58), resolution=64, texture_path=None)` | Sphere at the origin; equirectangular texture if given (or the default set below). |
| `set_central_body_texture(path)` | Default texture for subsequent `add_central_body` calls; `None` = flat grey. |
| `add_secondary_body(position_km, radius_km, color=..., resolution=64, texture_path=None, label=None)` | Static sphere at an arbitrary position (CR3BP primaries); optional billboard label. |
| `add_trajectory(points_km, color=(1,0.85,0.2), line_width=2.0, endpoint_markers=True, source_path=None)` | Static polyline (`points_km` is `(N,3)`); green/red endpoint spheres; `source_path` registers it for picking. |
| `add_point(position_km, radius_km=30.0, color=(1,0.25,0.25))` | One marker sphere (impact site style). |
| `add_points(positions_km, colors_rgb, radius_km=30.0)` | `(N,3)` positions + `(N,3)` RGB in one GPU-instanced actor — use this beyond a handful of markers. |
| `add_frame_triad(length_km, origin_km=(0,0,0), basis_in_scene=None, colors_xyz=..., labels_xyz=None, label_size=16, shaft_radius=0.006, tip_radius=0.022, tip_length=0.10, opacity=1.0)` | Static X/Y/Z arrow triad; `basis_in_scene` columns = the frame's axes in scene coords (`None` = identity). |
| `add_sun_arrow(direction, length_km, color=(1,0.85,0.2))` | Fixed arrow from the origin toward `direction`. |
| `add_legend(items, max_label_chars=36)` | Top-left viewport legend; `items = [(label, (r,g,b)), ...]`. |
| `set_skybox_texture(path)` | Equirectangular star map as background (galactic-coords maps are re-projected to ICRF automatically, cached on disk); `None` removes it. Sticky across `clear_scene()`. |
| `set_overlay_utc_text(text)` | Bottom-right readout pill; `""` hides it. The caller formats the string. |
| `clear_scene()` | Remove every data prop (skybox + overlay pill survive); resets animation and picking state. |

### Animation

All animated props share one timeline driven by
`set_animation_time`.

| call | what it does |
|------|--------------|
| `add_animated_trajectory(points_km, times_s, color=..., line_width=2.0, source_path=None, marker_radius_km=None, marker_texture_path=None, marker_R_bf_to_scene_sequence=None, marker_shadable=True, is_decoration=False)` | Polyline + moving marker interpolated on `times_s`. Textured marker = a real body (Moon as third body); `marker_R_...` `(N,3,3)` spins it (body-fixed attitude). `is_decoration=True` puts it on the far layer and excludes it from camera auto-fit. |
| `add_animated_arrow(times_s, positions_km, color, length_km, is_decoration=False)` | Fixed-length arrow at the origin re-aimed each tick toward the interpolated position (third-body direction indicators). |
| `add_animated_frame_triad(times_s, R_sequence, length_km, origin_km=(0,0,0), colors_xyz=..., labels_xyz=None, ..., opacity=1.0, is_decoration=False)` | Triad whose axes follow `R_sequence` (linear interp + re-orthonormalisation). |
| `set_central_body_animated_orientation(times_s, R_sequence)` | Rotate the central body actor over time (lunar libration, Earth rotation) so the texture tracks the body-fixed axes. |
| `set_sun_light(times_s, sun_dirs_unit)` | Replace the headlight with a directional sun (day/night terminator + ring). **Call LAST**: it freezes the lighting recipe of every actor present. |
| `set_animation_time(t_s)` | Advance every animated prop (markers, arrows, triads, body attitude, sun) to `t_s`; clamps at the ends. |
| `animation_time_range()` | `(t_min, t_max)` across all animated props, or `None`. |
| `has_animations()` | `bool`. |
| `set_trail_enabled(flag)` | Trail mode: each animated polyline shows only `[t_start, t_now]`. |

### Camera, rendering, picking

| call | what it does |
|------|--------------|
| `render()` | Repaint (call once after a batch of `add_*`). |
| `reset_camera()` | Fit to everything. |
| `reset_camera_on_origin()` | Fit + pin the focal point at the origin (recommended for body-centred scenes). |
| `capture_camera_pose()` / `restore_camera_pose(pose)` | Plain-dict camera snapshot, survives scene rebuilds. |
| `set_pick_callback(cb)` | `cb(source_path | None)` on **Ctrl+left-click**; hit trajectories get a line-width highlight. |

---

## `spoviz.decoration` — ephemeris-driven garnish

Everything takes explicit inputs; `ephemeris` is duck-typed on
`spopy.Ephemeris`: any object with
`position(center_naif, target_naif, et_s) -> (3,) km` works.
`pump` is an optional zero-arg callable invoked every 512 samples
(GUI hosts pass their event-loop pump).

| call | what it does |
|------|--------------|
| `add_reference_triads(scene, scene_frame, R_icrf_to_bf, radius_km, bf_frame_label="PA")` | The project-wide two-triad convention: body-fixed bright, ICRF muted. `scene_frame` = `'bf'` or `'icrf'`. |
| `add_third_bodies(scene, *, ephemeris, central_naif, central_radius_km, body_names, times_s, et_start_s, only=None, radius_km_by_name=None, texture_for=None, orientation_for=None, pump=None)` | One animated marker + direction arrow per body name. `texture_for(name)->Path|None`; `orientation_for(name)` returns an `(et_s, eph) -> R_icrf_to_bf` provider or `None`. |
| `add_sun_illumination(scene, *, ephemeris, central_naif, times_s, et_start_s, pump=None)` | Sample the Sun direction over the timeline and install `set_sun_light`. Call LAST. |
| `add_animated_body_frame(scene, *, times_s, radius_km, bf_frame_name="BF", ephemeris=None, bf_orientation=None, et_start_s=0.0, show_icrf=True, show_bf=True)` | Static muted ICRF triad + animated bright body-fixed triad + central-body attitude, all on the playback timeline. |
| `sample_positions(ephemeris, center_naif, target_naif, times_s, et_start_s, pump=None)` | `(N,3)` km or `None` on the first failed sample. |
| `TRIAD_BRIGHT_COLORS`, `TRIAD_MUTED_COLORS` | The two triad palettes. |

## `spoviz.bodies` — visual catalog

| name | what it is |
|------|------------|
| `BODY_NAIF` | `{"Sun": 10, "Earth": 399, "Moon": 301, ...}` |
| `BODY_COLORS` | display RGB per body name |
| `body_marker_radius_km(name, ref_radius_km, radius_km_by_name)` | marker display radius (physical or log-compressed per `USE_TRUE_RADII`) |
| `power_compress_positions(positions_km, ref_radius_km, exponent=DIST_EXPONENT)` | radial squeeze that preserves directions (`exponent < 1` folds the Sun into a Moon-scale view) |
| `DIST_EXPONENT`, `USE_TRUE_RADII`, `RADIUS_BASE_KM`, `RADIUS_PER_DECADE_KM`, `BODY_ARROW_LEN_RBODY` | the sizing knobs |

## `spoviz.textures` — equirectangular fixups

| call | what it does |
|------|--------------|
| `make_image_reader(path)` | vtk reader for a body map (JPEG/PNG/TIFF), routed through the meridian-roll cache; `None` on failure. |
| `ensure_uv0_meridian_cache(path)` | roll the prime meridian from image centre to u=0 (what `vtkTexturedSphereSource` expects); cached `<stem>_uv0.png`. |
| `ensure_icrf_aligned_skybox(path)` | re-project a galactic star map to vtkSkybox's ICRF sampling; cached `<stem>_icrf<ext>`. |

## `spoviz.widgets` — opt-in in-scene UI (no Qt)

Construct AFTER the scene is populated; after `clear_scene()` +
rebuild call `reinstall()` on each. The spody GUI does not use these
(it has Qt controls); they exist for standalone viewers.

| call | what it does |
|------|--------------|
| `PlaybackBar(scene, formatter=None, speeds=None, interval_ms=33, loop=False)` | play/pause + speed cycle + timeline slider (click-to-jump) + epoch readout in the overlay pill. `formatter(t_s)->str` owns the text; `speeds` = sim-seconds-per-wall-second cycle (`None` derives slow/medium/fast from the range); `.loop` is a live public flag. |
| `OptionsPanel(scene, options)` | menu button + checkbox rows; `options = [(label, initially_on, callback(bool)), ...]` — the callback owns the semantics. |
| `icon_image(kind, size=30)` | the numpy-drawn RGBA icons (`play`, `pause`, `speed`, `menu`, `box_off`, `box_on`), reusable for custom buttons. |

## `spoviz.qt.SceneWidget` — Qt host

```python
from spoviz.qt import SceneWidget       # the only PySide6 import path

widget = SceneWidget()                  # a QWidget
widget.scene                            # the Scene3D
widget.add_central_body(radius_km=6371.0)   # every Scene3D call is
widget.render()                              # delegated on the widget
```

One trap for contributors: a new `Scene3D` method whose name matches
an existing `QWidget` attribute must get an explicit override in
`qt.py` (as `render` has), or the QWidget name shadows the
delegation.

---

## Examples

### 1. Offscreen PNG, ~25 lines, no Qt

```python
import numpy as np
from vtkmodules.vtkRenderingCore import (vtkRenderWindow,
    vtkRenderWindowInteractor, vtkWindowToImageFilter)
from vtkmodules.vtkIOImage import vtkPNGWriter
from spoviz import Scene3D

rw = vtkRenderWindow(); rw.SetOffScreenRendering(1); rw.SetSize(1280, 800)
iren = vtkRenderWindowInteractor(); iren.SetRenderWindow(rw)
scene = Scene3D(rw, iren)

scene.add_central_body(radius_km=1737.4)
th = np.linspace(0, 4 * np.pi, 800)
ts = np.linspace(0.0, 86400.0, 800)
pts = np.column_stack([2050*np.cos(th), 1230*np.sin(th), 1400*np.sin(th/2)])
scene.add_animated_trajectory(pts, ts)
scene.add_frame_triad(length_km=3600.0, labels_xyz=("X", "Y", "Z"))
scene.reset_camera_on_origin()
scene.set_animation_time(43200.0)
scene.render()

w2i = vtkWindowToImageFilter(); w2i.SetInput(rw); w2i.Update()
w = vtkPNGWriter(); w.SetFileName("orbit.png")
w.SetInputConnection(w2i.GetOutputPort()); w.Write()
```

### 2. Interactive quicklook viewer with playback chrome

```python
from spoviz import Scene3D
from spoviz.widgets import PlaybackBar, OptionsPanel
# ... build rw/iren WITHOUT offscreen, and the scene as above ...

bar = PlaybackBar(scene, formatter=lambda t: f"T+ {t/3600:6.2f} h")
panel = OptionsPanel(scene, [
    ("Trail", False, scene.set_trail_enabled),
    ("Loop",  False, lambda on: setattr(bar, "loop", on)),
])
scene.render()
iren.Start()                      # native VTK event loop
```

### 3. A spody run, end to end (spody_io + spopy + spoviz)

```python
import numpy as np
from spody_io import read_trajectory
from spopy import Ephemeris
from spoviz import Scene3D, decoration
# ... rw/iren/scene as above ...

d = read_trajectory("output/<ts>/<ts>_orbit.bin")
scene.add_central_body(radius_km=1737.4)
scene.add_animated_trajectory(
    np.column_stack([d["x"], d["y"], d["z"]]), d["t"].astype(float))

eph = Ephemeris("path/to/de440.spody")
decoration.add_third_bodies(
    scene, ephemeris=eph, central_naif=301, central_radius_km=1737.4,
    body_names=["Earth", "Sun"], times_s=d["t"].astype(float),
    et_start_s=et_start)          # et_start from the run's input.toml
decoration.add_sun_illumination(
    scene, ephemeris=eph, central_naif=301,
    times_s=d["t"].astype(float), et_start_s=et_start)   # LAST
scene.reset_camera_on_origin()
scene.render()
```

### 4. Qt embedding

```python
from PySide6.QtWidgets import QApplication, QMainWindow
from spoviz.qt import SceneWidget

app = QApplication([])
win = QMainWindow()
canvas = SceneWidget()
win.setCentralWidget(canvas)
canvas.add_central_body(radius_km=6371.0)
canvas.reset_camera_on_origin()
win.resize(1200, 800); win.show()
app.exec()
```
