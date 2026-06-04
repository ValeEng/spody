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
"""Shared header parsing for the SpOdy binary outputs.

Every spody binary starts with the same 24-byte preamble:

    bytes  0..7  : 8-byte ASCII magic (no NUL terminator)
    bytes  8..11 : uint32 little-endian version number
    bytes 12..15 : uint32 little-endian payload (file-kind dependent)
    bytes 16..23 : reserved (two more uint32s, currently zero)
"""
from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import numpy as np

# 8-byte ASCII magics. Use bytes literals: we never want a NUL terminator.
SPODY_BIN_MAGIC = b"SPDYOUT_"
SPODY_ACC_MAGIC = b"SPDYACC_"
SPODY_EVT_MAGIC = b"SPDYEVT_"

HEADER_BYTES = 24   # 8 (magic) + 16 (four little-endian uint32)


def read_header(fp: BinaryIO, expected_magic: bytes) -> tuple[int, int]:
    """Read and validate the 24-byte preamble. Returns `(version, payload)`
    where payload is the second uint32 (state_dim for SPDYOUT_, record
    size in bytes for SPDYACC_ / SPDYEVT_). Raises ValueError on a
    truncated header or mismatched magic."""
    magic = fp.read(8)
    if magic != expected_magic:
        raise ValueError(
            f"unexpected magic {magic!r}; expected {expected_magic!r}"
        )
    raw = fp.read(16)
    if len(raw) != 16:
        raise ValueError("truncated header (less than 24 bytes total)")
    hdr = np.frombuffer(raw, dtype="<u4")
    return int(hdr[0]), int(hdr[1])


def _resolve_path(p: str | Path) -> Path:
    """Common entry-point boilerplate: accept str/Path, return a Path
    that exists or raise FileNotFoundError with the file name."""
    path = Path(p)
    if not path.is_file():
        raise FileNotFoundError(f"no such file: {path}")
    return path
