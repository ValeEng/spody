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

"""Info-tab row builders (per-kind summaries + diff overlay).

Each `info_rows_*` builder returns a flat list of (label, value)
pairs that the Info tab renders as a two-column key/value table. A
pair with `value is _SECTION` is rendered as a bold section header.
All numeric formatting goes through `fmt_num` so the same precision
rules apply everywhere. A new file kind gets its rows here.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from spody_io import (
    EVENT_KIND_ALT_CROSSING,
    EVENT_KIND_ECLIPSE,
    EVENT_KIND_IMPACT,
)

from .altitude_bands import analyze_altitude_bands, band_inputs_from_snapshot
from .registry import KIND_LABEL


SECTION = None  # sentinel: row is a section header


def fmt_num(x: float | int | None, unit: str = "", decimals: int = 6) -> str:
    """Format `x` as a short human-readable string with optional unit.
    Falls back to "-" on None / NaN / Inf so a missing metric never
    breaks the table layout."""
    if x is None:
        return "-"
    try:
        xf = float(x)
    except (TypeError, ValueError):
        return str(x)
    if not math.isfinite(xf):
        return "-"
    # Pick g-format with the requested significant digits; strip
    # trailing zeros for readability. Pad with the unit at the end.
    txt = f"{xf:.{decimals}g}"
    return f"{txt} {unit}".rstrip()


def fmt_duration(seconds: float | None) -> str:
    """Friendly duration: seconds with hours/days in parentheses
    once it crosses common thresholds."""
    if seconds is None or not math.isfinite(float(seconds)):
        return "-"
    s = float(seconds)
    if abs(s) < 60:
        return f"{s:.6g} s"
    if abs(s) < 3600:
        return f"{s:.6g} s ({s / 60:.3g} min)"
    if abs(s) < 86400:
        return f"{s:.6g} s ({s / 3600:.4g} h)"
    return f"{s:.6g} s ({s / 86400:.4g} d)"


def _kep_elements_at(d: np.ndarray, idx: int, mu: float
                     ) -> dict[str, float] | None:
    """Compute classical Kepler elements at one trajectory sample.
    Returns None on degenerate geometry (rectilinear / parabolic
    edge cases inside spopy) so the caller can drop the row."""
    try:
        from spopy import cartesian_to_keplerian
        r = np.array([d["x"][idx], d["y"][idx], d["z"][idx]],  dtype=float)
        v = np.array([d["vx"][idx], d["vy"][idx], d["vz"][idx]], dtype=float)
        el = cartesian_to_keplerian(r, v, mu)
        return {
            "a":    float(el["sma_km"]),
            "e":    float(el["ecc"]),
            "i":    math.degrees(float(el["inc_rad"])),
            "raan": math.degrees(float(el["raan_rad"])),
            "aop":  math.degrees(float(el["argp_rad"])),
            "nu":   math.degrees(float(el["true_anom_rad"])),
        }
    except Exception:
        return None


def info_rows_run_summary(path: Path,
                           kind: str,
                           n_records: int,
                           snapshot: dict | None,
                           central_body: "CentralBodySpec | None",
                           dynamics_model: str,
                           cr3bp_primaries: tuple
                           ) -> list[tuple[str, str | None]]:
    """File + run-level facts. Always shown at the top of the Info
    tab; snapshot-derived rows are skipped when no input.toml sits
    next to the binary."""
    rows: list[tuple[str, str | None]] = [
        ("Run summary", SECTION),
        ("File",       path.name),
        ("Folder",     str(path.parent)),
        ("Type",       KIND_LABEL.get(kind, kind)),
        ("Records",    f"{n_records}"),
    ]
    if central_body is not None:
        rows.append(("Central body", central_body.name))
    rows.append(("Dynamics model", dynamics_model))
    if cr3bp_primaries:
        p1, p2 = cr3bp_primaries
        rows.append(("CR3BP primaries", f"{p1.name} + {p2.name}"))
        mu_tot = p1.mu_km3_s2 + p2.mu_km3_s2
        rows.append(("CR3BP mass ratio µ",
                     fmt_num(p2.mu_km3_s2 / mu_tot, decimals=8)))
    if snapshot is not None:
        rows.append(("ET start [s]",      fmt_num(snapshot["et_start_s"])))
        rows.append(("Planned duration",  fmt_duration(snapshot["duration_s"])))
        eph = snapshot.get("ephemeris_path")
        if eph is not None:
            rows.append(("Ephemeris", eph.name))
        cases = snapshot.get("cases_file")
        if cases is not None:
            rows.append(("Cases file", cases.name))
    else:
        rows.append(("Snapshot TOML",
                     "(not found next to this .bin)"))
    return rows


def info_rows_traj(data: np.ndarray,
                    central_body: "CentralBodySpec | None",
                    dynamics_model: str
                    ) -> list[tuple[str, str | None]]:
    t = data["t"]
    r = np.stack((data["x"],  data["y"],  data["z"]),  axis=-1)
    v = np.stack((data["vx"], data["vy"], data["vz"]), axis=-1)
    r_mag = np.linalg.norm(r, axis=-1)
    v_mag = np.linalg.norm(v, axis=-1)
    dt = np.diff(t) if len(t) > 1 else np.array([0.0])
    rows: list[tuple[str, str | None]] = [
        ("Trajectory", SECTION),
        ("t range [s]",
         f"{fmt_num(t[0])}  →  {fmt_num(t[-1])}"),
        ("Time span", fmt_duration(float(t[-1] - t[0]))),
        ("Δt min / avg / max [s]",
         f"{fmt_num(dt.min(), decimals=4)} / "
         f"{fmt_num(dt.mean(), decimals=4)} / "
         f"{fmt_num(dt.max(), decimals=4)}"),
        ("|r| min / max [km]",
         f"{fmt_num(r_mag.min())} / {fmt_num(r_mag.max())}"),
        ("|v| min / max [km/s]",
         f"{fmt_num(v_mag.min())} / {fmt_num(v_mag.max())}"),
        ("Initial state", SECTION),
        ("r₀ [km]",   f"({fmt_num(r[0,0])}, {fmt_num(r[0,1])}, {fmt_num(r[0,2])})"),
        ("v₀ [km/s]", f"({fmt_num(v[0,0])}, {fmt_num(v[0,1])}, {fmt_num(v[0,2])})"),
        ("Final state", SECTION),
        ("r_f [km]",  f"({fmt_num(r[-1,0])}, {fmt_num(r[-1,1])}, {fmt_num(r[-1,2])})"),
        ("v_f [km/s]",f"({fmt_num(v[-1,0])}, {fmt_num(v[-1,1])}, {fmt_num(v[-1,2])})"),
    ]
    # Osculating Kepler elements at endpoints — only meaningful in HF
    # (CR3BP needs the primary-relative state, which is a separate
    # PlotContext branch; we surface them in the diff/plot-aware path
    # if the user wants them per primary).
    if dynamics_model == "high_fidelity" and central_body is not None:
        mu = central_body.mu_km3_s2
        el0 = _kep_elements_at(data, 0,  mu)
        elN = _kep_elements_at(data, -1, mu)
        if el0 is not None and elN is not None:
            rows.append(("Kepler elements (HF, central body)", SECTION))
            rows.append(("a [km]   (t0 / tf)",
                         f"{fmt_num(el0['a'])} / {fmt_num(elN['a'])}"))
            rows.append(("e        (t0 / tf)",
                         f"{fmt_num(el0['e'], decimals=5)} / "
                         f"{fmt_num(elN['e'], decimals=5)}"))
            rows.append(("i [deg]  (t0 / tf)",
                         f"{fmt_num(el0['i'], decimals=5)} / "
                         f"{fmt_num(elN['i'], decimals=5)}"))
            rows.append(("RAAN [deg] (t0 / tf)",
                         f"{fmt_num(el0['raan'], decimals=5)} / "
                         f"{fmt_num(elN['raan'], decimals=5)}"))
            rows.append(("ω [deg]    (t0 / tf)",
                         f"{fmt_num(el0['aop'], decimals=5)} / "
                         f"{fmt_num(elN['aop'], decimals=5)}"))
            rows.append(("ν [deg]    (t0 / tf)",
                         f"{fmt_num(el0['nu'], decimals=5)} / "
                         f"{fmt_num(elN['nu'], decimals=5)}"))
    return rows


def info_rows_accel(data: np.ndarray
                     ) -> list[tuple[str, str | None]]:
    t = data["t"]
    dt = np.diff(t) if len(t) > 1 else np.array([0.0])
    def _mag(field: str) -> np.ndarray:
        return np.linalg.norm(data[field], axis=-1)
    a_tot = _mag("acc_total")
    rows: list[tuple[str, str | None]] = [
        ("Accelerations", SECTION),
        ("t range [s]",
         f"{fmt_num(t[0])}  →  {fmt_num(t[-1])}"),
        ("Time span", fmt_duration(float(t[-1] - t[0]))),
        ("Δt min / avg / max [s]",
         f"{fmt_num(dt.min(), decimals=4)} / "
         f"{fmt_num(dt.mean(), decimals=4)} / "
         f"{fmt_num(dt.max(), decimals=4)}"),
        ("|a_total| min / max [km/s²]",
         f"{fmt_num(a_tot.min())} / {fmt_num(a_tot.max())}"),
        ("|a_total| mean / RMS [km/s²]",
         f"{fmt_num(a_tot.mean())} / "
         f"{fmt_num(math.sqrt(float((a_tot * a_tot).mean())))}"),
        ("Per-force RMS [km/s²]", SECTION),
        ("2-body",       fmt_num(
            math.sqrt(float((_mag('acc_2body') ** 2).mean())))),
        ("Harmonics",    fmt_num(
            math.sqrt(float((_mag('acc_sphericalharmonics') ** 2).mean())))),
        ("3rd-body",     fmt_num(
            math.sqrt(float((_mag('acc_thirdbody_total') ** 2).mean())))),
        ("SRP",          fmt_num(
            math.sqrt(float((_mag('acc_srp') ** 2).mean())))),
        ("Drag",         fmt_num(
            math.sqrt(float((_mag('acc_drag') ** 2).mean())))),
    ]
    if "eclipse_fraction" in data.dtype.names:
        ef = data["eclipse_fraction"].astype(float)
        # Trapezoidal time in shadow: integrate (1 - ef) over t.
        # `trapezoid` is NumPy >= 2.0; fall back to `trapz` for the
        # 1.20+ baseline the project still supports.
        _trapz = getattr(np, "trapezoid", np.trapz)
        shadow_s = float(_trapz(1.0 - ef, t)) if len(t) > 1 else 0.0
        rows.append(("Eclipse", SECTION))
        rows.append(("min eclipse_fraction", fmt_num(ef.min(), decimals=4)))
        rows.append(("Time in shadow",       fmt_duration(shadow_s)))
    return rows


def info_rows_events(data: np.ndarray, snapshot: dict | None,
                      central_body: "CentralBodySpec | None" = None
                      ) -> list[tuple[str, str | None]]:
    is_batch = "case_idx" in data.dtype.names
    impacts  = data[data["kind"] == EVENT_KIND_IMPACT]
    eclipses = data[data["kind"] == EVENT_KIND_ECLIPSE]
    altcross = data[data["kind"] == EVENT_KIND_ALT_CROSSING]
    rows: list[tuple[str, str | None]] = [
        ("Events", SECTION),
        ("Total records", f"{len(data)}"),
        ("IMPACT count",  f"{len(impacts)}"),
        ("ECLIPSE count", f"{len(eclipses)}"),
        ("ALT_CROSSING count", f"{len(altcross)}"),
    ]
    if is_batch:
        cases_with_events = np.unique(data["case_idx"]) if len(data) else np.array([])
        cases_impacted    = (np.unique(impacts["case_idx"])
                             if len(impacts) else np.array([]))
        rows.append(("Cases with events", f"{len(cases_with_events)}"))
        rows.append(("Cases with impact", f"{len(cases_impacted)}"))
        if snapshot is not None and snapshot.get("cases_file") is not None:
            try:
                # Count CSV data rows: skip '#' comments and blank
                # lines (the engine's loader does), minus the header.
                with snapshot["cases_file"].open() as fp:
                    n_total = max(0, sum(
                        1 for ln in fp
                        if ln.strip() and not ln.lstrip().startswith("#")
                    ) - 1)
                rows.append(("Cases total (CSV)", f"{n_total}"))
                rows.append(("Survivors (no impact)",
                             f"{max(0, n_total - len(cases_impacted))}"))
                if n_total > 0:
                    rate = 100.0 * len(cases_impacted) / n_total
                    rows.append(("Impact rate",
                                 f"{fmt_num(rate, decimals=4)} %"))
            except OSError:
                pass
    if len(impacts) > 0:
        ti = impacts["t"].astype(float)
        rows.append(("Impact timing", SECTION))
        rows.append(("First impact",  fmt_duration(float(ti.min()))))
        rows.append(("Last impact",   fmt_duration(float(ti.max()))))
        rows.append(("Median impact", fmt_duration(float(np.median(ti)))))
        rows.append(("Mean impact",   fmt_duration(float(ti.mean()))))
    if len(eclipses) > 0:
        # Pair consecutive triggers per occulter (per case in batch
        # mode): the engine emits one ECLIPSE record on every sign
        # crossing of (fraction - threshold), so successive triggers
        # for the same {case, occulter} alternate entry / exit; a pair
        # = one full eclipse with duration = t_exit - t_entry. Odd
        # tail (started or ended inside shadow) is silently dropped.
        groups: dict[tuple, list[float]] = {}
        for rec in eclipses:
            key = ((int(rec["case_idx"]), int(rec["naif_id"]))
                   if is_batch else int(rec["naif_id"]))
            groups.setdefault(key, []).append(float(rec["t"]))
        durations_s: list[float] = []
        for ts in groups.values():
            ts_sorted = sorted(ts)
            for i in range(len(ts_sorted) // 2):
                durations_s.append(ts_sorted[2 * i + 1] - ts_sorted[2 * i])
        rows.append(("Eclipses", SECTION))
        rows.append(("Trigger records",   f"{len(eclipses)}"))
        rows.append(("Complete eclipses", f"{len(durations_s)}"))
        if durations_s:
            d_arr = np.asarray(durations_s)
            rows.append(("Duration min", fmt_duration(float(d_arr.min()))))
            rows.append(("Duration avg", fmt_duration(float(d_arr.mean()))))
            rows.append(("Duration max", fmt_duration(float(d_arr.max()))))
    if len(altcross) > 0 and central_body is not None:
        rows += _altitude_band_rows(data, altcross, snapshot,
                                     central_body, is_batch)
    return rows


def _altitude_band_rows(data: np.ndarray, altcross: np.ndarray,
                         snapshot: dict | None,
                         central_body: "CentralBodySpec",
                         is_batch: bool
                         ) -> list[tuple[str, str | None]]:
    """Altitude-band occupancy section. The user's sorted thresholds
    (0 < h_a < h_b < ...) split the altitude axis into bands; the
    reconstruction from the crossing records lives in
    `analyze_altitude_bands` (see that module's docstring for the
    exact rules and caveats)."""
    thresholds, stop_thresholds, duration = band_inputs_from_snapshot(
        snapshot, central_body.name)

    rows: list[tuple[str, str | None]] = []
    res = analyze_altitude_bands(data, central_body.naif_id,
                                  thresholds_km=thresholds,
                                  stop_thresholds_km=stop_thresholds,
                                  duration_s=duration)
    if res is not None:
        rows.append((f"Altitude bands — h above {central_body.name} surface",
                     SECTION))
        thr_txt = " / ".join(fmt_num(h) for h in res.thresholds_km)
        src = "snapshot" if res.from_snapshot else "clustered from records"
        rows.append(("Thresholds [km]", f"{thr_txt}  ({src})"))
        if is_batch:
            rows.append(("Objects analysed",
                         f"{res.n_objects} (with ≥ 1 crossing)"))
        window_txt = fmt_duration(res.window_s)
        trunc = []
        if res.ended_by_impact:
            trunc.append(f"{res.ended_by_impact} end at impact")
        if res.ended_by_stop:
            trunc.append(f"{res.ended_by_stop} end at stop trigger")
        if trunc:
            window_txt += f"  ({', '.join(trunc)})"
        rows.append(("Analysis window", window_txt))
        for i, band in enumerate(res.bands):
            hi = ("∞" if math.isinf(band.hi_km)
                  else fmt_num(band.hi_km))
            rows.append((f"Band {i + 1}:  {fmt_num(band.lo_km)} – {hi} km",
                         SECTION))
            entries_txt = f"{band.entries}"
            if band.starts_inside:
                entries_txt += (f"  (+{band.starts_inside} starting inside)"
                                if is_batch else "  (started inside)")
            rows.append(("Entries", entries_txt))
            time_txt = fmt_duration(band.total_time_s)
            if not is_batch and res.window_s > 0.0:
                pct = 100.0 * band.total_time_s / res.window_s
                time_txt += f"  ({fmt_num(pct, decimals=4)} % of window)"
            rows.append(("Object-time in band" if is_batch
                         else "Time in band", time_txt))
            if band.visits > 0:
                rows.append(("Visit duration  min / avg / max",
                             f"{fmt_duration(band.dwell_min_s)} / "
                             f"{fmt_duration(band.dwell_mean_s)} / "
                             f"{fmt_duration(band.dwell_max_s)}"
                             f"  ({band.visits} visits)"))
            if is_batch:
                rows.append(("Population min / avg / max",
                             f"{band.pop_min} / "
                             f"{fmt_num(band.pop_mean, decimals=4)} / "
                             f"{band.pop_max}"))
                rows.append(("Objects visiting", f"{band.objects_visiting}"))

    # Crossings measured from other bodies (third bodies, CR3BP
    # primaries): the trigger state is not body-centric there, so no
    # occupancy reconstruction -- counts and observed altitudes only.
    others = altcross[altcross["naif_id"] != central_body.naif_id]
    if len(others) > 0:
        rows.append(("Altitude crossings — other bodies", SECTION))
        for naif in np.unique(others["naif_id"]):
            sub = others[others["naif_id"] == naif]
            h = sub["distance_km"].astype(float) - sub["radius_km"].astype(float)
            rows.append((f"NAIF {int(naif)}",
                         f"{len(sub)} crossings, h {fmt_num(h.min())} – "
                         f"{fmt_num(h.max())} km"))
    return rows


def info_rows_diff(data_a: np.ndarray, data_b: np.ndarray,
                    spec: "PlotSpec",
                    paths: list[Path],
                    was_interp: bool
                    ) -> list[tuple[str, str | None]]:
    """Diff-aware stats for the currently active diff plot. Receives
    the *aligned* arrays (same shape, same `t` grid) so the row math
    is straight numpy. Empty list for non-traj diffs (the only diff
    plots today are on the trajectory kind)."""
    if "x" not in data_a.dtype.names or "x" not in data_b.dtype.names:
        return []
    r_a = np.stack((data_a["x"],  data_a["y"],  data_a["z"]),  axis=-1)
    r_b = np.stack((data_b["x"],  data_b["y"],  data_b["z"]),  axis=-1)
    v_a = np.stack((data_a["vx"], data_a["vy"], data_a["vz"]), axis=-1)
    v_b = np.stack((data_b["vx"], data_b["vy"], data_b["vz"]), axis=-1)
    dr = r_a - r_b
    dv = v_a - v_b
    dr_mag = np.linalg.norm(dr, axis=-1)
    dv_mag = np.linalg.norm(dv, axis=-1)
    rows: list[tuple[str, str | None]] = [
        (f"Diff: {spec.label}", SECTION),
        ("A",  paths[0].name),
        ("B",  paths[1].name),
    ]
    if was_interp:
        rows.append(("Alignment", "B interpolated onto A's grid"))
    rows.append(("|Δr| max / mean / RMS [km]",
                 f"{fmt_num(dr_mag.max())} / "
                 f"{fmt_num(dr_mag.mean())} / "
                 f"{fmt_num(math.sqrt(float((dr_mag * dr_mag).mean())))}"))
    rows.append(("|Δr| final [km]", fmt_num(dr_mag[-1])))
    rows.append(("|Δv| max / mean / RMS [km/s]",
                 f"{fmt_num(dv_mag.max())} / "
                 f"{fmt_num(dv_mag.mean())} / "
                 f"{fmt_num(math.sqrt(float((dv_mag * dv_mag).mean())))}"))
    rows.append(("|Δv| final [km/s]", fmt_num(dv_mag[-1])))
    # Linear growth rate of |Δr| — slope of a least-squares line, in
    # km/day. Useful as a "how fast is the regression diverging"
    # one-liner for the diff plots.
    t = data_a["t"]
    if len(t) > 1 and t[-1] > t[0]:
        slope_km_s = float(np.polyfit(t, dr_mag, 1)[0])
        rows.append(("|Δr| linear growth",
                     f"{fmt_num(slope_km_s * 86400.0)} km/day"))
    # RIC decomposition of Δr in A's frame — the canonical orbit-
    # regression breakdown (radial / in-track / cross-track).
    r_mag_a = np.linalg.norm(r_a, axis=-1, keepdims=True)
    r_hat = np.divide(r_a, r_mag_a, where=r_mag_a > 0)
    h = np.cross(r_a, v_a)
    h_mag = np.linalg.norm(h, axis=-1, keepdims=True)
    c_hat = np.divide(h, h_mag, where=h_mag > 0)
    i_hat = np.cross(c_hat, r_hat)
    dR = np.abs(np.einsum("...j,...j->...", dr, r_hat))
    dI = np.abs(np.einsum("...j,...j->...", dr, i_hat))
    dC = np.abs(np.einsum("...j,...j->...", dr, c_hat))
    rows.append(("RIC frame (A) — |Δ| max [km]",
                 f"R {fmt_num(dR.max())}  /  "
                 f"I {fmt_num(dI.max())}  /  "
                 f"C {fmt_num(dC.max())}"))
    rows.append(("RIC frame (A) — |Δ| RMS [km]",
                 f"R {fmt_num(math.sqrt(float((dR * dR).mean())))}  /  "
                 f"I {fmt_num(math.sqrt(float((dI * dI).mean())))}  /  "
                 f"C {fmt_num(math.sqrt(float((dC * dC).mean())))}"))
    return rows
