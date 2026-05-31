"""TOML schema for SpOdy input files -- single source for autocompletion.

This mirrors the schema documented in `examples/README.md` and parsed by
`src/toml_input.c`. Keep them in sync: when a new section / key / enum
value is added on the C side, add it here too. The structure is plain
Python dicts so it can also be used for tooltips / future validation
overlays.
"""
from __future__ import annotations

# Sentinel for keys with free-form numeric / string values (no enum).
FREE = None


def _key(type_: str, desc: str, values: list[str] | None = FREE, *,
         required: bool = False, mode: str | None = None) -> dict:
    """One key descriptor. `mode` tags keys that only exist in one object
    parameterisation: "spacecraft" or "debris". None means shared/applies
    regardless."""
    return {
        "type":     type_,
        "desc":     desc,
        "values":   values,
        "required": required,
        "mode":     mode,
    }


# Every top-level section + sub-table. Keys are listed as they appear in
# the TOML document; nested tables (e.g. [spacecraft.srp]) get their own
# entry under the full dotted name.
SECTIONS: dict[str, dict[str, dict]] = {
    "simulation": {
        "name":       _key("string", "human-readable scenario name", required=True),
        "et_start_s": _key("float",  "epoch (TDB seconds past J2000)", required=True),
        "duration_s": _key("float",  "propagation duration in seconds, > 0", required=True),
    },
    "spacecraft": {
        "mass_kg":    _key("float",  "dry mass in kg, > 0", required=True, mode="spacecraft"),
    },
    "spacecraft.srp": {
        "area_m2":    _key("float",  "SRP cross-section [m^2]; XOR with am_srp", mode="spacecraft"),
        "am_srp":     _key("float",  "A/m [m^2/kg]; XOR with area_m2",            mode="spacecraft"),
        "Cr":         _key("float",  "reflectivity coefficient (1=absorb, 2=mirror)", required=True, mode="spacecraft"),
    },
    "debris": {
        "am_srp":     _key("float",  "A/m [m^2/kg], > 0", required=True, mode="debris"),
        "Cr":         _key("float",  "reflectivity coefficient (used if SRP on)", required=True, mode="debris"),
    },
    "initial_state": {
        "frame":         _key("string", "inertial frame label",
                              ['"central_inertial"'], required=True),
        "position_km":   _key("float[3]", "[x, y, z] position in km, central-body inertial", required=True),
        "velocity_kms":  _key("float[3]", "[vx, vy, vz] velocity in km/s", required=True),
    },
    "force_model": {
        "central_body":     _key("string", "central body name",
                                 ['"Moon"'], required=True),
        "harmonics_file":   _key("string (path)", "spherical-harmonics coefficient file", required=True),
        "harmonics_degree": _key("int",    "truncation degree, >= 2 and <= file max", required=True),
        "third_bodies":     _key("string[]", "list of NAIF body names",
                                 ['"Sun"', '"Mercury"', '"Venus"', '"Earth"', '"Moon"',
                                  '"Mars"', '"Jupiter"', '"Saturn"', '"Uranus"', '"Neptune"'],
                                 required=True),
        "srp":              _key("bool",   "enable cannonball SRP",
                                 ["true", "false"], required=True),
    },
    "ephemeris": {
        "file":             _key("string (path)", "DE-series ephemeris in .spody format", required=True),
    },
    "integrator": {
        "type":     _key("string", "integration scheme", ['"rkdp45"'], required=True),
        "rel_tol":  _key("float",  "relative tolerance per step", required=True),
        "h_init_s": _key("float",  "initial step size in seconds", required=True),
        "h_min_s":  _key("float",  "minimum step size in seconds", required=True),
        "h_max_s":  _key("float",  "maximum step size in seconds", required=True),
    },
    "output": {
        "mode":               _key("string", "output sampling mode",
                                   ['"fixed"', '"step"'], required=True),
        "interval_s":         _key("float",  "sample interval (mode='fixed')"),
        "csv_file":           _key("string (path)", "trajectory CSV output, empty = disabled"),
        "bin_file":           _key("string (path)", "trajectory binary output (SPDYOUT_)"),
        "log_file":           _key("string (path)", "tee stdout/stderr to a log file"),
        "accelerations_file": _key("string (path)", "per-force breakdown binary (SPDYACC_)"),
        "events_log":         _key("string (path)", "event triggers binary (SPDYEVT_)"),
    },
    "events": {
        "eclipse_threshold": _key("float", "fraction in [0,1]; crossing fires the event"),
    },
    "batch": {
        "name":          _key("string", "batch run name (drives per-case file names)", required=True),
        "output_dir":    _key("string (path)", "must exist; batch/ subdir is auto-created", required=True),
        "thread_number": _key("int",    "1 = sequential; >1 reserved for future OpenMP", required=True),
        "cases_file":    _key("string (path)", ".csv (today) or .spody (future)", required=True),
    },
    "batch.columns": {
        # No fixed keys -- user picks CSV column names -> dotted target paths.
        # Suggestion list for the value side is built by SUGGEST_BATCH_TARGETS.
    },
}


# Enum values offered as completion right after `= ` for a given key.
# Built from SECTIONS so we don't duplicate -- kept as a derived view.
ENUM_VALUES: dict[tuple[str, str], list[str]] = {
    (section, key): info["values"]
    for section, keys in SECTIONS.items()
    for key, info in keys.items()
    if info.get("values")
}


# Valid override targets for [batch.columns] -- mirrors the FIELD_TABLE in
# src/toml_input.c. Used to suggest right-hand-side values inside that
# section. Mode-tagged entries are filtered at completion time by the
# detected object schema ([spacecraft] vs [debris]) in the buffer.
BATCH_TARGETS: list[tuple[str, str | None]] = [
    ("simulation.et_start_s",          None),
    ("simulation.duration_s",          None),
    ("spacecraft.mass_kg",             "spacecraft"),
    ("spacecraft.srp.area_m2",         "spacecraft"),
    ("spacecraft.srp.Cr",              "spacecraft"),
    ("debris.am_srp",                  "debris"),
    ("debris.Cr",                      "debris"),
    ("initial_state.position_km[0]",   None),
    ("initial_state.position_km[1]",   None),
    ("initial_state.position_km[2]",   None),
    ("initial_state.velocity_kms[0]",  None),
    ("initial_state.velocity_kms[1]",  None),
    ("initial_state.velocity_kms[2]",  None),
    ("force_model.srp",                None),
    ("integrator.rel_tol",             None),
    ("integrator.h_init_s",            None),
    ("integrator.h_min_s",             None),
    ("integrator.h_max_s",             None),
    ("output.interval_s",              None),
]


# Snippet templates -- inserted on Tab after the keyword at line start, or
# via the Insert menu. One per section that is genuinely "new from
# scratch" (no template for batch.columns -- the keys are user-defined).
# The placeholder values are realistic LRO 6-day inputs so a brand-new
# TOML can be assembled by chaining snippets and only tweaking numbers.
SNIPPETS: dict[str, str] = {
    "simulation": """\
[simulation]
name       = "scenario_name"
et_start_s = 3.065472661824111e+08
duration_s = 5.184e+05
""",
    "spacecraft": """\
[spacecraft]
mass_kg = 1916.0

  [spacecraft.srp]
  area_m2 = 20.0
  Cr      = 1.3
""",
    "debris": """\
[debris]
am_srp = 0.02
Cr     = 1.3
""",
    "initial_state": """\
[initial_state]
frame        = "central_inertial"
position_km  = [ 1622.030233600,  512.084982400, -529.342614300]
velocity_kms = [    0.648832282,   -0.519033001,    1.440002498]
""",
    "force_model": """\
[force_model]
central_body     = "Moon"
harmonics_file   = "path/to/gggrx_1200b_sha.tab"
harmonics_degree = 80
third_bodies     = ["Earth", "Sun"]
srp              = false
""",
    "ephemeris": """\
[ephemeris]
file = "path/to/de440.spody"
""",
    "integrator": """\
[integrator]
type     = "rkdp45"
rel_tol  = 1.0e-11
h_init_s = 60.0
h_min_s  = 1.0e-5
h_max_s  = 2700.0
""",
    "output": """\
[output]
mode       = "fixed"
interval_s = 60.0
bin_file   = "output/run.bin"
""",
    "events": """\
[events]
eclipse_threshold = 0.5
""",
    "batch": """\
[batch]
name          = "sweep"
output_dir    = "output"
thread_number = 1
cases_file    = "cases.csv"

[batch.columns]
mass_kg = "spacecraft.mass_kg"
""",
}


# Public helpers ------------------------------------------------------

def section_names() -> list[str]:
    """All top-level [section] names available for header completion."""
    return [s for s in SECTIONS.keys() if "." not in s]


def all_section_names_including_nested() -> list[str]:
    """Top-level plus dotted sub-tables (e.g. spacecraft.srp, batch.columns)."""
    return list(SECTIONS.keys())


def keys_for_section(section: str) -> list[str]:
    """Keys that may appear inside `[section]`. Returns [] for unknown
    sections (e.g. batch.columns whose keys are user-defined)."""
    return list(SECTIONS.get(section, {}).keys())


def enum_for_key(section: str, key: str) -> list[str]:
    """Completion values for the right-hand side of `key = ` in
    `[section]`. Empty if the key takes a free numeric / string value."""
    return ENUM_VALUES.get((section, key), []) or []


def batch_target_paths(object_mode: str | None) -> list[str]:
    """Targets valid inside [batch.columns] for the current object
    schema. `object_mode` is "spacecraft", "debris", or None when neither
    block has been written yet."""
    return [
        p for p, tag in BATCH_TARGETS
        if tag is None or tag == object_mode or object_mode is None
    ]
