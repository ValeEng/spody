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

"""Event-log views: timeline, impact statistics, lat/lon maps and
the impact 3D scene.

`SPECS_SINGLE` serves per-run SPDYEVT_ files, `SPECS_BATCH` the
batch-aggregated SPDYEVTB kind (extra case_idx column). New event
views (e.g. for future event kinds like altitude bands) land here.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from matplotlib import colormaps as mpl_colormaps
from matplotlib.axes import Axes
from matplotlib.collections import LineCollection

from spody_io import (
    EVENT_KIND_ALT_CROSSING,
    EVENT_KIND_ECLIPSE,
    EVENT_KIND_IMPACT,
)

from ..vtk_canvas import VtkCanvas
from .altitude_bands import (
    altitude_band_segments,
    altitude_bands_per_object,
    analyze_altitude_bands,
    band_edge_labels,
    band_inputs_from_snapshot,
    cluster_altitudes,
)
from .context import PlotContext, ctx_missing_message, resolve_run_context
from .scene3d import add_reference_triads
from .spec import PlotSpec


# Seconds to days for everywhere the events views surface time -- the
# events file stores t in seconds (consistent with the integrator and
# the events.h C struct), but at user-visible scales (days-long batch
# runs, multi-day debris-cloud decay) day-level axes read better.
_SEC_PER_DAY = 86400.0


def _time_axis(span_s: float) -> tuple[float, str]:
    """Pick a readable time unit (divisor, label) for an events / band
    plot axis from the plotted span: seconds for a sub-minute orbit up
    to days for a multi-day batch. Keeps every timeline / band view
    from printing a raw six-digit second count on a days-long run."""
    if span_s >= 2 * 86400.0:
        return 86400.0, "days"
    if span_s >= 2 * 3600.0:
        return 3600.0, "h"
    if span_s >= 2 * 60.0:
        return 60.0, "min"
    return 1.0, "s"


# Cache for the grayscale-and-downsampled Mollweide central-body
# background, keyed by (texture_path, mtime). Avoids re-loading +
# resampling the 2K texture every time the user clicks an impact map
# -- the resample is the slow step (~80 ms on PIL/LANCZOS at
# 720x360) and the result never changes across clicks. Set lazily by
# `_load_body_grayscale_for_mollweide`; the panel doesn't bother
# evicting because the dict holds at most a handful of entries
# (one per texture path the user has ever set across bodies).
_BODY_BG_CACHE: dict[tuple[str, float], np.ndarray] = {}


def _load_body_grayscale_for_mollweide(texture_path: Path | None
                                        ) -> np.ndarray | None:
    """Return a (lat, lon) float array suitable for `pcolormesh` on a
    Mollweide axis. Downsamples to 720x360 (~0.25 MP), converts to
    grayscale, normalises to [0, 1]. Returns None when the texture
    is missing / unreadable / Pillow isn't installed.

    The grid orientation matches `_plot_events_impact_map_mollweide`:
    row 0 is lat=+90 (top), col 0 is lon=-180 (left). Cached per
    texture-mtime so the resample is paid once per session. Body-
    agnostic: the same pipeline works on a Moon, Earth or Mars
    equirectangular image."""
    if texture_path is None or not texture_path.is_file():
        return None
    try:
        mtime = texture_path.stat().st_mtime
    except OSError:
        return None
    key = (str(texture_path), mtime)
    cached = _BODY_BG_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(texture_path) as src:
            gray = src.convert("L").resize((720, 360), Image.LANCZOS)
        arr = np.asarray(gray, dtype=float) / 255.0
    except (OSError, ValueError):
        return None
    _BODY_BG_CACHE[key] = arr
    return arr


def _draw_mollweide_body_background(ax: Axes, ctx: "PlotContext",
                                     alpha: float = 0.8) -> bool:
    """Paint the central body's grayscale texture as a Mollweide
    background on `ax`. Returns True on success so the caller can
    adapt its foreground styling (e.g. white edges on top of the
    texture vs black on a plain background). Body-agnostic: the
    texture comes from `ctx.central_body_texture` which is resolved
    per-body upstream."""
    bg = _load_body_grayscale_for_mollweide(ctx.central_body_texture)
    if bg is None:
        return False
    # pcolormesh with shading='flat' wants (nrow, ncol) values and
    # (nrow+1, ncol+1) edges. `bg` is (360, 720); build matching
    # edge arrays in radians along lon (x) and lat (y). The Mollweide
    # projection accepts the lat/lon mesh directly and warps cells
    # to its ellipse for us.
    lon_edges = np.radians(np.linspace(-180.0, 180.0, bg.shape[1] + 1))
    lat_edges = np.radians(np.linspace( +90.0, -90.0, bg.shape[0] + 1))
    Lon, Lat = np.meshgrid(lon_edges, lat_edges)
    ax.pcolormesh(Lon, Lat, bg, cmap="gray", shading="flat",
                  vmin=0.0, vmax=1.0, alpha=alpha,
                  rasterized=True, zorder=1)
    return True


def _validate_impact_context(ax: Axes, d: np.ndarray, ctx: "PlotContext",
                              title: str
                              ) -> tuple[dict, np.ndarray, int] | None:
    """Shared gating for every impact-flavoured plot: aggregated-batch
    file, run-folder snapshot, central body with a registered
    body-fixed orientation provider, ephemeris reachable, at least
    one IMPACT row. On any miss draws the empty-state message on
    `ax` and returns None; on success returns
    `(info, impact_mask, n_impacts)`.

    Body-agnostic: anything in the central-body registry with a non-
    None `bf_orientation` callable (today Moon, Phase 2 Earth) is
    accepted; the lat/lon projection uses that callback instead of
    hardcoding lunar librations."""
    if "case_idx" not in d.dtype.names:
        ctx_missing_message(
            ax, title,
            "This view needs a batch-aggregated events file (SPDYEVTB).")
        return None
    info = resolve_run_context(ctx.path)
    if info is None:
        ctx_missing_message(
            ax, title,
            "No input.toml found next to this events file -- the run-folder "
            "snapshot is needed for et_start_s and the ephemeris path.")
        return None
    if ctx.central_body.bf_orientation is None:
        ctx_missing_message(
            ax, title,
            f"Central body '{ctx.central_body.name}' has no body-fixed "
            "orientation provider registered -- the lat/lon projection "
            "needs an ICRF -> body-fixed rotation. Register one in "
            "central_bodies._KNOWN_BODIES.")
        return None
    if info["ephemeris_path"] is None:
        ctx_missing_message(
            ax, title,
            "Could not locate the .spody ephemeris file referenced by the "
            "snapshot. Check that the path inside input.toml is still valid.")
        return None
    mask = d["kind"] == EVENT_KIND_IMPACT
    n_imp = int(mask.sum())
    if n_imp == 0:
        ctx_missing_message(ax, title, "No IMPACT events in this batch.")
        return None
    return info, mask, n_imp


def _compute_impact_latlon(d: np.ndarray, mask: np.ndarray, info: dict,
                            ctx: "PlotContext"
                            ) -> tuple[np.ndarray, np.ndarray,
                                        np.ndarray, np.ndarray] | None:
    """Project ICRF IMPACT rows of `d[mask]` onto the central body's
    body-fixed frame, returning `(lat_deg, lon_deg, t_days, case_idx)`.

    For every row:
        et    = sim.et_start_s + row.t
        R     = ctx.central_body.bf_orientation(et, eph)     (ICRF -> BF)
        r_bf  = R @ row.y[0:3]
        lat   = asin(z/|r|), lon = atan2(y, x)
    `t_days` is `row.t / 86400`. None on ephemeris failure (the
    caller falls back to the empty-state message). Body-agnostic
    via the CentralBodySpec orientation callback -- Moon today
    (DE440 lunar librations), Earth in Phase 2 (GMST / IAU 2006)."""
    from spopy import Ephemeris
    try:
        eph = Ephemeris(str(info["ephemeris_path"]))
    except (OSError, ValueError):
        return None
    bf_orientation = ctx.central_body.bf_orientation
    n        = int(mask.sum())
    et_start = info["et_start_s"]
    t_sim    = d["t"][mask]
    y_state  = d["y"][mask]
    case_id  = (d["case_idx"][mask].astype(int, copy=True)
                if "case_idx" in (d.dtype.names or ())
                else np.zeros(n, dtype=int))   # per-run file: single object
    r_icrf   = y_state[:, 0:3]
    lat_deg  = np.empty(n)
    lon_deg  = np.empty(n)
    for i in range(n):
        et = et_start + float(t_sim[i])
        R = bf_orientation(et, eph)
        r_bf = R @ r_icrf[i]
        norm = np.linalg.norm(r_bf)
        lat_deg[i] = np.degrees(np.arcsin(r_bf[2] / norm))
        lon_deg[i] = np.degrees(np.arctan2(r_bf[1], r_bf[0]))
    t_days = t_sim.astype(float) / _SEC_PER_DAY
    return lat_deg, lon_deg, t_days, case_id


def impacts_latlon_csv(d: np.ndarray, ctx: "PlotContext") -> str | None:
    """CSV of impact points: `case_id, lat_deg, lon_deg, tof_s,
    tof_days`, one row per IMPACT (ascending case id). Latitude and
    longitude are in the central body's body-fixed frame (the same
    projection the impact lat/lon maps use); time-of-flight is the
    trigger time from the run epoch. Returns None when there is no
    IMPACT row, no body-fixed orientation for the central body, or the
    run snapshot / ephemeris needed for the projection can't be
    resolved -- the caller surfaces that as a message."""
    if ctx.central_body.bf_orientation is None:
        return None
    mask = d["kind"] == EVENT_KIND_IMPACT
    if not mask.any():
        return None
    info = resolve_run_context(ctx.path)
    if info is None or info["ephemeris_path"] is None:
        return None
    geom = _compute_impact_latlon(d, mask, info, ctx)
    if geom is None:
        return None
    lat_deg, lon_deg, t_days, case_id = geom
    tof_s = d["t"][mask].astype(float)          # same (mask) order as geom
    order = np.argsort(case_id, kind="stable")
    lines = [
        "# SpOdy impact points (body-fixed lat/lon + time of flight)",
        f"# body_name,{ctx.central_body.name}",
        f"# body_naif,{ctx.central_body.naif_id}",
        f"# bf_frame,{ctx.central_body.bf_frame_name}",
        f"# n_impacts,{int(mask.sum())}",
        "case_id,lat_deg,lon_deg,tof_s,tof_days",
    ]
    for i in order:
        lines.append(",".join([
            str(int(case_id[i])),
            f"{lat_deg[i]:.6g}", f"{lon_deg[i]:.6g}",
            f"{tof_s[i]:.6g}", f"{t_days[i]:.6g}",
        ]))
    return "\n".join(lines) + "\n"


def _plot_events_timeline(ax: Axes, d: np.ndarray) -> None:
    """One y-row per event *series*: IMPACT, ECLIPSE, and one row per
    crossed altitude (labelled with the altitude, not the raw enum
    code). Altitude rows split ascending vs descending crossings by
    marker (▲ up / ▼ down); the altitude values are clustered from the
    records so the plot stays context-free (works with no input.toml).
    Third-body altitude crossings get their NAIF id in the row label
    when more than one body is involved."""
    if len(d) == 0:
        ax.set_title("No events recorded"); ax.set_xlabel("t [s]"); return

    # Each series is drawn on its own row. Build them in a stable order
    # (impacts on top, then eclipses, then altitudes ascending) so the
    # y-axis reads consistently across files.
    rows: list[tuple[str, list]] = []   # (label, [scatter kwargs dicts])

    m_imp = d["kind"] == EVENT_KIND_IMPACT
    if m_imp.any():
        rows.append(("IMPACT", [dict(t=d["t"][m_imp], color="tab:red",
                                     marker="|", s=200)]))
    m_ecl = d["kind"] == EVENT_KIND_ECLIPSE
    if m_ecl.any():
        rows.append(("ECLIPSE", [dict(t=d["t"][m_ecl], color="tab:blue",
                                      marker="|", s=200)]))

    m_alt = d["kind"] == EVENT_KIND_ALT_CROSSING
    if m_alt.any():
        alt = d[m_alt]
        naifs = np.unique(alt["naif_id"])
        multi_body = len(naifs) > 1
        alt_rows: list[tuple[float, str, list]] = []
        for naif in naifs:
            sub = alt[alt["naif_id"] == naif]
            h = sub["distance_km"].astype(float) - sub["radius_km"].astype(float)
            centers = cluster_altitudes(h)
            k_idx = np.abs(h[:, None] - centers[None, :]).argmin(axis=1)
            # Ascending vs descending from the radial velocity r·v at
            # the trigger state (body-centric for the central body;
            # for third bodies y is still central-frame so the sign is
            # only exact when the body sits at the origin -- acceptable
            # for a visual timeline).
            r = sub["y"][:, 0:3].astype(float)
            v = sub["y"][:, 3:6].astype(float)
            up = np.einsum("ij,ij->i", r, v) > 0.0
            for c, h_c in enumerate(centers):
                cm = k_idx == c
                label = f"alt {h_c:.4g} km"
                if multi_body:
                    label = f"NAIF {int(naif)}  " + label
                draws = []
                if (cm & up).any():
                    draws.append(dict(t=sub["t"][cm & up], color="tab:green",
                                      marker="^", s=44))
                if (cm & ~up).any():
                    draws.append(dict(t=sub["t"][cm & ~up], color="tab:green",
                                      marker="v", s=44))
                alt_rows.append((float(h_c), label, draws))
        alt_rows.sort(key=lambda e: e[0])
        rows += [(label, draws) for _h, label, draws in alt_rows]

    # Time unit from the full span of plotted events (days on a
    # days-long batch, seconds on a single short orbit).
    all_t = d["t"].astype(float)
    div, unit = _time_axis(float(all_t.max() - all_t.min()) if len(all_t) else 0.0)
    for y, (_label, draws) in enumerate(rows):
        for kw in draws:
            t = kw.pop("t")
            ax.scatter(np.asarray(t, dtype=float) / div,
                       np.full(len(t), y), **kw)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([label for label, _ in rows])
    ax.set_ylim(-0.5, len(rows) - 0.5)
    ax.set_xlabel(f"t [{unit}]")
    ax.set_title(f"Event timeline ({len(d)} triggers)")
    if m_alt.any():
        # One-off legend explaining the crossing-direction markers;
        # the per-altitude identity is on the y axis, not the legend.
        from matplotlib.lines import Line2D
        ax.legend(handles=[
            Line2D([0], [0], marker="^", color="tab:green", linestyle="",
                   label="ascending"),
            Line2D([0], [0], marker="v", color="tab:green", linestyle="",
                   label="descending"),
        ], loc="best", fontsize="small", framealpha=0.6)
    ax.grid(True, axis="x", alpha=0.3)


def _plot_events_timeline_density(ax: Axes, d: np.ndarray) -> None:
    """Aggregated companion to the marker timeline for large files: the
    same y-rows (IMPACT, ECLIPSE, one per crossed altitude), but each
    row is a time-binned **count heatmap** instead of individual
    markers. `np.histogram` per row is vectorised, so this stays fast
    and readable at millions of events where the scatter smears into a
    solid band. No ascending/descending split -- it counts crossings."""
    if len(d) == 0:
        ax.set_title("No events recorded"); ax.set_xlabel("t [s]"); return

    rows: list[tuple[str, np.ndarray]] = []
    m_imp = d["kind"] == EVENT_KIND_IMPACT
    if m_imp.any():
        rows.append(("IMPACT", d["t"][m_imp].astype(float)))
    m_ecl = d["kind"] == EVENT_KIND_ECLIPSE
    if m_ecl.any():
        rows.append(("ECLIPSE", d["t"][m_ecl].astype(float)))
    m_alt = d["kind"] == EVENT_KIND_ALT_CROSSING
    if m_alt.any():
        alt = d[m_alt]
        naifs = np.unique(alt["naif_id"])
        multi_body = len(naifs) > 1
        alt_rows: list[tuple[float, str, np.ndarray]] = []
        for naif in naifs:
            sub = alt[alt["naif_id"] == naif]
            h = sub["distance_km"].astype(float) - sub["radius_km"].astype(float)
            centers = cluster_altitudes(h)
            k_idx = np.abs(h[:, None] - centers[None, :]).argmin(axis=1)
            for c, h_c in enumerate(centers):
                label = f"alt {h_c:.4g} km"
                if multi_body:
                    label = f"NAIF {int(naif)}  " + label
                alt_rows.append((float(h_c), label,
                                 sub["t"][k_idx == c].astype(float)))
        alt_rows.sort(key=lambda e: e[0])
        rows += [(label, ts) for _h, label, ts in alt_rows]

    t_min = min(float(ts.min()) for _, ts in rows)
    t_max = max(float(ts.max()) for _, ts in rows)
    if t_max <= t_min:
        t_max = t_min + 1.0
    div, unit = _time_axis(t_max - t_min)
    n_bins = 300
    edges = np.linspace(t_min, t_max, n_bins + 1)
    hist = np.zeros((len(rows), n_bins))
    for i, (_label, ts) in enumerate(rows):
        hist[i], _ = np.histogram(ts, bins=edges)
    masked = np.ma.masked_where(hist == 0, hist)
    pm = ax.pcolormesh(edges / div, np.arange(len(rows) + 1) - 0.5, masked,
                       cmap="viridis", shading="flat")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([label for label, _ in rows])
    ax.set_xlabel(f"t [{unit}]")
    ax.set_title(f"Event timeline density  ({len(d)} triggers, "
                 f"{n_bins} bins)")
    cb = ax.figure.colorbar(pm, ax=ax, label="events per bin",
                            fraction=0.046, pad=0.02)
    cb.ax.tick_params(labelsize="x-small")


def _plot_events_time_to_impact_hist(ax: Axes, d: np.ndarray) -> None:
    """Histogram of trigger time `t` across cases that impacted.
    Operates on the batch-aggregated events file (SPDYEVTB) where each
    row is one trigger. ECLIPSE / other kinds are filtered out -- only
    IMPACT rows feed the histogram. X axis is in days for readability
    on multi-orbit / debris-cloud-decay scenarios."""
    mask = d["kind"] == EVENT_KIND_IMPACT
    n_imp = int(mask.sum())
    if n_imp == 0:
        ctx_missing_message(ax, "Time-to-impact histogram",
                             "No IMPACT events in this batch.")
        return
    t_imp_days = d["t"][mask] / _SEC_PER_DAY
    # Sturges' rule capped at 40 -- good for a few cases (4-8 bins)
    # without producing a forest of single-count spikes on large
    # debris clouds.
    n_bins = max(5, min(40, int(np.log2(n_imp) + 1) * 2))
    ax.hist(t_imp_days, bins=n_bins, color="tab:red",
            edgecolor="black", alpha=0.85)
    ax.set_xlabel("time of flight [days]")
    ax.set_ylabel("# impacts")
    ax.set_title(f"Time-to-impact distribution  ({n_imp} impacts, {n_bins} bins)")
    ax.grid(True, axis="y", alpha=0.3)


def _count_total_cases(ctx_info: dict, fallback_max: int) -> int:
    """Total number of cases in the batch -- the universe over which
    we compute survivors. Tries cases_file first (the canonical count)
    and falls back to `max(case_idx)+1` from the events data if the
    CSV isn't reachable.

    Counts non-blank, non-comment lines and subtracts 1 for the
    header. spody.exe accepts `#`-prefixed lines as comments in the
    cases file (see spody_csv reader), and the GUI's auto-generator
    + the hand-curated examples both use that convention liberally,
    so they must be skipped here too -- otherwise a CSV with a
    multi-paragraph header overcounts wildly."""
    cases_path: Path | None = ctx_info.get("cases_file")
    if cases_path is not None:
        try:
            with cases_path.open("r", encoding="utf-8") as fp:
                n = sum(1 for line in fp
                        if line.strip() and not line.lstrip().startswith("#"))
            n -= 1   # header row
            if n > 0:
                return n
        except OSError:
            pass
    return fallback_max


def _plot_events_survival_timeline(ax: Axes, d: np.ndarray,
                                   ctx: PlotContext) -> None:
    """Horizontal-bar 'who falls when' chart: one bar per case_idx
    ending at first IMPACT (red) or full sim duration (green = survivor).
    Cases that recorded only non-IMPACT events (e.g. eclipses) still
    count as survivors -- the bar extends to the full duration.

    Needs the per-run TOML for `duration_s` and the total case count.
    When the snapshot is missing we still draw the impacted cases only,
    with a degraded title that says so."""
    info = resolve_run_context(ctx.path)
    # Per-case earliest IMPACT time (np.inf for cases that survived).
    if "case_idx" not in d.dtype.names:
        ctx_missing_message(
            ax, "Survival timeline",
            "This view needs a batch-aggregated events file (SPDYEVTB).")
        return
    impacts = d[d["kind"] == EVENT_KIND_IMPACT]
    impact_t = {int(ci): float(t) for ci, t in zip(impacts["case_idx"],
                                                    impacts["t"])}
    # If a case has several IMPACTs (rare; predicate normally fires
    # once and the integrator stops) we want the earliest.
    for ci, t in zip(impacts["case_idx"], impacts["t"]):
        ci_i = int(ci)
        if t < impact_t.get(ci_i, np.inf):
            impact_t[ci_i] = float(t)

    seen_cases = set(int(c) for c in d["case_idx"]) | set(impact_t.keys())
    fallback_max = (max(seen_cases) + 1) if seen_cases else 0
    if info is not None:
        duration = info["duration_s"]
        total_n  = _count_total_cases(info, fallback_max)
        title    = (f"Survival timeline -- {total_n} cases, "
                    f"{len(impact_t)} impacted, "
                    f"{total_n - len(impact_t)} survived")
    else:
        # No TOML in sight: show impacted cases only, with a banner.
        duration = float(max((t for t in impact_t.values()), default=0.0))
        total_n  = fallback_max
        title    = (f"Survival timeline -- {len(impact_t)} impacted "
                    "(no input.toml found: survivors hidden)")

    if total_n == 0:
        ctx_missing_message(ax, "Survival timeline",
                             "No cases to plot.")
        return

    # Sort: impacted cases by t_impact ascending (earliest at top),
    # survivors at the bottom in case_idx order. Reads naturally.
    impacted = sorted(impact_t.items(), key=lambda kv: kv[1])
    survivors = sorted(i for i in range(total_n) if i not in impact_t)
    order = [ci for ci, _ in impacted] + survivors

    y_pos = np.arange(len(order))
    # x axis in days for parity with the time-to-impact histogram and
    # impact-map colorbar; matches what the user asked for ("non
    # secondi ma giorni").
    duration_days = duration / _SEC_PER_DAY
    widths_days   = np.array([impact_t.get(ci, duration) for ci in order]
                              ) / _SEC_PER_DAY
    is_impact_mask = np.array([ci in impact_t for ci in order], dtype=bool)

    # Up to ~200 cases we use matplotlib's `barh` because it gives
    # nice per-row Rectangle outlines and Excel-style readability;
    # above that the per-Rectangle artist overhead dominates the draw
    # path and a 9k-case batch freezes the GUI for ~20 s. Switch to a
    # single LineCollection (one Artist for the whole timeline) when
    # we cross the threshold -- 9577 cases then renders in well under
    # a second and the dropped Rectangle edges are invisible at that
    # density anyway. Per-row tick labels are also dropped because
    # 9k text objects are themselves slow to layout; we fall back to
    # numeric y-ticks every ~N/10 with a single 'case index' label.
    BAR_THRESHOLD = 200
    n_cases = len(order)
    if n_cases <= BAR_THRESHOLD:
        colors_bar = np.where(is_impact_mask, "tab:red", "tab:green")
        ax.barh(y_pos, widths_days, color=colors_bar,
                edgecolor="black", linewidth=0.3)
        ax.set_yticks(y_pos)
        ax.set_yticklabels([f"case {ci}" for ci in order], fontsize="x-small")
    else:
        red  = (0.84, 0.15, 0.16, 1.0)
        green = (0.17, 0.63, 0.17, 1.0)
        # One segment per case: (x0, y) -> (x1, y). LineCollection
        # accepts a list of (2, 2) arrays; we build the (N, 2, 2)
        # array directly with numpy for the no-Python-loop fast path.
        segs = np.empty((n_cases, 2, 2))
        segs[:, 0, 0] = 0.0
        segs[:, 1, 0] = widths_days
        segs[:, 0, 1] = y_pos
        segs[:, 1, 1] = y_pos
        seg_colors = np.where(is_impact_mask[:, None],
                              np.asarray(red),
                              np.asarray(green))
        lc = LineCollection(segs, colors=seg_colors, linewidths=1.0)
        ax.add_collection(lc)
        ax.set_xlim(0.0, max(duration_days, float(widths_days.max())) * 1.02)
        ax.set_ylim(-0.5, n_cases - 0.5)
        # Drop the y ticks entirely: a numeric value at row k would
        # show k (the rank in the impacted-first sort), NOT
        # order[k] (the real case_idx), which would mislead anyone
        # reading the value as a case label. The whole-N-cases
        # picture is what this view is good at; per-row lookup
        # belongs in the Table tab.
        ax.set_yticks([])
        ax.set_ylabel(f"{n_cases} cases  --  earliest impact at top, "
                      "survivors at bottom")
    ax.invert_yaxis()
    ax.set_xlabel("time [days]")
    if info is not None:
        # Solid divider at the sim end so survivors visually 'reach' it.
        ax.axvline(duration_days, color="black", linewidth=0.8,
                   linestyle="--", alpha=0.5)
    ax.set_title(title)
    ax.grid(True, axis="x", alpha=0.3)


def _plot_events_impact_map(ax: Axes, d: np.ndarray,
                            ctx: PlotContext) -> None:
    """Equirectangular scatter of impact points projected to lat/lon
    on the Moon's body-fixed Principal Axes frame. Points are coloured
    by time-of-flight (days from sim start) so a cloud's temporal
    decay shows up at a glance: dark blue = earliest impacts, red =
    latest. See `_compute_impact_latlon` for the projection pipeline."""
    title = f"Impact lat/lon on {ctx.central_body.name}"
    chk = _validate_impact_context(ax, d, ctx, title)
    if chk is None:
        return
    info, mask, n_imp = chk
    geom = _compute_impact_latlon(d, mask, info, ctx)
    if geom is None:
        ctx_missing_message(ax, title, "Could not open ephemeris file.")
        return
    lat_deg, lon_deg, t_days, _ = geom

    # Photographic background when the Moon texture is available.
    # NASA SVS files (and the GUI fallback path that points at them)
    # are equirectangular with the prime meridian at the centre
    # column, longitude going from -180 at the left edge to +180 at
    # the right. That matches `extent=[-180, 180, -90, 90]` with
    # `origin="upper"` directly -- no spatial transform needed.
    bg_ok = False
    if ctx.central_body_texture is not None and ctx.central_body_texture.is_file():
        try:
            import matplotlib.image as mpimg
            img = mpimg.imread(str(ctx.central_body_texture))
            ax.imshow(img, extent=[-180.0, 180.0, -90.0, 90.0],
                      origin="upper", aspect="equal",
                      interpolation="bilinear", alpha=0.85)
            bg_ok = True
        except Exception:  # noqa: BLE001 -- bad texture must not kill the plot
            pass

    sc = ax.scatter(lon_deg, lat_deg, c=t_days, cmap="turbo",
                    s=46, edgecolor="white" if bg_ok else "black",
                    linewidth=0.6, zorder=3)
    ax.set_xlim(-180, 180); ax.set_ylim(-90, 90)
    ax.set_xticks(np.arange(-180, 181, 30))
    ax.set_yticks(np.arange(-90,  91,  30))
    ax.set_aspect("equal", adjustable="box")
    bf_tag = ctx.central_body.bf_frame_name
    ax.set_xlabel(f"Longitude [deg, {bf_tag} frame]")
    ax.set_ylabel(f"Latitude [deg, {bf_tag} frame]")
    title = (f"Impact locations on {ctx.central_body.name} ({bf_tag} frame)  "
             f"--  {n_imp} impacts")
    if not bg_ok:
        title += "  (no texture)"
    ax.set_title(title)
    ax.grid(True, alpha=0.35 if bg_ok else 0.3,
            color="white" if bg_ok else None,
            linewidth=0.5 if bg_ok else 0.8)
    cb = ax.figure.colorbar(sc, ax=ax, label="time of flight [days]",
                            fraction=0.04, pad=0.02)
    cb.ax.tick_params(labelsize="x-small")


def _plot_events_impact_map_mollweide(ax: Axes, d: np.ndarray,
                                       ctx: PlotContext) -> None:
    """Mollweide-projected lat/lon scatter -- same data as the
    equirectangular view but on the equal-area ellipse so the surface
    areas near the poles aren't distorted. The Moon background is
    drawn in grayscale so the time-of-flight colormap on top reads
    without competing with the photo's brown/grey palette."""
    title = "Impact lat/lon (Mollweide)"
    chk = _validate_impact_context(ax, d, ctx, title)
    if chk is None:
        return
    info, mask, n_imp = chk
    geom = _compute_impact_latlon(d, mask, info, ctx)
    if geom is None:
        ctx_missing_message(ax, title, "Could not open ephemeris file.")
        return
    lat_deg, lon_deg, t_days, _ = geom

    bg_ok = _draw_mollweide_body_background(ax, ctx, alpha=0.85)
    sc = ax.scatter(np.radians(lon_deg), np.radians(lat_deg),
                    c=t_days, cmap="turbo",
                    s=60, edgecolor="white" if bg_ok else "black",
                    linewidth=0.7, zorder=3)
    ax.grid(True, alpha=0.4, color="white" if bg_ok else "gray",
            linewidth=0.4)
    # Mollweide axes show their own canonical lat/lon labels; turn
    # off the matplotlib auto-set ones for clarity.
    ax.set_xlabel(""); ax.set_ylabel("")
    bf_tag = ctx.central_body.bf_frame_name
    title_text = (f"Impact locations on {ctx.central_body.name} "
                  f"({bf_tag} frame, Mollweide)  --  {n_imp} impacts")
    if not bg_ok:
        title_text += "  (no texture)"
    ax.set_title(title_text)
    cb = ax.figure.colorbar(sc, ax=ax, label="time of flight [days]",
                            orientation="horizontal", pad=0.06,
                            fraction=0.04)
    cb.ax.tick_params(labelsize="x-small")


def _plot_events_impact_density(ax: Axes, d: np.ndarray,
                                 ctx: PlotContext) -> None:
    """Mollweide-projected 2D histogram of impact lat/lon: how the
    fragments distribute across the lunar surface, integrated over
    the whole batch. Each cell is `n_impacts_in_that_bin`; empty cells
    are transparent so the grayscale Moon shows through. Default
    binning is 10 degrees (36x18 cells); fine enough for a debris
    cloud of a few hundred fragments, coarse enough that single
    impacts still produce a visible coloured cell."""
    title = "Impact density heatmap"
    chk = _validate_impact_context(ax, d, ctx, title)
    if chk is None:
        return
    info, mask, n_imp = chk
    geom = _compute_impact_latlon(d, mask, info, ctx)
    if geom is None:
        ctx_missing_message(ax, title, "Could not open ephemeris file.")
        return
    lat_deg, lon_deg, _, _ = geom

    _draw_mollweide_body_background(ax, ctx, alpha=0.6)

    # 2.5-degree cells -- 4x denser than the original 10-deg default,
    # per user request. With a few thousand fragments the heatmap
    # starts looking continuous; for a 10-impact demo it stays sparse
    # but each filled cell is small enough not to mask the texture.
    n_lon, n_lat = 144, 72
    hist, lon_edges, lat_edges = np.histogram2d(
        lon_deg, lat_deg, bins=[n_lon, n_lat],
        range=[[-180.0, 180.0], [-90.0, 90.0]])
    # Hide empty cells so the photo backdrop stays readable; the
    # turbo gradient then runs from 1 (rarest filled cell) to the
    # max count, which keeps low/high-density spots distinguishable
    # even at small batch sizes.
    hist_masked = np.ma.masked_where(hist == 0, hist)
    Lon, Lat = np.meshgrid(np.radians(lon_edges), np.radians(lat_edges))
    pm = ax.pcolormesh(Lon, Lat, hist_masked.T, cmap="turbo",
                       shading="flat", alpha=0.85, zorder=2,
                       vmin=1, vmax=max(1.0, float(hist.max())))
    ax.grid(True, alpha=0.4, color="white", linewidth=0.4)
    ax.set_xlabel(""); ax.set_ylabel("")
    # Compute the per-cell angular size so the title carries the
    # physically meaningful number, not the raw bin counts.
    bin_lon_deg = 360.0 / n_lon
    bin_lat_deg = 180.0 / n_lat
    ax.set_title(f"Cumulative impact density (PA frame, Mollweide)  "
                 f"--  {n_imp} impacts, "
                 f"{bin_lon_deg:g}° x {bin_lat_deg:g}° bins")
    cb = ax.figure.colorbar(pm, ax=ax, label="# impacts per cell",
                            orientation="horizontal", pad=0.06,
                            fraction=0.04)
    cb.ax.tick_params(labelsize="x-small")


def _plot_events_impact_3d(canvas: VtkCanvas, d: np.ndarray,
                           ctx: PlotContext) -> None:
    """3D view of the central body's surface with one small sphere per
    impact placed in the body-fixed frame. Shares the lat/lon
    projection pipeline with `_plot_events_impact_map`: same et-shift,
    ICRF -> body-fixed rotation (via
    `ctx.central_body.bf_orientation`); the points are then rendered
    as physical small spheres on the textured body instead of a 2D
    scatter.

    Marker colours mirror the 2D map's `time of flight [days]`
    turbo lookup so the same colour means the same impact time
    across the two views (no in-scene colorbar -- VTK does not have
    a comfortable equivalent; the 2D Mollweide / equirect maps
    surface the legend).

    Body-agnostic via the CentralBodySpec orientation callback;
    marker radius scales with the body's mean radius so the
    physical-30-km Moon look maps to a proportionally-sized marker
    on Earth or any other registered body."""
    body = ctx.central_body
    body_args = dict(texture_path=ctx.central_body_texture,
                     radius_km=body.radius_km)
    if "case_idx" not in d.dtype.names:
        # Renderless wrong-format guard: draw just the body so the
        # 3D canvas doesn't look broken.
        canvas.add_central_body(**body_args)
        return
    info = resolve_run_context(ctx.path)
    if (info is None or info["ephemeris_path"] is None
            or body.bf_orientation is None):
        canvas.add_central_body(**body_args)
        return

    mask = d["kind"] == EVENT_KIND_IMPACT
    n_imp = int(mask.sum())
    if n_imp == 0:
        canvas.add_central_body(**body_args)
        return

    from spopy import Ephemeris
    try:
        eph = Ephemeris(str(info["ephemeris_path"]))
    except (OSError, ValueError):
        canvas.add_central_body(**body_args)
        return

    canvas.add_central_body(**body_args)

    et_start = info["et_start_s"]
    t_sim    = d["t"][mask]
    y_state  = d["y"][mask]
    r_icrf   = y_state[:, 0:3]

    # Frame triads -- drawn before the impact markers so the small
    # markers paint on top (z-fighting bias). Same convention as
    # every other 3D plot: body-fixed bright, ICRF muted (see
    # `add_reference_triads`). The scene IS the body-fixed frame,
    # so R_at_start is the ICRF -> BF rotation at the start epoch
    # which positions the ICRF triad inside the body-fixed scene.
    R_at_start = body.bf_orientation(et_start, eph)
    add_reference_triads(canvas, scene_frame="bf",
                           R_icrf_to_bf=R_at_start,
                           radius_km=body.radius_km,
                           bf_frame_label=body.bf_frame_name)

    # Build the time-of-flight colour lookup once and vectorise the
    # per-impact rotation. Each impact uses its own
    # R_icrf_to_bf(et_start + t_sim[i]) -- body orientation evolves
    # (e.g. ~1-day scale for lunar libration, ~1-rev/day for Earth
    # GMST), so we can NOT precompute a single R, but the per-impact
    # rotation itself is a cheap 3x3 matmul and stays well below the
    # cost of the old 9000-actor draw call. The markers themselves
    # ship through `add_points` as a single GPU-instanced actor
    # instead of a vtkSphereSource + actor per point, which on the
    # 9577-case LRO debris run drops the render-time freeze (~10s)
    # to a single-frame redraw.
    t_days = t_sim.astype(float) / _SEC_PER_DAY
    t_lo, t_hi = float(t_days.min()), float(t_days.max())
    span = max(t_hi - t_lo, 1e-9)
    cmap = mpl_colormaps["turbo"]
    r_bf_arr = np.empty_like(r_icrf)
    for i in range(n_imp):
        r_bf_arr[i] = body.bf_orientation(
            et_start + float(t_sim[i]), eph) @ r_icrf[i]
    # cmap accepts an array of fracs and returns an (N, 4) RGBA
    # in [0..1]. Slice off the alpha; the mapper's per-point uchar
    # array is RGB-only. Marker radius scales with the body radius
    # (Moon 30 km ~ 1.7% of R_moon; same fraction on Earth -> ~110 km).
    rgba = cmap((t_days - t_lo) / span)
    marker_km = 0.017 * body.radius_km
    canvas.add_points(r_bf_arr, rgba[:, :3], radius_km=marker_km)


# ======================================================================
# Altitude-band occupancy plots
# ======================================================================
# All four are `mode="context"` (they need the run snapshot's
# thresholds / stop actions / duration) and HF-only (the reconstruction
# measures altitude from the central body; the CR3BP synodic frame has
# no comparable body-fixed altitude). They degrade to a message when
# the file carries no central-body altitude crossings, exactly like the
# impact views do for a file with no IMPACT rows. The band maths lives
# in `analysis/altitude_bands.py`; these functions only draw.

def _band_colors(n: int) -> list:
    """One colour per band from a sequential map so the visual order
    (dark = low altitude, bright = high) matches the physical order."""
    cmap = mpl_colormaps["viridis"]
    return [cmap(i / max(1, n - 1)) for i in range(n)]


def _band_inputs_for(ctx: "PlotContext") -> tuple:
    """(thresholds, stop_thresholds, duration_s) from the run snapshot
    for the context's central body; empty/None when no snapshot."""
    info = resolve_run_context(ctx.path)
    if info is not None:
        return band_inputs_from_snapshot(info, ctx.central_body.name)
    return [], [], None


def _plot_bands_time(ax: Axes, d: np.ndarray, ctx: "PlotContext") -> None:
    """Horizontal bar per altitude band = time spent in the band (total
    for a single run, object-time summed over cases in batch). Lowest
    band at the bottom so the axis reads like an altitude axis."""
    title = f"Time per altitude band ({ctx.central_body.name})"
    thresholds, stop, duration = _band_inputs_for(ctx)
    res = analyze_altitude_bands(d, ctx.central_body.naif_id,
                                 thresholds_km=thresholds,
                                 stop_thresholds_km=stop, duration_s=duration)
    if res is None:
        ctx_missing_message(
            ax, title, "No central-body altitude-crossing events here.")
        return
    is_batch = "case_idx" in d.dtype.names
    labels = band_edge_labels(res.thresholds_km)
    div, unit = _time_axis(res.window_s)
    colors = _band_colors(len(res.bands))
    y = np.arange(len(res.bands))
    vals = np.array([b.total_time_s for b in res.bands]) / div
    ax.barh(y, vals, color=colors, edgecolor="black", linewidth=0.4)
    for i, b in enumerate(res.bands):
        if b.total_time_s <= 0.0:
            continue
        if is_batch:
            txt = f"{vals[i]:.4g}"
        else:
            pct = (100.0 * b.total_time_s / res.window_s
                   if res.window_s > 0 else 0.0)
            txt = f"{pct:.3g}%"
        ax.text(vals[i], i, "  " + txt, va="center", fontsize="small")
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    xlabel = f"time in band [{unit}]"
    if is_batch:
        xlabel += "  (object-time, summed over cases)"
    ax.set_xlabel(xlabel)
    ax.set_title(title + (f"  --  {res.n_objects} objects" if is_batch else ""))
    ax.grid(True, axis="x", alpha=0.3)


def _plot_bands_gantt(ax: Axes, d: np.ndarray, ctx: "PlotContext") -> None:
    """Occupancy timeline for a single object: one row per band, a
    coloured bar for every interval the object spends in that band.
    Reads as 'which altitude band, when' at a glance."""
    title = f"Band occupancy timeline ({ctx.central_body.name})"
    thresholds, stop, duration = _band_inputs_for(ctx)
    seg = altitude_band_segments(d, ctx.central_body.naif_id,
                                 thresholds_km=thresholds,
                                 stop_thresholds_km=stop, duration_s=duration)
    if seg is None:
        ctx_missing_message(
            ax, title, "No central-body altitude-crossing events here.")
        return
    labels = band_edge_labels(seg.thr)
    colors = _band_colors(len(labels))
    div, unit = _time_axis(seg.window_s)
    objs = np.unique(seg.obj)
    obj0 = int(objs[0])                # per-run file -> the only object
    m = seg.obj == obj0
    for b, s, e in zip(seg.band[m], seg.start[m], seg.end[m]):
        ax.barh(int(b), (e - s) / div, left=s / div, height=0.6,
                color=colors[int(b)], edgecolor="black", linewidth=0.3)
    ax.set_yticks(range(len(labels)))
    ax.set_yticklabels(labels)
    ax.set_ylabel("altitude band")
    ax.set_xlabel(f"t [{unit}]")
    note = (f"  ({len(objs)} objects; showing case {obj0})"
            if len(objs) > 1 else "")
    ax.set_title(title + note)
    ax.grid(True, axis="x", alpha=0.3)


def _plot_bands_population(ax: Axes, d: np.ndarray, ctx: "PlotContext") -> None:
    """Batch: stacked step-area of how many objects sit in each band
    over time. The stack height at t is the number of objects still
    alive (not yet impacted / stopped); each colour band is that band's
    instantaneous population -- the time-integral is the Info tab's
    'population mean'."""
    title = f"Band population over time ({ctx.central_body.name})"
    thresholds, stop, duration = _band_inputs_for(ctx)
    seg = altitude_band_segments(d, ctx.central_body.naif_id,
                                 thresholds_km=thresholds,
                                 stop_thresholds_km=stop, duration_s=duration)
    if seg is None:
        ctx_missing_message(
            ax, title, "No central-body altitude-crossing events here.")
        return
    n_bands = len(seg.thr) + 1
    labels = band_edge_labels(seg.thr)
    colors = _band_colors(n_bands)

    # Common time grid = every segment boundary. Per band, +1 at each
    # segment start node and -1 at each end node, then cumsum gives the
    # population step function -- vectorised (the only loop is over the
    # few bands, never over the potentially millions of segments).
    grid = np.unique(np.concatenate(
        [[0.0], seg.start, seg.end, [seg.window_s]]))
    div, unit = _time_axis(seg.window_s)
    x = grid / div
    base = np.zeros(len(grid))
    for b in range(n_bands):
        m = seg.band == b
        delta = np.zeros(len(grid))
        if m.any():
            np.add.at(delta, np.searchsorted(grid, seg.start[m]), 1.0)
            np.add.at(delta, np.searchsorted(grid, seg.end[m]), -1.0)
        pop = np.cumsum(delta)          # population on the cell at each node
        top = base + pop
        ax.fill_between(x, base, top, step="post", color=colors[b],
                        alpha=0.85, linewidth=0.0, label=labels[b])
        base = top
    ax.set_xlim(float(x[0]), float(x[-1]))
    ax.set_ylim(0, max(1, int(base.max())))
    ax.set_xlabel(f"t [{unit}]")
    ax.set_ylabel("objects in band")
    n_obj = int(np.unique(seg.obj).size)
    ax.set_title(f"{title}  --  {n_obj} objects")
    ax.legend(loc="upper right", fontsize="small", framealpha=0.6,
              title="altitude band")
    ax.grid(True, alpha=0.3)


def _plot_bands_heatmap(ax: Axes, d: np.ndarray, ctx: "PlotContext") -> None:
    """Batch: heatmap of time-in-band, rows = cases (ascending id),
    columns = bands. The visual companion of the per-element CSV
    export -- spot at a glance which cases live low vs high."""
    title = f"Per-case time in band ({ctx.central_body.name})"
    thresholds, stop, duration = _band_inputs_for(ctx)
    res = altitude_bands_per_object(d, ctx.central_body.naif_id,
                                    thresholds_km=thresholds,
                                    stop_thresholds_km=stop,
                                    duration_s=duration)
    if res is None:
        ctx_missing_message(
            ax, title, "No central-body altitude-crossing events here.")
        return
    thr, _from_snap, rows = res
    labels = band_edge_labels(thr)
    n_bands = len(labels)
    ids = [obj for (obj, _pb) in rows]
    times = np.array([[pb[b][0] for b in range(n_bands)]
                      for (_obj, pb) in rows], dtype=float)
    div, unit = _time_axis(float(times.max()) if times.size else 1.0)
    im = ax.imshow(times / div, aspect="auto", cmap="viridis",
                   origin="upper", interpolation="nearest")
    ax.set_xticks(range(n_bands))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize="small")
    if len(rows) <= 40:
        ax.set_yticks(range(len(rows)))
        ax.set_yticklabels([f"case {i}" for i in ids], fontsize="x-small")
    else:
        ax.set_yticks([])
        ax.set_ylabel(f"{len(rows)} cases  (case id ascending)")
    ax.set_xlabel("altitude band")
    ax.set_title(title)
    cb = ax.figure.colorbar(im, ax=ax, label=f"time in band [{unit}]",
                            fraction=0.046, pad=0.02)
    cb.ax.tick_params(labelsize="x-small")


_BAND_CAT = "Altitude bands"

SPECS_SINGLE: list[PlotSpec] = [
    PlotSpec("Events timeline",             "2d", _plot_events_timeline),
    PlotSpec("Events timeline (density)",   "2d", _plot_events_timeline_density),
    PlotSpec("Time per band",               "2d", _plot_bands_time,
             category=_BAND_CAT, mode="context",
             models=("high_fidelity",)),
    PlotSpec("Band occupancy timeline",     "2d", _plot_bands_gantt,
             category=_BAND_CAT, mode="context",
             models=("high_fidelity",)),
]

# Timeline goes first so a fresh load always shows something sensible
# even when the run-folder snapshot is missing (timeline + histogram
# are context-free; the impact views below need input.toml). Timeline
# + histogram + survival work for any dynamics model (the impact
# predicate fires on both HF central-body and CR3BP primary radii).
# The lat/lon / heatmap / 3D-on-body views project onto a body-fixed
# frame, which is HF-specific -- in CR3BP the synodic primary has no
# comparable body-fixed surface coordinate.
SPECS_BATCH: list[PlotSpec] = [
    PlotSpec("Events timeline",             "2d", _plot_events_timeline),
    PlotSpec("Events timeline (density)",   "2d", _plot_events_timeline_density),
    PlotSpec("Time-to-impact histogram",    "2d",
             _plot_events_time_to_impact_hist),
    PlotSpec("Survival timeline per case",  "2d",
             _plot_events_survival_timeline, mode="context"),
    PlotSpec("Impact lat/lon (equirect)",   "2d",
             _plot_events_impact_map,        mode="context",
             models=("high_fidelity",)),
    PlotSpec("Impact lat/lon (Mollweide)",  "2d",
             _plot_events_impact_map_mollweide,
             mode="context", projection="mollweide",
             models=("high_fidelity",)),
    PlotSpec("Impact density heatmap",      "2d",
             _plot_events_impact_density,
             mode="context", projection="mollweide",
             models=("high_fidelity",)),
    PlotSpec("Impact 3D on central body",   "3d",
             _plot_events_impact_3d,         mode="context",
             models=("high_fidelity",)),
    PlotSpec("Time per band",               "2d", _plot_bands_time,
             category=_BAND_CAT, mode="context",
             models=("high_fidelity",)),
    PlotSpec("Band population over time",    "2d", _plot_bands_population,
             category=_BAND_CAT, mode="context",
             models=("high_fidelity",)),
    PlotSpec("Per-case time in band",        "2d", _plot_bands_heatmap,
             category=_BAND_CAT, mode="context",
             models=("high_fidelity",)),
]
