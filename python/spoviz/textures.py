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

"""Equirectangular texture pipeline for the 3D scene.

Published body maps and star maps almost never come in the exact
convention VTK samples them with; this module owns the (cached,
Pillow-based) pixel-space fixups:

* `ensure_uv0_meridian_cache` -- roll a body map's prime meridian
  from the image centre (NASA SVS / Solar System Scope convention)
  to the left edge (what `vtkTexturedSphereSource` maps onto the
  body's local +X axis).
* `ensure_icrf_aligned_skybox` -- re-project a galactic-coordinates
  star map so `vtkSkybox.Sphere`'s hard-coded sampling convention
  lands on the right star when interpreted in ICRF world coords.
* `make_image_reader` -- route any input (JPEG / PNG / TIFF) through
  the meridian-roll transcoder and hand back a ready `vtkPNGReader`.

Everything degrades silently: on any failure (Pillow missing, file
unreadable, cache not writable) the callers fall back to a flat-
colour sphere / misaligned skybox rather than crashing the scene.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from vtkmodules.vtkIOImage import vtkPNGReader


def ensure_icrf_aligned_skybox(src: Path) -> Path:
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


def ensure_uv0_meridian_cache(src_path: Path) -> Path | None:
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


def make_image_reader(path: Path):
    """Pick a vtk image reader for an equirectangular body texture.
    Returns None if the file is missing, the extension is
    unsupported, or the meridian-alignment cache can't be built --
    the caller then falls back to the flat-colour sphere.

    Every input (JPEG / PNG / TIFF) is routed through the same
    Pillow-based transcoder that bakes in the W/2 longitude roll
    (see `ensure_uv0_meridian_cache`): published equirectangular
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
    png_cache = ensure_uv0_meridian_cache(path)
    if png_cache is None:
        return None
    reader = vtkPNGReader()
    reader.SetFileName(str(png_cache))
    return reader
