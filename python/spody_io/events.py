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
"""Readers for the SpOdy events log binaries.

Two on-disk formats, both starting with the standard 24-byte SpOdy
preamble (see `headers.py`):

- **SPDYEVT_** (v1) -- per-run log written by `spody propagate`. One
  record = one `EventRecord` C struct (80 bytes on x86_64). Mirrors
  `external/spody-core/include/spody_events.h::EventRecord`:

      double t            sim time of trigger [s]
      int32  kind         spody_event_kind enum (IMPACT=0, ECLIPSE=1,
                          ALT_CROSSING=2)
      int32  naif_id      body involved in the trigger
      double radius_km    threshold used for the predicate (for
                          ALT_CROSSING: the body's physical radius --
                          the crossed altitude is
                          distance_km - radius_km)
      double distance_km  observed value at trigger (distance in km
                          for IMPACT / ALT_CROSSING, eclipse fraction
                          for ECLIPSE)
      double y[6]         interpolated state at trigger (km, km/s)

- **SPDYEVTB** (v1) -- aggregated log written by `spody batch` when
  [output].events_log is enabled. One record per trigger across the
  WHOLE batch (no per-case files), with a `case_idx` field added at
  the front so post-processing can join on `cases.csv`. 88 bytes:

      int32  case_idx     0-based row index in cases_file
      int32  _pad         keeps `t` 8-byte aligned
      ... (then the same EventRecord fields as above)

`read_events` auto-detects the format by peeking the magic and
returns a structured numpy array. The two formats produce different
dtypes (the per-run one has no `case_idx` column); a single
caller-side `"case_idx" in arr.dtype.names` test is the standard way
to branch.
"""
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import numpy as np

from .headers import (
    HEADER_BYTES,
    SPODY_EVTB_MAGIC,
    SPODY_EVT_MAGIC,
    _resolve_path,
    read_header,
)

# Mirror of spody_event_kind in spody_events.h. Keep in sync if more
# kinds are added (apsides, geodetic regions, ...).
EVENT_KIND_IMPACT       = 0
EVENT_KIND_ECLIPSE      = 1
EVENT_KIND_ALT_CROSSING = 2

# Per-run record (SPDYEVT_): 80 bytes.
EVENT_DTYPE = np.dtype({
    "names":   ["t", "kind", "naif_id", "radius_km", "distance_km", "y"],
    "formats": ["<f8", "<i4", "<i4",     "<f8",       "<f8",        ("<f8", 6)],
}, align=True)
assert EVENT_DTYPE.itemsize == 80, (
    f"EventRecord size drift: dtype is {EVENT_DTYPE.itemsize}, expected 80"
)

# Aggregated batch record (SPDYEVTB): 88 bytes. case_idx + _pad (8) +
# the same 80-byte EventRecord payload. The pad field is exposed so the
# dtype's itemsize matches the C struct byte-for-byte.
BATCH_EVENT_DTYPE = np.dtype({
    "names":   ["case_idx", "_pad",
                "t", "kind", "naif_id", "radius_km", "distance_km", "y"],
    "formats": ["<i4",      "<i4",
                "<f8", "<i4", "<i4",    "<f8",       "<f8",         ("<f8", 6)],
}, align=True)
assert BATCH_EVENT_DTYPE.itemsize == 88, (
    f"BatchEventRecord size drift: dtype is {BATCH_EVENT_DTYPE.itemsize}, "
    f"expected 88"
)


def _peek_magic(fp: BinaryIO) -> bytes:
    """Read the first 8 bytes (the magic) without consuming them."""
    magic = fp.read(8)
    fp.seek(-len(magic), 1)
    return magic


def read_events(path: str | Path) -> np.ndarray:
    """Load a SpOdy events log into a structured NumPy array.

    Auto-detects the format from the file's magic:

    - SPDYEVT_ (per-run) -> array with dtype = EVENT_DTYPE
    - SPDYEVTB (batch)   -> array with dtype = BATCH_EVENT_DTYPE
                            (the extra `case_idx` field is the 0-based
                            row index in the batch's cases_file)

    Callers that want to handle both formats uniformly can branch on
    `"case_idx" in arr.dtype.names`.
    """
    path = _resolve_path(path)
    with path.open("rb") as fp:
        magic = _peek_magic(fp)
        if magic == SPODY_EVT_MAGIC:
            return _read_per_run(fp, path)
        if magic == SPODY_EVTB_MAGIC:
            return _read_batch(fp, path)
        raise ValueError(
            f"{path}: unrecognised events-log magic {magic!r} "
            f"(expected {SPODY_EVT_MAGIC!r} or {SPODY_EVTB_MAGIC!r})")


def _read_per_run(fp: BinaryIO, path: Path) -> np.ndarray:
    version, record_size = read_header(fp, SPODY_EVT_MAGIC)
    if version != 1:
        raise ValueError(f"{path}: unsupported per-run events v{version}")
    if record_size != EVENT_DTYPE.itemsize:
        raise ValueError(
            f"{path}: record_size={record_size} but reader expects "
            f"{EVENT_DTYPE.itemsize} -- spody-core ABI may have changed")
    return np.fromfile(fp, dtype=EVENT_DTYPE)


def _read_batch(fp: BinaryIO, path: Path) -> np.ndarray:
    version, record_size = read_header(fp, SPODY_EVTB_MAGIC)
    if version != 1:
        raise ValueError(f"{path}: unsupported batch events v{version}")
    if record_size != BATCH_EVENT_DTYPE.itemsize:
        raise ValueError(
            f"{path}: record_size={record_size} but reader expects "
            f"{BATCH_EVENT_DTYPE.itemsize} -- spody.exe ABI may have changed")
    return np.fromfile(fp, dtype=BATCH_EVENT_DTYPE)


# Re-export for backward-compat callers that imported HEADER_BYTES from here.
__all__ = [
    "EVENT_KIND_IMPACT", "EVENT_KIND_ECLIPSE", "EVENT_KIND_ALT_CROSSING",
    "EVENT_DTYPE", "BATCH_EVENT_DTYPE",
    "read_events", "HEADER_BYTES",
]
