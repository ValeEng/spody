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
  - the initial band comes from the object's INITIAL_STATE life marker
    (`distance_km - radius_km` at t0). Only on a file written before
    those existed is it inferred from the FIRST event instead: an
    up-crossing of k means the object started in band k, a
    down-crossing in band k+1.

Life markers also fix the two holes the crossing stream alone cannot
cover. An object confined between two thresholds crosses nothing for
the whole run: with markers it is a first-class object with one
full-window segment in its birth band, without them it was absent
from the analysis AND from its own denominator. And the analysis
window per object is read off its FINAL_STATE marker, written at the
instant the propagation actually stopped.

Without markers the window still closes at the earliest of: the
planned duration (run snapshot), the object's IMPACT trigger, or its
first crossing of a threshold configured with a stop-class action --
and a run stopped by `action = "stop"` (no log) leaves no trace, so
its tail is attributed to the planned duration. `log_and_stop` was
the workaround; on a file with markers it no longer matters.

Only crossings measured from the CENTRAL body are analysed: their `y`
state is body-centric, so the radial-velocity direction test is exact.
Crossings on third bodies (or CR3BP primaries) are counted by the
caller but get no occupancy reconstruction.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from spody_io import (
    EVENT_KIND_ALT_CROSSING,
    EVENT_KIND_FINAL_STATE,
    EVENT_KIND_IMPACT,
    EVENT_KIND_INITIAL_STATE,
)

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
    "BandStats", "BandAnalysis", "BandSegments", "BandSnapshot",
    "cluster_altitudes",
    "analyze_altitude_bands", "altitude_bands_per_object",
    "altitude_bands_per_object_grids",
    "altitude_band_segments", "band_edge_labels", "band_population",
    "band_snapshot", "band_snapshot_to_csv",
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
    n_objects: int                     # every propagated object once the
                                       # file carries life markers; before
                                       # them, only objects with >= 1 crossing
    window_s: float                    # max per-object end time
    ended_by_impact: int               # objects whose window closed early
    ended_by_stop: int                 #   (impact trigger / stop-class alt)
    from_markers: bool                 # start band / window measured, not inferred
    n_no_crossing: int                 # objects confined inside one band
    start_band_conflicts: int          # marker vs inference disagreements


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
    from_markers: bool           # life markers drove start band / window
    n_no_crossing: int           # objects that never crossed a threshold
    start_band_conflicts: int    # marker vs direction-inference mismatch
    group_obj: np.ndarray        # (G,)  object id per group (ascending)
    init_band: np.ndarray        # (G,)  band each object starts in
    t_end_pg: np.ndarray         # (G,)  end of each object's own window
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
    kind = events["kind"]
    on_body = events["naif_id"] == central_naif
    m_alt = (kind == EVENT_KIND_ALT_CROSSING) & on_body
    m_ini = (kind == EVENT_KIND_INITIAL_STATE) & on_body
    m_fin = (kind == EVENT_KIND_FINAL_STATE) & on_body
    from_markers = bool(m_ini.any())
    if not (m_alt.any() or from_markers):
        return None

    h_obs = (np.asarray(events["distance_km"][m_alt], dtype=float)
             - np.asarray(events["radius_km"][m_alt], dtype=float))
    if thresholds_km:
        thr = np.unique(np.asarray(sorted(thresholds_km), dtype=float))
        from_snapshot = True
    elif h_obs.size:
        thr = cluster_altitudes(h_obs)
        from_snapshot = False
    else:
        # Markers but no crossing anywhere and no configured thresholds:
        # there is nothing to infer band edges from. The caller's
        # "no bands for this file" path is the honest answer.
        return None
    n_bands = len(thr) + 1

    is_batch = "case_idx" in (events.dtype.names or ())

    def case_ids(mask: np.ndarray) -> np.ndarray:
        return (np.asarray(events["case_idx"][mask], dtype=np.int64)
                if is_batch else np.zeros(int(mask.sum()), dtype=np.int64))

    # Life markers: the object's own record of where it started and when
    # its run ended. They replace two inferences -- the start band read
    # off the first crossing's direction, and the window end guessed
    # from (planned duration, impact, stop action) -- with measurements,
    # and they are the only trace of an object that crossed nothing.
    ini_obj = case_ids(m_ini)
    ini_h = (np.asarray(events["distance_km"][m_ini], dtype=float)
             - np.asarray(events["radius_km"][m_ini], dtype=float))
    fin_obj = case_ids(m_fin)
    fin_t = np.asarray(events["t"][m_fin], dtype=float)

    # Nearest-threshold index + direction (r.v sign), all vectorised.
    k_idx = nearest_index(h_obs, thr)
    y = events["y"]
    rdot = np.einsum("ij,ij->i", y[:, 0:3], y[:, 3:6])[m_alt]
    valid = rdot != 0.0          # drop tangent grazings (direction undefined)
    if not (valid.any() or from_markers):
        return None
    t = np.asarray(events["t"][m_alt], dtype=float)[valid]
    k_idx = k_idx[valid]
    up = (rdot[valid] > 0.0).astype(np.int64)
    obj = case_ids(m_alt)[valid]

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
    is_first = np.zeros(N, bool)
    is_last = np.zeros(N, bool)
    if N:
        is_first[0] = True
        is_first[1:] = so[1:] != so[:-1]
        is_last[-1] = True
        is_last[:-1] = so[:-1] != so[1:]
    cross_starts = np.flatnonzero(is_first)

    # Object universe. With life markers it is every propagated object,
    # which is exactly what the crossing stream alone could not give:
    # an object confined inside one band fires nothing and used to
    # vanish from the analysis (and from its denominator). Without
    # markers -- files written before they existed -- it degrades to
    # the old behaviour, the set of objects that crossed something.
    group_obj = (np.union1d(ini_obj, so[cross_starts]) if from_markers
                 else so[cross_starts])
    G = len(group_obj)
    if G == 0:
        return None
    grp = np.searchsorted(group_obj, so)         # group index per crossing

    # Band each crossing lands in (up -> k+1, down -> k). The band a
    # segment sits in is band_after of the PREVIOUS event; the first
    # event of each object is pinned to its start band instead.
    band_after = sk + sup
    band_during = np.empty(N, np.int64)
    start_band_all = sk + (1 - sup)             # up -> k, down -> k+1
    seg_start = np.empty(N)
    if N:
        band_during[1:] = band_after[:-1]
        seg_start[1:] = st[:-1]
        seg_start[is_first] = 0.0

    # --- start band per object --------------------------------------
    # Measured from the INITIAL_STATE marker when there is one; only
    # otherwise inferred from the first crossing's direction. Where both
    # exist they must agree: a mismatch means a crossing was missed
    # (an unrefined step jumping a whole band) or the direction test is
    # invalid for this file, so it is counted and surfaced rather than
    # quietly resolved.
    init_band = np.full(G, -1, np.int64)
    if from_markers:
        init_band[np.searchsorted(group_obj, ini_obj)] = np.searchsorted(
            thr, ini_h, side="right")
    conflicts = 0
    if N:
        g_first = grp[is_first]
        inferred = start_band_all[is_first]
        known = init_band[g_first] >= 0
        conflicts = int(np.count_nonzero(
            init_band[g_first][known] != inferred[known]))
        init_band[g_first[~known]] = inferred[~known]
        band_during[is_first] = init_band[g_first]
    np.maximum(init_band, 0, out=init_band)      # unreachable, keeps indices safe

    # --- per-object window end: min(duration, impact, stop) but never
    #     before the object's last crossing ----------------------------
    t_last = np.zeros(G)
    if N:
        t_last[grp[is_last]] = st[is_last]
    stop_pg = np.full(G, np.inf)
    if stop_bands and N:
        is_stop = np.isin(sk, list(stop_bands))
        stop_pg[grp[cross_starts]] = np.minimum.reduceat(
            np.where(is_stop, st, np.inf), cross_starts)
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
    # A FINAL_STATE marker IS the end of that object's run -- written at
    # the instant the propagation stopped, whatever stopped it -- so it
    # replaces the reconstruction above instead of being merged with it.
    # `cause` is left as computed: it labels WHY the run ended, which the
    # marker does not carry.
    if fin_t.size:
        t_end_pg[np.searchsorted(group_obj, fin_obj)] = fin_t
        t_end_pg = np.maximum(t_end_pg, t_last)
    ended_by_impact = int((cause == 1).sum())
    ended_by_stop = int((cause == 2).sum())
    window_s = float(t_end_pg.max())

    # --- segments (dur > 0): regular [seg_start, t] in band_during,
    #     plus one tail [t_last, t_end] per object in its last band.
    #     For an object that never crossed anything the tail IS the
    #     whole run: [0, t_end] in its start band. ---------------------
    reg_m = (st - seg_start) > 0.0
    has_cross = np.zeros(G, bool)
    tail_band = np.zeros(G, np.int64)
    tail_start = np.zeros(G)
    if N:
        g_last = grp[is_last]
        has_cross[g_last] = True
        tail_band[g_last] = band_after[is_last]
        tail_start[g_last] = st[is_last]
    tail_band[~has_cross] = init_band[~has_cross]
    tail_m = (t_end_pg - tail_start) > 0.0
    seg_band = np.concatenate([band_during[reg_m], tail_band[tail_m]])
    seg_start_all = np.concatenate([seg_start[reg_m], tail_start[tail_m]])
    seg_end_all = np.concatenate([st[reg_m], t_end_pg[tail_m]])
    seg_group = np.concatenate([grp[reg_m], np.arange(G)[tail_m]])

    # Entries: a crossing that actually changes band (band change is
    # essentially always true; the mask guards the rare snapped tie).
    entry_m = band_after != band_during
    return _Recon(
        thr=thr, from_snapshot=from_snapshot, n_bands=n_bands, n_objects=G,
        window_s=window_s, ended_by_impact=ended_by_impact,
        ended_by_stop=ended_by_stop, group_obj=group_obj,
        init_band=init_band,
        seg_band=seg_band, seg_start=seg_start_all, seg_end=seg_end_all,
        seg_group=seg_group, ent_band=band_after[entry_m],
        ent_group=grp[entry_m], ent_time=st[entry_m], t_end_pg=t_end_pg,
        from_markers=from_markers,
        n_no_crossing=int((~has_cross).sum()),
        start_band_conflicts=conflicts)


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
        from_markers=rec.from_markers,
        n_no_crossing=rec.n_no_crossing,
        start_band_conflicts=rec.start_band_conflicts,
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
class BandSnapshot:
    """Where every object is at ONE instant, as opposed to the pooled
    views, which are all cumulative over a window.

    `band[g]` is the band object `obj[g]` occupies at `t_s`, or -1 when
    its propagation had already ended by then (impact, stop trigger, or
    simply a shorter window). Keeping the ended objects in the array
    rather than dropping them is what makes `counts / n_objects` an
    honest fraction of the ORIGINAL population: a debris cloud that has
    lost half its objects to impacts must not read as if the survivors
    were everything."""
    thr: np.ndarray
    from_snapshot: bool
    from_markers: bool
    t_s: float
    obj: np.ndarray              # (G,) object ids, ascending
    band: np.ndarray             # (G,) band at t_s, -1 = already ended
    counts: np.ndarray           # (n_bands,) objects per band at t_s
    n_objects: int               # G, the whole population
    n_ended: int                 # objects already gone at t_s
    window_s: float              # latest end time over all objects


def band_snapshot(events: np.ndarray, central_naif: int, t_s: float,
                  thresholds_km: "list[float] | None" = None,
                  stop_thresholds_km: "list[float] | None" = None,
                  duration_s: "float | None" = None,
                  ) -> "BandSnapshot | None":
    """Per-object band occupancy AT `t_s` (seconds of sim time).

    Reads the same cached reconstruction the cumulative views use, so
    asking for several instants of one file costs one reconstruction
    plus a vectorised interval lookup each. None when the file carries
    no usable central-body crossing or marker.

    A segment is the half-open interval [start, end), which is what
    makes the lookup unambiguous at an interior band change: at the
    exact instant of a crossing the object is already in the band it
    moved INTO. The one exception is an object's very last instant --
    `t_s == t_end` would fall outside every half-open segment and read
    as "gone" for an object that is merely at the end of its window --
    so the final segment is closed at its right end."""
    rec = _recon_cached(events, central_naif,
                        thresholds_km, stop_thresholds_km, duration_s)
    if rec is None:
        return None
    G = rec.n_objects
    t = float(t_s)

    band = np.full(G, -1, np.int64)
    inside = (rec.seg_start <= t) & (t < rec.seg_end)
    band[rec.seg_group[inside]] = rec.seg_band[inside]

    # Right-closed final segment: pick each object's last band and use
    # it for the objects that are alive at t but matched no half-open
    # interval, which can only be the t == t_end boundary.
    is_final = rec.seg_end >= rec.t_end_pg[rec.seg_group]
    final_band = np.full(G, -1, np.int64)
    final_band[rec.seg_group[is_final]] = rec.seg_band[is_final]
    at_end = (band < 0) & (t >= 0.0) & (t <= rec.t_end_pg)
    band[at_end] = final_band[at_end]

    alive = band >= 0
    return BandSnapshot(
        thr=rec.thr, from_snapshot=rec.from_snapshot,
        from_markers=rec.from_markers, t_s=t,
        obj=rec.group_obj, band=band,
        counts=np.bincount(band[alive], minlength=rec.n_bands),
        n_objects=G, n_ended=int((~alive).sum()), window_s=rec.window_s)


def band_snapshot_to_csv(snap: BandSnapshot, body_naif: int,
                         body_name: str = "") -> str:
    """Serialise a `BandSnapshot`: `#`-comment metadata header, then one
    row per object with the band it occupies at the snapshot instant and
    that band's altitude range.

    An object whose run had already ended is written with `band = -1`
    and empty range cells rather than being omitted -- the row set is
    then the whole population, and a reader can compute fractions of it
    without a second file. No altitude column: the reconstruction knows
    which band an object is in between two crossings, not where inside
    it, and inventing an interpolated altitude would be a fabrication."""
    thr = snap.thr
    n_bands = len(thr) + 1
    edges = [0.0, *thr.tolist(), float("inf")]
    lines = [
        "# SpOdy altitude-band snapshot (per object, at one instant)",
        f"# body_name,{body_name}",
        f"# body_naif,{body_naif}",
        f"# thresholds_km,{';'.join(f'{h:g}' for h in thr)}",
        f"# threshold_source,"
        f"{'snapshot' if snap.from_snapshot else 'clustered_from_records'}",
        f"# object_source,"
        f"{'all_propagated' if snap.from_markers else 'only_with_crossings'}",
        f"# t_s,{snap.t_s:.6g}",
        f"# t_days,{snap.t_s / 86400.0:.6g}",
        f"# n_objects,{snap.n_objects}",
        f"# n_ended,{snap.n_ended}",
        "# band,-1 = propagation already ended at this instant; "
        "0 = below the lowest threshold",
        "case_id,band,band_lo_km,band_hi_km",
    ]
    for obj_id, b in zip(snap.obj.tolist(), snap.band.tolist()):
        if b < 0:
            lines.append(f"{obj_id},-1,,")
        else:
            hi = "inf" if b == n_bands - 1 else f"{edges[b + 1]:g}"
            lines.append(f"{obj_id},{b},{edges[b]:g},{hi}")
    return "\n".join(lines) + "\n"


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
                            t_max_s: "float | None" = None,
                            from_markers: bool = False) -> str:
    """Serialise `altitude_bands_per_object` output as CSV: a
    `#`-comment metadata header, then ONE ROW PER OBJECT (ascending id)
    with, for each band in ascending-altitude order, a pair of columns
    `t_<lo>-<hi>km_s` (total time in band) and `entries_<lo>-<hi>km`
    (crossings into the band, entries only).

    `t_max_s` is the analysis window's upper bound (from
    `altitude_bands_per_object`); it is recorded in the metadata header
    so the file states which slice of the run it covers. `None` =
    whole run.

    `from_markers` says whether the row set is EVERY propagated object
    (life markers present) or only those that crossed a threshold. That
    single flag decides whether `n_objects` here is a valid denominator
    for per-object fractions, so it is written into the header rather
    than left for the reader to guess."""
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
        f"# object_source,{'all_propagated' if from_markers else 'only_with_crossings'}",
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
