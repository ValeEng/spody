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


@dataclass(frozen=True)
class _Streams:
    """Shared front-end product: the sorted thresholds and the
    per-object crossing streams both the pooled analysis and the
    per-object CSV are built from."""
    thr: np.ndarray                          # sorted ascending
    from_snapshot: bool
    streams: dict                            # obj_id -> [(t, k_idx, up)]
    stop_idx: set                            # band-boundary indices
    impact_t: dict                           # obj_id -> earliest impact t


def _prepare_streams(events: np.ndarray, central_naif: int,
                     thresholds_km: "list[float] | None",
                     stop_thresholds_km: "list[float] | None"
                     ) -> "_Streams | None":
    """Filter the central-body ALT_CROSSING records, derive the sorted
    thresholds, and group the crossings into per-object streams tagged
    with (time, nearest-threshold index, ascending?). Returns None when
    there is no usable crossing (no records, or all tangent)."""
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

    # Nearest-threshold index per record (snaps refined=false sloppiness
    # back onto the configured boundary).
    k_idx = np.abs(h_obs[:, None] - thr[None, :]).argmin(axis=1)

    # Direction: sign of r.v at the trigger state (body-centric y).
    r = alt["y"][:, 0:3].astype(float)
    v = alt["y"][:, 3:6].astype(float)
    rdot = np.einsum("ij,ij->i", r, v)

    stop_idx: set[int] = set()
    for h_stop in (stop_thresholds_km or []):
        stop_idx.add(int(np.abs(thr - float(h_stop)).argmin()))

    is_batch = "case_idx" in (events.dtype.names or ())
    impacts = events[events["kind"] == EVENT_KIND_IMPACT]
    impact_t: dict[int, float] = {}
    for rec in impacts:
        key = int(rec["case_idx"]) if is_batch else 0
        t_i = float(rec["t"])
        impact_t[key] = min(t_i, impact_t.get(key, t_i))

    obj_ids = (alt["case_idx"].astype(int) if is_batch
               else np.zeros(len(alt), dtype=int))
    streams: dict[int, list[tuple[float, int, bool]]] = {}
    for i in range(len(alt)):
        if rdot[i] == 0.0:
            continue   # tangent grazing: direction undefined, drop
        streams.setdefault(int(obj_ids[i]), []).append(
            (float(alt["t"][i]), int(k_idx[i]), bool(rdot[i] > 0.0)))
    if not streams:
        return None
    return _Streams(thr=thr, from_snapshot=from_snapshot, streams=streams,
                    stop_idx=stop_idx, impact_t=impact_t)


def _object_band_intervals(evs: list, n_bands: int, stop_idx: set,
                           duration_s: "float | None",
                           impact_time: "float | None"
                           ) -> tuple:
    """Reconstruct one object's band timeline from its sorted crossing
    stream. Returns `(t_end, cause, start_band, intervals, entries)`:

      intervals[b] : list of (t_start, t_end) the object spent in band b
      entries[b]   : number of times it crossed INTO band b (ENTRIES
                     ONLY -- an up-crossing of threshold k counts once,
                     against the band above; the matching departure from
                     the band below is never tallied). The band the
                     object starts in is `start_band` and is NOT an
                     entry.

    The window closes at the earliest of the planned duration, the
    object's impact, or its first stop-class crossing -- but never
    before the last logged crossing."""
    evs = sorted(evs, key=lambda e: e[0])
    t_last = evs[-1][0]
    t_end = duration_s if (duration_s is not None and duration_s > 0.0) else None
    cause = "duration"
    if impact_time is not None and (t_end is None or impact_time < t_end):
        t_end, cause = impact_time, "impact"
    t_stop = next((t for (t, k, _up) in evs if k in stop_idx), None)
    if t_stop is not None and (t_end is None or t_stop < t_end):
        t_end, cause = t_stop, "stop"
    if t_end is None or t_end < t_last:
        t_end = t_last

    intervals: list[list[tuple[float, float]]] = [[] for _ in range(n_bands)]
    entries = [0] * n_bands
    _t0, k0, up0 = evs[0]
    band = k0 if up0 else k0 + 1
    start_band = band
    seg_start = 0.0
    for (t_e, k_e, up_e) in evs:
        if t_e > t_end:
            break
        if t_e > seg_start:
            intervals[band].append((seg_start, t_e))
        band_next = k_e + 1 if up_e else k_e
        if band_next != band:          # zero-length segments collapse
            entries[band_next] += 1
        band = band_next
        seg_start = t_e
    if t_end > seg_start:
        intervals[band].append((seg_start, t_end))
    return t_end, cause, start_band, intervals, entries


def analyze_altitude_bands(events: np.ndarray,
                            central_naif: int,
                            thresholds_km: "list[float] | None" = None,
                            stop_thresholds_km: "list[float] | None" = None,
                            duration_s: "float | None" = None,
                            ) -> BandAnalysis | None:
    """Reconstruct pooled per-band occupancy from an events array
    (either the per-run or the batch dtype). Returns None when the file
    has no usable central-body ALT_CROSSING record."""
    prep = _prepare_streams(events, central_naif,
                            thresholds_km, stop_thresholds_km)
    if prep is None:
        return None
    thr, streams = prep.thr, prep.streams
    m = len(thr)
    n_bands = m + 1

    entries        = [0] * n_bands
    starts_inside  = [0] * n_bands
    visits         = [0] * n_bands
    dwell: list[list[float]] = [[] for _ in range(n_bands)]
    visited_by: list[set[int]] = [set() for _ in range(n_bands)]
    # Population sweep input: per band, list of (t, +1/-1) deltas.
    pop_deltas: list[list[tuple[float, int]]] = [[] for _ in range(n_bands)]

    ended_by_impact = 0
    ended_by_stop   = 0
    window_s        = 0.0

    for obj, evs in streams.items():
        t_end, cause, start_band, intervals, obj_entries = \
            _object_band_intervals(evs, n_bands, prep.stop_idx,
                                   duration_s, prep.impact_t.get(obj))
        if cause == "impact":
            ended_by_impact += 1
        elif cause == "stop":
            ended_by_stop += 1
        window_s = max(window_s, t_end)
        starts_inside[start_band] += 1
        for b in range(n_bands):
            entries[b] += obj_entries[b]
            for (s, e) in intervals[b]:
                visits[b] += 1
                dwell[b].append(e - s)
                visited_by[b].add(obj)
                pop_deltas[b].append((s, +1))
                pop_deltas[b].append((e, -1))

    bands: list[BandStats] = []
    for b in range(n_bands):
        d = np.asarray(dwell[b], dtype=float)
        # Population sweep: -1 before +1 at equal t so an instantaneous
        # handover between objects doesn't spike the max; only levels
        # held for dt > 0 count towards min / max / mean.
        deltas = sorted(pop_deltas[b], key=lambda e: (e[0], e[1]))
        p, p_min, p_max, integral = 0, 0, 0, 0.0
        i = 0
        first_level = True
        while i < len(deltas):
            t_here = deltas[i][0]
            while i < len(deltas) and deltas[i][0] == t_here:
                p += deltas[i][1]
                i += 1
            t_next = deltas[i][0] if i < len(deltas) else window_s
            if t_next > t_here:
                if first_level and t_here > 0.0:
                    p_min = 0   # band unoccupied before the first entry
                    first_level = False
                p_min = p if first_level else min(p_min, p)
                p_max = max(p_max, p)
                first_level = False
                integral += p * (t_next - t_here)
        bands.append(BandStats(
            lo_km=0.0 if b == 0 else float(thr[b - 1]),
            hi_km=float(thr[b]) if b < m else float("inf"),
            entries=entries[b],
            starts_inside=starts_inside[b],
            total_time_s=float(d.sum()) if len(d) else 0.0,
            dwell_min_s=float(d.min()) if len(d) else float("nan"),
            dwell_mean_s=float(d.mean()) if len(d) else float("nan"),
            dwell_max_s=float(d.max()) if len(d) else float("nan"),
            visits=visits[b],
            objects_visiting=len(visited_by[b]),
            pop_min=p_min,
            pop_max=p_max,
            pop_mean=(integral / window_s) if window_s > 0.0 else 0.0,
        ))

    return BandAnalysis(
        thresholds_km=tuple(float(x) for x in thr),
        from_snapshot=prep.from_snapshot,
        bands=tuple(bands),
        n_objects=len(streams),
        window_s=window_s,
        ended_by_impact=ended_by_impact,
        ended_by_stop=ended_by_stop,
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
    prep = _prepare_streams(events, central_naif,
                            thresholds_km, stop_thresholds_km)
    if prep is None:
        return None
    n_bands = len(prep.thr) + 1
    rows: list[tuple[int, list[tuple[float, int]]]] = []
    for obj in sorted(prep.streams):
        _t_end, _cause, _start, intervals, entries = _object_band_intervals(
            prep.streams[obj], n_bands, prep.stop_idx,
            duration_s, prep.impact_t.get(obj))
        per_band = [(float(sum(e - s for (s, e) in intervals[b])),
                     int(entries[b]))
                    for b in range(n_bands)]
        rows.append((int(obj), per_band))
    return prep.thr, prep.from_snapshot, rows


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
