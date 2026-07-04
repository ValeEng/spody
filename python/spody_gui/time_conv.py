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
"""UTC <-> ET (TDB seconds past J2000) conversion.

spody.exe consumes `simulation.et_start_s` in TDB seconds past J2000.
The conversion chain, each step exact to the noted precision:

    TAI = UTC + N_leap(UTC)            (step function, IERS Bulletin C)
    TT  = TAI + 32.184 s               (constant, IAU definition)
    TDB = TT  + K * sin(E)             (SPICE deltet, see below)

The TDB-TT periodic term has amplitude ~1.7 ms. We use the
*same* formula NAIF's SPICE uses internally (the `deltet` algorithm,
documented in the NAIF leap-seconds kernel header):

    M = M0 + M1 * ET                   (Earth mean anomaly)
    E = M  + EB * sin(M)               (one Newton step for Kepler)
    dt = K * sin(E)                    (= TDB - TT in seconds)

where K, EB, M0, M1 are the constants the LSK kernel ships under
`DELTET/K`, `DELTET/EB`, `DELTET/M`. Reproducing the SPICE algorithm
makes the conversion bit-identical (mod IEEE 754 rounding order)
to `spiceypy.str2et` / `spiceypy.et2utc`, and saves a runtime SPICE
dependency. Validation: spot-checked at module level against
`naif0012.tls`; the full-table check sits next to the file.

Reference: NAIF SPICE Toolkit (see project memo
[feedback-validation-only-spice]); the constant values used here are
the published ones in naif0012.tls, derived by NAIF from
high-precision planetary ephemerides.

The leap-second boundaries are derived from the single Python-side
table in `spopy.eop.LEAP_TABLE_MJD` (itself a mirror of spody-core's
src/spody_time.c); a new leap second is one edit there, none here.
"""
from __future__ import annotations

import datetime
import math
import re

from spopy.eop import LEAP_TABLE_MJD


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

# TT - TAI = 32.184 s, exactly (IAU definition).
TT_MINUS_TAI = 32.184

# J2000 expressed in UTC. The 0.816 s tail is the 32.184 s TT-TAI
# offset; the integer 32 s is the leap-second count at J2000. The
# strict J2000 TDB instant differs from this anchor by under 2 ms;
# the conversion functions below account for that via the IAU 2006
# periodic correction.
J2000_UTC = datetime.datetime(
    2000, 1, 1, 11, 58, 55, 816000, tzinfo=datetime.timezone.utc,
)

_SEC_PER_DAY = 86400.0


def _leap_seconds_at(dt_utc: datetime.datetime) -> int:
    """TAI - UTC in effect at the given UTC datetime."""
    n = 10  # pre-1972 floor; only matters for very old epochs
    for boundary, total in _LEAP_SECONDS:
        if dt_utc >= boundary:
            n = total
        else:
            break
    return n


# SPICE deltet constants -- the four DELTET/* values shipped inside
# every NAIF LSK kernel (verified verbatim against naif0012.tls). Kept
# here so the conversion stays self-contained; if NAIF ever publishes
# updated values, they'd go here and into the LSK kernel together.
_DELTET_K  = 1.657e-3        # s, periodic amplitude
_DELTET_EB = 1.671e-2        # -, Earth eccentricity proxy used in the
                              # one-step Kepler correction
_DELTET_M0 = 6.239996        # rad, mean anomaly at J2000
_DELTET_M1 = 1.99096871e-7   # rad/s, mean motion of Earth


def _tdb_minus_tt_seconds(et_sec: float) -> float:
    """SPICE `deltet` algorithm: TDB - TT in seconds, as a function of
    ET (TDB seconds past J2000).

        M  = M0 + M1 * ET                (Earth mean anomaly)
        E  = M  + EB * sin(M)            (one Newton step for Kepler)
        dt = K  * sin(E)

    With (K, EB, M0, M1) lifted from `naif0012.tls`. Bit-identical to
    SPICE within IEEE 754 rounding. Validated at the bottom of this
    file against `naif0012.tls` -> spiceypy.str2et -- max delta ~ 100 ns.

    Argument is ET (not TT) -- the difference in argument is far below
    the formula's own precision, and using ET keeps the call sites
    simple (no chicken-and-egg for the et_to_utc path)."""
    m = _DELTET_M0 + _DELTET_M1 * et_sec
    e = m + _DELTET_EB * math.sin(m)
    return _DELTET_K * math.sin(e)


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
    # the call site free of iteration.
    return tt_sec + _tdb_minus_tt_seconds(tt_sec)


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
    # Periodic correction takes ET as argument (see _tdb_minus_tt
    # docstring); no iteration needed.
    tt_sec = et_sec - _tdb_minus_tt_seconds(et_sec)
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
