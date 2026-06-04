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
"""TOML read/write for the form-based input UI.

Reading goes through `tomli` (becomes `tomllib` in 3.11+, identical
behaviour, ~10 KB pure-Python). Writing is hand-rolled and
schema-aware: it knows the order of sections and keys spody expects
and emits a canonical formatted document that diffs cleanly between
runs.

The emitter is intentionally NOT a general TOML serialiser. It only
covers the value kinds spody uses:
    - string, int, float, bool
    - list of strings (e.g. `third_bodies`)
    - list of floats / ints   (e.g. `position_km`, `velocity_kms`)
    - nested sub-tables 1 level deep (`[spacecraft.srp]`, `[batch.columns]`)
    - inline tables           (used for `[batch.columns]` entries with
                                `mode = "delta"`)

Anything else raises an explicit `ValueError` so a form bug surfaces
loudly rather than silently producing garbage TOML.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import tomli


# Canonical section order in emitted TOML files. Matches what the
# example TOMLs use and what spody_load_input expects. Sections whose
# data is empty (e.g. [events] not enabled) are skipped at emit time.
_SECTION_ORDER: tuple[str, ...] = (
    "simulation",
    "spacecraft",
    "debris",
    "initial_state",
    "force_model",
    "ephemeris",
    "integrator",
    "output",
    "events",
    "batch",
)

# For each top-level section, the preferred order of scalar keys. Keys
# not in this list are emitted at the end alphabetically (so an
# unknown / new key from the form still round-trips).
_KEY_ORDER: dict[str, tuple[str, ...]] = {
    "simulation":    ("name", "et_start_s", "duration_s"),
    "spacecraft":    ("mass_kg",),
    "debris":        ("am_srp", "Cr"),
    "initial_state": ("frame", "position_km", "velocity_kms"),
    "force_model":   ("central_body", "harmonics_file", "harmonics_degree",
                      "third_bodies", "srp"),
    "ephemeris":     ("file",),
    "integrator":    ("type", "rel_tol", "h_init_s", "h_min_s", "h_max_s"),
    "output":        ("mode", "interval_s", "csv_file", "bin_file", "log_file",
                      "accelerations_file", "events_log"),
    "events":        ("eclipse_threshold",),
    "batch":         ("name", "output_dir", "thread_number", "cases_file"),
}


def read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file into a plain dict. Raises FileNotFoundError if
    missing, tomli.TOMLDecodeError on syntax errors -- the caller
    surfaces both as a message box."""
    with Path(path).open("rb") as fp:
        return tomli.load(fp)


def format_toml(data: dict[str, Any]) -> str:
    """Render `data` as canonically-formatted TOML text (without
    writing it). Same emitter as `write_toml`, exposed standalone so
    the GUI can show a live preview of what would be written."""
    return _format_document(data)


def write_toml(path: Path, data: dict[str, Any]) -> None:
    """Emit `data` as a canonically-formatted TOML file at `path`.
    Sections appear in `_SECTION_ORDER`; keys inside each section
    follow `_KEY_ORDER` then alphabetical for anything unknown.

    Sub-tables that are themselves dicts are emitted after their
    parent's scalar keys as `[parent.child]` sections. Empty sections
    (no scalar keys and no sub-tables) are skipped entirely so opting
    out of an optional block (events, batch, spacecraft.srp) just
    means not putting it in the dict."""
    Path(path).write_text(format_toml(data), encoding="utf-8")


# ----------------------------------------------------------------------
# Emitter internals
# ----------------------------------------------------------------------
def _format_document(data: dict[str, Any]) -> str:
    sections = list(_SECTION_ORDER)
    # Append any unknown top-level sections at the end (alphabetical)
    # so a future schema addition still gets emitted somewhere.
    for name in sorted(data.keys()):
        if name not in sections:
            sections.append(name)

    parts: list[str] = []
    for name in sections:
        sub = data.get(name)
        if not isinstance(sub, dict) or not sub:
            continue
        parts.append(_format_section(name, sub))
    # Each section ends with its own newline; join with blank lines
    # between sections to keep the file readable.
    return "\n".join(parts).rstrip() + "\n"


def _format_section(name: str, sub: dict[str, Any]) -> str:
    """A `[section]` header followed by its scalar key lines, followed
    by any `[section.child]` sub-tables emitted as separate sections."""
    # Split scalar values from sub-table dicts so the latter are
    # emitted with their own header AFTER the scalar lines.
    scalars: dict[str, Any] = {}
    subtables: dict[str, dict[str, Any]] = {}
    for k, v in sub.items():
        if isinstance(v, dict) and not _is_inline_table_value(v):
            subtables[k] = v
        else:
            scalars[k] = v

    out: list[str] = [f"[{name}]"]
    for k in _ordered_keys(name, scalars.keys()):
        out.append(f"{k} = {_format_value(scalars[k])}")

    if subtables:
        for child_name in sorted(subtables.keys()):
            out.append("")
            out.append(_format_section(f"{name}.{child_name}", subtables[child_name]))

    return "\n".join(out) + "\n"


def _ordered_keys(section: str, present: Any) -> list[str]:
    """Preferred order for a section, then alphabetical for anything
    not pre-declared. `present` is an iterable of the keys actually
    in the dict so we don't emit slots that have no value."""
    present_set = set(present)
    preferred = _KEY_ORDER.get(section, ())
    out: list[str] = [k for k in preferred if k in present_set]
    extras = sorted(present_set - set(preferred))
    out.extend(extras)
    return out


def _is_inline_table_value(d: dict[str, Any]) -> bool:
    """Heuristic for distinguishing a `[parent.child]` sub-table from
    an inline-table value like `{ target = "...", mode = "delta" }`.

    spody only uses inline tables inside `[batch.columns]` as column
    descriptors; they always have a `target` key. Real sub-tables
    never have that key at the top of their schema."""
    return "target" in d


def _format_value(v: Any) -> str:
    """Render a single right-hand-side TOML value. Lists and inline
    tables are formatted inline (single line); strings are
    double-quoted with the small set of escapes TOML mandates."""
    if isinstance(v, bool):
        # bool must be checked before int (True is an int in Python)
        return "true" if v else "false"
    if isinstance(v, (int,)):
        return str(int(v))
    if isinstance(v, float):
        # repr() round-trips losslessly for finite floats; TOML's float
        # syntax accepts the same forms (1.0, 1e-3, -2.5e+08).
        return repr(float(v))
    if isinstance(v, str):
        return _format_string(v)
    if isinstance(v, list):
        return "[" + ", ".join(_format_value(x) for x in v) + "]"
    if isinstance(v, dict):
        # Inline table (e.g. batch.columns delta descriptor).
        body = ", ".join(f"{k} = {_format_value(val)}" for k, val in v.items())
        return "{ " + body + " }"
    raise ValueError(
        f"unsupported TOML value type: {type(v).__name__} ({v!r})"
    )


def _format_string(s: str) -> str:
    """Double-quoted TOML string with the minimal escape set spody
    actually produces. We never write multi-line strings."""
    escaped = (s
               .replace("\\", "\\\\")
               .replace('"', '\\"')
               .replace("\n", "\\n")
               .replace("\t", "\\t"))
    return f'"{escaped}"'
