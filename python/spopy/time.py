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
"""Calendar / time-scale conversions: the Python twin of spody-core's
`src/spody_time.c`, plus the datetime / ISO 8601 helpers the GUI
builds on top of it.

Mirrored from the C side, same operation order so the results are
bit-identical (kept in lockstep -- change one, change both):

    LEAP_TABLE_MJD   <->  leap_table          (spody_time.c)
    tai_minus_utc    <->  spody_tai_minus_utc
    tdb_minus_tt     <->  spody_tdb_minus_tt  (SPICE deltet)
    et_to_mjd_utc    <->  spody_et_to_mjd_utc

spody.exe consumes ET = TDB seconds past J2000. The conversion
chain, each step exact to the noted precision:

    TAI = UTC + N_leap(UTC)            (step function, IERS Bulletin C)
    TT  = TAI + 32.184 s               (constant, IAU definition)
    TDB = TT  + K * sin(E)             (SPICE deltet, see below)

The TDB-TT periodic term has amplitude ~1.7 ms. We use the *same*
formula NAIF's SPICE uses internally (the `deltet` algorithm,
documented in the NAIF leap-seconds kernel header):

    M = M0 + M1 * ET                   (Earth mean anomaly)
    E = M  + EB * sin(M)               (one Newton step for Kepler)
    dt = K * sin(E)                    (= TDB - TT in seconds)

where K, EB, M0, M1 are the constants the LSK kernel ships under
`DELTET/K`, `DELTET/EB`, `DELTET/M`. Reproducing the SPICE algorithm
makes the conversion bit-identical (mod IEEE 754 rounding order)
to `spiceypy.str2et` / `spiceypy.et2utc`, and saves a runtime SPICE
dependency. Validation: spot-checked against `naif0012.tls` ->
spiceypy.str2et -- max delta ~ 100 ns.

Adding a new leap second is one entry in `LEAP_TABLE_MJD` (THE single
Python-side copy) plus its C twin in spody_time.c; every other Python
consumer derives from here.
"""
from __future__ import annotations

import datetime
import math
import re

import numpy as np


# ----------------------------------------------------------------------
# Leap-second table: (MJD_UTC at which leap second is INTRODUCED,
# cumulative TAI - UTC in seconds after that introduction).
# Source: IERS Bulletin C history.
# ----------------------------------------------------------------------
LEAP_TABLE_MJD: tuple[tuple[float, float], ...] = (
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

_LEAP_MJDS = np.array([row[0] for row in LEAP_TABLE_MJD])
_LEAP_TAI_MINUS_UTC = np.array([row[1] for row in LEAP_TABLE_MJD])

# Constants mirroring spody-core's spody_const.h (SECONDSxDAY,
# JD_J2000, JD_MJD_EPOCH, -TT2TAI_SEC).
SECONDS_PER_DAY = 86400.0
JD_J2000_TT = 2451545.0
MJD_OFFSET = 2400000.5
TT_MINUS_TAI = 32.184     # TT - TAI, exact (IAU definition)

# SPICE deltet constants -- the four DELTET/* values shipped inside
# every NAIF LSK kernel (verified verbatim against naif0012.tls) and
# mirrored in spody_const.h. If NAIF ever publishes updated values,
# they'd go here, in spody_const.h and into the LSK kernel together.
_DELTET_K  = 1.657e-3        # s, periodic amplitude
_DELTET_EB = 1.671e-2        # -, Earth eccentricity proxy used in the
                              # one-step Kepler correction
_DELTET_M0 = 6.239996        # rad, mean anomaly at J2000
_DELTET_M1 = 1.99096871e-7   # rad/s, mean motion of Earth


def tai_minus_utc(mjd_utc: float) -> float:
    """TAI-UTC at the given UTC MJD. Step-function: piecewise constant
    between consecutive leap-second insertions. Mirrors spody-core's
    `spody_tai_minus_utc` (src/spody_time.c)."""
    if mjd_utc < _LEAP_MJDS[0]:
        return float(_LEAP_TAI_MINUS_UTC[0])
    # searchsorted(side='right') gives the index of the FIRST entry
    # > mjd_utc; the leap value is the table entry just before that.
    idx = int(np.searchsorted(_LEAP_MJDS, mjd_utc, side="right")) - 1
    if idx < 0:
        idx = 0
    return float(_LEAP_TAI_MINUS_UTC[idx])


def tdb_minus_tt(et_sec: float) -> float:
    """SPICE `deltet` algorithm: TDB - TT in seconds, as a function of
    ET (TDB seconds past J2000). Mirrors spody-core's
    `spody_tdb_minus_tt` (src/spody_time.c) bit-for-bit.

        M  = M0 + M1 * ET                (Earth mean anomaly)
        E  = M  + EB * sin(M)            (one Newton step for Kepler)
        dt = K  * sin(E)

    With (K, EB, M0, M1) lifted from `naif0012.tls`. Bit-identical to
    SPICE within IEEE 754 rounding (validated against `naif0012.tls`
    -> spiceypy.str2et -- max delta ~ 100 ns).

    Argument is ET (not TT) -- the difference in argument is far below
    the formula's own precision, and using ET keeps the call sites
    simple (no chicken-and-egg for the et_to_utc path)."""
    m = _DELTET_M0 + _DELTET_M1 * et_sec
    e = m + _DELTET_EB * math.sin(m)
    return _DELTET_K * math.sin(e)


def et_to_mjd_utc(et: float) -> float:
    """ET (TDB s past J2000) -> UTC MJD. Full chain (TT = ET - deltet,
    then the leap-second step function), as a two-iteration fixed
    point: TAI-UTC is a function of UTC itself, so the step function
    is evaluated first at TAI, then at the estimated UTC. Mirrors
    spody-core's `spody_et_to_mjd_utc` bit-for-bit."""
    tt_sec = et - tdb_minus_tt(et)
    mjd_tt = (tt_sec / SECONDS_PER_DAY) + (JD_J2000_TT - MJD_OFFSET)
    mjd_tai = mjd_tt - TT_MINUS_TAI / SECONDS_PER_DAY
    leap = tai_minus_utc(mjd_tai)
    mjd_utc = mjd_tai - leap / SECONDS_PER_DAY
    leap = tai_minus_utc(mjd_utc)
    return mjd_tai - leap / SECONDS_PER_DAY


# ----------------------------------------------------------------------
# datetime-level helpers (Python-only: no C sibling). The GUI's
# ET <-> UTC fields and the analysis overlay build on these.
# ----------------------------------------------------------------------

# UTC calendar boundaries of the leap-second steps, derived from the
# canonical MJD table. MJD 0 = 1858-11-17 00:00 UTC; the entries are
# exact midnights, so the timedelta conversion is lossless.
_MJD_EPOCH_UTC = datetime.datetime(
    1858, 11, 17, tzinfo=datetime.timezone.utc)
_LEAP_SECONDS: tuple[tuple[datetime.datetime, int], ...] = tuple(
    (_MJD_EPOCH_UTC + datetime.timedelta(days=mjd), int(tai_utc))
    for mjd, tai_utc in LEAP_TABLE_MJD
)

# TAI - UTC at J2000. Used as the reference offset so that delta_leaps
# arithmetic stays simple (delta = 0 at J2000, +5 in 2026).
_LEAP_AT_J2000 = 32

# J2000 expressed in UTC. The 0.816 s tail is the 32.184 s TT-TAI
# offset; the integer 32 s is the leap-second count at J2000. The
# strict J2000 TDB instant differs from this anchor by under 2 ms;
# the conversion functions below account for that via the deltet
# periodic correction.
J2000_UTC = datetime.datetime(
    2000, 1, 1, 11, 58, 55, 816000, tzinfo=datetime.timezone.utc,
)


def _leap_seconds_at(dt_utc: datetime.datetime) -> int:
    """TAI - UTC in effect at the given UTC datetime."""
    n = 10  # pre-1972 floor; only matters for very old epochs
    for boundary, total in _LEAP_SECONDS:
        if dt_utc >= boundary:
            n = total
        else:
            break
    return n


def utc_to_et(dt_utc: datetime.datetime) -> float:
    """UTC datetime (timezone-aware) -> ET (TDB seconds past J2000).

    Naive datetimes raise ValueError -- forcing the caller to commit
    to a timezone avoids the silent 'local time treated as UTC' bug
    that bites every astronomical pipeline once."""
    if dt_utc.tzinfo is None:
        raise ValueError(
            "utc_to_et needs a tz-aware datetime; "
            "pass tzinfo=datetime.timezone.utc")
    dt_utc = dt_utc.astimezone(datetime.timezone.utc)
    leaps = _leap_seconds_at(dt_utc)
    # TT seconds past J2000_TT epoch. J2000_UTC was built with the
    # J2000-era leap count + 32.184 s already baked in, so the only
    # leap-related quantity we need now is the *change* in leaps since
    # J2000.
    delta_leaps = leaps - _LEAP_AT_J2000
    tt_sec = (dt_utc - J2000_UTC).total_seconds() + delta_leaps
    # Periodic(TT) vs periodic(TDB) differ by < 1 ns at any realistic
    # epoch -- passing TT into the deltet formula is fine and keeps
    # the call site free of iteration. Same recipe as the spody-core
    # GNSS converters (tt + spody_tdb_minus_tt(tt)).
    return tt_sec + tdb_minus_tt(tt_sec)


def et_to_utc(et_sec: float) -> datetime.datetime:
    """ET (TDB seconds past J2000) -> UTC datetime (tz-aware,
    microsecond resolution).

    Inverse of `utc_to_et`. No iteration needed because the SPICE
    deltet formula already takes ET as its argument.

    The leap-count lookup uses an approximate UTC built from
    et_sec ignoring the leap correction. Worst-case bin error ~64 s,
    which only misclassifies the leap count for instants within 64 s
    of midnight UTC of a leap-second boundary date. Outside that
    window (overwhelmingly common) the count is exact."""
    # Periodic correction takes ET as argument (see tdb_minus_tt
    # docstring); no iteration needed.
    tt_sec = et_sec - tdb_minus_tt(et_sec)
    approx_utc = J2000_UTC + datetime.timedelta(seconds=tt_sec)
    leaps = _leap_seconds_at(approx_utc)
    delta_leaps = leaps - _LEAP_AT_J2000
    return J2000_UTC + datetime.timedelta(seconds=tt_sec - delta_leaps)


# ISO 8601 instants we accept on input.
_ISO_INPUT_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})"
    r"[T ](\d{2}):(\d{2}):(\d{2})(\.\d+)?"
    r"(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_utc_iso(text: str) -> datetime.datetime:
    """Parse an ISO 8601 UTC string into a tz-aware datetime.

    Accepts 'YYYY-MM-DDThh:mm:ss[.fff][Z|+HH:MM]'. The trailing 'Z'
    is treated as +00:00. Missing timezone is assumed UTC (the form
    field is documented as 'UTC ISO 8601' so this is the friendly
    interpretation -- defenders of strict ISO can append 'Z')."""
    text = text.strip()
    if not text:
        raise ValueError("empty UTC string")
    if not _ISO_INPUT_RE.match(text):
        raise ValueError(
            f"not a recognised ISO 8601 UTC string: {text!r}.\n"
            "Expected something like 2009-09-18T12:00:00 or "
            "2009-09-18T12:00:00.123Z.")
    # datetime.fromisoformat accepts 'Z' suffix only from Python 3.11+;
    # normalise to +00:00 first so 3.9/3.10 work.
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def format_utc_iso(dt_utc: datetime.datetime,
                   trailing_z: bool = True,
                   fractional_digits: int = 6) -> str:
    """Render a tz-aware datetime as ISO 8601 UTC. The default keeps
    full microsecond precision (6 digits) so a UTC string round-trips
    losslessly through utc_to_et -> et_to_utc when the underlying ET
    value is exact."""
    if dt_utc.tzinfo is None:
        raise ValueError("format_utc_iso needs a tz-aware datetime")
    dt_utc = dt_utc.astimezone(datetime.timezone.utc)
    base = dt_utc.strftime("%Y-%m-%dT%H:%M:%S")
    if fractional_digits > 0:
        digits = f"{dt_utc.microsecond:06d}"[:fractional_digits]
        base = f"{base}.{digits}"
    return base + ("Z" if trailing_z else "+00:00")
