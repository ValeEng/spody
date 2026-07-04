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

"""Per-kind dispatch tables: plots, readers, labels, kind sniffing.

Assembles the PLOTS dict from each plot module's SPECS list (order
preserved -> tree folder order). Adding a view to an existing kind is
done in that module; adding a NEW file kind means: reader + magic in
spody_io, one entry in each dict below, and a SPECS list in a new
plots module.
"""

from __future__ import annotations

from pathlib import Path

from spody_io import (
    SPODY_ACC_MAGIC,
    SPODY_BIN_MAGIC,
    SPODY_EVT_MAGIC,
    read_accelerations,
    read_events,
    read_trajectory,
)
# The aggregated batch-events magic is exposed through the same
# package but not re-exported by spody_io.__init__; import directly so
# detect_kind can tell the two events formats apart.
from spody_io.headers import SPODY_EVTB_MAGIC

from . import plots_accel, plots_cr3bp, plots_diff, plots_events, plots_traj
from .spec import PlotSpec


# Plot registry, grouped by file kind, assembled from the per-module
# SPECS lists (order preserved: categories stack in the order they
# first appear in the concatenation).
PLOTS: dict[str, list[PlotSpec]] = {
    "traj": [
        *plots_traj.SPECS,
        *plots_cr3bp.TRAJ_SPECS,
        *plots_diff.SPECS,
    ],
    "accel":        list(plots_accel.SPECS),
    "events":       list(plots_events.SPECS_SINGLE),
    "events_batch": list(plots_events.SPECS_BATCH),
}


# Friendly names for the kind tag shown in the type label.
KIND_LABEL = {
    "traj":         "trajectory  (SPDYOUT_)",
    "accel":        "accelerations  (SPDYACC_)",
    "events":       "events log  (SPDYEVT_)",
    "events_batch": "events log  (SPDYEVTB, batch-aggregated)",
}

# `read_events` auto-detects per-run vs batch by peeking the magic and
# returns the matching numpy dtype; both kinds share the reader. The
# split in this map is only there so PLOTS / KIND_LABEL can address
# them separately (batch events carry a `case_idx` column).
READERS = {
    "traj":         read_trajectory,
    "accel":        read_accelerations,
    "events":       read_events,
    "events_batch": read_events,
}


def detect_kind(path: Path) -> str | None:
    """Read the first 8 bytes and match against the known magics."""
    try:
        with path.open("rb") as fp:
            m = fp.read(8)
    except OSError:
        return None
    if m == SPODY_BIN_MAGIC:  return "traj"
    if m == SPODY_ACC_MAGIC:  return "accel"
    if m == SPODY_EVT_MAGIC:  return "events"
    if m == SPODY_EVTB_MAGIC: return "events_batch"
    return None
