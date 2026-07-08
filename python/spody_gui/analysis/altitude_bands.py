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

"""Altitude-band occupancy reconstruction from ALT_CROSSING events.

The user's [[events.altitude_crossing]] thresholds, sorted ascending
(0 < h_a < h_b < ...), split the altitude axis into m+1 bands:

    band 0 = [surface, h_a)   band 1 = [h_a, h_b)   ...   band m = [h_top, inf)

Every trigger record is a transition between two ADJACENT bands
(continuity: to move two bands the object must cross the threshold in
between, which is also registered and also fires). That makes the
timeline reconstruction exact without needing the trajectory file:

  - the crossed threshold k is recovered from the record itself
    (`distance_km - radius_km`, snapped to the nearest configured
    threshold so `refined = false` triggers land on the right band
    boundary despite their step-sized localisation error);
  - the direction comes from the sign of the radial velocity r.v at
    the trigger state `y` (documented contract in spody_events.h) --
    up-crossing of threshold k lands in band k+1, down-crossing lands
    in band k;
  - the FIRST event pins the initial band: an up-crossing of k means
    the object started in band k, a down-crossing in band k+1.

The analysis window per object closes at the earliest of: the planned
duration (run snapshot), the object's IMPACT trigger, or its first
crossing of a threshold configured with a stop-class action. A run
stopped by `action = "stop"` (no log) leaves no trace in the events
file, so its tail segment is attributed to the planned duration --
prefer `log_and_stop` when the occupancy statistics matter.

Only crossings measured from the CENTRAL body are analysed: their `y`
state is body-centric, so the radial-velocity direction test is exact.
Crossings on third bodies (or CR3BP primaries) are counted by the
caller but get no occupancy reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from spody_io import EVENT_KIND_ALT_CROSSING, EVENT_KIND_IMPACT


@dataclass(frozen=True)
class BandStats:
    """Occupancy statistics for one altitude band, pooled over every
    analysed object (a single run contributes one object; a batch one
    per case with at least one crossing)."""
    lo_km: float
    hi_km: float                 # inf for the top band
    entries: int                 # crossings INTO the band
    starts_inside: int           # objects whose t=0 segment is this band
    total_time_s: float          # object-time spent in the band
    dwell_min_s: float           # per-visit duration stats (NaN if no visit)
    dwell_mean_s: float
    dwell_max_s: float
    visits: int                  # segments spent in the band (>= entries)
    objects_visiting: int        # objects with at least one segment here
    pop_min: int                 # population level stats over the window
    pop_max: int                 # (population == objects simultaneously
    pop_mean: float              #  in band; 0/1 for a single run)


@dataclass(frozen=True)
class BandAnalysis:
    thresholds_km: tuple[float, ...]   # sorted ascending
    from_snapshot: bool                # False = clustered from records
    bands: tuple[BandStats, ...]       # len == len(thresholds_km) + 1
    n_objects: int                     # objects with >= 1 crossing
    window_s: float                    # max per-object end time
    ended_by_impact: int               # objects whose window closed early
    ended_by_stop: int                 #   (impact trigger / stop-class alt)


def cluster_altitudes(h_obs: np.ndarray) -> np.ndarray:
    """Group observed crossing altitudes into clusters separated by
    gaps > max(2 km, 0.5 %) and return the sorted cluster means. Used
    as the threshold fallback when no snapshot lists the configured
    altitudes, and by the event-timeline plot to give each crossed
    altitude its own labelled row. Exact for refined triggers
    (sub-microsecond localisation); best-effort for `refined = false`
    ones."""
    h_sorted = np.sort(h_obs.astype(float))
    centers: list[float] = []
    start = 0
    for i in range(1, len(h_sorted) + 1):
        if i == len(h_sorted) or (
                h_sorted[i] - h_sorted[i - 1]
                > max(2.0, 0.005 * h_sorted[i - 1])):
            centers.append(float(h_sorted[start:i].mean()))
            start = i
    return np.asarray(centers)


# --- reconstruction cache --------------------------------------------
# The band reconstruction is the one O(N) step, and the Info tab re-runs
# it on every switch to the Info tab while the four plots each call it
# too. A small content-keyed cache makes all of them share one
# reconstruction (and one pooled analysis) per loaded file, so only the
# first touch pays and later tab switches / plot clicks are instant.
# Keyed by the array's buffer address + byte size + first/last
# timestamps + the analysis params: distinct files land on distinct
# keys; the same file re-analysed with the same params reuses the
# result. `None` results (no crossings) are cached too.
_MISS = object()
_BAND_CACHE: "dict[tuple, object]" = {}
_BAND_CACHE_ORDER: "list[tuple]" = []
_BAND_CACHE_MAX = 6


def _cache_key(tag: str, events: np.ndarray, central_naif: int,
               thresholds_km, stop_thresholds_km, duration_s):
    if len(events) == 0:
        return None
    return (tag, events.ctypes.data, int(events.nbytes),
            float(events["t"][0]), float(events["t"][-1]),
            int(central_naif), tuple(thresholds_km or ()),
            tuple(stop_thresholds_km or ()), duration_s)


def _cached(key, compute):
    if key is None:
        return compute()
    hit = _BAND_CACHE.get(key, _MISS)
    if hit is not _MISS:
        return hit
    val = compute()
    _BAND_CACHE[key] = val
    _BAND_CACHE_ORDER.append(key)
    if len(_BAND_CACHE_ORDER) > _BAND_CACHE_MAX:
        _BAND_CACHE.pop(_BAND_CACHE_ORDER.pop(0), None)
    return val


@dataclass(frozen=True)
class _Recon:
    """Vectorised reconstruction product shared by the pooled analysis,
    the per-object CSV and the segment plots. Everything is flat numpy
    arrays so the per-record work stays in C: NO Python loop over the
    (potentially millions of) crossing records. The only Python-level
    loops downstream are over the handful of BANDS.

    Segments are (band, start, end) intervals an object spent in a band,
    already filtered to dur > 0, tagged with their group (object) index.
    Entries are per-event band-crossings INTO a band (exits never
    counted). Per-group arrays are ordered by ascending object id."""
    thr: np.ndarray
    from_snapshot: bool
    n_bands: int
    n_objects: int
    window_s: float
    ended_by_impact: int
    ended_by_stop: int
    group_obj: np.ndarray        # (G,)  object id per group (ascending)
    init_band: np.ndarray        # (G,)  band each object starts in
    seg_band: np.ndarray         # (S,)  band of each segment
    seg_start: np.ndarray        # (S,)
    seg_end: np.ndarray          # (S,)
    seg_group: np.ndarray        # (S,)  group index of each segment
    ent_band: np.ndarray         # (E,)  band entered (one per crossing-in)
    ent_group: np.ndarray        # (E,)  group index of the entering event


def _reconstruct(events: np.ndarray, central_naif: int,
                 thresholds_km: "list[float] | None",
                 stop_thresholds_km: "list[float] | None",
                 duration_s: "float | None") -> "_Recon | None":
    """Fully vectorised band-timeline reconstruction. Same maths as the
    old per-record Python loop (bit-identical), expressed as array ops:
    one `lexsort` groups every crossing by (object, time), the band each
    segment belongs to is `band_after` of the previous event within the
    object, and the per-object window truncation (impact / stop /
    duration, clamped to the last crossing) is a per-group reduce.
    Returns None when there is no usable central-body crossing."""
    alt = events[(events["kind"] == EVENT_KIND_ALT_CROSSING)
                 & (events["naif_id"] == central_naif)]
    if len(alt) == 0:
        return None

    h_obs = alt["distance_km"].astype(float) - alt["radius_km"].astype(float)
    if thresholds_km:
        thr = np.unique(np.asarray(sorted(thresholds_km), dtype=float))
        from_snapshot = True
    else:
        thr = cluster_altitudes(h_obs)
        from_snapshot = False
    n_bands = len(thr) + 1

    # Nearest-threshold index + direction (r.v sign), all vectorised.
    k_idx = np.abs(h_obs[:, None] - thr[None, :]).argmin(axis=1)
    r = alt["y"][:, 0:3].astype(float)
    v = alt["y"][:, 3:6].astype(float)
    rdot = np.einsum("ij,ij->i", r, v)
    valid = rdot != 0.0          # drop tangent grazings (direction undefined)
    if not valid.any():
        return None
    t = alt["t"].astype(float)[valid]
    k_idx = k_idx[valid]
    up = (rdot[valid] > 0.0).astype(np.int64)
    is_batch = "case_idx" in (events.dtype.names or ())
    obj = (alt["case_idx"].astype(np.int64)[valid] if is_batch
           else np.zeros(int(valid.sum()), dtype=np.int64))

    # Stop-class thresholds -> band-boundary indices.
    stop_bands: set[int] = set()
    for h_stop in (stop_thresholds_km or []):
        stop_bands.add(int(np.abs(thr - float(h_stop)).argmin()))

    # Per-object earliest impact time (few impacts -> cheap dict).
    impact_dict: dict[int, float] = {}
    for rec in events[events["kind"] == EVENT_KIND_IMPACT]:
        key = int(rec["case_idx"]) if is_batch else 0
        ti = float(rec["t"])
        if key not in impact_dict or ti < impact_dict[key]:
            impact_dict[key] = ti

    # --- sort by (object, time); one C-level sort over all records ----
    order = np.lexsort((t, obj))
    so, st, sk, sup = obj[order], t[order], k_idx[order], up[order]
    N = len(so)
    is_first = np.empty(N, bool)
    is_first[0] = True
    is_first[1:] = so[1:] != so[:-1]
    is_last = np.empty(N, bool)
    is_last[-1] = True
    is_last[:-1] = so[:-1] != so[1:]
    grp = np.cumsum(is_first) - 1               # group index 0..G-1
    grp_starts = np.flatnonzero(is_first)
    G = len(grp_starts)
    group_obj = so[grp_starts]                  # ascending object id

    # Band each crossing lands in (up -> k+1, down -> k). The band a
    # segment sits in is band_after of the PREVIOUS event; the first
    # event of each object is pinned to its start band instead.
    band_after = sk + sup
    band_during = np.empty(N, np.int64)
    band_during[1:] = band_after[:-1]
    start_band_all = sk + (1 - sup)             # up -> k, down -> k+1
    band_during[is_first] = start_band_all[is_first]
    seg_start = np.empty(N)
    seg_start[1:] = st[:-1]
    seg_start[is_first] = 0.0

    # --- per-object window end: min(duration, impact, stop) but never
    #     before the object's last crossing ----------------------------
    t_last = st[is_last]                         # max time per group
    if stop_bands:
        is_stop = np.isin(sk, list(stop_bands))
        stop_pg = np.minimum.reduceat(np.where(is_stop, st, np.inf),
                                      grp_starts)
    else:
        stop_pg = np.full(G, np.inf)
    impact_pg = np.full(G, np.inf)
    for key, ti in impact_dict.items():
        gi = int(np.searchsorted(group_obj, key))
        if gi < G and group_obj[gi] == key:
            impact_pg[gi] = ti
    dur = duration_s if (duration_s is not None and duration_s > 0.0) else np.inf
    val = np.full(G, float(dur))
    cause = np.zeros(G, np.int8)                 # 0 = duration
    m_imp = impact_pg < val
    val = np.where(m_imp, impact_pg, val)
    cause = np.where(m_imp, 1, cause)
    m_stop = stop_pg < val
    val = np.where(m_stop, stop_pg, val)
    cause = np.where(m_stop, 2, cause)
    t_end_pg = np.maximum(val, t_last)
    ended_by_impact = int((cause == 1).sum())
    ended_by_stop = int((cause == 2).sum())
    window_s = float(t_end_pg.max())

    # --- segments (dur > 0): regular [seg_start, t] in band_during,
    #     plus one final [t_last, t_end] per object in its last band ----
    reg_dur = st - seg_start
    reg_m = reg_dur > 0.0
    fin_band = band_after[is_last]               # per group (ascending)
    fin_m = (t_end_pg - t_last) > 0.0
    seg_band = np.concatenate([band_during[reg_m], fin_band[fin_m]])
    seg_start_all = np.concatenate([seg_start[reg_m], t_last[fin_m]])
    seg_end_all = np.concatenate([st[reg_m], t_end_pg[fin_m]])
    seg_group = np.concatenate([grp[reg_m], np.arange(G)[fin_m]])

    # Entries: a crossing that actually changes band (band change is
    # essentially always true; the mask guards the rare snapped tie).
    entry_m = band_after != band_during
    return _Recon(
        thr=thr, from_snapshot=from_snapshot, n_bands=n_bands, n_objects=G,
        window_s=window_s, ended_by_impact=ended_by_impact,
        ended_by_stop=ended_by_stop, group_obj=group_obj,
        init_band=start_band_all[is_first],
        seg_band=seg_band, seg_start=seg_start_all, seg_end=seg_end_all,
        seg_group=seg_group, ent_band=band_after[entry_m],
        ent_group=grp[entry_m])


def _recon_cached(events, central_naif, thresholds_km,
                  stop_thresholds_km, duration_s) -> "_Recon | None":
    """Content-keyed memo around `_reconstruct` so the Info tab, the
    plots and the CSV exports share one reconstruction per file."""
    key = _cache_key("recon", events, central_naif,
                     thresholds_km, stop_thresholds_km, duration_s)
    return _cached(key, lambda: _reconstruct(
        events, central_naif, thresholds_km, stop_thresholds_km, duration_s))


def _band_pop(starts: np.ndarray, ends: np.ndarray,
              window_s: float) -> tuple[int, int, float]:
    """Vectorised population sweep for ONE band: min / max / time-mean of
    the number of objects simultaneously in the band over [0, window_s].
    -1 (exit) sorts before +1 (entry) at equal times so an instantaneous
    hand-over between objects doesn't spike the max; only levels held for
    dt > 0 count (so tied-time transients drop out with zero weight)."""
    n = starts.size
    if n == 0:
        return 0, 0, 0.0
    times = np.concatenate([starts, ends])
    deltas = np.concatenate([np.ones(n, np.int64), -np.ones(n, np.int64)])
    order = np.lexsort((deltas, times))          # time primary, -1 before +1
    st_s = times[order]
    p_after = np.cumsum(deltas[order])           # population after each event
    bounds = np.concatenate([[0.0], st_s, [float(window_s)]])
    dts = np.diff(bounds)
    levels = np.concatenate([[0], p_after])      # level held on each gap
    held = dts > 0.0
    if not held.any():
        return 0, 0, 0.0
    lv = levels[held]
    integral = float((lv * dts[held]).sum())
    return int(lv.min()), int(lv.max()), (integral / window_s
                                          if window_s > 0.0 else 0.0)


def analyze_altitude_bands(events: np.ndarray,
                            central_naif: int,
                            thresholds_km: "list[float] | None" = None,
                            stop_thresholds_km: "list[float] | None" = None,
                            duration_s: "float | None" = None,
                            ) -> BandAnalysis | None:
    """Reconstruct pooled per-band occupancy from an events array
    (either the per-run or the batch dtype). Returns None when the file
    has no usable central-body ALT_CROSSING record. Vectorised via
    `_reconstruct`; the only loop here is over the (few) bands. Result
    is cached per file (the Info tab re-calls this on every tab switch)."""
    key = _cache_key("analyze", events, central_naif,
                     thresholds_km, stop_thresholds_km, duration_s)
    return _cached(key, lambda: _analyze_impl(
        events, central_naif, thresholds_km, stop_thresholds_km, duration_s))


def _analyze_impl(events, central_naif, thresholds_km,
                  stop_thresholds_km, duration_s) -> BandAnalysis | None:
    rec = _recon_cached(events, central_naif,
                        thresholds_km, stop_thresholds_km, duration_s)
    if rec is None:
        return None
    thr, n_bands, m = rec.thr, rec.n_bands, len(rec.thr)
    seg_dur = rec.seg_end - rec.seg_start
    entries_pb = np.bincount(rec.ent_band, minlength=n_bands)
    starts_pb = np.bincount(rec.init_band, minlength=n_bands)

    bands: list[BandStats] = []
    for b in range(n_bands):
        sm = rec.seg_band == b
        dur_b = seg_dur[sm]
        pmin, pmax, pmean = _band_pop(rec.seg_start[sm], rec.seg_end[sm],
                                      rec.window_s)
        bands.append(BandStats(
            lo_km=0.0 if b == 0 else float(thr[b - 1]),
            hi_km=float(thr[b]) if b < m else float("inf"),
            entries=int(entries_pb[b]),
            starts_inside=int(starts_pb[b]),
            total_time_s=float(dur_b.sum()) if dur_b.size else 0.0,
            dwell_min_s=float(dur_b.min()) if dur_b.size else float("nan"),
            dwell_mean_s=float(dur_b.mean()) if dur_b.size else float("nan"),
            dwell_max_s=float(dur_b.max()) if dur_b.size else float("nan"),
            visits=int(sm.sum()),
            objects_visiting=int(np.unique(rec.seg_group[sm]).size),
            pop_min=pmin,
            pop_max=pmax,
            pop_mean=pmean,
        ))

    return BandAnalysis(
        thresholds_km=tuple(float(x) for x in thr),
        from_snapshot=rec.from_snapshot,
        bands=tuple(bands),
        n_objects=rec.n_objects,
        window_s=rec.window_s,
        ended_by_impact=rec.ended_by_impact,
        ended_by_stop=rec.ended_by_stop,
    )


def band_inputs_from_snapshot(snapshot: "dict | None", body_name: str
                              ) -> tuple[list[float], list[float],
                                         "float | None"]:
    """Pull the configured thresholds for `body_name` out of a run
    snapshot: returns `(thresholds_km, stop_thresholds_km, duration_s)`.
    Empty lists + None when the snapshot is missing or lists no
    altitude crossing for the body (the analysis then falls back to
    clustering the thresholds out of the records). Shared by the Info
    tab and the CSV export so the two never disagree on which
    thresholds / stop actions / window apply."""
    thresholds: list[float] = []
    stop_thresholds: list[float] = []
    duration: float | None = None
    if snapshot is not None:
        for entry in snapshot.get("altitude_crossings", []):
            if entry["body"].lower() != body_name.lower():
                continue
            thresholds.append(entry["altitude_km"])
            if entry["action"] in ("stop", "log_and_stop"):
                stop_thresholds.append(entry["altitude_km"])
        if snapshot.get("duration_s", 0.0) > 0.0:
            duration = float(snapshot["duration_s"])
    return thresholds, stop_thresholds, duration


def _csv_num(x: float, decimals: int = 6) -> str:
    """CSV cell for a float: `%g` with the requested significant
    digits, empty string for NaN (a band never visited has no dwell
    stats) so the column parses cleanly downstream. `inf` is written
    verbatim (numpy / pandas read it back as np.inf for the open top
    band)."""
    if x != x:                       # NaN
        return ""
    if x == float("inf"):
        return "inf"
    return f"{x:.{decimals}g}"


def altitude_bands_per_object(events: np.ndarray, central_naif: int,
                              thresholds_km: "list[float] | None" = None,
                              stop_thresholds_km: "list[float] | None" = None,
                              duration_s: "float | None" = None,
                              ) -> "tuple[np.ndarray, bool, list] | None":
    """Per-object band occupancy for the CSV export.

    Returns `(thresholds_km, from_snapshot, rows)` where `rows` is one
    entry per analysed object, SORTED by object id (case index in
    batch; a lone 0 for a per-run file). Each row is
    `(obj_id, per_band)` with `per_band[b] = (total_time_s, entries)`:
    the time the object spent in band b and how many times it crossed
    INTO band b (entries only -- exits are never counted). Returns None
    when the file carries no usable central-body crossing."""
    rec = _recon_cached(events, central_naif,
                        thresholds_km, stop_thresholds_km, duration_s)
    if rec is None:
        return None
    G, n_bands = rec.n_objects, rec.n_bands
    # Scatter-add into (object x band) grids: O(segments) in C, no
    # per-record Python loop.
    time2d = np.zeros((G, n_bands), dtype=float)
    np.add.at(time2d, (rec.seg_group, rec.seg_band), rec.seg_end - rec.seg_start)
    ent2d = np.zeros((G, n_bands), dtype=np.int64)
    np.add.at(ent2d, (rec.ent_group, rec.ent_band), 1)
    rows = [(int(rec.group_obj[g]),
             [(float(time2d[g, b]), int(ent2d[g, b])) for b in range(n_bands)])
            for g in range(G)]
    return rec.thr, rec.from_snapshot, rows


@dataclass(frozen=True)
class BandSegments:
    """Flat per-segment arrays for the occupancy plots: each index is one
    interval an object spent in a band. Kept as parallel numpy arrays (no
    per-object nesting) so a million-segment batch stays vectorised --
    the Gantt filters by `obj`, the population view uses them all."""
    thr: np.ndarray
    from_snapshot: bool
    window_s: float
    band: np.ndarray             # (S,) band index of each segment
    start: np.ndarray            # (S,)
    end: np.ndarray              # (S,)
    obj: np.ndarray              # (S,) object id of each segment


def altitude_band_segments(events: np.ndarray, central_naif: int,
                           thresholds_km: "list[float] | None" = None,
                           stop_thresholds_km: "list[float] | None" = None,
                           duration_s: "float | None" = None,
                           ) -> "BandSegments | None":
    """Per-object band *segments* for the occupancy plots, as flat numpy
    arrays (see `BandSegments`). None when the file carries no usable
    central-body crossing."""
    rec = _recon_cached(events, central_naif,
                        thresholds_km, stop_thresholds_km, duration_s)
    if rec is None:
        return None
    return BandSegments(
        thr=rec.thr, from_snapshot=rec.from_snapshot, window_s=rec.window_s,
        band=rec.seg_band, start=rec.seg_start, end=rec.seg_end,
        obj=rec.group_obj[rec.seg_group])


def band_edge_labels(thr: np.ndarray) -> list[str]:
    """`['0-45 km', '45-60 km', '60-inf km', ...]` for the n+1 bands
    defined by the sorted thresholds `thr`. Shared by the Info tab and
    the band plots so band names read identically everywhere."""
    n_bands = len(thr) + 1
    out = []
    for b in range(n_bands):
        lo = 0.0 if b == 0 else float(thr[b - 1])
        hi = "inf" if b == n_bands - 1 else f"{float(thr[b]):g}"
        out.append(f"{lo:g}-{hi} km")
    return out


def _band_label(thr: np.ndarray, b: int) -> str:
    """Human/column-safe altitude range for band `b`: `<lo>-<hi>km`
    with `inf` for the open top band (no commas -> safe as a CSV
    column-name fragment)."""
    lo = 0.0 if b == 0 else float(thr[b - 1])
    hi = "inf" if b == len(thr) else f"{float(thr[b]):g}"
    return f"{lo:g}-{hi}km"


def per_object_bands_to_csv(thr: np.ndarray, from_snapshot: bool,
                            rows: list, body_naif: int,
                            body_name: str = "") -> str:
    """Serialise `altitude_bands_per_object` output as CSV: a
    `#`-comment metadata header, then ONE ROW PER OBJECT (ascending id)
    with, for each band in ascending-altitude order, a pair of columns
    `t_<lo>-<hi>km_s` (total time in band) and `entries_<lo>-<hi>km`
    (crossings into the band, entries only)."""
    n_bands = len(thr) + 1
    thr_txt = ";".join(f"{h:g}" for h in thr)
    header = ["case_id"]
    for b in range(n_bands):
        lbl = _band_label(thr, b)
        header.append(f"t_{lbl}_s")
        header.append(f"entries_{lbl}")
    lines = [
        "# SpOdy altitude-band occupancy analysis (per batch element)",
        f"# body_name,{body_name}",
        f"# body_naif,{body_naif}",
        f"# thresholds_km,{thr_txt}",
        f"# threshold_source,{'snapshot' if from_snapshot else 'clustered_from_records'}",
        f"# n_objects,{len(rows)}",
        "# columns: per band a (total time in band [s], entries into band) pair",
        ",".join(header),
    ]
    for obj_id, per_band in rows:
        cells = [str(obj_id)]
        for (time_s, entries) in per_band:
            cells.append(_csv_num(time_s))
            cells.append(str(entries))
        lines.append(",".join(cells))
    return "\n".join(lines) + "\n"
