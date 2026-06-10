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
"""Reference-frame conversions for batch input CSVs.

The spody propagator integrates in the central-body inertial, ICRF-aligned
frame (harmonics rotation pipeline and ephemeris third-body resolution both
assume it). This module lets the GUI accept batch cases whose state-vector
columns are expressed in a *different* frame and pre-process them into ICRF
before spody.exe sees them.

Convention used here for RIC
----------------------------
The RIC (radial / in-track / cross-track) basis is the instantaneous local
frame of a reference orbit:

    r_hat = r_ref / |r_ref|
    c_hat = (r_ref x v_ref) / |r_ref x v_ref|        (cross-track / h-hat)
    s_hat = c_hat x r_hat                            (in-track, NOT v_ref)

`s_hat` aligns with the in-track direction but is NOT equal to v_ref unless
the orbit is exactly circular -- it is the component of velocity orthogonal
to r_hat, normalised.

Pure-rotation contract
----------------------
`rotate_state_csv_ric_to_icrf` is exactly that: a per-row change of basis,
no translation. Given a row whose state columns hold
`[r_x, r_y, r_z]_ric` and `[v_x, v_y, v_z]_ric` it writes the same row with
those columns replaced by `R @ r_ric` and `R @ v_ric` (with R built from
the reference orbit). Everything else passes through unchanged.

The reference orbit is added back by **spody.exe**, not by this function:
the canonical pairing is with `[batch.columns]` entries marked
`mode = "delta"`, so spody computes `final = base + delta` per case with
`base` taken from `[initial_state]`. That keeps the conversion side-effect
free and reusable from CLI without dragging in the rest of the batch
config.

Velocity convention: snapshot / sensor-frame -- no `omega x r` term. The
dv components are treated as plain vector projections onto the
instantaneous RIC axes (what an onboard sensor would report). This is NOT
the Hill / Clohessy-Wiltshire rotating-frame convention used in rendezvous
literature.

Convention used here for LVLH
-----------------------------
The LVLH (Local Vertical / Local Horizontal) basis follows the
NASA/Goddard convention used by CCSDS conjunction messages and the
standard NASA breakup-model tooling:

    z_lvlh = -r_hat                (nadir, toward the central body)
    y_lvlh = -h_hat                (anti orbit normal,  h = r x v)
    x_lvlh =  y_lvlh x z_lvlh      (horizontal; aligns with +v_ref
                                    for a circular orbit)

`lvlh_basis(r, v)` returns the column-stacked `(x_lvlh, y_lvlh, z_lvlh)`
so multiplying it by a vector expressed in LVLH components produces the
same vector in ICRF -- matching the SPICE-style C reference the user's
breakup-model code emits:

    R[i][j] = (x_lvlh[i], y_lvlh[i], z_lvlh[i])[j]
    v_icrf = R @ v_lvlh

Pure-rotation contract identical to the RIC side: the GUI pre-rotates the
per-row state offsets, spody.exe adds them on top of `[initial_state]` via
`mode = "delta"`. No omega-cross-r term either -- LVLH input is taken as a
sensor-frame snapshot of the fragment ejection velocities (the NASA EVOLVE
/ ORDEM breakup-model convention).
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np


def ric_basis(r_ref: np.ndarray, v_ref: np.ndarray) -> np.ndarray:
    """Build the rotation matrix R_RIC2ECI from a reference state in ICRF.

    Columns are (r_hat, s_hat, c_hat). Multiplying R by a vector expressed
    in RIC components returns the same vector expressed in ICRF components.

    Parameters
    ----------
    r_ref : array_like, shape (3,)
        Reference position in ICRF central-inertial [km].
    v_ref : array_like, shape (3,)
        Reference velocity in ICRF central-inertial [km/s].

    Returns
    -------
    R : ndarray, shape (3, 3)
        Rotation matrix that maps RIC -> ICRF.

    Raises
    ------
    ValueError
        If r_ref is zero or r_ref and v_ref are parallel (no angular
        momentum, RIC frame undefined).
    """
    r = np.asarray(r_ref, dtype=float).reshape(3)
    v = np.asarray(v_ref, dtype=float).reshape(3)

    r_norm = np.linalg.norm(r)
    if r_norm < 1.0e-9:
        raise ValueError("reference position is at the origin; RIC undefined")

    h = np.cross(r, v)
    h_norm = np.linalg.norm(h)
    if h_norm < 1.0e-12:
        raise ValueError(
            "reference r_ref and v_ref are parallel (h = r x v = 0); "
            "RIC frame undefined"
        )

    r_hat = r / r_norm
    c_hat = h / h_norm
    s_hat = np.cross(c_hat, r_hat)

    # Column-stacked: each column is one axis expressed in ICRF coordinates,
    # so R @ x_ric == x_eci by construction.
    return np.column_stack((r_hat, s_hat, c_hat))


def lvlh_basis(r_ref: np.ndarray, v_ref: np.ndarray) -> np.ndarray:
    """Build the rotation matrix R_LVLH2ICRF from a reference state in ICRF.

    NASA/Goddard LVLH convention:

        z_lvlh = -r_hat                (nadir)
        y_lvlh = -h_hat                (anti orbit normal, h = r x v)
        x_lvlh =  y_lvlh x z_lvlh      (horizontal)

    Columns are (x_lvlh, y_lvlh, z_lvlh). Multiplying R by a vector
    expressed in LVLH components returns the same vector expressed in
    ICRF components -- bit-for-bit equivalent to the C reference
    (`unorm_c` + `ucrss_c` + sign flips) used by the user's breakup-model
    pipeline.

    Parameters
    ----------
    r_ref : array_like, shape (3,)
        Reference position in ICRF central-inertial [km].
    v_ref : array_like, shape (3,)
        Reference velocity in ICRF central-inertial [km/s].

    Returns
    -------
    R : ndarray, shape (3, 3)
        Rotation matrix that maps LVLH -> ICRF.

    Raises
    ------
    ValueError
        If r_ref is zero or r_ref and v_ref are parallel (h = 0;
        LVLH undefined).
    """
    r = np.asarray(r_ref, dtype=float).reshape(3)
    v = np.asarray(v_ref, dtype=float).reshape(3)

    r_norm = np.linalg.norm(r)
    if r_norm < 1.0e-9:
        raise ValueError(
            "reference position is at the origin; LVLH undefined")

    h = np.cross(r, v)
    h_norm = np.linalg.norm(h)
    if h_norm < 1.0e-12:
        raise ValueError(
            "reference r_ref and v_ref are parallel (h = r x v = 0); "
            "LVLH frame undefined"
        )

    # Sign flips encode the nadir / anti-orbit-normal choice; cross of
    # the two gives the horizontal "x" axis (~ +v for circular orbits).
    z_lvlh = -r / r_norm
    y_lvlh = -h / h_norm
    x_lvlh = np.cross(y_lvlh, z_lvlh)
    # x is already unit length (y, z are unit and orthogonal), but
    # normalise defensively against accumulated float roundoff.
    x_lvlh /= np.linalg.norm(x_lvlh)

    return np.column_stack((x_lvlh, y_lvlh, z_lvlh))


def rotate_state_csv_lvlh_to_icrf(
    input_path: str | Path,
    output_path: str | Path,
    r_ref_km: np.ndarray,
    v_ref_kms: np.ndarray,
    pos_columns: tuple[str, str, str] | None,
    vel_columns: tuple[str, str, str] | None,
) -> dict:
    """LVLH counterpart of `rotate_state_csv_ric_to_icrf`.

    Same pure-rotation contract: reads `input_path`, replaces the
    declared state-column triplets with `R @ v_lvlh` per row (R from
    `lvlh_basis`), writes the result to `output_path`. No translation
    is added -- pair with `mode = "delta"` in `[batch.columns]` so
    spody.exe combines the rotated offsets with `[initial_state]`.

    The on-disk header is preserved column-by-column; only the rotated
    cells change. The header banner emitted at the top of the output
    file documents the reference orbit + which columns were rotated,
    so the produced CSV is self-describing.

    Parameters, return value and error semantics mirror
    `rotate_state_csv_ric_to_icrf`; see that docstring for the full
    contract.
    """
    in_path  = Path(input_path)
    out_path = Path(output_path)
    if not in_path.is_file():
        raise FileNotFoundError(f"batch CSV not found: {in_path}")
    if in_path.resolve() == out_path.resolve():
        raise ValueError(
            f"refusing to rewrite '{in_path}' in place; "
            f"pick a distinct output path")
    if pos_columns is None and vel_columns is None:
        raise ValueError(
            "rotate_state_csv_lvlh_to_icrf called with neither pos_columns "
            "nor vel_columns -- nothing to rotate")

    R = lvlh_basis(r_ref_km, v_ref_kms)
    r_ref = np.asarray(r_ref_km,  dtype=float).reshape(3)
    v_ref = np.asarray(v_ref_kms, dtype=float).reshape(3)

    with in_path.open(encoding="utf-8", newline="") as f:
        data_lines = [
            ln for ln in f
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
    if not data_lines:
        raise ValueError(f"{in_path}: no header/data lines (only comments?)")

    reader = csv.DictReader(data_lines, skipinitialspace=True)
    raw_header = reader.fieldnames or []
    header = [h.strip() for h in raw_header]
    reader.fieldnames = header

    declared = []
    if pos_columns is not None: declared.extend(pos_columns)
    if vel_columns is not None: declared.extend(vel_columns)
    missing = [c for c in declared if c not in header]
    if missing:
        raise ValueError(
            f"{in_path}: missing state columns declared by the GUI mapping: "
            f"{missing}. CSV header: {header}")

    state_set = set(declared)
    passthrough = [c for c in header if c not in state_set]

    out_header = list(header)

    n_rows = 0
    with out_path.open("w", encoding="utf-8", newline="") as f_out:
        rr = [repr(float(x)) for x in r_ref]
        vv = [repr(float(x)) for x in v_ref]
        f_out.write(
            f"# generated by spody_gui.frames.rotate_state_csv_lvlh_to_icrf\n"
            f"# source     = {in_path.name}\n"
            f"# r_ref_km   = [{rr[0]}, {rr[1]}, {rr[2]}]\n"
            f"# v_ref_kms  = [{vv[0]}, {vv[1]}, {vv[2]}]\n"
            f"# rotated    = pos:{pos_columns} vel:{vel_columns}\n"
            f"# convention = pure rotation (LVLH -> ICRF); pair with [batch.columns] mode='delta'\n"
        )
        writer = csv.DictWriter(f_out, fieldnames=out_header)
        writer.writeheader()

        for row_idx, row in enumerate(reader, start=1):
            out_row = dict(row)

            if pos_columns is not None:
                try:
                    r_lvlh = np.array([float(row[c]) for c in pos_columns])
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{in_path}: row {row_idx}: non-numeric value in "
                        f"position columns {pos_columns} ({exc})") from exc
                r_icrf = R @ r_lvlh
                for col, val in zip(pos_columns, r_icrf):
                    out_row[col] = repr(float(val))

            if vel_columns is not None:
                try:
                    v_lvlh = np.array([float(row[c]) for c in vel_columns])
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{in_path}: row {row_idx}: non-numeric value in "
                        f"velocity columns {vel_columns} ({exc})") from exc
                v_icrf = R @ v_lvlh
                for col, val in zip(vel_columns, v_icrf):
                    out_row[col] = repr(float(val))

            writer.writerow(out_row)
            n_rows += 1

    return {
        "n_rows":              n_rows,
        "rotated_pos_columns": pos_columns,
        "rotated_vel_columns": vel_columns,
        "passthrough_columns": passthrough,
    }


def rotate_state_csv_ric_to_icrf(
    input_path: str | Path,
    output_path: str | Path,
    r_ref_km: np.ndarray,
    v_ref_kms: np.ndarray,
    pos_columns: tuple[str, str, str] | None,
    vel_columns: tuple[str, str, str] | None,
) -> dict:
    """Rotate the per-row state columns of a batch CSV from RIC to ICRF.

    Pure change-of-basis, ONE row at a time. The reference orbit (r_ref,
    v_ref) defines the RIC axes; the rotation R is computed once and
    applied to each row's `[pos_columns]` / `[vel_columns]` triplet.
    There is NO addition of r_ref / v_ref to the output -- pair this
    pre-processing step with `mode = "delta"` entries in `[batch.columns]`
    so spody.exe does the additive composition with `[initial_state]`.

    The 6 state columns are passed in explicitly (rather than guessed
    from names) because the GUI already knows which CSV column maps to
    which `initial_state.*` field via the user-edited `[batch.columns]`
    table. Either triplet may be `None` to skip that group (e.g. a CSV
    that only sweeps position offsets), in which case the corresponding
    columns are not touched.

    Parameters
    ----------
    input_path, output_path : str | Path
        Source CSV (RIC) and destination CSV (ICRF). They may NOT be the
        same path -- in-place rewrite is rejected to keep the source of
        truth recoverable.
    r_ref_km, v_ref_kms : array_like, shape (3,)
        Reference orbit state in ICRF central-inertial.
    pos_columns, vel_columns : (str, str, str) | None
        Header names of the x/y/z position and vx/vy/vz velocity columns
        in input_path. `None` means "this triplet is not in this CSV"
        (the matching rotation is skipped).

    Returns
    -------
    dict with keys:
        n_rows                : int, rows written
        rotated_pos_columns   : tuple[str,str,str] | None
        rotated_vel_columns   : tuple[str,str,str] | None
        passthrough_columns   : list[str]

    Raises
    ------
    FileNotFoundError
        input_path missing.
    ValueError
        - input_path == output_path
        - both pos_columns and vel_columns are None (nothing to do)
        - a declared state column is absent from the header
        - a row has a non-numeric value in a declared state column
        - r_ref / v_ref are degenerate (raised by `ric_basis`)
    """
    in_path  = Path(input_path)
    out_path = Path(output_path)
    if not in_path.is_file():
        raise FileNotFoundError(f"batch CSV not found: {in_path}")
    if in_path.resolve() == out_path.resolve():
        raise ValueError(
            f"refusing to rewrite '{in_path}' in place; "
            f"pick a distinct output path")
    if pos_columns is None and vel_columns is None:
        raise ValueError(
            "rotate_state_csv_ric_to_icrf called with neither pos_columns "
            "nor vel_columns -- nothing to rotate")

    # Fail before any I/O if the reference orbit is degenerate.
    R = ric_basis(r_ref_km, v_ref_kms)
    r_ref = np.asarray(r_ref_km,  dtype=float).reshape(3)
    v_ref = np.asarray(v_ref_kms, dtype=float).reshape(3)

    # Strip comments and DictReader the rest. csv has no native comment
    # handling and we want spody's `# ...` convention to be respected.
    with in_path.open(encoding="utf-8", newline="") as f:
        data_lines = [
            ln for ln in f
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
    if not data_lines:
        raise ValueError(f"{in_path}: no header/data lines (only comments?)")

    reader = csv.DictReader(data_lines, skipinitialspace=True)
    raw_header = reader.fieldnames or []
    header = [h.strip() for h in raw_header]
    reader.fieldnames = header

    declared = []
    if pos_columns is not None: declared.extend(pos_columns)
    if vel_columns is not None: declared.extend(vel_columns)
    missing = [c for c in declared if c not in header]
    if missing:
        raise ValueError(
            f"{in_path}: missing state columns declared by the GUI mapping: "
            f"{missing}. CSV header: {header}")

    state_set = set(declared)
    passthrough = [c for c in header if c not in state_set]

    # Output header preserves input order so visual diff vs the source
    # is friendly (only the 6 cells per row change, not their positions).
    out_header = list(header)

    n_rows = 0
    with out_path.open("w", encoding="utf-8", newline="") as f_out:
        rr = [repr(float(x)) for x in r_ref]
        vv = [repr(float(x)) for x in v_ref]
        f_out.write(
            f"# generated by spody_gui.frames.rotate_state_csv_ric_to_icrf\n"
            f"# source     = {in_path.name}\n"
            f"# r_ref_km   = [{rr[0]}, {rr[1]}, {rr[2]}]\n"
            f"# v_ref_kms  = [{vv[0]}, {vv[1]}, {vv[2]}]\n"
            f"# rotated    = pos:{pos_columns} vel:{vel_columns}\n"
            f"# convention = pure rotation (RIC -> ICRF); pair with [batch.columns] mode='delta'\n"
        )
        writer = csv.DictWriter(f_out, fieldnames=out_header)
        writer.writeheader()

        for row_idx, row in enumerate(reader, start=1):
            out_row = dict(row)  # start with everything verbatim

            if pos_columns is not None:
                try:
                    r_ric = np.array([float(row[c]) for c in pos_columns])
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{in_path}: row {row_idx}: non-numeric value in "
                        f"position columns {pos_columns} ({exc})") from exc
                r_eci = R @ r_ric
                for col, val in zip(pos_columns, r_eci):
                    out_row[col] = repr(float(val))

            if vel_columns is not None:
                try:
                    v_ric = np.array([float(row[c]) for c in vel_columns])
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        f"{in_path}: row {row_idx}: non-numeric value in "
                        f"velocity columns {vel_columns} ({exc})") from exc
                v_eci = R @ v_ric
                for col, val in zip(vel_columns, v_eci):
                    out_row[col] = repr(float(val))

            writer.writerow(out_row)
            n_rows += 1

    return {
        "n_rows":              n_rows,
        "rotated_pos_columns": pos_columns,
        "rotated_vel_columns": vel_columns,
        "passthrough_columns": passthrough,
    }


if __name__ == "__main__":
    # Run as `python -m spody_gui.frames` for a quick smoke check.
    import sys
    import tempfile

    failed = []

    def _check(name: str, cond: bool, extra: str = "") -> None:
        tag = "PASS" if cond else "FAIL"
        print(f"  [{tag}] {name}" + (f" -- {extra}" if extra else ""))
        if not cond:
            failed.append(name)

    print("frames.py self-test")

    # 1. Canonical equatorial-circular reference: r=(7000,0,0), v=(0,7,0)
    #    -> RIC axes coincide with ICRF (R = identity).
    r_ref = np.array([7000.0, 0.0, 0.0])
    v_ref = np.array([0.0, 7.0, 0.0])
    R = ric_basis(r_ref, v_ref)
    _check("canonical basis is identity",
           np.allclose(R, np.eye(3), atol=1.0e-12),
           f"R=\n{R}")

    # 2. Degenerate basis: r and v parallel -> raises.
    try:
        ric_basis(np.array([1.0, 0.0, 0.0]), np.array([2.0, 0.0, 0.0]))
        _check("parallel r,v raises", False, "did not raise")
    except ValueError:
        _check("parallel r,v raises", True)

    # 2b. LVLH canonical reference + property checks.
    r_can = np.array([7000.0, 0.0, 0.0])
    v_can = np.array([0.0,     7.0, 0.0])
    L = lvlh_basis(r_can, v_can)
    # Columns: x_lvlh, y_lvlh, z_lvlh expressed in ICRF.
    # For r=+X, v=+Y: h = +Z, so z_lvlh = -r_hat = -X; y_lvlh = -h_hat = -Z;
    # x_lvlh = y_lvlh x z_lvlh = (-Z) x (-X) = (Z x X) = +Y.
    _check("LVLH canonical: x_lvlh = +Y",
           np.allclose(L[:, 0], [0.0, 1.0, 0.0], atol=1e-12),
           f"L[:,0]={L[:,0]}")
    _check("LVLH canonical: y_lvlh = -Z",
           np.allclose(L[:, 1], [0.0, 0.0, -1.0], atol=1e-12),
           f"L[:,1]={L[:,1]}")
    _check("LVLH canonical: z_lvlh = -X (nadir)",
           np.allclose(L[:, 2], [-1.0, 0.0, 0.0], atol=1e-12),
           f"L[:,2]={L[:,2]}")
    _check("LVLH columns are orthonormal (LL^T = I)",
           np.allclose(L @ L.T, np.eye(3), atol=1e-12))
    _check("LVLH right-handed (det == +1)",
           abs(np.linalg.det(L) - 1.0) < 1e-12,
           f"det={np.linalg.det(L)}")

    # 2c. LVLH degenerate basis raises.
    try:
        lvlh_basis(np.array([1.0, 0.0, 0.0]), np.array([2.0, 0.0, 0.0]))
        _check("LVLH parallel r,v raises", False, "did not raise")
    except ValueError:
        _check("LVLH parallel r,v raises", True)

    # 3. CSV rotation round-trip.
    tmpdir = Path(tempfile.mkdtemp(prefix="spody_frames_test_"))
    try:
        ric_csv = tmpdir / "in.csv"
        eci_csv = tmpdir / "out.csv"
        # 3 cases on a NON-canonical reference orbit so R is not identity.
        # Source-of-truth components in RIC; expected ICRF computed by hand.
        r_ref_lro = np.array([1622.030233600,  512.084982400, -529.342614300])
        v_ref_lro = np.array([   0.648832282,   -0.519033001,    1.440002498])
        ric_csv.write_text(
            "# debris cloud in RIC, mass/Cr per case\n"
            "id, dr_x_km, dr_y_km, dr_z_km, dv_x_kms, dv_y_kms, dv_z_kms, mass_kg\n"
            "zero,  0.0,    0.0,     0.0,    0.0,      0.0,      0.0,      100.0\n"
            "rad,   1.0,    0.0,     0.0,    0.0,      0.0,      0.0,      200.0\n"
            "intr,  0.0,    1.0,     0.0,    0.0,      0.0,      0.0,      300.0\n",
            encoding="utf-8",
        )
        info = rotate_state_csv_ric_to_icrf(
            ric_csv, eci_csv, r_ref_lro, v_ref_lro,
            pos_columns=("dr_x_km", "dr_y_km", "dr_z_km"),
            vel_columns=("dv_x_kms", "dv_y_kms", "dv_z_kms"),
        )
        _check("rotation: 3 rows written", info["n_rows"] == 3)
        _check("rotation: passthrough preserved",
               info["passthrough_columns"] == ["id", "mass_kg"])

        out_lines = [ln for ln in eci_csv.read_text(encoding="utf-8").splitlines()
                     if ln.strip() and not ln.lstrip().startswith("#")]
        rows = list(csv.DictReader(out_lines))

        # 'zero' row: 0 vector rotates to 0 vector (no addition of r_ref).
        z = np.array([float(rows[0][c]) for c in ("dr_x_km","dr_y_km","dr_z_km")])
        _check("rotation: zero stays zero (no translation!)",
               np.allclose(z, [0.0, 0.0, 0.0], atol=1e-12),
               f"got {z}")

        # 'rad' row: r_ric=[1,0,0] -> R@[1,0,0] = r_hat of LRO.
        r_norm = np.linalg.norm(r_ref_lro)
        expected_rad = r_ref_lro / r_norm   # i.e. r_hat itself
        got_rad = np.array([float(rows[1][c])
                            for c in ("dr_x_km","dr_y_km","dr_z_km")])
        _check("rotation: 1 km radial -> r_hat",
               np.allclose(got_rad, expected_rad, atol=1e-12),
               f"got {got_rad} expected {expected_rad}")

        # 'intr' row: r_ric=[0,1,0] -> R@[0,1,0] = s_hat of LRO.
        h = np.cross(r_ref_lro, v_ref_lro)
        c_hat = h / np.linalg.norm(h)
        r_hat = r_ref_lro / r_norm
        s_hat = np.cross(c_hat, r_hat)
        got_intr = np.array([float(rows[2][c])
                             for c in ("dr_x_km","dr_y_km","dr_z_km")])
        _check("rotation: 1 km in-track -> s_hat",
               np.allclose(got_intr, s_hat, atol=1e-12),
               f"got {got_intr} expected {s_hat}")

        # pass-through preserved
        _check("rotation: mass_kg passthrough preserved",
               rows[1]["mass_kg"] == "200.0")

        # in-place output rejected
        try:
            rotate_state_csv_ric_to_icrf(
                ric_csv, ric_csv, r_ref_lro, v_ref_lro,
                pos_columns=("dr_x_km","dr_y_km","dr_z_km"),
                vel_columns=None)
            _check("in-place rewrite rejected", False, "did not raise")
        except ValueError:
            _check("in-place rewrite rejected", True)

        # missing-column detection
        bad = tmpdir / "bad.csv"
        bad.write_text("id, dr_x_km\nA, 0.0\n", encoding="utf-8")
        try:
            rotate_state_csv_ric_to_icrf(
                bad, tmpdir / "bad_out.csv",
                r_ref_lro, v_ref_lro,
                pos_columns=("dr_x_km","dr_y_km","dr_z_km"),
                vel_columns=None)
            _check("missing-column raises", False, "did not raise")
        except ValueError:
            _check("missing-column raises", True)

        # vel-only mapping (no pos triplet) still works
        vel_only = tmpdir / "vel.csv"
        vel_out  = tmpdir / "vel_out.csv"
        vel_only.write_text(
            "id, dv_x_kms, dv_y_kms, dv_z_kms\nA, 0.0, 1.0, 0.0\n",
            encoding="utf-8")
        info_v = rotate_state_csv_ric_to_icrf(
            vel_only, vel_out, r_ref_lro, v_ref_lro,
            pos_columns=None,
            vel_columns=("dv_x_kms","dv_y_kms","dv_z_kms"))
        _check("vel-only call: 1 row written", info_v["n_rows"] == 1)

        # both None raises
        try:
            rotate_state_csv_ric_to_icrf(
                vel_only, vel_out, r_ref_lro, v_ref_lro,
                pos_columns=None, vel_columns=None)
            _check("both-None raises", False, "did not raise")
        except ValueError:
            _check("both-None raises", True)
    finally:
        for p in tmpdir.iterdir():
            try: p.unlink()
            except OSError: pass
        try: tmpdir.rmdir()
        except OSError: pass

    print()
    if failed:
        print(f"FAILED: {len(failed)} check(s): {failed}")
        sys.exit(1)
    print("OK -- all checks passed")
