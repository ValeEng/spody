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

from spody_io import EVENT_KIND_ECLIPSE, EVENT_KIND_IMPACT

from ..vtk_canvas import VtkCanvas
from .context import PlotContext, ctx_missing_message, resolve_run_context
from .scene3d import add_reference_triads
from .spec import PlotSpec


# Seconds to days for everywhere the events views surface time -- the
# events file stores t in seconds (consistent with the integrator and
# the events.h C struct), but at user-visible scales (days-long batch
# runs, multi-day debris-cloud decay) day-level axes read better.
_SEC_PER_DAY = 86400.0

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
    case_id  = d["case_idx"][mask].astype(int, copy=True)
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


def _plot_events_timeline(ax: Axes, d: np.ndarray) -> None:
    if len(d) == 0:
        ax.set_title("No events recorded"); ax.set_xlabel("t [s]"); return
    labels = {EVENT_KIND_IMPACT: "IMPACT", EVENT_KIND_ECLIPSE: "ECLIPSE"}
    colors = {EVENT_KIND_IMPACT: "tab:red", EVENT_KIND_ECLIPSE: "tab:blue"}
    kinds  = sorted(int(k) for k in np.unique(d["kind"]))
    for k in kinds:
        mask = d["kind"] == k
        ax.scatter(d["t"][mask], np.full(mask.sum(), k),
                   color=colors.get(k, "tab:gray"),
                   label=labels.get(k, f"kind {k}"),
                   marker="|", s=200)
    ax.set_yticks(kinds)
    ax.set_yticklabels([labels.get(k, str(k)) for k in kinds])
    ax.set_xlabel("t [s]")
    ax.set_title(f"Event timeline ({len(d)} triggers)")
    ax.grid(True, axis="x", alpha=0.3)


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


SPECS_SINGLE: list[PlotSpec] = [
    PlotSpec("Events timeline",             "2d", _plot_events_timeline),
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
]
