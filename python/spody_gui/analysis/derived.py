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

"""Per-file derived event data, computed once and shared by every
consumer (Info tab, event plots, CSV exports).

An events binary from a debris batch routinely carries millions of
records; the Info rows and half a dozen plots all want the same
handful of derivations from it (per-kind splits, the crossed-altitude
clusters, the eclipse pairing, the body-fixed impact projection).
Recomputing those per tab switch and per plot click is what made the
Analysis tab scale badly, so they live here behind a content-keyed
cache: the FIRST touch of a file pays, every later one is a dict hit.

Three things live in this module:

- `cache_key` / `cached` -- the generic content-keyed memo (also used
  by `altitude_bands.py` for the band reconstruction). Keyed on the
  array's buffer identity + the analysis parameters, so distinct files
  land on distinct keys and a re-analysis of the same file with the
  same parameters reuses the result.
- `events_digest` -- the per-file `EventsDigest` (see its docstring):
  everything the Info rows and the timeline views need, in flat numpy.
- `impact_latlon` -- the ICRF -> body-fixed projection of the IMPACT
  rows, shared by the four impact views and the impact CSV export.

Plus two axis/rendering helpers shared by the plot modules:
`decimate_for_display` (no event plot may hand matplotlib more artists
than the canvas has pixels) and `time_axis` (no view prints a raw
six-digit second count).

INVARIANT: everything here is a pure function of its inputs. Nothing
reads QSettings, the widget tree, or the filesystem beyond the
ephemeris the caller already resolved -- that is what makes the
caching sound.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from spody_io import (
    EVENT_KIND_ALT_CROSSING,
    EVENT_KIND_ECLIPSE,
    EVENT_KIND_IMPACT,
)


# ----------------------------------------------------------------------
# Content-keyed cache
# ----------------------------------------------------------------------
# Keyed by the array's buffer address + byte size + first/last timestamps
# + the analysis params. `None` results are cached too (a file with no
# crossings must not re-scan on every click just because the answer was
# "nothing here"). FIFO eviction: entries hold arrays proportional to the
# loaded file, so the cap is a memory bound, not a hit-rate knob.
_MISS = object()
_CACHE: "dict[tuple, object]" = {}
_CACHE_ORDER: "list[tuple]" = []
_CACHE_MAX = 6


def cache_key(tag: str, events: np.ndarray, *params):
    """Content key for `events` under `tag`, or None when the array is
    empty (nothing worth caching, and `ctypes.data` is not a stable
    identity for a zero-length array)."""
    if len(events) == 0:
        return None
    return (tag, events.ctypes.data, int(events.nbytes),
            float(events["t"][0]), float(events["t"][-1]), params)


def cached(key, compute):
    """Return `compute()`, memoized under `key`. A None key bypasses
    the cache entirely."""
    if key is None:
        return compute()
    hit = _CACHE.get(key, _MISS)
    if hit is not _MISS:
        return hit
    val = compute()
    _CACHE[key] = val
    _CACHE_ORDER.append(key)
    if len(_CACHE_ORDER) > _CACHE_MAX:
        _CACHE.pop(_CACHE_ORDER.pop(0), None)
    return val


# ----------------------------------------------------------------------
# Altitude clustering
# ----------------------------------------------------------------------

def cluster_altitudes(h_obs: np.ndarray) -> np.ndarray:
    """Group observed crossing altitudes into clusters separated by
    gaps > max(2 km, 0.5 %) and return the sorted cluster means. Used
    as the threshold fallback when no snapshot lists the configured
    altitudes, and by the event-timeline plots to give each crossed
    altitude its own labelled row. Exact for refined triggers
    (sub-microsecond localisation); best-effort for `refined = false`
    ones.

    The cluster boundaries come out of one vectorised `diff` -- the
    only Python-level loop is over the (handful of) clusters, so a
    ten-million-crossing file costs one sort, not ten million
    interpreter steps."""
    h_sorted = np.sort(np.asarray(h_obs, dtype=float))
    if h_sorted.size == 0:
        return np.asarray([])
    gap = np.diff(h_sorted) > np.maximum(2.0, 0.005 * h_sorted[:-1])
    starts = np.concatenate(([0], np.flatnonzero(gap) + 1))
    ends = np.concatenate((starts[1:], [h_sorted.size]))
    return np.asarray([float(h_sorted[s:e].mean())
                       for s, e in zip(starts, ends)])


def stable_group_order(ids: np.ndarray) -> np.ndarray:
    """Stable argsort of an array of small non-negative integer ids
    (case indices, band indices, NAIF ids).

    Why this exists instead of a plain `np.argsort(ids, kind="stable")`:
    numpy only reaches for radix sort on 16-bit-and-narrower integers.
    On anything wider `kind="stable"` falls back to timsort, and on a
    ten-million-crossing batch that is the single most expensive
    operation in the whole Analysis tab -- 5.2 s against 0.17 s for the
    identical permutation. Case ids are dense and small, so we narrow
    them first and, past 65536 objects, do the classic two-pass LSD
    radix (low half then high half, each pass stable) rather than give
    up the fast path."""
    if ids.size == 0:
        return np.zeros(0, dtype=np.intp)
    top = int(ids.max())
    if top < (1 << 16):
        return np.argsort(ids.astype(np.uint16), kind="stable")
    if top < (1 << 32):
        low = np.argsort((ids & 0xFFFF).astype(np.uint16), kind="stable")
        high = (np.asarray(ids, dtype=np.int64) >> 16).astype(np.uint16)
        return low[np.argsort(high[low], kind="stable")]
    return np.argsort(ids, kind="stable")


def nearest_index(values: np.ndarray, centers: np.ndarray) -> np.ndarray:
    """Index of the nearest entry of ascending `centers` for every entry
    of `values`, ties going to the lower index.

    Equivalent to `np.abs(values[:, None] - centers[None, :]).argmin(1)`
    but without its (N x K) temporary -- at ten million crossings that
    matrix alone is hundreds of megabytes, which is why the pairwise
    form is banned here. `searchsorted` on the midpoints is O(N log K)
    with no allocation beyond the result."""
    centers = np.asarray(centers, dtype=float)
    if centers.size <= 1:
        return np.zeros(len(values), dtype=np.intp)
    mid = 0.5 * (centers[1:] + centers[:-1])
    return np.searchsorted(mid, values, side="left")


# ----------------------------------------------------------------------
# Per-file digest
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class AltRow:
    """One crossed-altitude series: every trigger of one clustered
    altitude on one body, split by crossing direction. The event
    timeline draws one plot row per entry; the density view histograms
    `t_up` + `t_down` together."""
    naif_id: int
    center_km: float
    t_up: np.ndarray
    t_down: np.ndarray


@dataclass(frozen=True)
class EventsDigest:
    """Everything the Info rows and the timeline views read out of an
    events array, derived once. Flat numpy throughout: the per-record
    work stays in C and the only Python loops downstream are over the
    handful of series / bodies.

    `t_impact` / `case_impact` are the compacted IMPACT columns (a
    debris batch has thousands of impacts against millions of records,
    so carrying them separately keeps every impact statistic cheap).
    `eclipse_durations_s` is the paired entry/exit reconstruction
    described in `info_rows_events`. `alt_body_span` is one
    `(naif_id, h_min, h_max, count)` per body carrying crossings, for
    the third-body summary rows."""
    n_records: int
    is_batch: bool
    n_impact: int
    n_eclipse: int
    n_altcross: int
    t_impact: np.ndarray
    case_impact: np.ndarray
    eclipse_durations_s: np.ndarray
    n_cases_with_events: int
    n_cases_impacted: int
    alt_rows: tuple[AltRow, ...]
    alt_body_span: tuple[tuple[int, float, float, int], ...]


def events_digest(events: np.ndarray) -> EventsDigest:
    """Per-file `EventsDigest`, cached. Safe to call from anywhere on
    the same array -- the first caller pays, the rest hit the memo."""
    return cached(cache_key("digest", events), lambda: _digest_impl(events))


def _digest_impl(events: np.ndarray) -> EventsDigest:
    names = events.dtype.names or ()
    is_batch = "case_idx" in names
    kind = events["kind"]
    t_all = events["t"]

    m_imp = kind == EVENT_KIND_IMPACT
    m_ecl = kind == EVENT_KIND_ECLIPSE
    m_alt = kind == EVENT_KIND_ALT_CROSSING
    t_impact = np.asarray(t_all[m_imp], dtype=float)

    # Case bookkeeping via bincount: O(N) with no sort, where the
    # obvious `np.unique` would sort ten million ids on every load.
    case_all = events["case_idx"] if is_batch else None
    case_impact = (np.asarray(case_all[m_imp], dtype=np.int64) if is_batch
                   else np.zeros(0, dtype=np.int64))
    if is_batch and len(events):
        n_cases_with_events = int(np.count_nonzero(
            np.bincount(np.asarray(case_all, dtype=np.int64))))
        n_cases_impacted = int(np.count_nonzero(
            np.bincount(case_impact)) if case_impact.size else 0)
    else:
        n_cases_with_events = n_cases_impacted = 0

    # --- eclipse pairing ------------------------------------------------
    # The engine emits one ECLIPSE record per sign crossing of
    # (fraction - threshold), so successive triggers for the same
    # {case, occulter} alternate entry / exit and a pair is one full
    # eclipse. Grouping is a lexsort + a within-group rank: pairs are
    # the even-ranked records, each closed by its successor, and an odd
    # tail (a run that started or ended inside shadow) drops out.
    ecl_t = np.asarray(t_all[m_ecl], dtype=float)
    if ecl_t.size:
        ecl_naif = np.asarray(events["naif_id"][m_ecl], dtype=np.int64)
        ecl_case = (np.asarray(case_all[m_ecl], dtype=np.int64) if is_batch
                    else np.zeros(ecl_t.size, dtype=np.int64))
        order = np.lexsort((ecl_t, ecl_naif, ecl_case))
        st, sn, sc = ecl_t[order], ecl_naif[order], ecl_case[order]
        is_first = np.empty(st.size, bool)
        is_first[0] = True
        is_first[1:] = (sc[1:] != sc[:-1]) | (sn[1:] != sn[:-1])
        # Rank within group: position minus the group's start position.
        pos = np.arange(st.size)
        grp_start = np.maximum.accumulate(np.where(is_first, pos, 0))
        rank = pos - grp_start
        # Even rank opens a pair; it closes only if the next record is
        # still the same group (i.e. is not a group start).
        opens = (rank % 2 == 0)
        opens[-1] = False
        opens[:-1] &= ~is_first[1:]
        eclipse_durations_s = st[np.flatnonzero(opens) + 1] - st[opens]
    else:
        eclipse_durations_s = np.zeros(0)

    # --- crossed-altitude series ---------------------------------------
    alt_rows: list[AltRow] = []
    spans: list[tuple[int, float, float, int]] = []
    if m_alt.any():
        alt_naif = np.asarray(events["naif_id"][m_alt], dtype=np.int64)
        alt_t = np.asarray(t_all[m_alt], dtype=float)
        alt_h = (np.asarray(events["distance_km"][m_alt], dtype=float)
                 - np.asarray(events["radius_km"][m_alt], dtype=float))
        # Direction from the radial velocity r.v at the trigger state
        # (documented contract in spody_events.h). Computed on the
        # strided (N, 6) view and masked afterwards, which costs one
        # float column instead of copying the whole record block.
        y = events["y"]
        rdot_all = np.einsum("ij,ij->i", y[:, 0:3], y[:, 3:6])
        alt_up = np.asarray(rdot_all[m_alt], dtype=float) > 0.0
        for naif in np.unique(alt_naif):
            bm = alt_naif == naif
            h_b, t_b, up_b = alt_h[bm], alt_t[bm], alt_up[bm]
            spans.append((int(naif), float(h_b.min()), float(h_b.max()),
                          int(h_b.size)))
            centers = cluster_altitudes(h_b)
            k_idx = nearest_index(h_b, centers)
            for c, h_c in enumerate(centers):
                cm = k_idx == c
                alt_rows.append(AltRow(
                    naif_id=int(naif), center_km=float(h_c),
                    t_up=t_b[cm & up_b], t_down=t_b[cm & ~up_b]))

    return EventsDigest(
        n_records=len(events), is_batch=is_batch,
        n_impact=int(m_imp.sum()), n_eclipse=int(m_ecl.sum()),
        n_altcross=int(m_alt.sum()),
        t_impact=t_impact, case_impact=case_impact,
        eclipse_durations_s=eclipse_durations_s,
        n_cases_with_events=n_cases_with_events,
        n_cases_impacted=n_cases_impacted,
        alt_rows=tuple(alt_rows), alt_body_span=tuple(spans))


# ----------------------------------------------------------------------
# Impact projection
# ----------------------------------------------------------------------

def impact_latlon(events: np.ndarray, info: dict, central_body
                  ) -> "tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None":
    """Project the ICRF IMPACT rows onto the central body's body-fixed
    frame; returns `(lat_deg, lon_deg, t_days, case_idx)` or None when
    the ephemeris cannot be opened.

    For every impact row:

        et    = sim.et_start_s + row.t
        R     = central_body.bf_orientation(et, eph)     (ICRF -> BF)
        r_bf  = R @ row.y[0:3]
        lat   = asin(z/|r|),  lon = atan2(y, x)

    The body's orientation evolves (lunar libration on a ~day scale,
    Earth GMST on a ~rev/day one) so each impact needs its own rotation
    -- there is no single R to precompute. The whole projection is
    therefore cached per (file, body, epoch, ephemeris): the four
    impact views and the CSV export share ONE pass instead of five.

    Body-agnostic via the CentralBodySpec orientation callback -- Moon
    (DE440 librations) and Earth (IAU 2006) today."""
    key = cache_key("latlon", events, int(central_body.naif_id),
                    float(info["et_start_s"]), str(info["ephemeris_path"]))
    return cached(key, lambda: _impact_latlon_impl(events, info, central_body))


def _impact_latlon_impl(events, info, central_body):
    from spopy import Ephemeris
    try:
        eph = Ephemeris(str(info["ephemeris_path"]))
    except (OSError, ValueError):
        return None
    mask = events["kind"] == EVENT_KIND_IMPACT
    n = int(mask.sum())
    bf_orientation = central_body.bf_orientation
    et_start = info["et_start_s"]
    t_sim = np.asarray(events["t"][mask], dtype=float)
    r_icrf = np.asarray(events["y"][mask][:, 0:3], dtype=float)
    case_id = (np.asarray(events["case_idx"][mask], dtype=int)
               if "case_idx" in (events.dtype.names or ())
               else np.zeros(n, dtype=int))   # per-run file: single object
    lat_deg = np.empty(n)
    lon_deg = np.empty(n)
    for i in range(n):
        r_bf = bf_orientation(et_start + float(t_sim[i]), eph) @ r_icrf[i]
        norm = np.linalg.norm(r_bf)
        lat_deg[i] = np.degrees(np.arcsin(r_bf[2] / norm))
        lon_deg[i] = np.degrees(np.arctan2(r_bf[1], r_bf[0]))
    return lat_deg, lon_deg, t_sim / 86400.0, case_id


# ----------------------------------------------------------------------
# Axis presentation
# ----------------------------------------------------------------------


def time_axis(span_s: float) -> tuple[float, str]:
    """Pick a readable time unit (divisor, label) for a plot axis from
    the plotted span: seconds for a sub-minute orbit up to days for a
    multi-day run. Keeps every view from printing a raw six-digit
    second count on a days-long run.

    Every binary SpOdy writes stores t in seconds (consistent with the
    integrator and the C structs); the unit is a display choice only,
    made per view from what it is about to draw."""
    if span_s >= 2 * 86400.0:
        return 86400.0, "days"
    if span_s >= 2 * 3600.0:
        return 3600.0, "h"
    if span_s >= 2 * 60.0:
        return 60.0, "min"
    return 1.0, "s"


# ----------------------------------------------------------------------
# Rendering budget
# ----------------------------------------------------------------------
# A plot canvas is ~1200 pixels wide. Handing matplotlib one marker or
# one polygon vertex per event past that point buys nothing visible and
# costs seconds per redraw -- and it costs them AGAIN on every zoom and
# pan, which no amount of caching can fix. Every event view that can
# receive millions of records therefore runs its x data through the
# helpers below first.

# Columns kept when decimating a marker series: ~3x a full-width canvas,
# so a decimated row is indistinguishable from the full one on screen
# and stays sharp through a few zoom steps.
DISPLAY_COLUMNS = 4000


def decimate_for_display(t: np.ndarray, t_lo: float, t_hi: float,
                         columns: int = DISPLAY_COLUMNS
                         ) -> "tuple[np.ndarray, bool]":
    """Thin `t` down to at most one marker per display column over
    `[t_lo, t_hi]`; returns `(times, was_decimated)`.

    Below `columns` samples the input is returned untouched, so small
    and mid-sized runs keep every exact timestamp and render exactly as
    they did before. Above it, occupied columns are reduced to their
    centre time -- a sub-pixel shift at canvas resolution. The scatter
    is O(N) with a single index array: no sort, no per-record Python.
    Callers must say so in the plot title (`was_decimated`)."""
    if t.size <= columns:
        return t, False
    span = float(t_hi - t_lo)
    if not np.isfinite(span) or span <= 0.0:
        return t[:1], True
    col = ((t - t_lo) * (columns / span)).astype(np.int64)
    np.clip(col, 0, columns - 1, out=col)
    occupied = np.zeros(columns, dtype=bool)
    occupied[col] = True
    kept = np.flatnonzero(occupied)
    return t_lo + (kept + 0.5) * (span / columns), True
