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

from .derived import (
    cache_key,
    cached,
    cluster_altitudes,
    nearest_index,
    stable_group_order,
)

# `cluster_altitudes` moved to `derived.py` (it is the shared crossed-
# altitude helper: the event timeline views cluster the same way). Kept
# importable from here for the callers that already reach for it.
__all__ = [
    "BandStats", "BandAnalysis", "BandSegments", "cluster_altitudes",
    "analyze_altitude_bands", "altitude_bands_per_object",
    "altitude_bands_per_object_grids",
    "altitude_band_segments", "band_edge_labels", "band_population",
    "band_inputs_from_snapshot", "per_object_bands_to_csv",
]


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


# The band reconstruction is the one O(N) step, and the Info tab re-runs
# it on every switch to the Info tab while the four plots each call it
# too. The shared content-keyed cache in `derived.py` makes all of them
# share one reconstruction (and one pooled analysis) per loaded file, so
# only the first touch pays and later tab switches / plot clicks are
# instant. `None` results (no crossings) are cached too.

def _cache_key(tag: str, events: np.ndarray, central_naif: int,
               thresholds_km, stop_thresholds_km, duration_s):
    return cache_key(tag, events, int(central_naif),
                     tuple(thresholds_km or ()),
                     tuple(stop_thresholds_km or ()), duration_s)


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
    ent_time: np.ndarray         # (E,)  sim time [s] of the entering crossing


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
    Returns None when there is no usable central-body crossing.

    Memory discipline matters here: at ten million records the obvious
    `events[mask]` sub-array is a ~900 MB struct copy and the pairwise
    nearest-threshold form allocates another (N x K) float matrix. Both
    are avoided -- fields are masked one column at a time, and `r.v` is
    contracted on the strided (N, 6) view before masking, so the peak
    extra allocation is a handful of N-element float columns."""
    m_alt = ((events["kind"] == EVENT_KIND_ALT_CROSSING)
             & (events["naif_id"] == central_naif))
    if not m_alt.any():
        return None

    h_obs = (np.asarray(events["distance_km"][m_alt], dtype=float)
             - np.asarray(events["radius_km"][m_alt], dtype=float))
    if thresholds_km:
        thr = np.unique(np.asarray(sorted(thresholds_km), dtype=float))
        from_snapshot = True
    else:
        thr = cluster_altitudes(h_obs)
        from_snapshot = False
    n_bands = len(thr) + 1

    # Nearest-threshold index + direction (r.v sign), all vectorised.
    k_idx = nearest_index(h_obs, thr)
    y = events["y"]
    rdot = np.einsum("ij,ij->i", y[:, 0:3], y[:, 3:6])[m_alt]
    valid = rdot != 0.0          # drop tangent grazings (direction undefined)
    if not valid.any():
        return None
    t = np.asarray(events["t"][m_alt], dtype=float)[valid]
    k_idx = k_idx[valid]
    up = (rdot[valid] > 0.0).astype(np.int64)
    is_batch = "case_idx" in (events.dtype.names or ())
    obj = (np.asarray(events["case_idx"][m_alt], dtype=np.int64)[valid]
           if is_batch else np.zeros(int(valid.sum()), dtype=np.int64))

    # Stop-class thresholds -> band-boundary indices.
    stop_bands: set[int] = set()
    for h_stop in (stop_thresholds_km or []):
        stop_bands.add(int(np.abs(thr - float(h_stop)).argmin()))

    # Per-object earliest impact time (few impacts -> cheap dict, but
    # read out of the masked columns rather than by iterating a struct
    # sub-array, which would box every field of every impact record).
    m_imp = events["kind"] == EVENT_KIND_IMPACT
    imp_t = np.asarray(events["t"][m_imp], dtype=float)
    imp_case = (np.asarray(events["case_idx"][m_imp], dtype=np.int64)
                if is_batch else np.zeros(imp_t.size, dtype=np.int64))
    impact_dict: dict[int, float] = {}
    for key, ti in zip(imp_case.tolist(), imp_t.tolist()):
        if key not in impact_dict or ti < impact_dict[key]:
            impact_dict[key] = ti

    # --- sort by (object, time); one C-level sort over all records ----
    # An events log written in time order (the common case: the engine
    # appends triggers as they fire) only needs a STABLE sort on the
    # object id to end up ordered by (object, time). That halves the
    # work of the general lexsort AND unlocks the radix path in
    # `stable_group_order`, which together turn seconds into
    # milliseconds on a ten-million-crossing batch. The guard is one
    # vectorised comparison, so the fallback costs nothing measurable
    # when the file is not in time order.
    if t.size and bool(np.all(t[1:] >= t[:-1])):
        order = stable_group_order(obj)
    else:
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
        ent_group=grp[entry_m], ent_time=st[entry_m])


def _recon_cached(events, central_naif, thresholds_km,
                  stop_thresholds_km, duration_s) -> "_Recon | None":
    """Content-keyed memo around `_reconstruct` so the Info tab, the
    plots and the CSV exports share one reconstruction per file."""
    key = _cache_key("recon", events, central_naif,
                     thresholds_km, stop_thresholds_km, duration_s)
    return cached(key, lambda: _reconstruct(
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
    # The level at a node is `#starts <= t` minus `#ends <= t`, read off
    # the two independently sorted boundary lists. That is the same
    # step function the old cumsum-over-a-sorted-delta-stream produced,
    # but built from plain `np.sort` (introsort) instead of an
    # `argsort(kind="stable")` over the merged stream -- an order of
    # magnitude cheaper per band, because sorting values is far cheaper
    # than sorting values AND materialising their permutation.
    #
    # Ties need no special casing any more: at a time where exits and
    # entries coincide, this formula already yields the post-transition
    # level, and every intermediate node of a tie group spans dt = 0 and
    # is dropped by `held` below. That is what keeps an instantaneous
    # hand-over between objects from spiking the max.
    s_sorted = np.sort(starts)
    e_sorted = np.sort(ends)
    st_s = np.concatenate([s_sorted, e_sorted])
    st_s.sort()
    p_after = (np.searchsorted(s_sorted, st_s, side="right")
               - np.searchsorted(e_sorted, st_s, side="right"))
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
    return cached(key, lambda: _analyze_impl(
        events, central_naif, thresholds_km, stop_thresholds_km, duration_s))


def _analyze_impl(events, central_naif, thresholds_km,
                  stop_thresholds_km, duration_s) -> BandAnalysis | None:
    rec = _recon_cached(events, central_naif,
                        thresholds_km, stop_thresholds_km, duration_s)
    if rec is None:
        return None
    thr, n_bands, m = rec.thr, rec.n_bands, len(rec.thr)
    entries_pb = np.bincount(rec.ent_band, minlength=n_bands)
    starts_pb = np.bincount(rec.init_band, minlength=n_bands)

    # Group the segments by band ONCE and take contiguous slices, rather
    # than re-masking the (potentially ten-million-entry) segment arrays
    # per band. The sort is stable, so the segments inside a slice keep
    # their original relative order -- every per-band reduction below
    # therefore sees exactly the array the old `seg_dur[seg_band == b]`
    # produced, element for element, and the statistics are unchanged
    # down to the last bit rather than merely to within rounding.
    order = stable_group_order(rec.seg_band)
    sb = rec.seg_band[order]
    s_start = rec.seg_start[order]
    s_end = rec.seg_end[order]
    s_group = rec.seg_group[order]
    edges = np.searchsorted(sb, np.arange(n_bands + 1))

    bands: list[BandStats] = []
    for b in range(n_bands):
        lo, hi = int(edges[b]), int(edges[b + 1])
        starts_b, ends_b = s_start[lo:hi], s_end[lo:hi]
        dur_b = ends_b - starts_b
        pmin, pmax, pmean = _band_pop(starts_b, ends_b, rec.window_s)
        # Distinct objects via bincount rather than `np.unique`: the
        # group ids are dense and small, so counting beats sorting.
        objects_visiting = int(np.count_nonzero(
            np.bincount(s_group[lo:hi], minlength=rec.n_objects)))
        bands.append(BandStats(
            lo_km=0.0 if b == 0 else float(thr[b - 1]),
            hi_km=float(thr[b]) if b < m else float("inf"),
            entries=int(entries_pb[b]),
            starts_inside=int(starts_pb[b]),
            total_time_s=float(dur_b.sum()) if dur_b.size else 0.0,
            dwell_min_s=float(dur_b.min()) if dur_b.size else float("nan"),
            dwell_mean_s=float(dur_b.mean()) if dur_b.size else float("nan"),
            dwell_max_s=float(dur_b.max()) if dur_b.size else float("nan"),
            visits=hi - lo,
            objects_visiting=objects_visiting,
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


def altitude_bands_per_object_grids(events: np.ndarray, central_naif: int,
                                    thresholds_km: "list[float] | None" = None,
                                    stop_thresholds_km: "list[float] | None" = None,
                                    duration_s: "float | None" = None,
                                    t_max_s: "float | None" = None,
                                    ) -> "tuple[np.ndarray, bool, np.ndarray, np.ndarray, np.ndarray] | None":
    """Per-object band occupancy as raw grids:
    `(thresholds_km, from_snapshot, obj_ids, time2d, ent2d)` with both
    grids shaped `(n_objects, n_bands)` and `obj_ids` ascending.
    `time2d[g, b]` is the time object g spent in band b; `ent2d[g, b]`
    how many times it crossed INTO band b (entries only -- exits are
    never counted). None when the file carries no usable central-body
    crossing.

    This is the form the per-case heatmap wants; `altitude_bands_per_object`
    reshapes it into the per-row tuples the CSV writer wants. Keeping
    the grids separate means the plot never pays for building
    `n_objects x n_bands` Python tuples it would immediately unpack.

    `t_max_s` (when set) restricts the statistics to the window
    `[0, t_max_s]` of sim time: segments are clipped to that upper bound
    (a segment straddling t_max_s contributes only its `[start, t_max_s]`
    part) and only crossings at `t <= t_max_s` count as entries. The
    reconstruction itself is window-independent and cached; this is a
    cheap vectorised clip on top of it, so exporting several windows of
    one file re-uses the same reconstruction. `None` = whole run."""
    rec = _recon_cached(events, central_naif,
                        thresholds_km, stop_thresholds_km, duration_s)
    if rec is None:
        return None
    G, n_bands = rec.n_objects, rec.n_bands

    seg_group, seg_band = rec.seg_group, rec.seg_band
    seg_dur = rec.seg_end - rec.seg_start
    ent_group, ent_band = rec.ent_group, rec.ent_band
    if t_max_s is not None:
        # Clip segments to [0, t_max_s]; every object's first segment
        # starts at 0, so the lower bound needs no handling. A segment
        # fully past t_max_s drops out (clipped duration <= 0).
        clipped_end = np.minimum(rec.seg_end, t_max_s)
        seg_dur = clipped_end - rec.seg_start
        keep = seg_dur > 0.0
        seg_group, seg_band, seg_dur = seg_group[keep], seg_band[keep], seg_dur[keep]
        ent_keep = rec.ent_time <= t_max_s
        ent_group, ent_band = ent_group[ent_keep], ent_band[ent_keep]

    # Scatter-add into (object x band) grids via a flattened bincount:
    # `np.add.at` does the same job but is unbuffered, which at a
    # million-plus segments costs seconds rather than milliseconds.
    flat = seg_group * n_bands + seg_band
    time2d = np.bincount(flat, weights=seg_dur,
                         minlength=G * n_bands).reshape(G, n_bands)
    ent2d = np.bincount(ent_group * n_bands + ent_band,
                        minlength=G * n_bands).reshape(G, n_bands)
    return rec.thr, rec.from_snapshot, rec.group_obj, time2d, ent2d


def altitude_bands_per_object(events: np.ndarray, central_naif: int,
                              thresholds_km: "list[float] | None" = None,
                              stop_thresholds_km: "list[float] | None" = None,
                              duration_s: "float | None" = None,
                              t_max_s: "float | None" = None,
                              ) -> "tuple[np.ndarray, bool, list] | None":
    """Per-object band occupancy for the CSV export.

    Returns `(thresholds_km, from_snapshot, rows)` where `rows` is one
    entry per analysed object, SORTED by object id (case index in
    batch; a lone 0 for a per-run file). Each row is
    `(obj_id, per_band)` with `per_band[b] = (total_time_s, entries)`.
    Returns None when the file carries no usable central-body crossing.
    See `altitude_bands_per_object_grids` for the argument semantics."""
    grids = altitude_bands_per_object_grids(
        events, central_naif, thresholds_km, stop_thresholds_km,
        duration_s, t_max_s)
    if grids is None:
        return None
    thr, from_snapshot, group_obj, time2d, ent2d = grids
    n_bands = time2d.shape[1]
    rows = [(int(group_obj[g]),
             [(float(time2d[g, b]), int(ent2d[g, b])) for b in range(n_bands)])
            for g in range(len(group_obj))]
    return thr, from_snapshot, rows


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


# Node budget for the population step function. A canvas is ~1200 px
# wide, so a couple of thousand nodes already carries more detail than
# the screen can show; the alternative (one node per segment boundary)
# is twenty million nodes on a large debris batch, which is minutes of
# `fill_between` for a picture no different from this one.
POPULATION_MAX_NODES = 2000


def band_population(seg: BandSegments,
                    max_nodes: int = POPULATION_MAX_NODES
                    ) -> "tuple[np.ndarray, np.ndarray, bool]":
    """Instantaneous per-band population over time.

    Returns `(t_nodes, pop, exact)` where `pop` has shape
    `(n_bands, len(t_nodes))` and `pop[b, i]` is the number of objects
    in band b on the interval starting at `t_nodes[i]` -- i.e. the
    level a `step="post"` fill should hold from that node to the next.

    When the run is small enough that every segment boundary fits the
    node budget, the boundaries themselves are the nodes and the result
    is the exact step function (`exact = True`) -- unchanged from a
    per-boundary reconstruction. Past the budget the step function is
    SAMPLED on a uniform grid (`exact = False`): each level is still
    counted exactly at its node, but transitions closer together than
    one grid step are not resolved. Callers must surface that in the
    plot title.

    The count at a node is `#starts <= t` minus `#ends <= t`, which is
    two `searchsorted` calls against the band's sorted boundaries --
    the per-band sorts are the only O(S log S) work, and they replace
    the unbuffered `np.add.at` scatter over a twenty-million-node grid
    that made this the slowest view in the Analysis tab."""
    n_bands = len(seg.thr) + 1
    n_seg = int(seg.band.size)
    exact = 2 * n_seg + 2 <= max_nodes
    if exact:
        nodes = np.unique(np.concatenate(
            ([0.0], seg.start, seg.end, [seg.window_s])))
    else:
        nodes = np.linspace(0.0, float(seg.window_s), max_nodes)
    pop = np.empty((n_bands, nodes.size))
    for b in range(n_bands):
        m = seg.band == b
        pop[b] = (np.searchsorted(np.sort(seg.start[m]), nodes, side="right")
                  - np.searchsorted(np.sort(seg.end[m]), nodes, side="right"))
    return nodes, pop, exact


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
                            body_name: str = "",
                            t_max_s: "float | None" = None) -> str:
    """Serialise `altitude_bands_per_object` output as CSV: a
    `#`-comment metadata header, then ONE ROW PER OBJECT (ascending id)
    with, for each band in ascending-altitude order, a pair of columns
    `t_<lo>-<hi>km_s` (total time in band) and `entries_<lo>-<hi>km`
    (crossings into the band, entries only).

    `t_max_s` is the analysis window's upper bound (from
    `altitude_bands_per_object`); it is recorded in the metadata header
    so the file states which slice of the run it covers. `None` =
    whole run."""
    n_bands = len(thr) + 1
    thr_txt = ";".join(f"{h:g}" for h in thr)
    window_txt = "full_run" if t_max_s is None else f"0..{t_max_s:g}"
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
        f"# window_s,{window_txt}",
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
