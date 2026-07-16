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
"""Reader for the .spody JPL ephemeris binary.

Mirrors the on-disk layout produced by
`spody_createfile_MappedEphemerisData` and read by
`spody_setup_MappedEphemerisData` in
[external/spody-core/src/spody_ephemeris.c](../../../external/spody-core/src/spody_ephemeris.c).

Layout
------
Header (224 bytes, little-endian):

    offset  type        field
      0     char[8]     magic            ("SPDYEPET")
      8     uint32      format_version   (1)
     12     uint32      reserved         (0)
     16     double      start_epoch      (ET seconds past J2000 TDB)
     24     double      end_epoch        (same units as start_epoch)
     32     int32       seconds_per_record
     36     int32       bytes_per_record
     40     int32       number_coefficients_per_record
     44     int32[15]   location           (1-based start index per body)
    104     int32[15]   number_coefficients_per_component
    164     int32[15]   number_complete_sets_coefficients_per_record

Each record (bytes_per_record bytes):

    offset  type        field
      0     int32       record_number
      4     int32       number_coefficients_per_record
      8     double      record_start_epoch (ET seconds past J2000 TDB)
     16     double      record_end_epoch
     24     double[N]   coefficients  (N = number_coefficients_per_record)

The first 2 values in `coefficients` are legacy JD bounds (kept for
ASCII-source reproducibility). The actual Chebyshev coefficients for
body `i` start at `coefficients[location[i] - 1]`; within that slice
the per-record time interval is split into
`number_complete_sets_coefficients_per_record[i]` equal sub-intervals,
each carrying `3 * number_coefficients_per_component[i]` doubles
(`x` then `y` then `z`).

Body indices (0-based slot inside the file):

    0 Mercury        5 Saturn         10 Sun
    1 Venus          6 Uranus         11 Earth nutations (2 angles, not exposed)
    2 EMB            7 Neptune        12 Lunar libration angles (phi, theta, psi)
    3 Mars           8 Pluto          13/14 reserved (n_coeffs == 0 in DE440)
    4 Jupiter        9 Moon (geocentric, relative to EMB)

NAIF mappings (matches `get_body_position_ssb` in the C source):
    0 (SSB)                   -> all zeros
    1 / 199 -> Mercury        5 / 599 -> Jupiter
    2 / 299 -> Venus          6 / 699 -> Saturn
    3       -> EMB            7 / 799 -> Uranus
    4 / 499 -> Mars           8 / 899 -> Neptune
                              9 / 999 -> Pluto      10 -> Sun
    301 -> Moon (centre)      399 -> Earth (centre)

Position queries return central-body-relative km in the ICRF
(equator-and-equinox at J2000) frame; velocity/state queries add the
exact km/s rates from the analytic derivative of the same Chebyshev
series (no finite differences). Time is ET in seconds past J2000 TDB
-- the same convention spody.exe uses for `simulation.et_start_s`.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np


# ---------- on-disk constants ----------------------------------------
_MAGIC_ET            = b"SPDYEPET"
_HEADER_BYTES        = 224
_RECORD_PREFIX_BYTES = 24    # record_number + n_coeffs + start_et + end_et
_N_BODY_SLOTS        = 15    # location[] / n_coeffs[] / n_sets[] arrays
_SUPPORTED_VERSION   = 1

# ---------- DE440 in-file body indices --------------------------------
IDX_MERCURY            = 0
IDX_VENUS              = 1
IDX_EMB                = 2     # Earth-Moon barycentre
IDX_MARS               = 3
IDX_JUPITER            = 4
IDX_SATURN             = 5
IDX_URANUS             = 6
IDX_NEPTUNE            = 7
IDX_PLUTO              = 8
IDX_MOON_GEOCENTRIC    = 9     # Moon relative to Earth (NOT to SSB)
IDX_SUN                = 10
IDX_EARTH_NUTATIONS    = 11    # 2 angles; not exposed via this reader
IDX_LUNAR_LIBRATION    = 12    # (phi, theta, psi) Euler 313 -> Moon PA

# ---------- NAIF id constants -----------------------------------------
NAIF_SSB     = 0
NAIF_MERCURY = 199
NAIF_VENUS   = 299
NAIF_EARTH   = 399
NAIF_MOON    = 301
NAIF_MARS    = 499
NAIF_JUPITER = 599
NAIF_SATURN  = 699
NAIF_URANUS  = 799
NAIF_NEPTUNE = 899
NAIF_PLUTO   = 999
NAIF_SUN     = 10
NAIF_EMB     = 3
# Short aliases also accepted (1..9) per the C source's switch().
_NAIF_TO_IDX: dict[int, int] = {
    1: IDX_MERCURY, 199: IDX_MERCURY,
    2: IDX_VENUS,   299: IDX_VENUS,
    3: IDX_EMB,
    4: IDX_MARS,    499: IDX_MARS,
    5: IDX_JUPITER, 599: IDX_JUPITER,
    6: IDX_SATURN,  699: IDX_SATURN,
    7: IDX_URANUS,  799: IDX_URANUS,
    8: IDX_NEPTUNE, 899: IDX_NEPTUNE,
    9: IDX_PLUTO,   999: IDX_PLUTO,
    10: IDX_SUN,
}

# Earth-Moon mass ratio used by the EMB <-> Earth / Moon split. Mirrors
# `EMRAT` in external/spody-core/include/spody_const.h.
EMRAT = 0.813005682214972154e+02


def _chebyshev_eval(tau: float, coeffs: np.ndarray) -> float:
    """Clenshaw recurrence for Chebyshev T_n on [-1, 1]. Mirrors
    `chebyshev_evaluate` in spody_ephemeris.c byte for byte."""
    n = coeffs.size
    if n == 0:
        return 0.0
    if n == 1:
        return float(coeffs[0])
    two_x = 2.0 * tau
    v_kp1 = 0.0
    v_k   = 0.0
    for k in range(n - 1, 0, -1):
        v_km1 = float(coeffs[k]) + two_x * v_k - v_kp1
        v_kp1 = v_k
        v_k   = v_km1
    return float(coeffs[0]) + tau * v_k - v_kp1


def _chebyshev_eval_deriv(tau: float, coeffs: np.ndarray) -> float:
    """d/d(tau) of the Chebyshev series, via T'_k = k * U_{k-1} and the
    same backward Clenshaw scheme on the second-kind series (the result
    is simply b_0 for the U recurrence). Mirrors
    `chebyshev_evaluate_derivative` in spody_ephemeris.c byte for byte.
    The caller rescales by d(tau)/dt to get a physical rate."""
    n = coeffs.size
    if n <= 1:
        return 0.0
    two_x = 2.0 * tau
    b_kp1 = 0.0
    b_k   = 0.0
    for k in range(n - 2, -1, -1):
        # series term d_k = (k+1) * c_{k+1}
        b_km1 = float(k + 1) * float(coeffs[k + 1]) + two_x * b_k - b_kp1
        b_kp1 = b_k
        b_k   = b_km1
    return b_k


class Ephemeris:
    """Read-only handle on a `.spody` JPL ephemeris binary.

    Open once, query many: the file is memory-resident (read into a
    bytearray on `__init__`), and per-body Chebyshev coefficients are
    located by direct offset arithmetic at every call. A 1-slot cache
    per body keeps repeated (idx, et) queries free, mirroring the C
    `MappedEphemeris.cache_*` behaviour.

    Thread-safety: the cache makes an instance non-thread-safe. Each
    worker thread should have its own `Ephemeris`. The on-disk file
    itself is fine to open from multiple instances; only the per-
    instance Python state mutates.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if not self._path.is_file():
            raise FileNotFoundError(f"no such ephemeris file: {self._path}")
        with self._path.open("rb") as fp:
            self._buf = fp.read()
        self._parse_header()
        # Build numpy views over the records so position queries avoid
        # repeated struct.unpack: each record is a 1-D float64 array
        # backed by the in-memory bytes.
        self._records = self._build_record_views()
        # Per-body 1-slot cache (mirrors EPH_CACHE_SLOTS in the C side).
        # Velocity has its own valid flag: position-only queries leave
        # the velocity slot stale (mirrors cache_vel_valid in C).
        self._cache_et:  list[float | None] = [None] * _N_BODY_SLOTS
        self._cache_pos: list[np.ndarray]   = [np.zeros(3) for _ in range(_N_BODY_SLOTS)]
        self._cache_vel: list[np.ndarray]   = [np.zeros(3) for _ in range(_N_BODY_SLOTS)]
        self._cache_vel_valid: list[bool]   = [False] * _N_BODY_SLOTS

    # ------------------------------------------------------------------
    # Header parsing
    # ------------------------------------------------------------------
    def _parse_header(self) -> None:
        b = self._buf
        if len(b) < _HEADER_BYTES:
            raise ValueError(
                f"{self._path}: file too small ({len(b)} bytes) "
                f"for ephemeris header ({_HEADER_BYTES} bytes)")
        if b[:8] != _MAGIC_ET:
            raise ValueError(
                f"{self._path}: bad magic {b[:8]!r}, expected {_MAGIC_ET!r}")
        version = struct.unpack_from("<I", b,  8)[0]
        if version != _SUPPORTED_VERSION:
            raise ValueError(
                f"{self._path}: unsupported format version {version} "
                f"(reader supports v{_SUPPORTED_VERSION})")
        self.start_epoch_et         = struct.unpack_from("<d", b, 16)[0]
        self.end_epoch_et           = struct.unpack_from("<d", b, 24)[0]
        self.seconds_per_record     = struct.unpack_from("<i", b, 32)[0]
        self.bytes_per_record       = struct.unpack_from("<i", b, 36)[0]
        self.n_coefficients_per_rec = struct.unpack_from("<i", b, 40)[0]
        self.location               = struct.unpack_from(f"<{_N_BODY_SLOTS}i", b, 44)
        self.n_coeffs_per_component = struct.unpack_from(
            f"<{_N_BODY_SLOTS}i", b, 44 + 4 * _N_BODY_SLOTS)
        self.n_complete_sets        = struct.unpack_from(
            f"<{_N_BODY_SLOTS}i", b, 44 + 8 * _N_BODY_SLOTS)

        payload_bytes = len(b) - _HEADER_BYTES
        if payload_bytes % self.bytes_per_record != 0:
            raise ValueError(
                f"{self._path}: payload size {payload_bytes} is not a "
                f"multiple of bytes_per_record={self.bytes_per_record}; "
                f"the file looks truncated")
        self.num_records = payload_bytes // self.bytes_per_record

        # Subset files (a partial DE440 conversion covering only the
        # chunks the user downloaded) may carry the full-span epochs
        # read from header.440 before the converter knew which chunks
        # it would write. The records are the truth: reconcile so the
        # record-index arithmetic below stays exact. Mirrors the same
        # reconciliation in spody-core's ephemeris_map_file
        # (record prefix: int32 x2, then start/end ET doubles).
        if self.num_records:
            rec0_start = struct.unpack_from("<d", b, _HEADER_BYTES + 8)[0]
            last_off = (_HEADER_BYTES
                        + (self.num_records - 1) * self.bytes_per_record)
            last_end = struct.unpack_from("<d", b, last_off + 16)[0]
            if (self.start_epoch_et != rec0_start
                    or self.end_epoch_et != last_end):
                self.start_epoch_et = rec0_start
                self.end_epoch_et = last_end

    def _build_record_views(self) -> list[np.ndarray]:
        """One float64 numpy view per record over the in-memory bytes,
        sliced past the 24-byte record prefix. Indexed by record_id;
        view[k] is coefficients[k] (the raw coefficient block)."""
        n_coeffs = self.n_coefficients_per_rec
        views: list[np.ndarray] = []
        # `frombuffer` would copy on a bytes object; np.ndarray over a
        # writeable bytearray would let us re-use slices without copy,
        # but `bytes` here is fine because we only read.
        for i in range(self.num_records):
            offset = _HEADER_BYTES + i * self.bytes_per_record + _RECORD_PREFIX_BYTES
            views.append(np.frombuffer(self._buf, dtype="<f8",
                                       count=n_coeffs, offset=offset))
        return views

    # ------------------------------------------------------------------
    # Low-level: position (and optional velocity) of body i
    # (DE440 slot index, 0..14) at ET
    # ------------------------------------------------------------------
    def _posvel_idx(self, idx: int, et: float,
                    want_vel: bool) -> tuple[np.ndarray, np.ndarray | None]:
        """Mirrors `calculate_body_posvel` in spody_ephemeris.c byte for
        byte: same lookup, same Chebyshev evaluations, same cache policy
        (a position-only call invalidates the velocity slot)."""
        if not (0 <= idx < _N_BODY_SLOTS):
            raise ValueError(f"body slot index out of range: {idx}")

        # cache hit -- mirrors `if (... map->cache_jd[idx] == et)` in C;
        # a velocity request also needs the velocity slot to be valid.
        if (self._cache_et[idx] is not None
                and self._cache_et[idx] == et
                and (not want_vel or self._cache_vel_valid[idx])):
            pos = self._cache_pos[idx].copy()
            return pos, (self._cache_vel[idx].copy() if want_vel else None)

        if not (self.start_epoch_et <= et <= self.end_epoch_et):
            raise ValueError(
                f"et={et:.6e} is outside the ephemeris range "
                f"[{self.start_epoch_et:.6e}, {self.end_epoch_et:.6e}]")

        record_id = int((et - self.start_epoch_et) // self.seconds_per_record)
        if record_id >= self.num_records:
            record_id = self.num_records - 1

        rec = self._records[record_id]
        # The first two doubles in `rec` are legacy JD bounds. The C
        # side recomputes start/end from them, but the file also stores
        # ET start/end in the per-record prefix (4..20 bytes). Use the
        # nominal record window instead, derived from header bounds so
        # this code does not rely on the legacy JDs.
        rec_start = self.start_epoch_et + record_id * self.seconds_per_record
        rec_end   = rec_start + self.seconds_per_record

        n_coeffs  = self.n_coeffs_per_component[idx]
        n_sets    = self.n_complete_sets[idx]
        start_ix  = self.location[idx] - 1   # 1-based -> 0-based
        if n_coeffs == 0 or n_sets == 0:
            raise ValueError(
                f"body slot {idx} has no coefficients in this ephemeris "
                f"(n_coeffs={n_coeffs}, n_sets={n_sets})")

        set_duration = (rec_end - rec_start) / n_sets
        set_id = int((et - rec_start) // set_duration)
        if set_id >= n_sets:
            set_id = n_sets - 1   # floating-point edge guard

        t_gran_start = rec_start + set_id * set_duration
        t_gran_end   = t_gran_start + set_duration
        tau = (2.0 * et - t_gran_start - t_gran_end) / (t_gran_end - t_gran_start)

        set_length = 3 * n_coeffs
        offset = start_ix + set_id * set_length
        pos = np.empty(3)
        vel = np.empty(3) if want_vel else None
        # the series runs over tau, so the physical rate is the tau-
        # derivative rescaled by d(tau)/dt = 2 / set_duration -> km/s
        dtau_dt = 2.0 / set_duration
        for i in range(3):
            ci = offset + i * n_coeffs
            pos[i] = _chebyshev_eval(tau, rec[ci:ci + n_coeffs])
            if want_vel:
                vel[i] = _chebyshev_eval_deriv(tau, rec[ci:ci + n_coeffs]) * dtau_dt

        self._cache_et[idx]  = et
        self._cache_pos[idx] = pos.copy()
        if want_vel:
            self._cache_vel[idx] = vel.copy()
            self._cache_vel_valid[idx] = True
        else:
            self._cache_vel_valid[idx] = False
        return pos, vel

    def _position_idx(self, idx: int, et: float) -> np.ndarray:
        return self._posvel_idx(idx, et, False)[0]

    # ------------------------------------------------------------------
    # NAIF-id position relative to the solar system barycentre
    # ------------------------------------------------------------------
    def _position_ssb(self, naif: int, et: float) -> np.ndarray:
        """Body position in ICRF relative to the solar system barycentre,
        in km. Handles the EMB <-> Earth / Moon split via EMRAT and the
        SSB == 0 shortcut, matching `get_body_position_ssb` in
        spody_ephemeris.c."""
        if naif == NAIF_SSB:
            return np.zeros(3)
        if naif == NAIF_EARTH:
            # Earth_ssb = EMB_ssb - 1/(1+EMRAT) * r_moon_wrt_earth.
            # Op order (reciprocal first, then scale) mirrors the C
            # `f * temp[i]` accumulation for bit-identity.
            emb  = self._position_idx(IDX_EMB,             et)
            mge  = self._position_idx(IDX_MOON_GEOCENTRIC, et)
            return emb + mge * (-1.0 / (1.0 + EMRAT))
        if naif == NAIF_MOON:
            # Moon_ssb = EMB_ssb + EMRAT/(1+EMRAT) * r_moon_wrt_earth
            emb  = self._position_idx(IDX_EMB,             et)
            mge  = self._position_idx(IDX_MOON_GEOCENTRIC, et)
            return emb + mge * (EMRAT / (1.0 + EMRAT))
        idx = _NAIF_TO_IDX.get(naif)
        if idx is None:
            raise ValueError(f"unsupported NAIF body id: {naif}")
        return self._position_idx(idx, et)

    def _state_ssb(self, naif: int,
                   et: float) -> tuple[np.ndarray, np.ndarray]:
        """(position, velocity) in ICRF relative to the solar system
        barycentre, km and km/s. Matches `get_body_state_ssb` in
        spody_ephemeris.c (same EMRAT split applied to both halves)."""
        if naif == NAIF_SSB:
            return np.zeros(3), np.zeros(3)
        if naif == NAIF_EARTH:
            emb_p, emb_v = self._posvel_idx(IDX_EMB,             et, True)
            mge_p, mge_v = self._posvel_idx(IDX_MOON_GEOCENTRIC, et, True)
            f = -1.0 / (1.0 + EMRAT)
            return emb_p + mge_p * f, emb_v + mge_v * f
        if naif == NAIF_MOON:
            emb_p, emb_v = self._posvel_idx(IDX_EMB,             et, True)
            mge_p, mge_v = self._posvel_idx(IDX_MOON_GEOCENTRIC, et, True)
            f = EMRAT / (1.0 + EMRAT)
            return emb_p + mge_p * f, emb_v + mge_v * f
        idx = _NAIF_TO_IDX.get(naif)
        if idx is None:
            raise ValueError(f"unsupported NAIF body id: {naif}")
        pos, vel = self._posvel_idx(idx, et, True)
        return pos, vel

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def position(self, naif_central: int, naif_target: int,
                 et: float) -> np.ndarray:
        """Return the position of `naif_target` relative to `naif_central`
        at the given ET, expressed in ICRF km. Same contract as
        `spody_get_ephposition` in spody-core.

        Fast paths: Earth<->Moon resolves in a single Chebyshev evaluation
        (idx 9 = Moon geocentric, optionally negated)."""
        # Fast path for the Earth <-> Moon pair.
        if naif_central == NAIF_EARTH and naif_target == NAIF_MOON:
            return self._position_idx(IDX_MOON_GEOCENTRIC, et)
        if naif_central == NAIF_MOON and naif_target == NAIF_EARTH:
            return -self._position_idx(IDX_MOON_GEOCENTRIC, et)
        target  = self._position_ssb(naif_target,  et)
        central = self._position_ssb(naif_central, et)
        return target - central

    def state(self, naif_central: int, naif_target: int,
              et: float) -> np.ndarray:
        """Return [x, y, z, vx, vy, vz] of `naif_target` relative to
        `naif_central` at the given ET, in ICRF km and km/s. Same
        contract as `spody_get_ephstate` in spody-core: the position
        half is bit-identical to `position()`, the velocity half is the
        analytic derivative of the Chebyshev series (exact, no finite
        differences)."""
        # Fast path for the Earth <-> Moon pair.
        if naif_central == NAIF_EARTH and naif_target == NAIF_MOON:
            pos, vel = self._posvel_idx(IDX_MOON_GEOCENTRIC, et, True)
            return np.concatenate((pos, vel))
        if naif_central == NAIF_MOON and naif_target == NAIF_EARTH:
            pos, vel = self._posvel_idx(IDX_MOON_GEOCENTRIC, et, True)
            return np.concatenate((-pos, -vel))
        tpos, tvel = self._state_ssb(naif_target,  et)
        cpos, cvel = self._state_ssb(naif_central, et)
        return np.concatenate((tpos - cpos, tvel - cvel))

    def velocity(self, naif_central: int, naif_target: int,
                 et: float) -> np.ndarray:
        """Return the velocity of `naif_target` relative to
        `naif_central` at the given ET, in ICRF km/s. Same contract as
        `spody_get_ephvelocity` in spody-core (which is the velocity
        half of `state()`)."""
        return self.state(naif_central, naif_target, et)[3:].copy()

    def lunar_libration_angles(self, et: float) -> np.ndarray:
        """Lunar libration angles (phi, theta, psi) in radians at `et`.
        Feed straight to `spopy.mapping.icrf_to_moon_pa` to get the
        ICRF -> Moon Principal Axes rotation matrix.

        Matches `spody_get_lunarlibrationangles` in the C source
        (which reads slot 12). The returned values are the lunar
        attitude Euler 313 angles from the DE440 lunar mantle model."""
        return self._position_idx(IDX_LUNAR_LIBRATION, et)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def __repr__(self) -> str:
        years = (self.end_epoch_et - self.start_epoch_et) / (86400.0 * 365.25)
        return (f"<Ephemeris {self._path.name} "
                f"{self.num_records} records, ~{years:.0f} y of coverage>")


if __name__ == "__main__":
    # Smoke test: requires the bundled DE440 binary. Sanity-checks the
    # reader on well-known geometric facts (orders of magnitude only;
    # validation against SPICE belongs in a proper test module).
    import sys

    failed = []
    def _check(name: str, cond: bool, extra: str = "") -> None:
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {name}" + (f" -- {extra}" if extra else ""))
        if not cond:
            failed.append(name)

    here = Path(__file__).resolve().parents[2]
    spody_path = here / "external/spody-core/raw_data/DE440/de440.spody"
    print(f"ephemeris.py self-test ({spody_path})")
    eph = Ephemeris(spody_path)
    print(f"  loaded: {eph!r}")

    # 1. J2000 (et = 0): the Sun should sit at ~1 AU from Earth.
    AU_KM = 149597870.7
    r_es = eph.position(NAIF_EARTH, NAIF_SUN, 0.0)
    d_es = float(np.linalg.norm(r_es))
    _check("Sun is ~1 AU from Earth at J2000",
           0.95 * AU_KM < d_es < 1.05 * AU_KM,
           f"|r| = {d_es:.3e} km ({d_es/AU_KM:.4f} AU)")

    # 2. Moon should sit at ~384400 km from Earth.
    r_em = eph.position(NAIF_EARTH, NAIF_MOON, 0.0)
    d_em = float(np.linalg.norm(r_em))
    _check("Moon is ~384 400 km from Earth at J2000",
           350_000 < d_em < 410_000,
           f"|r| = {d_em:.3f} km")

    # 3. Earth from Moon == -(Moon from Earth) exact.
    r_me = eph.position(NAIF_MOON, NAIF_EARTH, 0.0)
    _check("Earth-from-Moon negates Moon-from-Earth (fast path)",
           np.allclose(r_me, -r_em))

    # 4. Lunar libration angles are O(deg-rad); psi is a slow secular
    #    rotation, theta is bounded by lunar obliquity ~6 deg.
    angles = eph.lunar_libration_angles(0.0)
    _check("Lunar libration angles return shape (3,)", angles.shape == (3,))
    theta_deg = abs(np.degrees(angles[1])) % 360
    _check("Lunar libration theta is plausible (|theta| < ~30 deg)",
           theta_deg < 30 or 330 < theta_deg < 360,
           f"theta = {theta_deg:.3f} deg")

    # 5. LRO scenario epoch: 2009-09-18 12:00 UTC ~ et = 3.065e8 s.
    et_lro = 3.065472661824111e+08
    r_em2 = eph.position(NAIF_EARTH, NAIF_MOON, et_lro)
    d_em2 = float(np.linalg.norm(r_em2))
    _check("Earth-Moon distance at LRO epoch ~ 350k..410k km",
           350_000 < d_em2 < 410_000,
           f"|r| = {d_em2:.3f} km")

    # 6. Out-of-range et raises.
    try:
        eph.position(NAIF_EARTH, NAIF_MOON, eph.end_epoch_et + 1e9)
        _check("out-of-range et raises", False, "did not raise")
    except ValueError:
        _check("out-of-range et raises", True)

    # 7. state(): position half bit-identical to position(); velocity
    #    half bit-identical to velocity(); across a mix of pairs.
    ok_pos, ok_vel = True, True
    for c, t in [(NAIF_EARTH, NAIF_MOON), (NAIF_MOON, NAIF_EARTH),
                 (NAIF_EARTH, NAIF_SUN), (NAIF_SSB, NAIF_JUPITER)]:
        s = eph.state(c, t, et_lro)
        if not (s[:3] == eph.position(c, t, et_lro)).all():
            ok_pos = False
        if not (s[3:] == eph.velocity(c, t, et_lro)).all():
            ok_vel = False
    _check("state[:3] bit-identical to position()", ok_pos)
    _check("state[3:] bit-identical to velocity()", ok_vel)

    # 8. Analytic velocity matches a 5-point central finite difference
    #    of position (h = 32 s keeps FD roundoff ~1e-9 km/s).
    h = 32.0
    ok_fd, max_fd = True, 0.0
    for c, t in [(NAIF_EARTH, NAIF_MOON), (NAIF_EARTH, NAIF_SUN),
                 (NAIF_SUN, NAIF_MARS)]:
        v = eph.velocity(c, t, et_lro)
        fd = (eph.position(c, t, et_lro - 2*h)
              - 8.0 * eph.position(c, t, et_lro - h)
              + 8.0 * eph.position(c, t, et_lro + h)
              - eph.position(c, t, et_lro + 2*h)) / (12.0 * h)
        err = float(np.max(np.abs(fd - v)))
        max_fd = max(max_fd, err)
        if err > 1e-6:
            ok_fd = False
    _check("velocity matches finite difference of position",
           ok_fd, f"max |v - FD| = {max_fd:.3e} km/s")

    # 9. Velocity symmetry on the fast path + magnitude sanity.
    v_em = eph.velocity(NAIF_EARTH, NAIF_MOON, 0.0)
    v_me = eph.velocity(NAIF_MOON, NAIF_EARTH, 0.0)
    _check("velocity(399->301) == -velocity(301->399)",
           (v_em == -v_me).all())
    n_em = float(np.linalg.norm(v_em))
    _check("geocentric Moon speed ~1 km/s",
           0.8 < n_em < 1.2, f"|v| = {n_em:.5f} km/s")

    print()
    if failed:
        print(f"FAILED: {len(failed)} check(s): {failed}")
        sys.exit(1)
    print("OK -- all checks passed")
