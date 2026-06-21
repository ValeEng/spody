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
"""Pure-Python parser + interpolator for IERS finals2000A.all.

Mirrors `spody_eop.c` in spody-core: same fixed-width column layout
(USNO readme.finals2000A), same Bulletin-B-preferred selection, same
ET -> UTC MJD time chain (TDB ~= TT, TAI = TT - 32.184 s, UTC = TAI -
leap_seconds), same linear interpolation across daily-stepped records.

Used by `spopy.earth_orientation.icrf_to_itrs` to assemble the IAU
2006/2000A_R06 rotation matrix at the GUI's interactive query speed,
without spawning subprocesses or loading the C engine as a shared
library. Adding a new leap second is one entry in `_LEAP_TABLE`.

The values returned by `MappedEOP.interpolate` are bit-equivalent to
the C engine's `spody_interpolate_eop` (cross-checked at MJD 60324
against `spody-core/tvb/tests/test_eop_load.c`).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np


# ----------------------------------------------------------------------
# Leap-second table: (MJD_UTC at which leap second is INTRODUCED,
# cumulative TAI - UTC in seconds after that introduction).
# Source: IERS Bulletin C history.
# Mirrors spody-core's `_leap_table` in src/spody_eop.c.
# ----------------------------------------------------------------------
_LEAP_TABLE: tuple[tuple[float, float], ...] = (
    (41317.0, 10.0),  # 1972-01-01 -- first IERS-defined entry
    (41499.0, 11.0),  # 1972-07-01
    (41683.0, 12.0),  # 1973-01-01
    (42048.0, 13.0),  # 1974-01-01
    (42413.0, 14.0),  # 1975-01-01
    (42778.0, 15.0),  # 1976-01-01
    (43144.0, 16.0),  # 1977-01-01
    (43509.0, 17.0),  # 1978-01-01
    (43874.0, 18.0),  # 1979-01-01
    (44239.0, 19.0),  # 1980-01-01
    (44786.0, 20.0),  # 1981-07-01
    (45151.0, 21.0),  # 1982-07-01
    (45516.0, 22.0),  # 1983-07-01
    (46247.0, 23.0),  # 1985-07-01
    (47161.0, 24.0),  # 1988-01-01
    (47892.0, 25.0),  # 1990-01-01
    (48257.0, 26.0),  # 1991-01-01
    (48804.0, 27.0),  # 1992-07-01
    (49169.0, 28.0),  # 1993-07-01
    (49534.0, 29.0),  # 1994-07-01
    (50083.0, 30.0),  # 1996-01-01
    (50630.0, 31.0),  # 1997-07-01
    (51179.0, 32.0),  # 1999-01-01
    (53736.0, 33.0),  # 2006-01-01
    (54832.0, 34.0),  # 2009-01-01
    (56109.0, 35.0),  # 2012-07-01
    (57204.0, 36.0),  # 2015-07-01
    (57754.0, 37.0),  # 2017-01-01 -- current value as of 2026
)

_LEAP_MJDS = np.array([row[0] for row in _LEAP_TABLE])
_LEAP_TAI_MINUS_UTC = np.array([row[1] for row in _LEAP_TABLE])

# Constants. SECONDS_PER_DAY and JD_J2000 mirror spody-core's
# `SECONDSxDAY` and `JD_J2000` in spody_const.h.
_SECONDS_PER_DAY = 86400.0
_JD_J2000_TT = 2451545.0
_MJD_OFFSET = 2400000.5
_TT_MINUS_TAI = 32.184


def _tai_minus_utc(mjd_utc: float) -> float:
    """TAI-UTC at the given UTC MJD. Step-function: piecewise constant
    between consecutive leap-second insertions. Mirrors spody-core's
    `_tai_minus_utc` (src/spody_eop.c)."""
    if mjd_utc < _LEAP_MJDS[0]:
        return float(_LEAP_TAI_MINUS_UTC[0])
    # searchsorted(side='right') gives the index of the FIRST entry
    # > mjd_utc; the leap value is the table entry just before that.
    idx = int(np.searchsorted(_LEAP_MJDS, mjd_utc, side="right")) - 1
    if idx < 0:
        idx = 0
    return float(_LEAP_TAI_MINUS_UTC[idx])


def _et_to_mjd_utc(et: float) -> float:
    """ET (TDB s past J2000) -> UTC MJD. Two-iteration fixed-point
    around the leap-second step function, identical to spody-core's
    `_et_to_mjd_utc`. TDB-TT is <2 ms over the EOP table's coverage
    and folded into the daily interpolation noise."""
    mjd_tt = (et / _SECONDS_PER_DAY) + (_JD_J2000_TT - _MJD_OFFSET)
    mjd_tai = mjd_tt - _TT_MINUS_TAI / _SECONDS_PER_DAY
    leap = _tai_minus_utc(mjd_tai)
    mjd_utc = mjd_tai - leap / _SECONDS_PER_DAY
    leap = _tai_minus_utc(mjd_utc)
    return mjd_tai - leap / _SECONDS_PER_DAY


# ----------------------------------------------------------------------
# finals2000A.all parser
#
# Column widths (1-based, inclusive). In Python slice form `line[a:b]`
# the start is (col-1) and the end is col_end (Python's exclusive
# stop matches the 1-based inclusive end).
# ----------------------------------------------------------------------
def _read_field(line: str, col_start: int, col_end_inclusive: int) -> float | None:
    """Slice + strtod a fixed-width F-format field; return None when
    the field is blank or unparseable."""
    a = col_start - 1
    b = col_end_inclusive   # Python's exclusive stop = 1-based end
    if len(line) < b:
        return None
    chunk = line[a:b]
    if not chunk.strip():
        return None
    try:
        return float(chunk)
    except ValueError:
        return None


def _parse_line(line: str) -> tuple[float, float, float, float, float, float] | None:
    """Parse one finals2000A.all line. Returns
    (mjd, xp_arcsec, yp_arcsec, dut1_sec, dx_mas, dy_mas) preferring
    Bulletin B values when present, else Bulletin A. Returns None for
    header/empty rows."""
    mjd = _read_field(line, 8, 15)
    xa  = _read_field(line, 18, 27)
    ya  = _read_field(line, 38, 46)
    dut1a = _read_field(line, 59, 68)
    if mjd is None or xa is None or ya is None or dut1a is None:
        return None

    # Bulletin A CIP corrections (mas). Old rows have these blank;
    # treat blank as 0 (no correction).
    dxa = _read_field(line, 98, 106) or 0.0
    dya = _read_field(line, 118, 126) or 0.0

    # Bulletin B columns. Blank on prediction days.
    xb    = _read_field(line, 135, 144)
    yb    = _read_field(line, 145, 154)
    dut1b = _read_field(line, 155, 165)
    has_b = (xb is not None and yb is not None and dut1b is not None)

    if has_b:
        dxb = _read_field(line, 166, 175)
        dyb = _read_field(line, 176, 185)
        # dX/dY in Bulletin B may be blank even when xp/yp/UT1 are
        # filled: fall back to Bulletin A values.
        dx_out = dxb if dxb is not None else dxa
        dy_out = dyb if dyb is not None else dya
        return (mjd, xb, yb, dut1b, dx_out, dy_out)
    return (mjd, xa, ya, dut1a, dxa, dya)


class MappedEOP:
    """In-memory EOP table loaded from finals2000A.all. Equivalent to
    spody-core's `MappedEOPData` + `MappedEOP` rolled into one. Indexed
    daily; linear interpolation between consecutive rows."""

    __slots__ = ("_records", "mjd_first", "mjd_last_predicted",
                 "mjd_last_observed", "_cached_idx")

    def __init__(self, filename: str | Path) -> None:
        records: list[tuple[float, float, float, float, float, float]] = []
        mjd_last_b: float = -float("inf")
        with open(filename, encoding="ascii") as f:
            for line in f:
                rec = _parse_line(line)
                if rec is None:
                    continue
                records.append(rec)
                # Bulletin-B presence = the dut1 column was read from
                # the Bulletin B slot (which is blank in pred-rows).
                if _read_field(line, 155, 165) is not None:
                    if rec[0] > mjd_last_b:
                        mjd_last_b = rec[0]
        if not records:
            raise ValueError(f"no usable EOP records in {filename}")
        # Columns: mjd, xp_arcsec, yp_arcsec, dut1_sec, dx_mas, dy_mas
        self._records = np.asarray(records, dtype=np.float64)
        self.mjd_first = float(self._records[0, 0])
        self.mjd_last_predicted = float(self._records[-1, 0])
        self.mjd_last_observed = (mjd_last_b if mjd_last_b > -float("inf")
                                  else self.mjd_first)
        self._cached_idx = 0

    def interpolate(self, et: float) -> tuple[float, float, float, float, float] | None:
        """Linear-interpolate (xp, yp, dut1, dX, dY) at ET. Returns
        None when `et` falls outside the table's coverage. Matches
        spody-core's `spody_interpolate_eop` numerically."""
        mjd = _et_to_mjd_utc(et)
        if mjd < self.mjd_first or mjd > self.mjd_last_predicted:
            return None
        mjds = self._records[:, 0]
        # Find largest index i with records[i].mjd <= mjd. The cached
        # index handles the common interactive case (animation frames
        # walking forward); fall back to searchsorted otherwise.
        i = self._cached_idx
        n = len(mjds)
        if not (0 <= i < n - 1 and mjds[i] <= mjd < mjds[i + 1]):
            i = int(np.searchsorted(mjds, mjd, side="right")) - 1
            if i < 0:
                i = 0
            if i >= n - 1:
                # Boundary: mjd exactly on the last record.
                r = self._records[i]
                return (float(r[1]), float(r[2]), float(r[3]),
                        float(r[4]), float(r[5]))
            self._cached_idx = i
        lo = self._records[i]
        hi = self._records[i + 1]
        dmjd = hi[0] - lo[0]
        frac = (mjd - lo[0]) / dmjd if dmjd > 0.0 else 0.0
        out = lo[1:] + frac * (hi[1:] - lo[1:])
        return (float(out[0]), float(out[1]), float(out[2]),
                float(out[3]), float(out[4]))
