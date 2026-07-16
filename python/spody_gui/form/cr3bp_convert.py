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

"""[initial_state] "From CR3BP..." popup (next to the frame combo).

Converts a CR3BP synodic-rotating state (JPL periodic-orbit catalog
convention; dimensional or nondimensional; barycentric or
primary-centered) into the central-body-centered ICRF cartesian state
the high-fidelity model propagates, evaluated at the form's
`simulation.et_start_s`.

The mapping is the instantaneous pulsating-frame transform: the
characteristic length l* is the ACTUAL primary-primary distance from
the ephemeris at the epoch, the axes come from the actual geometry
(x = primary_1 -> primary_2, z = orbit normal), and the velocity
carries the instantaneous angular rate omega = h / l*^2 plus the
radial pulsation l*dot. Both primaries land exactly on their DE440
states under this mapping, so a catalog state enters the engine's
frame with no further approximation. Everything runs in-process on
spopy -- no engine call.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)


class Cr3bpConvertDialog(QDialog):
    """Modal CR3BP -> ICRF state converter.

    Explicit inputs, no back-references into the form:

      pairs     list of dicts {name1, name2, mu1, mu2, naif1, naif2,
                L} -- the curated CR3BP pairs (catalog.CR3BP_PAIRS
                joined with the central-body registry)
      et_s      simulation.et_start_s value (TDB s past J2000)
      utc_text  its ISO-8601 display twin ("" when unavailable)
      eph       spopy.Ephemeris, or None when the form has no
                [ephemeris] file yet (Convert is then refused)
      out_body  force_model.central_body name: the output center
      prefill   previous session values (the dialog is reopened once
                per orbit point, so the fields persist across opens)

    After exec() returns Accepted, `result_rv` holds (r_km, v_kms)
    numpy arrays in out_body-centered ICRF.
    """

    def __init__(self, parent, pairs: list, et_s: float, utc_text: str,
                 eph, out_body: str, prefill: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("From CR3BP")
        self._pairs = pairs
        self._et_s = float(et_s)
        self._eph = eph
        self._out_body = out_body
        self.result_rv: tuple | None = None

        form = QFormLayout(self)

        self._vec_edit = QLineEdit()
        self._vec_edit.setPlaceholderText("x  y  z  vx  vy  vz")
        self._vec_edit.setToolTip(
            "The six state components in the synodic rotating frame, "
            "separated by spaces and/or commas -- paste a catalog row "
            "as-is. Units per the selector below.")
        form.addRow("state vector", self._vec_edit)

        self._units = QComboBox()
        self._units.addItem("dimensional [km, km/s]")
        self._units.addItem("nondimensional")
        form.addRow("units", self._units)

        self._L_edit = QLineEdit()
        self._L_edit.setToolTip(
            "CR3BP characteristic length: the primary-primary "
            "separation the state was built with. Nondimensional "
            "input is scaled by it (velocity by L*omega, omega = "
            "sqrt((mu1+mu2)/L^3)); dimensional input is divided by "
            "it to recover the catalog state before the epoch's "
            "actual geometry is applied.")
        form.addRow("L [km]", self._L_edit)

        self._pair = QComboBox()
        for p in pairs:
            self._pair.addItem(f"{p['name1']} - {p['name2']}")
        form.addRow("primaries", self._pair)

        self._origin = QComboBox()
        form.addRow("coordinates centered on", self._origin)

        epoch_lbl = QLabel(f"{et_s!r}" + (f"   ({utc_text})"
                                          if utc_text else ""))
        epoch_lbl.setToolTip(
            "simulation.et_start_s from the form: the synodic frame "
            "is anchored to the actual primary geometry at this "
            "epoch. Change it in [simulation] and reopen to convert "
            "for a different epoch.")
        form.addRow("epoch [ET s]", epoch_lbl)
        form.addRow("output", QLabel(
            f"{out_body}-centered ICRF (frame = central_inertial)"))

        self._convert_btn = QPushButton("Convert")
        self._convert_btn.clicked.connect(self._on_convert)
        form.addRow(self._convert_btn)

        mono = "font-family: monospace;"
        self._res_r = QLabel("")
        self._res_r.setStyleSheet(mono)
        self._res_r.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._res_v = QLabel("")
        self._res_v.setStyleSheet(mono)
        self._res_v.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        self._res_info = QLabel("")
        self._res_info.setStyleSheet("color: gray;")
        form.addRow("position_km",  self._res_r)
        form.addRow("velocity_kms", self._res_v)
        form.addRow("", self._res_info)
        self._err = QLabel("")
        self._err.setStyleSheet("color: red;")
        self._err.setWordWrap(True)
        form.addRow(self._err)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel)
        self._insert_btn = buttons.button(
            QDialogButtonBox.StandardButton.Ok)
        self._insert_btn.setText("Insert into form")
        self._insert_btn.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

        self._pair.currentIndexChanged.connect(self._on_pair_changed)
        self._on_pair_changed(0)
        if prefill:
            self._vec_edit.setText(prefill.get("vector", ""))
            self._units.setCurrentIndex(int(prefill.get("units", 0)))
            self._pair.setCurrentIndex(int(prefill.get("pair", 0)))
            self._origin.setCurrentIndex(int(prefill.get("origin", 0)))
            if prefill.get("L"):
                self._L_edit.setText(prefill["L"])
        # Any input change invalidates a previous conversion: the
        # Insert button must never carry a stale state into the form.
        self._vec_edit.textChanged.connect(self._stale)
        self._L_edit.textChanged.connect(self._stale)
        self._units.currentIndexChanged.connect(self._stale)
        self._pair.currentIndexChanged.connect(self._stale)
        self._origin.currentIndexChanged.connect(self._stale)

    def snapshot(self) -> dict:
        """Field values, for prefilling the next open."""
        return {
            "vector": self._vec_edit.text(),
            "units":  self._units.currentIndex(),
            "pair":   self._pair.currentIndex(),
            "origin": self._origin.currentIndex(),
            "L":      self._L_edit.text(),
        }

    def _on_pair_changed(self, idx: int) -> None:
        p = self._pairs[idx]
        self._L_edit.setText(repr(float(p["L"])))
        prev = self._origin.currentIndex()
        self._origin.blockSignals(True)
        self._origin.clear()
        for name in ("barycenter", p["name1"], p["name2"]):
            self._origin.addItem(name)
        self._origin.setCurrentIndex(prev if 0 <= prev < 3 else 0)
        self._origin.blockSignals(False)

    def _stale(self, *_a) -> None:
        self.result_rv = None
        self._insert_btn.setEnabled(False)
        self._res_r.setText("")
        self._res_v.setText("")
        self._res_info.setText("")
        self._err.setText("")

    def _fail(self, msg: str) -> None:
        self._err.setText(msg)

    def _on_convert(self) -> None:
        self._stale()
        p = self._pairs[self._pair.currentIndex()]
        if self._eph is None:
            self._fail("No readable [ephemeris] file in the form -- "
                       "the epoch's actual primary geometry comes "
                       "from it. Pick one and reopen.")
            return
        if self._out_body not in (p["name1"], p["name2"]):
            self._fail(f"force_model.central_body is "
                       f"'{self._out_body}', which is not one of the "
                       f"selected primaries -- the converted state "
                       f"would be expressed around a different body "
                       f"than the engine propagates. Change the "
                       f"central body (or the pair) and reopen.")
            return
        toks = self._vec_edit.text().replace(",", " ").split()
        try:
            vals = [float(t) for t in toks]
        except ValueError:
            vals = []
        if len(vals) != 6:
            self._fail("The state vector needs exactly 6 numbers "
                       f"(got {len(vals)}).")
            return
        try:
            L = float(self._L_edit.text())
        except ValueError:
            L = 0.0
        if not L > 0.0:
            self._fail("L must be a positive length in km.")
            return

        mu1, mu2 = float(p["mu1"]), float(p["mu2"])
        mu_tot = mu1 + mu2
        mu = mu2 / mu_tot                    # CR3BP mass parameter
        omega_mean = math.sqrt(mu_tot / L ** 3)
        s = np.array(vals[:3], dtype=float)
        sv = np.array(vals[3:], dtype=float)
        if self._units.currentIndex() == 0:  # dimensional
            rho, rhop = s / L, sv / (L * omega_mean)
        else:
            rho, rhop = s, sv
        # Origin -> barycentric: primary_1 sits at x = -mu, primary_2
        # at x = 1-mu (nondim). A translation fixed in the rotating
        # frame leaves the rotating-frame velocity untouched.
        origin = self._origin.currentIndex()
        if origin == 1:
            rho = rho + np.array([-mu, 0.0, 0.0])
        elif origin == 2:
            rho = rho + np.array([1.0 - mu, 0.0, 0.0])

        # Instantaneous pulsating transform at the epoch. The relative
        # velocity is the exact ephemeris rate (analytic Chebyshev
        # derivative), not a finite difference.
        try:
            s12 = self._eph.state(p["naif1"], p["naif2"], self._et_s)
        except (ValueError, KeyError) as exc:
            self._fail(f"Ephemeris lookup failed at this epoch: {exc}")
            return
        r12, v12 = s12[:3], s12[3:]
        l_inst = float(np.linalg.norm(r12))
        l_dot = float(r12 @ v12) / l_inst
        h_vec = np.cross(r12, v12)
        x_hat = r12 / l_inst
        z_hat = h_vec / np.linalg.norm(h_vec)
        y_hat = np.cross(z_hat, x_hat)
        C = np.column_stack([x_hat, y_hat, z_hat])
        omega_vec = h_vec / l_inst ** 2
        t_star = math.sqrt(l_inst ** 3 / mu_tot)

        out_x = -mu if self._out_body == p["name1"] else 1.0 - mu
        drho = rho - np.array([out_x, 0.0, 0.0])
        r_icrf = l_inst * (C @ drho)
        v_icrf = (l_dot * (C @ drho) + (l_inst / t_star) * (C @ rhop)
                  + np.cross(omega_vec, r_icrf))

        self.result_rv = (r_icrf, v_icrf)
        self._res_r.setText("[" + ", ".join(f"{x:.9f}" for x in r_icrf)
                            + "]")
        self._res_v.setText("[" + ", ".join(f"{x:.12f}" for x in v_icrf)
                            + "]")
        self._res_info.setText(
            f"|r| = {np.linalg.norm(r_icrf):.1f} km from "
            f"{self._out_body} center; actual {p['name1']}-"
            f"{p['name2']} distance at epoch: {l_inst:.1f} km "
            f"(L/{L:g} ratio {l_inst / L:.6f})")
        self._insert_btn.setEnabled(True)
