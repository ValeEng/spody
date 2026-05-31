"""SpOdy binary readers.

A small standalone library that turns the three on-disk artefacts
produced by `spody` into structured NumPy arrays. No Qt, no plotting --
designed to be used from notebooks, batch analysis scripts, and the
GUI alike.

Each binary has a 24-byte header (8-byte ASCII magic + four
little-endian uint32) followed by fixed-size records. The header
encodes a version and a payload value whose meaning depends on the
file kind:

    SPDYOUT_  payload = state dimension (always 6 in v0)
    SPDYACC_  payload = sizeof(ForceBreakdown) record in bytes (360)
    SPDYEVT_  payload = sizeof(EventRecord)   record in bytes  (80)

The on-disk format matches the C structs verbatim (no padding tricks
needed on x86_64 / aarch64) and is little-endian by definition; the
readers explicitly request `<` byte order so they behave the same on
any host.
"""

from .accel import (
    ACCEL_DTYPE,
    SPODY_FM_MAX_THIRD,
    read_accelerations,
)
from .events import (
    EVENT_DTYPE,
    EVENT_KIND_ECLIPSE,
    EVENT_KIND_IMPACT,
    read_events,
)
from .headers import (
    HEADER_BYTES,
    SPODY_ACC_MAGIC,
    SPODY_BIN_MAGIC,
    SPODY_EVT_MAGIC,
    read_header,
)
from .traj import TRAJ_DTYPE, read_trajectory

__all__ = [
    "ACCEL_DTYPE", "EVENT_DTYPE", "TRAJ_DTYPE",
    "EVENT_KIND_ECLIPSE", "EVENT_KIND_IMPACT",
    "HEADER_BYTES",
    "SPODY_ACC_MAGIC", "SPODY_BIN_MAGIC", "SPODY_EVT_MAGIC",
    "SPODY_FM_MAX_THIRD",
    "read_accelerations", "read_events", "read_header", "read_trajectory",
]
