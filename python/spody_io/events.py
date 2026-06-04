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
"""Reader for the SPDYEVT_ events log binary.

One record = one `EventRecord` C struct (80 bytes on x86_64). Layout
mirrors `external/spody-core/include/spody_events.h`:

    double t            sim time of trigger [s]
    int32  kind         spody_event_kind enum (IMPACT=0, ECLIPSE=1)
    int32  naif_id      body involved in the trigger
    double radius_km    threshold used for the predicate
    double distance_km  observed value at trigger (for IMPACT)
    double y[6]         interpolated state at trigger (km, km/s)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from .headers import SPODY_EVT_MAGIC, _resolve_path, read_header

# Mirror of spody_event_kind in spody_events.h. Keep in sync if more
# kinds are added (altitude crossings, apsides, ...).
EVENT_KIND_IMPACT  = 0
EVENT_KIND_ECLIPSE = 1

EVENT_DTYPE = np.dtype({
    "names":   ["t", "kind", "naif_id", "radius_km", "distance_km", "y"],
    "formats": ["<f8", "<i4", "<i4",     "<f8",       "<f8",        ("<f8", 6)],
}, align=True)
assert EVENT_DTYPE.itemsize == 80, (
    f"EventRecord size drift: dtype is {EVENT_DTYPE.itemsize}, expected 80"
)


def read_events(path: str | Path) -> np.ndarray:
    """Load a SPDYEVT_ binary into a structured NumPy array (dtype =
    EVENT_DTYPE)."""
    path = _resolve_path(path)
    with path.open("rb") as fp:
        version, record_size = read_header(fp, SPODY_EVT_MAGIC)
        if version != 1:
            raise ValueError(f"{path}: unsupported events format v{version}")
        if record_size != EVENT_DTYPE.itemsize:
            raise ValueError(
                f"{path}: record_size={record_size} but reader expects "
                f"{EVENT_DTYPE.itemsize} -- spody-core ABI may have changed"
            )
        return np.fromfile(fp, dtype=EVENT_DTYPE)
