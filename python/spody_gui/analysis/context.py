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

"""PlotContext: the run-scoped information a plot function may need.

Resolved once per loaded file from the run folder's input.toml
snapshot (epoch, ephemeris path, central body, dynamics model, ...).
This is the stable interface between the panel and every plot module:
new context needs for future views are added HERE, not threaded
through the panel.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from matplotlib.axes import Axes

from ..central_bodies import CentralBodySpec, default_central_body
from ..scene_options import SceneOptions
from ..toml_io import read_toml


# ----------------------------------------------------------------------
# A handful of event plots (impact lat/lon map, survival timeline) need
# information that isn't carried inside the events binary itself:
# - simulation.et_start_s         to convert sim time `t` to ET
# - simulation.duration_s         to know how long survivors lasted
# - ephemeris.file                to evaluate Moon libration angles
# - force_model.central_body      to sanity-check 'Moon' before lat/lon
# - batch.cases_file              to count the total cases (survivors)
#
# All four live in the per-run input.toml snapshot spody.exe drops
# inside the run folder (see spody_io_make_run_subdir in app_io.c).
# The plot dispatcher builds a PlotContext from the loaded file's path
# and hands it to the plot fn; the fn calls resolve_run_context()
# which walks up to find input.toml and parses out what it needs.

@dataclass(frozen=True)
class CR3BPPrimary:
    """Fixed-position primary in the synodic CR3BP scene. Built by
    the analysis panel from the loaded run's [cr3bp] section + the
    Earth-Moon (or any future pair's) reference distance in
    spody_const.h. The two primaries are visualised as static spheres
    at the synodic positions (-mu/(mu1+mu2)*L, 0, 0) and
    (+mu1/(mu1+mu2)*L, 0, 0)."""
    name:        str
    position_km: tuple[float, float, float]
    radius_km:   float
    mu_km3_s2:   float


@dataclass(frozen=True)
class PlotContext:
    """Side-channel context passed to context-aware plot functions
    (PlotSpec.mode == 'context').

    `path`                : currently loaded file -- the plot fn walks
                            ancestors from here to locate the per-run
                            input.toml snapshot (for et_start_s,
                            ephemeris path, duration, cases_file).
    `central_body_texture`: equirectangular texture for the run's
                            central body, or None. 2D plots use it
                            as a lat/lon background; 3D plots forward
                            it to VtkCanvas. Resolved by the panel
                            via `assets.central_body_texture_path` for
                            the active body (Settings override is
                            consulted only for the legacy Moon case),
                            so plot fns never reach back into
                            QSettings.
    `scene_options`       : SceneOptions controlling what the 3D plot
                            draws (trajectory / triads / per-body
                            visibility). Defaults are 'show
                            everything'. The Scene options dialog
                            mutates the panel's SceneOptions in place
                            and the panel passes that instance
                            through here, so a re-render sees the
                            current toggles without any rebuild of
                            this context.
    `central_body`        : CentralBodySpec for the run's central
                            body (radius, NAIF id, body-fixed frame
                            name, orientation provider). Resolved
                            from the snapshot TOML's
                            `force_model.central_body` at file-load
                            time; falls back to the Moon default
                            when no snapshot is found. Plot fns use
                            it instead of hardcoding Moon constants
                            / labels.
    `dynamics_model`      : "high_fidelity" (default) or "cr3bp", read
                            from the snapshot's
                            `simulation.dynamics_model`. Plot fns
                            branch on this to render the right scene
                            (HF: single central body; CR3BP: two
                            primaries in the synodic frame).
    `cr3bp_primaries`     : Two CR3BPPrimary entries when
                            dynamics_model == "cr3bp", empty
                            otherwise. Drives the synodic 3D scene
                            geometry: primary positions, radii, and
                            display names.
    """
    path: Path
    central_body_texture: Path | None = None
    scene_options: SceneOptions = field(default_factory=SceneOptions)
    central_body: CentralBodySpec = field(default_factory=default_central_body)
    dynamics_model: str = "high_fidelity"
    cr3bp_primaries: tuple[CR3BPPrimary, ...] = ()
    # "icrf" (default) plots state vectors and Keplerian angles in the
    # central-body inertial frame; "bf" rotates the state into the
    # central body's body-fixed basis (Earth ITRS, Moon PA) via the
    # registered orientation provider. Magnitudes (|r|, |v|, a, e, i)
    # are frame-invariant; only angular elements (RAAN, AOP, ν) and
    # vector components (x/y/z, vx/vy/vz, orbit projections) change.
    # The selector lives in the Plot-options dialog; CR3BP runs and
    # bodies without a registered bf_orientation silently fall back
    # to "icrf" inside `_state_in_plot_frame`.
    plot_frame: str = "icrf"


def find_run_input_toml(events_path: Path) -> Path | None:
    """Walk up from `events_path` looking for the per-run input.toml
    snapshot.

    spody.exe writes one inside every run folder at launch. Modern
    runs name it `<ts>_input.toml` where `<ts>` is the parent
    folder's own timestamp (so editors can't conflate it with the
    sibling source `input.toml` up the tree); legacy runs (pre-
    timestamp-prefix) use plain `input.toml`. Both layouts are
    accepted so analysing old runs keeps working."""
    for parent in events_path.parents:
        candidate = parent / f"{parent.name}_input.toml"
        if candidate.is_file():
            return candidate
        candidate = parent / "input.toml"
        if candidate.is_file():
            return candidate
    return None


def resolve_ephemeris_path(eph_raw: str, toml_path: Path) -> Path | None:
    """Best-effort resolution of `[ephemeris].file` from a run-folder
    snapshot. The snapshot is a verbatim copy, so any relative path
    inside it was written against the *original* TOML's directory --
    NOT the snapshot's. Typical layout is
    `<project>/<example>/input.toml` with `<output_dir>/<run>/` two
    levels down, so the run folder's grandparent is usually where the
    user originally lived; we try a few candidates so the lookup
    survives most projects without asking the user.

    Returns the first existing path or None."""
    if not eph_raw:
        return None
    p = Path(eph_raw)
    if p.is_absolute() and p.is_file():
        return p
    candidates = [
        toml_path.parent / eph_raw,                  # inside run folder (rare)
        toml_path.parent.parent / eph_raw,           # one up: <output_dir>/
        toml_path.parent.parent.parent / eph_raw,    # two up: original TOML's dir
        Path.cwd() / eph_raw,                        # whatever cwd is now
    ]
    for c in candidates:
        try:
            r = c.resolve()
        except OSError:
            continue
        if r.is_file():
            return r
    return None


def resolve_run_context(events_path: Path) -> dict | None:
    """Parse the per-run input.toml sitting next to the events file
    and return the bits the impact-analysis plots need.

    Returns None when the snapshot is missing (the caller surfaces a
    user-facing hint inside the plot itself). Returns a dict otherwise:
        et_start_s     : float
        duration_s     : float
        ephemeris_path : Path | None
        central_body   : str
        cases_file     : Path | None  (resolved next to the snapshot)
        toml_path      : Path
    """
    toml_path = find_run_input_toml(events_path)
    if toml_path is None:
        return None
    try:
        cfg = read_toml(toml_path)
    except (OSError, ValueError):
        return None
    sim   = cfg.get("simulation",  {})
    force = cfg.get("force_model", {})
    eph   = cfg.get("ephemeris",   {})
    batch = cfg.get("batch",       {})
    cases_raw = batch.get("cases_file", "")
    cases_path: Path | None = None
    if cases_raw:
        # The cases CSV is read from the TOML's directory; spody.exe
        # copies only input.toml into the run folder, not the CSV, so
        # we try the snapshot dir first (in case the user copied it
        # by hand) and then walk up to where the original TOML lived
        # (same candidate ladder as ephemeris resolution).
        for cand_base in (toml_path.parent,
                          toml_path.parent.parent,
                          toml_path.parent.parent.parent,
                          Path.cwd()):
            cand = cand_base / cases_raw
            try:
                if cand.is_file():
                    cases_path = cand.resolve()
                    break
            except OSError:
                continue
    return {
        "et_start_s":     float(sim.get("et_start_s", 0.0)),
        "duration_s":     float(sim.get("duration_s", 0.0)),
        "ephemeris_path": resolve_ephemeris_path(eph.get("file", ""), toml_path),
        "central_body":   str(force.get("central_body", "")),
        "cases_file":     cases_path,
        "toml_path":      toml_path,
    }


def ctx_missing_message(ax: Axes, title: str, reason: str) -> None:
    """Render a centred 'cannot draw' message on `ax` in lieu of the
    real plot when the run-folder context is missing or wrong. Keeps
    the title slot so the plot tree leaf remains recognisable."""
    ax.text(0.5, 0.5, reason, ha="center", va="center",
            transform=ax.transAxes, color="tab:red", wrap=True)
    ax.set_title(title)
    try:
        ax.set_xticks([]); ax.set_yticks([])
    except (NotImplementedError, ValueError):
        # Mollweide-projected axes refuse arbitrary tick lists; the
        # message body is enough on those.
        pass
