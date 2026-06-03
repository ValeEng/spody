"""Registry of the *required* and *optional* data assets the setup
wizard manages.

A spody run needs two on-disk inputs that are NOT shipped in the GUI
bundle (because they are large and externally maintained):

  * a planetary ephemeris in spody's `.spody` binary format
    (`DE440/de440.spody`), derived from the JPL DE440 ASCII chunks.
  * a lunar harmonic-gravity model (`GRGM1200B/gggrx_1200b_sha.tab`
    plus its `.lbl` companion), shipped as-is.

This module defines the canonical list of assets, where they go inside
the data root, the URL the wizard offers as a starting point, and how
to check whether each is locally "present" (the file exists and is at
least `min_bytes` large; small/zero-byte placeholders count as
missing). The URLs are intentionally tweakable from the wizard UI --
the user can paste a corrected URL and re-try without editing this
file.

The minimum set of *raw* files needed before the wizard's conversion
step is:
    DE440/header.440      (mandatory header)
    DE440/ascp01950.440   (modern-era coverage, 1950-2050)
    GRGM1200B/gggrx_1200b_sha.tab
    GRGM1200B/gggrx_1200b_sha.lbl

Plus the *derived* file:
    DE440/de440.spody     (produced by `spody convert ephemeris`)
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

# Optional ASCII DE440 chunks the wizard can offer in addition to the
# default 1950-2050. The chunk file name encodes the start year (zero-
# padded to four digits, century * 100). Each chunk covers ~100 years.
DE440_EXTRA_CHUNKS: tuple[str, ...] = (
    "01550", "01650", "01750", "01850",          # historical
    "02050", "02150", "02250", "02350", "02450", "02550",  # future
)


@dataclass(frozen=True)
class Asset:
    """One file the wizard knows how to download/derive."""
    name: str          # Human label shown in the wizard table.
    url: str           # Best-known direct URL (editable in the UI).
    relpath: str       # Path inside the data root, with forward slashes.
    min_bytes: int     # Sanity floor; below this the file is "missing".
    kind: str          # "raw" (download) or "derived" (produced locally).
    required: bool     # Hard requirement to run spody at all.


# Canonical asset list. Order matters only for display.
ASSETS: tuple[Asset, ...] = (
    Asset(
        name="DE440 header",
        url="https://ssd.jpl.nasa.gov/ftp/eph/planets/ascii/de440/header.440",
        relpath="DE440/header.440",
        min_bytes=10_000,
        kind="raw",
        required=True,
    ),
    Asset(
        name="DE440 ASCII 1950-2050",
        url="https://ssd.jpl.nasa.gov/ftp/eph/planets/ascii/de440/ascp01950.440",
        relpath="DE440/ascp01950.440",
        min_bytes=10_000_000,
        kind="raw",
        required=True,
    ),
    Asset(
        name="DE440 binary (de440.spody)",
        # Empty URL -> derived: produced by `spody convert ephemeris`.
        url="",
        relpath="DE440/de440.spody",
        min_bytes=1_000_000,
        kind="derived",
        required=True,
    ),
    Asset(
        name="GRGM1200B .tab (harmonics)",
        # Direct URL TBD; PGDA hosts these behind product pages. Show the
        # product landing page so the user can fix the URL in the wizard.
        url="https://pgda.gsfc.nasa.gov/products/50",
        relpath="GRGM1200B/gggrx_1200b_sha.tab",
        min_bytes=10_000_000,
        kind="raw",
        required=True,
    ),
    Asset(
        name="GRGM1200B .lbl (metadata)",
        url="https://pgda.gsfc.nasa.gov/products/50",
        relpath="GRGM1200B/gggrx_1200b_sha.lbl",
        min_bytes=1_000,
        kind="raw",
        required=True,
    ),
)


def for_extra_de440_chunk(date_id: str) -> Asset:
    """Build an Asset descriptor for an extra DE440 chunk the user
    enables in the wizard. Not part of the default required set."""
    return Asset(
        name=f"DE440 ASCII {_chunk_label(date_id)}",
        url=f"https://ssd.jpl.nasa.gov/ftp/eph/planets/ascii/de440/ascp{date_id}.440",
        relpath=f"DE440/ascp{date_id}.440",
        min_bytes=10_000_000,
        kind="raw",
        required=False,
    )


def is_present(asset: Asset, root: Path) -> bool:
    """A file is *present* iff it exists and is at least `min_bytes`
    big -- this catches half-downloads / browser placeholder files."""
    p = root / asset.relpath
    try:
        return p.is_file() and p.stat().st_size >= asset.min_bytes
    except OSError:
        return False


def missing_required(root: Path) -> list[Asset]:
    """List of required assets that are NOT present under `root`."""
    return [a for a in ASSETS if a.required and not is_present(a, root)]


def all_required_present(root: Path) -> bool:
    return not missing_required(root)


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
