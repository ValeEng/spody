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
"""Registry of the *required* data assets the setup wizard manages.

A spody run needs two on-disk inputs that are NOT shipped in the GUI
bundle (because they are large and externally maintained):

  * a planetary ephemeris in spody's `.spody` binary format
    (`DE440/de440.spody`), derived from the JPL DE440 ASCII chunks.
  * a lunar harmonic-gravity model (`GRGM1200B/gggrx_1200b_sha.tab`
    plus its `.lbl` companion), shipped as-is.

The DE440 ASCII source comes split into ~100-year chunks. The user
picks between two coverage profiles, persisted in QSettings under
`wizard/de440_coverage`:

  * "modern"  -> just `ascp01950.440` (covers 1950..2050, ~30 MB).
                  Right default for anyone running near-present epochs.
  * "full"    -> all 11 chunks 01550..02550 (1550..2650, ~340 MB).
                  Needed only for historical / far-future scenarios.

The list of "required" assets therefore depends on the coverage
profile -- `required_assets()` is a function, not a constant.

The URLs are intentionally tweakable from the wizard UI -- the user
can paste a corrected URL and re-try without editing this file. Once
a URL is proven, we bake it here so the next user doesn't have to.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import QSettings


# QSettings key for the coverage profile. Defaults to "modern" so the
# first-launch wizard ships a ~30 MB download instead of ~340 MB.
KEY_COVERAGE = "wizard/de440_coverage"

# DE440 ASCII chunk ids (zero-padded year * 100). Each chunk covers
# ~100 years; the JPL file naming convention is `ascpXXXXX.440`.
DE440_CHUNKS_MODERN: tuple[str, ...] = ("01950",)
DE440_CHUNKS_FULL:   tuple[str, ...] = (
    "01550", "01650", "01750", "01850", "01950",
    "02050", "02150", "02250", "02350", "02450", "02550",
)


@dataclass(frozen=True)
class Asset:
    """One file the wizard knows how to download/derive.

    `category` and `body` let the GUI form filter assets when populating
    the harmonics / ephemeris combo boxes -- e.g. show only files of
    category 'harmonics' for the currently-selected `central_body`.
    Default values keep the rest of the wizard backward-compat: when an
    asset is not categorised, it just never shows up in the form
    dropdowns (the wizard panel itself still lists everything for
    download)."""
    name: str          # Human label shown in the wizard table.
    url: str           # Best-known direct URL (editable in the UI).
    relpath: str       # Path inside the data root, with forward slashes.
    min_bytes: int     # Sanity floor; below this the file is "missing".
    kind: str          # "raw" (download) or "derived" (produced locally).
    required: bool     # Hard requirement to run spody at all.
    category: str = ""
    """Semantic role: 'harmonics' | 'ephemeris' | 'ephemeris_source' |
    'harmonics_meta' | 'texture' | '' (uncategorised). The form's
    combo widgets filter on this so a row that isn't a harmonics or
    ephemeris file never pollutes the dropdowns."""
    body: str | None = None
    """Central body the asset describes ('Moon', 'Earth', ...) or None
    for body-agnostic assets (multi-body ephemerides like DE440 cover
    every planet at once)."""


# --------------------------------------------------------------------------
# Static asset descriptors (DE440 header + GRGM1200B pair + derived .spody).
# DE440 ASCII chunks are built on demand by `_chunk_asset`.
# --------------------------------------------------------------------------
DE440_HEADER = Asset(
    name="DE440 header",
    url="https://ssd.jpl.nasa.gov/ftp/eph/planets/ascii/de440/header.440",
    relpath="DE440/header.440",
    min_bytes=10_000,
    kind="raw",
    required=True,
    category="ephemeris_source",
)

DE440_SPODY = Asset(
    name="DE440 binary (de440.spody)",
    # Empty URL -> derived: produced by `spody convert ephemeris`.
    url="",
    relpath="DE440/de440.spody",
    min_bytes=1_000_000,
    kind="derived",
    required=True,
    category="ephemeris",
    body=None,   # DE440 covers all planets; body-agnostic
)

# GRGM1200B is mirrored on the PDS Geosciences node (Washington U.).
# Switching to GRGM1200A only needs the 'b' replaced with 'a' in both
# the URL and the relpath.
GRGM1200B_TAB = Asset(
    name="GRGM1200B .tab (harmonics)",
    url=("https://pds-geosciences.wustl.edu/grail/grail-l-lgrs-5-rdr-v1/"
         "grail_1001/shadr/gggrx_1200b_sha.tab"),
    relpath="GRGM1200B/gggrx_1200b_sha.tab",
    min_bytes=10_000_000,
    kind="raw",
    required=True,
    category="harmonics",
    body="Moon",
)

GRGM1200B_LBL = Asset(
    name="GRGM1200B .lbl (metadata)",
    url=("https://pds-geosciences.wustl.edu/grail/grail-l-lgrs-5-rdr-v1/"
         "grail_1001/shadr/gggrx_1200b_sha.lbl"),
    relpath="GRGM1200B/gggrx_1200b_sha.lbl",
    min_bytes=1_000,
    kind="raw",
    required=True,
    category="harmonics_meta",
    body="Moon",
)

# NASA SVS "CGI Moon Kit" -- LROC color mosaic, equirectangular
# projection (longitude 0..360 left-to-right, latitude -90..+90
# bottom-to-top). Optional: spody runs without it, the 3D Analysis
# scene just falls back to the flat-grey sphere and the impact lat/lon
# map drops the photographic background. 2K is the default tradeoff
# (~10 MB, sub-degree detail); URL is editable in the wizard for users
# who want to swap to 4K (~37 MB) or 8K (~135 MB) -- adjust min_bytes
# if you go higher.
MOON_TEXTURE = Asset(
    name="Moon texture (NASA SVS LROC color, 2K)",
    # SVS only publishes the 1K JPEG variant; 2K/4K/8K are TIFFs in
    # the same directory. VTK reads TIFF via vtkTIFFReader and
    # matplotlib via PIL, so this is fine to use directly without a
    # decode step.
    url=("https://svs.gsfc.nasa.gov/vis/a000000/a004700/a004720/"
         "lroc_color_poles_2k.tif"),
    relpath="Moon/lroc_color_poles_2k.tif",
    min_bytes=2_000_000,
    kind="raw",
    required=False,
    category="texture",
    body="Moon",
)


# --------------------------------------------------------------------------
# Coverage profile (read/write QSettings)
# --------------------------------------------------------------------------
def coverage() -> str:
    """Return the currently-selected DE440 coverage profile. Always
    returns "modern" or "full" -- an unrecognised stored value is
    treated as "modern" so the wizard never gets stuck."""
    v = QSettings().value(KEY_COVERAGE, "modern", type=str)
    return v if v in ("modern", "full") else "modern"


def set_coverage(value: str) -> None:
    """Persist the DE440 coverage choice. Caller must pass "modern"
    or "full"."""
    if value not in ("modern", "full"):
        raise ValueError(f"unknown coverage profile: {value!r}")
    QSettings().setValue(KEY_COVERAGE, value)


# --------------------------------------------------------------------------
# Required-asset list (computed from the current coverage)
# --------------------------------------------------------------------------
def required_assets(coverage_value: str | None = None) -> tuple[Asset, ...]:
    """Build the list of assets the wizard must have on disk before
    spody can run. `coverage_value` defaults to the persisted choice
    via `coverage()`; pass it explicitly only when probing alternative
    profiles (e.g. previewing a switch)."""
    cov = coverage_value if coverage_value is not None else coverage()
    chunks = DE440_CHUNKS_FULL if cov == "full" else DE440_CHUNKS_MODERN
    out: list[Asset] = [DE440_HEADER]
    out.extend(_chunk_asset(c) for c in chunks)
    out.append(DE440_SPODY)
    out.append(GRGM1200B_TAB)
    out.append(GRGM1200B_LBL)
    # Optional assets sit at the bottom of the wizard card list so the
    # required ones stay grouped at top.
    out.append(MOON_TEXTURE)
    return tuple(out)


def _chunk_asset(date_id: str) -> Asset:
    """One ascpXXXXX.440 chunk as an Asset descriptor."""
    return Asset(
        name=f"DE440 ASCII {_chunk_label(date_id)}",
        url=f"https://ssd.jpl.nasa.gov/ftp/eph/planets/ascii/de440/ascp{date_id}.440",
        relpath=f"DE440/ascp{date_id}.440",
        min_bytes=10_000_000,
        kind="raw",
        required=True,
    )


# --------------------------------------------------------------------------
# On-disk status helpers
# --------------------------------------------------------------------------
def is_present(asset: Asset, root: Path) -> bool:
    """A file is *present* iff it exists and is at least `min_bytes`
    big -- this catches half-downloads / browser placeholder files."""
    p = root / asset.relpath
    try:
        return p.is_file() and p.stat().st_size >= asset.min_bytes
    except OSError:
        return False


def missing_required(root: Path, coverage_value: str | None = None) -> list[Asset]:
    """List of required assets that are NOT present under `root`."""
    return [a for a in required_assets(coverage_value)
            if a.required and not is_present(a, root)]


def all_required_present(root: Path, coverage_value: str | None = None) -> bool:
    return not missing_required(root, coverage_value)


def effective_paths(root: Path) -> dict[str, str]:
    """Best-known absolute paths for the two TOML fields the runner
    needs: harmonics and ephemeris. Used by the form to auto-fill
    those rows when they're left blank. Empty string when the asset
    has not been downloaded yet -- the caller treats that as a guard
    failure."""
    eph = root / "DE440" / "de440.spody"
    har = root / "GRGM1200B" / "gggrx_1200b_sha.tab"
    return {
        "ephemeris_file": str(eph) if eph.is_file() else "",
        "harmonics_file": str(har) if har.is_file() else "",
    }


def moon_texture_path(root: Path) -> Path | None:
    """Return the wizard-downloaded Moon texture path if present, else
    None. Thin wrapper around `central_body_texture_path(root, "Moon")`
    kept so older settings paths and PyInstaller bundles can keep
    calling the Moon-specific helper. New code should call the
    body-aware variant below."""
    return central_body_texture_path(root, "Moon")


def central_body_texture_path(root: Path, body: str) -> Path | None:
    """First registered texture asset (under `root`) for the named
    central body, or None when no asset is registered / downloaded yet.
    Phase-1 the only registered body is Moon; adding Earth in Phase 2
    is one extra Asset entry above with `category='texture',
    body='Earth'` and this helper picks it up automatically."""
    for a in assets_by_category("texture", body=body):
        p = root / a.relpath
        if p.is_file():
            return p
    return None


def assets_by_category(category: str, body: str | None = None
                       ) -> tuple[Asset, ...]:
    """All registered assets matching the requested category, optionally
    filtered by central body. Pass `body=None` to skip the body filter
    (useful for body-agnostic categories like 'ephemeris')."""
    out: list[Asset] = []
    for a in required_assets():
        if a.category != category:
            continue
        if body is not None and a.body is not None and a.body != body:
            continue
        out.append(a)
    return tuple(out)


def present_files_for(category: str, root: Path, body: str | None = None
                      ) -> list[tuple[str, Path]]:
    """Return `(display_name, absolute_path)` pairs for every asset of
    the given category+body that's actually on disk under `root`.

    Used by the TOML form to populate ephemeris / harmonics combo
    boxes: each entry's text is the asset's `Asset.name`, its data is
    the resolved Path the TOML will reference. Returns an empty list
    when no matching asset has been downloaded yet."""
    out: list[tuple[str, Path]] = []
    for a in assets_by_category(category, body):
        if is_present(a, root):
            out.append((a.name, root / a.relpath))
    return out


def with_url(asset: Asset, new_url: str) -> Asset:
    """Return a copy of `asset` with the URL replaced. Used when the
    wizard's per-row URL edit fires."""
    return replace(asset, url=new_url)


def _chunk_label(date_id: str) -> str:
    """Pretty label for a DE440 chunk id like "01950" -> "1950-2050"."""
    try:
        start = int(date_id)
        return f"{start}-{start + 100}"
    except ValueError:
        return date_id
