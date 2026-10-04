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

"""Monte Carlo views (`spody uncertainty montecarlo`, manual ch. 14).

Two file kinds:
  * SPDYUQM_ (moments): sigma, bias, RMS, correlations, curvilinear
    moments and the number of cases still flying, against time;
  * SPDYUQC_ (clouds): every case at the snapshots, in the nominal's
    RIC axes (and curvilinear), with the 3-sigma ellipse of the cloud.

RIC axes are the nominal's at each epoch, rotation only (the engine's
convention), from `spopy.rotations.icrf_to_ric`, the 1:1 twin of
spody_getrotmatrix_icrf2ric -- never a GUI-side copy. A clouds file
carries deviations only, so its views find the nominal state in the
moments file of the same run folder (mode="context").
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from matplotlib.axes import Axes

from spody_io import read_uq_moments
from spody_io.uq import lower_to_full
from spopy.rotations import icrf_to_ric

from .context import PlotContext, ctx_missing_message
from .derived import time_axis
from .spec import PlotSpec

_AXES = ("R", "I", "C")


def _time(ax: Axes, t: np.ndarray) -> tuple[np.ndarray, str]:
    div, label = time_axis(float(t[-1] - t[0]) if t.size > 1 else 0.0)
    return t / div, label


def _ric_moments(d: np.ndarray):
    """Per epoch: position / velocity covariance and the bias in the
    nominal's RIC axes. NaN where n < 2 (the file stores NaN there)."""
    k = len(d)
    Pp = np.full((k, 3, 3), np.nan)
    Pv = np.full((k, 3, 3), np.nan)
    b = np.full((k, 3), np.nan)
    C = lower_to_full(d["cov"], 6)
    for e in range(k):
        x = d["nominal"][e]
        try:
            R = icrf_to_ric(x[:3], x[3:])
        except ValueError:
            continue
        Pp[e] = R @ C[e, :3, :3] @ R.T
        Pv[e] = R @ C[e, 3:, 3:] @ R.T
        b[e] = R @ d["bias"][e, :3]
    return Pp, Pv, b


def _sig(P: np.ndarray) -> np.ndarray:
    return np.sqrt(np.diagonal(P, axis1=1, axis2=2))


def _finish(ax: Axes, unit: str, ylabel: str, title: str, log: bool) -> None:
    if log:
        ax.set_yscale("log")
    ax.set_xlabel(f"t [{unit}]"); ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(loc="best", fontsize="small")
    ax.grid(True, which="both", alpha=0.3)


def _plot_sigma_pos(ax: Axes, d: np.ndarray) -> None:
    Pp, _, _ = _ric_moments(d)
    t, unit = _time(ax, d["t"])
    for i, s in enumerate(_sig(Pp).T):
        ax.plot(t, s * 1e3, label=f"σ {_AXES[i]}")
    _finish(ax, unit, "σ [m]", "Position σ in the nominal's RIC axes", True)


def _plot_sigma_vel(ax: Axes, d: np.ndarray) -> None:
    _, Pv, _ = _ric_moments(d)
    t, unit = _time(ax, d["t"])
    for i, s in enumerate(_sig(Pv).T):
        ax.plot(t, s * 1e6, label=f"σ v{_AXES[i]}")
    _finish(ax, unit, "σ [mm/s]", "Velocity σ in the nominal's RIC axes", True)


def _plot_bias(ax: Axes, d: np.ndarray) -> None:
    Pp, _, b = _ric_moments(d)
    t, unit = _time(ax, d["t"])
    se = _sig(Pp) / np.sqrt(np.maximum(d["n"], 1.0))[:, None]
    for i in range(3):
        line, = ax.plot(t, b[:, i] * 1e3, label=f"bias {_AXES[i]}")
        # +-2 standard errors of the mean: a bias inside the band is
        # not distinguishable from sampling noise.
        ax.fill_between(t, -2e3 * se[:, i], 2e3 * se[:, i],
                        color=line.get_color(), alpha=0.12)
    ax.axhline(0.0, color="0.5", lw=0.8)
    _finish(ax, unit, "mean − nominal [m]",
            "Bias of the cloud (band: ±2 standard errors of the mean)", False)


def _plot_rms(ax: Axes, d: np.ndarray) -> None:
    Pp, _, b = _ric_moments(d)
    t, unit = _time(ax, d["t"])
    n = d["n"]
    w = np.where(n > 0, (n - 1) / np.maximum(n, 1), np.nan)
    for i in range(3):
        rms = np.sqrt(Pp[:, i, i] * w + b[:, i] ** 2)
        ax.plot(t, rms * 1e3, label=f"RMS {_AXES[i]}")
    _finish(ax, unit, "RMS [m]", "RMS about the nominal (σ and bias together)", True)


def _plot_corr(ax: Axes, d: np.ndarray) -> None:
    Pp, _, _ = _ric_moments(d)
    t, unit = _time(ax, d["t"])
    s = _sig(Pp)
    for (i, j) in ((0, 1), (0, 2), (1, 2)):
        ax.plot(t, Pp[:, i, j] / (s[:, i] * s[:, j]),
                label=f"ρ {_AXES[i]}{_AXES[j]}")
    ax.set_ylim(-1.05, 1.05)
    _finish(ax, unit, "correlation", "Position correlations (RIC)", False)


def _plot_curvilinear(ax: Axes, d: np.ndarray) -> None:
    Pp, _, _ = _ric_moments(d)
    t, unit = _time(ax, d["t"])
    Cc = lower_to_full(d["curv_cov"], 3)
    sc = np.sqrt(np.diagonal(Cc, axis1=1, axis2=2))
    sr = _sig(Pp)
    # RIC as a wide pale band underneath, curvilinear as a thin line on
    # top: where the two agree (a cloud much shorter than the orbit
    # radius) the line sits inside the band instead of hiding it.
    for i in range(3):
        band, = ax.plot(t, sr[:, i] * 1e3, lw=5.0, alpha=0.3,
                        label=f"RIC σ {_AXES[i]}")
        ax.plot(t, sc[:, i] * 1e3, lw=1.1, color=band.get_color(),
                label=f"curvilinear σ {_AXES[i]}")
    _finish(ax, unit, "σ [m]",
            "Curvilinear vs straight RIC σ (they part where the cloud bends)", True)


def _plot_alive(ax: Axes, d: np.ndarray) -> None:
    t, unit = _time(ax, d["t"])
    ax.plot(t, d["n"], drawstyle="steps-post")
    ax.set_ylim(bottom=0)
    ax.set_xlabel(f"t [{unit}]"); ax.set_ylabel("n")
    ax.set_title("Cases with a state at t (they stop counting at impact)")
    ax.grid(True, alpha=0.3)


# ----------------------------------------------------------------------
# Clouds
# ----------------------------------------------------------------------

def nominal_at(clouds_path: Path, times: np.ndarray):
    """Nominal states at the snapshot times, from the moments file next
    to the clouds file. None when it is missing or does not hold them."""
    found = sorted(Path(clouds_path).parent.glob("*_moments.uq.bin"))
    if not found:
        return None
    mom, _ = read_uq_moments(found[0])
    out = {}
    for t in np.unique(times):
        k = np.where(mom["t"] == t)[0]
        if k.size == 0:
            return None
        out[float(t)] = mom["nominal"][k[0]]
    return out


def cloud_points(d: np.ndarray, clouds_path, curvilinear: bool):
    """{snapshot t: (n, 3) RIC or curvilinear position deviations [m]}."""
    nominal = nominal_at(clouds_path, d["t"]) if clouds_path is not None else None
    if nominal is None:
        return None
    out = {}
    for t, xn in nominal.items():
        sel = d["t"] == t
        dev = d["d"][sel, :3]
        R = icrf_to_ric(xn[:3], xn[3:])
        if not curvilinear:
            out[t] = dev @ R.T * 1e3
            continue
        # Same definition as the engine's moments (Vallado & Alfano):
        # radius difference, arc in the nominal plane, arc out of it.
        r = xn[:3] + dev
        rn = np.linalg.norm(xn[:3])
        rr = np.linalg.norm(r, axis=1)
        p = r @ R.T
        out[t] = np.column_stack([rr - rn, rn * np.arctan2(p[:, 1], p[:, 0]),
                                  rn * np.arcsin(p[:, 2] / rr)]) * 1e3
    return out


def _ellipse(ax: Axes, pts: np.ndarray, color) -> None:
    """3-sigma ellipse of the points' own sample covariance."""
    if len(pts) < 3:
        return
    c = np.cov(pts, rowvar=False)
    w, v = np.linalg.eigh(c)
    a = np.linspace(0.0, 2.0 * np.pi, 200)
    circle = np.column_stack([np.cos(a), np.sin(a)]) * 3.0 * np.sqrt(np.maximum(w, 0.0))
    xy = circle @ v.T + pts.mean(axis=0)
    ax.plot(xy[:, 0], xy[:, 1], color=color, lw=1.0)


def _make_cloud(i: int, j: int, curvilinear: bool):
    kind = "curvilinear" if curvilinear else "RIC"
    title = f"Cloud {_AXES[i]}–{_AXES[j]} ({kind}, nominal at 0, 3σ ellipse)"

    def plot(ax: Axes, d: np.ndarray, ctx: "PlotContext | None" = None) -> None:
        clouds = cloud_points(d, ctx.path if ctx is not None else None, curvilinear)
        if clouds is None:
            ctx_missing_message(
                ax, title,
                "The nominal state at the snapshots comes from the\n"
                "moments file (*_moments.uq.bin) of the same run folder,\n"
                "which is missing or does not hold these epochs.")
            return
        for t, pts in clouds.items():
            div, unit = time_axis(t)
            sc = ax.scatter(pts[:, i], pts[:, j], s=6, alpha=0.6,
                            label=f"t = {t / div:g} {unit}  (n = {len(pts)})")
            _ellipse(ax, pts[:, [i, j]], sc.get_facecolor()[0])
        ax.plot(0.0, 0.0, "k+", ms=12, mew=1.5)
        ax.set_xlabel(f"{_AXES[i]} [m]"); ax.set_ylabel(f"{_AXES[j]} [m]")
        ax.set_title(title)
        ax.legend(loc="best", fontsize="small")
        ax.grid(True, alpha=0.3)
    return plot


# Snapshot colours for the 3D view (matplotlib's default cycle, as in
# the 2D clouds, so a snapshot has one colour in every view).
_SNAP_RGB = [(0.122, 0.467, 0.706), (1.000, 0.498, 0.055), (0.173, 0.627, 0.173),
             (0.839, 0.153, 0.157), (0.580, 0.404, 0.741), (0.549, 0.337, 0.294),
             (0.890, 0.467, 0.761), (0.498, 0.498, 0.498), (0.737, 0.741, 0.133),
             (0.090, 0.745, 0.812)]


def plain(x: float, digits: int = 4) -> str:
    """A number without scientific notation: `digits` significant
    figures, trailing zeros trimmed (13950, 46.94, 0.03589)."""
    if x is None or not np.isfinite(x):
        return "-"
    return np.format_float_positional(float(x), precision=digits, unique=False,
                                      fractional=False, trim="-")


class CloudView:
    """What the 3D cloud shows, set from its options bar."""
    coords = "ric"            # "ric" | "curvilinear"
    snapshot = -1             # index into the file's snapshots, -1 = all
    point_size = 1.0          # relative marker size
    snapshots: list = []      # filled by the plot for the bar's combo
    on_snapshots = None       # bar callback to refresh its combo


# Fraction of a 3D Gaussian outside its 3-sigma ellipsoid: the chi-square
# (3 dof) tail at 9, erfc(3/sqrt 2) + sqrt(2/pi) * 3 * e^(-9/2) = 2.93 %.
_OUT_3SIGMA_3D = math.erfc(3.0 / math.sqrt(2.0)) + math.sqrt(2.0 / math.pi) * 3.0 * math.exp(-4.5)

CLOUD_3D_NOTE = (
    "Each point is one Monte Carlo case at the chosen snapshot, placed in the "
    "radial / in-track / cross-track (RIC) axes of the nominal trajectory "
    "(case 0), which sits at the origin. In curvilinear coordinates the "
    "in-track and cross-track components are arc lengths along the nominal "
    "orbit instead of straight-line distances.\n\n"
    "The three components differ by orders of magnitude (in-track errors grow "
    "to kilometres, radial and cross-track stay at tens of metres), so each "
    "axis is stretched by its own standard deviation: one unit along R, I or C "
    "is one sigma of the points shown, worth the number of metres given in the "
    "legend. Without correlations the cloud would look like a round ball; an "
    "elongated or tilted cloud shows correlated errors.\n\n"
    "The sigmas are computed over all the points on screen. With \"all "
    "snapshots\" they come from the clouds together, dominated by the latest "
    "and widest one, so the earlier clouds look small: that is the scale, not "
    "an error. Pick a single snapshot to see that cloud at its own scale.\n\n"
    "The R, I, C arrows start at the nominal and are 3 sigma long. The cloud's "
    "centre is off the origin when the cases are biased (drag, for instance, "
    "running them ahead of the nominal): that offset is the bias of the "
    "\"Centre and error\" plots.\n\n"
    "Each translucent shell is the 3-sigma ellipsoid of one snapshot's cloud, "
    "centred on its mean and built from its full covariance, correlations "
    "included. For a Gaussian cloud %.1f %% of the cases fall outside it (in "
    "three dimensions 3 sigma encloses %.1f %%, not the 99.7 %% of one "
    "dimension). Many more cases outside means the cloud is not Gaussian, "
    "typically because the dynamics curl it into a banana along the orbit; the "
    "curvilinear coordinates straighten that shape."
) % (100.0 * _OUT_3SIGMA_3D, 100.0 * (1.0 - _OUT_3SIGMA_3D))


def _plot_cloud_3d(canvas, d: np.ndarray, ctx: "PlotContext | None" = None) -> None:
    """The cases at the snapshots as points in the nominal's RIC (or
    curvilinear) axes, origin = the nominal, each axis stretched so
    one unit is one standard deviation of the points shown, with the
    3-sigma ellipsoid of each snapshot's cloud. A dedicated scene with
    its own options bar (`cloud_options_bar`); `CLOUD_3D_NOTE` is the
    reading guide the bar opens."""
    v = CloudView
    clouds = cloud_points(d, ctx.path if ctx is not None else None,
                          v.coords == "curvilinear")
    if not clouds:
        return
    times = list(clouds.keys())
    if times != v.snapshots:
        v.snapshots = times
        if v.snapshot >= len(times):
            v.snapshot = -1
        if v.on_snapshots is not None:
            v.on_snapshots(times)
    shown = [(k, t) for k, t in enumerate(times) if v.snapshot in (-1, k)]
    scale = np.vstack([clouds[t] for _, t in shown]).std(axis=0)
    scale[scale == 0.0] = 1.0
    legend = []
    for k, t in shown:
        pts = clouds[t] / scale
        rgb = _SNAP_RGB[k % len(_SNAP_RGB)]
        canvas.add_points(pts, np.tile(rgb, (len(pts), 1)), radius_km=0.04 * v.point_size)
        div, unit = time_axis(t)
        label = f"t = {t / div:g} {unit}: {len(pts)} cases"
        if len(pts) > 3:
            mean = pts.mean(axis=0)
            cov = np.cov(pts, rowvar=False)
            w, vec = np.linalg.eigh(cov)
            canvas.add_ellipsoid(mean, vec * (3.0 * np.sqrt(np.clip(w, 0.0, None))),
                                 color=rgb, opacity=0.12)
            dev = pts - mean
            d2 = np.einsum("ij,jk,ik->i", dev, np.linalg.pinv(cov), dev)
            out = int(np.count_nonzero(d2 > 9.0))
            label += f", {out} outside 3 sigma ({plain(100.0 * out / len(pts), 2)} %)"
        legend.append((label, rgb))
    canvas.add_frame_triad(3.0, labels_xyz=("R", "I", "C"))
    grey = (0.85, 0.85, 0.85)
    kind = "curvilinear" if v.coords == "curvilinear" else "RIC"
    legend.append((f"{kind} axes of the nominal, at the origin", grey))
    for i, ax in enumerate("RIC"):
        legend.append((f"{ax}: 1 unit = 1 sigma = {plain(scale[i])} m", grey))
    legend.append(("arrows 3 sigma, shells 3 sigma ellipsoids", grey))
    legend.append((f"Gaussian: {plain(100.0 * _OUT_3SIGMA_3D, 2)} % outside", grey))
    canvas.add_legend(legend, max_label_chars=60)


def cloud_options_bar(replot):
    """Options bar of the 3D cloud (PlotSpec.options_bar)."""
    from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QHBoxLayout,
                                   QLabel, QMessageBox, QPushButton, QWidget)
    v = CloudView
    bar = QWidget()
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(4, 2, 4, 2)

    coords = QComboBox()
    coords.addItems(["RIC", "curvilinear"])
    coords.setCurrentIndex(1 if v.coords == "curvilinear" else 0)
    coords.currentIndexChanged.connect(
        lambda i: (setattr(v, "coords", "curvilinear" if i else "ric"), replot()))

    snap = QComboBox()

    def fill(times):
        snap.blockSignals(True)
        snap.clear()
        snap.addItem("all snapshots")
        for t in times:
            div, unit = time_axis(t)
            snap.addItem(f"t = {t / div:g} {unit}")
        snap.setCurrentIndex(v.snapshot + 1)
        snap.blockSignals(False)
    fill(v.snapshots)
    v.on_snapshots = fill
    snap.currentIndexChanged.connect(
        lambda i: (setattr(v, "snapshot", i - 1), replot()))

    size = QDoubleSpinBox()
    size.setRange(0.2, 5.0)
    size.setSingleStep(0.2)
    size.setValue(v.point_size)
    size.valueChanged.connect(lambda x: (setattr(v, "point_size", x), replot()))

    for label, w in (("Coordinates", coords), ("Show", snap), ("Point size", size)):
        lay.addWidget(QLabel(label))
        lay.addWidget(w)
    lay.addStretch(1)

    about = QPushButton("How to read this view")
    about.clicked.connect(
        lambda: QMessageBox.information(bar, "3D cloud: how to read it", CLOUD_3D_NOTE))
    lay.addWidget(about)
    return bar


_HF = ("high_fidelity",)
_CAT_SIGMA = "Spread"
_CAT_CENTRE = "Centre and error"

SPECS_MOMENTS: list[PlotSpec] = [
    PlotSpec("Position σ R/I/C (log y)", "2d", _plot_sigma_pos,
             category=_CAT_SIGMA, models=_HF),
    PlotSpec("Velocity σ R/I/C (log y)", "2d", _plot_sigma_vel,
             category=_CAT_SIGMA, models=_HF),
    PlotSpec("Curvilinear vs RIC σ (log y)", "2d", _plot_curvilinear,
             category=_CAT_SIGMA, models=_HF),
    PlotSpec("Position correlations", "2d", _plot_corr,
             category=_CAT_SIGMA, models=_HF),
    PlotSpec("Bias R/I/C", "2d", _plot_bias,
             category=_CAT_CENTRE, models=_HF),
    PlotSpec("RMS about the nominal (log y)", "2d", _plot_rms,
             category=_CAT_CENTRE, models=_HF),
    PlotSpec("Cases alive n(t)", "2d", _plot_alive,
             category=_CAT_CENTRE, models=_HF),
]

SPECS_CLOUDS: list[PlotSpec] = [
    PlotSpec("Cloud I–R (in-track vs radial)", "2d", _make_cloud(1, 0, False), mode="context",
             category="RIC", models=_HF),
    PlotSpec("Cloud I–C (in-track vs cross-track)", "2d", _make_cloud(1, 2, False), mode="context",
             category="RIC", models=_HF),
    PlotSpec("Cloud C–R (cross-track vs radial)", "2d", _make_cloud(2, 0, False), mode="context",
             category="RIC", models=_HF),
    PlotSpec("Cloud I–R curvilinear", "2d", _make_cloud(1, 0, True), mode="context",
             category="Curvilinear", models=_HF),
    PlotSpec("Cloud 3D", "3d", _plot_cloud_3d, mode="context",
             category="3D", models=_HF, options_bar=cloud_options_bar),
]
