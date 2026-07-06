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

"""Declarative catalogue behind the TOML form.

Enum/list values offered as drop-downs or checkbox sets, per-field
tooltips, validators, unit suffixes and output-file naming tables.
Everything here mirrors an engine-side table (src/toml_input.c) or
spody_const.h via the constants module -- extending the engine
usually means touching exactly one tuple/dict in this file.
"""

from __future__ import annotations

from typing import Any

from .. import constants
from ..central_bodies import known_central_body_names


# ----------------------------------------------------------------------
# Catalogue of "known" enum / list values offered as drop-down or
# checkbox sets. Kept here (rather than in the autocomplete schema)
# because the form's needs are slightly different -- e.g. third bodies
# get individual checkboxes, not a combo list. `CENTRAL_BODIES` is
# sourced from the registry in central_bodies.py so adding a body
# (Phase 2 = Earth) is one line there and shows up in the form
# automatically; the other tuples are short enough to extend in place.
# ----------------------------------------------------------------------


CENTRAL_BODIES   = known_central_body_names()
DYNAMICS_MODELS  = ("high_fidelity", "cr3bp")
# Per-model valid frames -- the [initial_state] frame combo is filtered
# to the entries valid under the currently-selected dynamics_model.
# `central_body_fixed` is GUI-only: the user types the [initial_state]
# vectors (cart or kep angles) in the central body's body-fixed basis
# (Earth ITRS, Moon PA), and the form rotates them to ICRF at TOML
# emit so the engine sees plain `central_inertial`. The combo entry
# is auto-hidden for bodies without a registered orientation provider.
FRAMES_BY_MODEL: dict[str, tuple[str, ...]] = {
    "high_fidelity": ("central_inertial", "central_body_fixed"),
    "cr3bp":         ("synodic_rotating",),
}
# Curated CR3BP primary pairs. Mirror of CR3BP_PAIRS in
# src/toml_input.c; adding a pair on either side without the other is
# a load-time validate error from the engine, so the two lists must
# stay in lockstep.
CR3BP_PAIRS: tuple[tuple[str, str], ...] = (
    ("Earth", "Moon"),
)
# Primary-primary separation (km) for each curated pair. Needed by the
# Keplerian <-> Cartesian swap in [initial_state] under CR3BP so the
# GUI can call spopy.inertial_to_synodic without going back to the
# engine. Read from EARTH_MOON_DISTANCE_KM in spody-core's
# spody_const.h (via constants.py), the same #define behind the
# lookup_cr3bp_pair table in src/toml_input.c.
CR3BP_L_KM: dict[tuple[str, str], float] = {
    ("Earth", "Moon"): constants.EARTH_MOON_DISTANCE_KM,
}
INTEGRATORS      = ("rkdp45",)
OUTPUT_MODES     = ("fixed", "step")
THIRD_BODIES_ALL = ("Sun", "Mercury", "Venus", "Earth", "Moon",
                    "Mars", "Jupiter", "Saturn", "Uranus", "Neptune")

# Valid override-target paths for [batch.columns]. Mirrors the
# FIELD_TABLE in src/toml_input.c. Each entry is (path, mode_tag):
# mode_tag is None for shared targets, "spacecraft" or "debris" when
# the path only makes sense under that object schema. The form filters
# by the current object selection.
BATCH_TARGETS: tuple[tuple[str, str | None], ...] = (
    ("simulation.et_start_s",          None),
    ("simulation.duration_s",          None),
    ("spacecraft.mass_kg",             "spacecraft"),
    ("spacecraft.srp.area_m2",         "spacecraft"),
    ("spacecraft.srp.Cr",              "spacecraft"),
    ("spacecraft.drag.area_m2",        "spacecraft"),
    ("spacecraft.drag.Cd",             "spacecraft"),
    ("debris.am_srp",                  "debris"),
    ("debris.Cr",                      "debris"),
    ("debris.am_drag",                 "debris"),
    ("debris.Cd",                      "debris"),
    ("initial_state.position_km[0]",   None),
    ("initial_state.position_km[1]",   None),
    ("initial_state.position_km[2]",   None),
    ("initial_state.velocity_kms[0]",  None),
    ("initial_state.velocity_kms[1]",  None),
    ("initial_state.velocity_kms[2]",  None),
    ("force_model.srp",                None),
    ("force_model.drag",               None),
    ("integrator.rel_tol",             None),
    ("integrator.h_init_s",            None),
    ("integrator.h_min_s",             None),
    ("integrator.h_max_s",             None),
    ("output.interval_s",              None),
)

# Sentinel string shown when a column has no target assigned. We use
# a leading "(" so it sorts above real path names alphabetically and
# is visually distinct from a real target.
UNASSIGNED = "(unassigned)"


# Per-field tooltips. One line each, shown on hover (and amended with
# the validation error when a value is out of range). Keys are the
# dotted paths the form uses internally.
TOOLTIPS: dict[str, str] = {
    "simulation.name":               "Human-readable scenario name. Drives single-run output names AND batch per-case file names (the form mirrors this into batch.name on emit).",
    "simulation.dynamics_model":     "Physics model. 'high_fidelity' = Cowell perturbations around a central body (needs spacecraft / force_model / ephemeris). 'cr3bp' = Circular Restricted 3-Body Problem in the synodic rotating frame (needs only [cr3bp] + [initial_state]).",
    "simulation.et_start_s":         "Start epoch in TDB seconds past J2000. Required by high_fidelity (anchors ephemeris / EOP lookups); ignored by cr3bp (autonomous).",
    "simulation.duration_s":         "Propagation duration in seconds; > 0.",
    "cr3bp.primary_1":               "Bigger primary (heavier body). GM looked up from the body table; primary-primary separation L from the curated CR3BP_PAIRS in spody-core.",
    "cr3bp.primary_2":               "Smaller primary. Must form a pair with primary_1 that is registered in the curated CR3BP table (today: Earth+Moon).",
    "spacecraft.mass_kg":            "Dry mass in kilograms; must be > 0.",
    "spacecraft.srp.area_m2":        "SRP cross-section in m²; A/m derived as area_m2 / mass_kg.",
    "spacecraft.srp.am_srp":         "A/m directly in m²/kg; alternative to area_m2 (XOR).",
    "spacecraft.srp.Cr":             "Reflectivity coefficient (1 = absorb, 2 = mirror).",
    "spacecraft.drag.area_m2":       "Drag cross-section in m²; A/m derived as area_m2 / mass_kg.",
    "spacecraft.drag.am_drag":       "A/m directly in m²/kg; alternative to area_m2 (XOR).",
    "spacecraft.drag.Cd":            "Drag coefficient (cannonball; ~2.2 for compact satellites).",
    "debris.am_srp":                 "Area-to-mass ratio in m²/kg; > 0.",
    "debris.Cr":                     "Reflectivity coefficient (used only when SRP is enabled).",
    "debris.am_drag":                "Drag area-to-mass ratio in m²/kg; > 0 (may differ from am_srp).",
    "debris.Cd":                     "Drag coefficient (used only when drag is enabled).",
    "initial_state.frame":           "Inertial reference frame. v0 supports only 'central_inertial'.",
    "initial_state.kind":            "Cartesian (default) gives [x, y, z] and [vx, vy, vz] directly. Keplerian gives six classical orbital elements + a reference body; the engine (and the form's swap helper) converts to Cartesian on the fly.",
    "initial_state.reference_body":  "Which body the Keplerian elements reference. HF: 'central' (implicit). CR3BP: 'primary_1' (bigger) or 'primary_2' (smaller); required, no default.",
    "initial_state.semi_major_axis_km": "Semi-major axis a, in km; > 0 for elliptical orbits.",
    "initial_state.eccentricity":    "Eccentricity e in [0, 1). Hyperbolic / parabolic orbits are not supported via Keplerian input.",
    "initial_state.inclination_deg": "Inclination i, in degrees, in [0, 180].",
    "initial_state.raan_deg":        "Right ascension of the ascending node, in degrees. Folded into argument of periapsis when the orbit is equatorial.",
    "initial_state.arg_periapsis_deg": "Argument of periapsis omega, in degrees. Folded into 0 when the orbit is circular.",
    "initial_state.anomaly_deg":     "Anomaly value at t = 0, in degrees. Interpreted as true OR mean depending on anomaly_type below.",
    "initial_state.anomaly_type":    "Is anomaly_deg true anomaly (most natural) or mean anomaly (catalog convention; converted via Kepler's equation).",
    "initial_state.position_km":     "[x, y, z] position in km, central-body inertial frame.",
    "initial_state.velocity_kms":    "[vx, vy, vz] velocity in km/s, same frame as position.",
    "force_model.central_body":      "Central body of the propagation. Supported: " + ", ".join(f"'{n}'" for n in CENTRAL_BODIES) + ".",
    "force_model.harmonics_file":    "Spherical-harmonics coefficient file (e.g. GRGM1200B).",
    "force_model.harmonics_degree":  "Truncation degree; ≥ 2 and ≤ the N declared in the chosen harmonics file (1200 for GRGM1200B Moon, 2190 for EIGEN-6C4 Earth; schema cap is 2200).",
    "force_model.eop_file":          "IERS Earth Orientation Parameters file (finals2000A.all). Required when central_body = 'Earth'.",
    "force_model.iau2006_dir":       "Directory containing the IAU 2006/2000A series tables (tab5.2a.txt, tab5.2b.txt, tab5.2d.txt). Required when central_body = 'Earth'.",
    "force_model.third_bodies":      "Perturbing bodies; pick from the standard NAIF set.",
    "force_model.srp":               "Enable cannonball SRP. Requires [spacecraft.srp] in spacecraft mode.",
    "force_model.drag":              "Enable atmospheric drag (NRLMSISE-00 density, storm-time 3-hour Ap mode). Needs a central body with an atmosphere model (Earth), [spacecraft.drag] (or debris am_drag/Cd) and space_weather_file.",
    "force_model.space_weather_file": "CelesTrak combined space weather CSV (SW-All.csv: daily F10.7 + 3-hour Ap). Required when drag = true; the run window must sit at least 3 days after the table start and inside its predicted horizon.",
    "ephemeris.file":                "DE-series ephemeris in the .spody binary format.",
    "integrator.type":               "Integration scheme. v0 supports only RK Dormand-Prince 5(4).",
    "integrator.rel_tol":            "Relative tolerance per accepted step; > 0.",
    "integrator.h_init_s":           "Initial step size in seconds; > 0, normally in [h_min_s, h_max_s].",
    "integrator.h_min_s":            "Minimum step size in seconds; > 0.",
    "integrator.h_max_s":            "Maximum step size in seconds; > h_min_s.",
    "output.mode":                   "'fixed' = uniform interval_s sampling; 'step' = one record per accepted step.",
    "output.interval_s":             "Sample interval in seconds; required when mode = fixed.",
    "output.output_dir":             "Parent directory for the per-run timestamp folder spody.exe creates at launch (<output_dir>/<UTC-ISO8601>/). Each run is self-contained: a snapshot of this TOML lands there as input.toml alongside all output files. Applies to BOTH single-run propagation and batch (the form mirrors this into batch.output_dir on emit). Leave empty to write outputs to the TOML's own directory with no per-run folder.",
    "output.csv_file":               "State vector CSV stream. Auto-named '<sim_name>_state_icrf.csv' under output_dir.",
    "output.bin_file":               "State vector binary stream (SPDYOUT_). Auto-named '<sim_name>_state_icrf.bin'.",
    "output.log_file":               "Tee stdout/stderr to a file. Auto-named '<sim_name>.log'.",
    "output.accelerations_file":     "Per-force acceleration breakdown (SPDYACC_). Auto-named '<sim_name>_acc_icrf.bin'.",
    "output.events_log":             "Event triggers binary. Per-run SPDYEVT_ ('<sim_name>_events.bin') in single-propagate; aggregated SPDYEVTB ('<batch_name>_events.bin') in batch.",
    "events.eclipse_threshold":      "Sunlight-fraction crossing that fires the eclipse event; in [0, 1].",
    "batch.thread_number":           "1 = sequential. > 1 needs the OpenMP-enabled spody build.",
    "batch.cases_file":              "CSV (today) or .spody (future): one row per case, header = column names.",
}


# Per-field value validators. Each takes the coerced field value
# (float / int / str) and returns "" if acceptable, or a short error
# message used to populate the tooltip and turn the field red.
# Fields with no entry are not validated beyond the type-validator
# (QDoubleValidator / QIntValidator) attached to the widget.
def _pos    (v: float) -> str: return "" if v >  0.0 else "must be > 0"
def _nonneg (v: float) -> str: return "" if v >= 0.0 else "must be >= 0"
def _harm_deg(v: int)  -> str: return "" if 2 <= v <= 2200 else "must be in [2, 2200] (schema cap; file maximum is the effective ceiling: 1200 for GRGM1200B, 2190 for EIGEN-6C4)"
def _frac01 (v: float) -> str: return "" if 0.0 <= v <= 1.0 else "must be in [0, 1]"

def _ecc01    (v: float) -> str: return "" if 0.0 <= v < 1.0 else "must be in [0, 1) (hyperbolic / parabolic not supported)"
def _inc_deg  (v: float) -> str: return "" if 0.0 <= v <= 180.0 else "must be in [0, 180] deg"

VALIDATORS: dict[str, Any] = {
    "simulation.duration_s":          _pos,
    "spacecraft.mass_kg":             _pos,
    "spacecraft.srp.area_m2":         _pos,
    "spacecraft.srp.am_srp":          _pos,
    "spacecraft.srp.Cr":              _nonneg,
    "spacecraft.drag.area_m2":        _pos,
    "spacecraft.drag.am_drag":        _pos,
    "spacecraft.drag.Cd":             _pos,
    "debris.am_srp":                  _pos,
    "debris.Cr":                      _nonneg,
    "debris.am_drag":                 _pos,
    "debris.Cd":                      _pos,
    "force_model.harmonics_degree":   _harm_deg,
    "initial_state.semi_major_axis_km": _pos,
    "initial_state.eccentricity":     _ecc01,
    "initial_state.inclination_deg":  _inc_deg,
    "integrator.rel_tol":             _pos,
    "integrator.h_init_s":            _pos,
    "integrator.h_min_s":             _pos,
    "integrator.h_max_s":             _pos,
    "output.interval_s":              _pos,
    "events.eclipse_threshold":       _frac01,
    # batch.thread_number cap is already enforced by QIntValidator(1, cpu_n).
}

# QSS for an invalid field. Kept conservative so it works on light and
# dark Qt themes alike -- a thin red border + a tinted background.
INVALID_QSS = (
    "QLineEdit { border: 1px solid #d04040; "
    "background-color: rgba(255, 200, 200, 60); }"
)


# A short label appended to certain QLineEdits to remind the user of
# the unit; not parsed, purely cosmetic.
UNIT = {
    "et_start_s":  "s (TDB past J2000)",
    "duration_s":  "s",
    "mass_kg":     "kg",
    "am_srp":      "m²/kg",
    "am_drag":     "m²/kg",
    "area_m2":     "m²",
    "rel_tol":     "",
    "h_init_s":    "s",
    "h_min_s":     "s",
    "h_max_s":     "s",
    "interval_s":  "s",
    "position_km": "km",
    "velocity_kms":"km/s",
    "semi_major_axis_km": "km",
    "inclination_deg":    "deg",
    "raan_deg":           "deg",
    "arg_periapsis_deg":  "deg",
    "anomaly_deg":        "deg",
}


def unit_suffix(key: str) -> str:
    u = UNIT.get(key, "")
    return f"  [{u}]" if u else ""


# Conversion factors for seconds-valued fields rendered with a unit
# combo (currently only `duration_s`). The TOML always carries seconds;
# the combo is purely a display/typing aid driven by user preference.
DURATION_FACTORS: dict[str, float] = {
    "s":    1.0,
    "min":  60.0,
    "h":    3600.0,
    "days": 86400.0,
}
# Order tried by the auto-pick on load -- the first unit whose factor
# does not exceed |value| wins, with "s" as the fallback so sub-second
# durations stay rendered in seconds.
DURATION_UNIT_AUTOPICK = ("days", "h", "min")


# Standard file-name suffixes for the auto-named output streams. Keys are
# the same dotted TOML paths as the form widgets; values are appended to
# the simulation's `[simulation].name` to form the basename. Pattern is
# `_<subject>_<frame>` for streams whose payload is in a specific frame
# (state, acc), bare suffix for the rest (events have no frame; log is
# plain text).
OUTPUT_FILE_SUFFIX: dict[str, str] = {
    "output.csv_file":           "_state_icrf.csv",
    "output.bin_file":           "_state_icrf.bin",
    "output.accelerations_file": "_acc_icrf.bin",
    "output.events_log":         "_events.bin",
    "output.log_file":           ".log",
}

# Display label for each output checkbox: short + tells the user
# what the auto-name will look like once a `[simulation].name` is set.
OUTPUT_CHECK_LABEL: dict[str, str] = {
    "output.csv_file":           "state vector CSV    (<sim_name>_state_icrf.csv)",
    "output.bin_file":           "state vector binary (<sim_name>_state_icrf.bin)",
    "output.accelerations_file": "accelerations       (<sim_name>_acc_icrf.bin)",
    "output.events_log":         "events              (<sim_name>_events.bin)",
    "output.log_file":           "log                 (<sim_name>.log)",
}
