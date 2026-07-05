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

"""Section builders: one `_build_<section>` per TOML table.

Adding a new engine section ([drag], ...) = one builder here + one
call in TomlForm.__init__ + its rows in the catalog/visibility
tables. Builders only assemble widgets via the factory mixin.
"""

from __future__ import annotations

import os

import numpy as np
from PySide6.QtGui import QFont, QIntValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .catalog import (
    CENTRAL_BODIES,
    CR3BP_L_KM,
    CR3BP_PAIRS,
    DYNAMICS_MODELS,
    FRAMES_BY_MODEL,
    INTEGRATORS,
    OUTPUT_CHECK_LABEL,
    OUTPUT_FILE_SUFFIX,
    OUTPUT_MODES,
    THIRD_BODIES_ALL,
    TOOLTIPS,
    unit_suffix,
)
from .widgets import hwrap


class SectionBuildersMixin:
    """Per-section UI builders mixed into TomlForm (incl. the
    [initial_state] representation swap cache and output
    auto-naming)."""

    # ==================================================================
    # Section builders
    # ==================================================================
    def _build_simulation(self) -> QGroupBox:
        g = QGroupBox("[simulation]")
        self._sim_form = QFormLayout(g)
        f = self._sim_form
        self._add_string(f, "simulation.name",       "name")
        # dynamics_model selects which downstream sections are needed.
        # Switching this combo hides / shows [cr3bp] vs the HF stack
        # (object / force_model / ephemeris) -- see
        # _on_dynamics_model_changed below.
        self._add_enum(f, "simulation.dynamics_model", "dynamics_model",
                       DYNAMICS_MODELS)
        # ET stays the only thing the TOML carries; the UTC cell next
        # to it is a typing aid driven by `→` / `←` convert buttons.
        self._add_et_with_utc(f, "simulation.et_start_s", "et_start_s")
        # Remember the et_start_s row so we can mark it optional when
        # the model is autonomous (cr3bp).
        self._sim_et_row = f.rowCount() - 1
        self._add_duration_seconds(f, "simulation.duration_s", "duration_s")

        dm_combo = self._widgets["simulation.dynamics_model"]
        dm_combo.currentTextChanged.connect(self._on_dynamics_model_changed)
        # et_start_s drives R_icrf_to_bf; touching it invalidates the
        # cached BF / inertial cross-derivations so the next toggle
        # re-derives from the current visible block. dyn_model also
        # invalidates because cr3bp flips which mu / synodic chain
        # _ic_block_to_cart_inertial picks.
        et_w = self._widgets.get("simulation.et_start_s")
        if isinstance(et_w, QLineEdit):
            et_w.editingFinished.connect(self._invalidate_ic_cache)
        dm_combo.currentTextChanged.connect(
            lambda _t: self._invalidate_ic_cache())
        return g

    def _add_et_with_utc(self, layout: QFormLayout, key: str,
                          label: str) -> None:
        """Composite row: ET (the TOML-bound float) on the left, UTC
        ISO 8601 (a display/typing aid, NOT serialised) on the right,
        with a vertical stack of two convert buttons between them:

            ┌──────────┐   ┌─→┐   ┌──────────────────────┐
            │ ET sec   │   └──┘   │ UTC ISO 8601         │
            │          │   ┌─←┐   │                      │
            └──────────┘   └──┘   └──────────────────────┘

        `→` fills the UTC cell from the current ET value; `←` does
        the inverse. Conversion goes through `spopy.time` which
        matches SPICE `str2et` to ~1 ns (see its module docstring)."""
        et_edit = QLineEdit()
        et_edit.textChanged.connect(self._touch)
        self._widgets[key] = et_edit
        self._float_keys.add(key)

        utc_edit = QLineEdit()
        utc_edit.setPlaceholderText("YYYY-MM-DDThh:mm:ss[.fff]Z")
        # UTC is NOT registered in self._widgets -- the TOML carries
        # only `et_start_s`; the UTC text is a transient display.

        from spopy.time import (
            et_to_utc, format_utc_iso, parse_utc_iso, utc_to_et,
        )

        def _et_to_utc() -> None:
            text = et_edit.text().strip()
            if not text:
                QMessageBox.information(
                    self, "ET → UTC", "Fill in et_start_s first.")
                return
            try:
                et = float(text)
            except ValueError:
                QMessageBox.warning(
                    self, "ET → UTC",
                    f"et_start_s is not a number: {text!r}")
                return
            utc_edit.setText(format_utc_iso(et_to_utc(et)))

        def _utc_to_et() -> None:
            text = utc_edit.text().strip()
            if not text:
                QMessageBox.information(
                    self, "UTC → ET", "Type a UTC ISO 8601 instant first "
                    "(e.g. 2009-09-18T12:00:00Z).")
                return
            try:
                dt = parse_utc_iso(text)
                et = utc_to_et(dt)
            except ValueError as exc:
                QMessageBox.warning(self, "UTC → ET", str(exc))
                return
            # repr() keeps every double-precision bit so a round-trip
            # UTC -> ET -> UTC stays exact at the cost of a long tail.
            et_edit.setText(repr(et))

        btn_to_utc = QPushButton("→")
        btn_to_utc.setToolTip("Compute UTC from this ET value (et_start_s → utc)")
        btn_to_utc.setMaximumWidth(28)
        btn_to_et  = QPushButton("←")
        btn_to_et.setToolTip("Compute ET from this UTC value (utc → et_start_s)")
        btn_to_et.setMaximumWidth(28)
        btn_to_utc.clicked.connect(_et_to_utc)
        btn_to_et.clicked.connect(_utc_to_et)

        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(2)
        btn_col.addWidget(btn_to_utc)
        btn_col.addWidget(btn_to_et)
        btn_wrap = QWidget()
        btn_wrap.setLayout(btn_col)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(et_edit, 1)
        row.addWidget(btn_wrap)
        row.addWidget(utc_edit, 1)
        layout.addRow(label + unit_suffix("et_start_s"), hwrap(row))

    def _build_object(self) -> QGroupBox:
        """Spacecraft XOR debris. A pair of radios at the top swaps
        which sub-section is visible; only the active sub-section's
        fields contribute to the emitted TOML."""
        g = QGroupBox("Object  --  [spacecraft] XOR [debris]")
        v = QVBoxLayout(g)

        self._radio_spc = QRadioButton("Spacecraft  (mass + area)")
        self._radio_dbr = QRadioButton("Debris      (A/m only)")
        self._radio_spc.setChecked(True)
        radio_row = QHBoxLayout()
        radio_row.addWidget(self._radio_spc)
        radio_row.addWidget(self._radio_dbr)
        radio_row.addStretch(1)
        v.addLayout(radio_row)

        # ---- Spacecraft box ----------------------------------------
        self._spc_box = QWidget()
        spc_form = QFormLayout(self._spc_box)
        self._add_float(spc_form, "spacecraft.mass_kg", "mass_kg")

        # Optional [spacecraft.srp] sub-block, gated by a checkbox.
        self._srp_check = QCheckBox("Enable [spacecraft.srp]")
        self._srp_check.toggled.connect(self._on_srp_toggled)
        self._srp_check.toggled.connect(lambda _: self._touch())
        spc_form.addRow("", self._srp_check)

        self._srp_box = QWidget()
        srp_form = QFormLayout(self._srp_box)

        # Inside [spacecraft.srp], area_m2 XOR am_srp.
        self._srp_radio_area = QRadioButton("area_m2 (derive A/m)")
        self._srp_radio_am   = QRadioButton("am_srp (direct A/m)")
        self._srp_radio_area.setChecked(True)
        srp_radio_row = QHBoxLayout()
        srp_radio_row.addWidget(self._srp_radio_area)
        srp_radio_row.addWidget(self._srp_radio_am)
        srp_radio_row.addStretch(1)
        srp_form.addRow("Parameter", hwrap(srp_radio_row))

        self._add_float(srp_form, "spacecraft.srp.area_m2", "area_m2")
        self._add_float(srp_form, "spacecraft.srp.am_srp",  "am_srp")
        self._add_float(srp_form, "spacecraft.srp.Cr",      "Cr")
        self._srp_radio_area.toggled.connect(self._on_srp_param_toggled)
        self._srp_radio_area.toggled.connect(lambda _: self._touch())
        spc_form.addRow(self._srp_box)
        self._srp_box.setVisible(False)
        self._on_srp_param_toggled()

        v.addWidget(self._spc_box)

        # ---- Debris box --------------------------------------------
        self._dbr_box = QWidget()
        dbr_form = QFormLayout(self._dbr_box)
        self._add_float(dbr_form, "debris.am_srp", "am_srp")
        self._add_float(dbr_form, "debris.Cr",     "Cr")
        v.addWidget(self._dbr_box)
        self._dbr_box.setVisible(False)

        self._radio_spc.toggled.connect(self._on_object_radio_toggled)
        self._radio_spc.toggled.connect(lambda _: self._touch())
        return g

    def _build_initial_state(self) -> QGroupBox:
        """Two-flavour [initial_state] form: a `kind` combo at the top
        swaps between the legacy Cartesian block (position_km +
        velocity_kms) and the Keplerian block (six classical elements
        + reference_body + anomaly_type). Switching `kind` after the
        user has typed values converts in place via spopy so nothing
        is lost; the swap is silent if any source field is empty /
        unparseable.

        The reference_body combo's items are filtered by the active
        dynamics_model -- HF defaults to 'central' (the only option);
        CR3BP exposes 'primary_1' and 'primary_2'."""
        g = QGroupBox("[initial_state]")
        v_outer = QVBoxLayout(g)
        v_outer.setContentsMargins(8, 6, 8, 6)

        # Top form row: frame + kind. Always visible.
        top = QFormLayout()
        top.setContentsMargins(0, 0, 0, 0)
        v_outer.addLayout(top)
        # Frame combo: items are filtered by the active dynamics model.
        # HF accepts `central_inertial` (engine-native) or
        # `central_body_fixed` (GUI-only: the form rotates the typed
        # values to ICRF at TOML emit so the engine sees
        # `central_inertial`). CR3BP only allows `synodic_rotating`.
        # Switching between `central_inertial` and `central_body_fixed`
        # does a live rotation of the currently typed cart / kep
        # values (same UX as the cartesian <-> keplerian swap).
        self._add_enum(top, "initial_state.frame", "frame",
                       FRAMES_BY_MODEL["high_fidelity"])
        self._add_enum(top, "initial_state.kind", "kind",
                       ("cartesian", "keplerian"))
        frame_combo = self._widgets["initial_state.frame"]
        frame_combo.currentTextChanged.connect(self._on_input_frame_changed)
        # Tracks the last frame so the converter knows which direction
        # to rotate. Synced to the combo on every successful swap.
        self._input_frame_prev: str = "central_inertial"

        # --- Cartesian block --------------------------------------------------
        self._init_cart_block = QWidget()
        cart_form = QFormLayout(self._init_cart_block)
        cart_form.setContentsMargins(0, 0, 0, 0)
        self._add_vec3(cart_form, "initial_state.position_km",  "position_km")
        self._add_vec3(cart_form, "initial_state.velocity_kms", "velocity_kms")
        v_outer.addWidget(self._init_cart_block)

        # --- Keplerian block --------------------------------------------------
        self._init_kep_block = QWidget()
        kep_form = QFormLayout(self._init_kep_block)
        kep_form.setContentsMargins(0, 0, 0, 0)
        # reference_body: cascade visibility / contents from dynamics_model.
        # Seed with the HF-only entry; _on_dynamics_model_changed reflows it.
        self._add_enum(kep_form, "initial_state.reference_body",
                       "reference_body", ("central",))
        self._add_float(kep_form, "initial_state.semi_major_axis_km",
                        "semi_major_axis_km")
        self._add_float(kep_form, "initial_state.eccentricity",
                        "eccentricity")
        self._add_float(kep_form, "initial_state.inclination_deg",
                        "inclination_deg")
        self._add_float(kep_form, "initial_state.raan_deg", "raan_deg")
        self._add_float(kep_form, "initial_state.arg_periapsis_deg",
                        "arg_periapsis_deg")
        self._add_float(kep_form, "initial_state.anomaly_deg", "anomaly_deg")
        self._add_enum(kep_form, "initial_state.anomaly_type",
                       "anomaly_type", ("true", "mean"))
        v_outer.addWidget(self._init_kep_block)

        # Default kind = cartesian -> hide kep block.
        self._init_kep_block.setVisible(False)

        # Wire the kind combo: on change, convert (best-effort) and swap.
        kind_combo = self._widgets["initial_state.kind"]
        kind_combo.currentTextChanged.connect(self._on_init_kind_changed)
        # Wire editingFinished on every cart / kep widget so each
        # finalised edit recomputes the four-representation cache.
        # Sub-table inside the form-level _ic_cache so toggles between
        # (kind, frame) become lossless lookups rather than chained
        # spopy conversions that drift after a few flips.
        for vec_key in ("initial_state.position_km",
                         "initial_state.velocity_kms"):
            trio = self._widgets.get(vec_key)
            if isinstance(trio, tuple):
                for le in trio:
                    le.editingFinished.connect(self._on_ic_field_finished)
        for k_key in ("initial_state.semi_major_axis_km",
                       "initial_state.eccentricity",
                       "initial_state.inclination_deg",
                       "initial_state.raan_deg",
                       "initial_state.arg_periapsis_deg",
                       "initial_state.anomaly_deg"):
            w = self._widgets.get(k_key)
            if isinstance(w, QLineEdit):
                w.editingFinished.connect(self._on_ic_field_finished)
        # Anomaly type and reference body re-interpret the cached
        # keplerian numbers, so flipping either invalidates the
        # cache wholesale (the user has to touch a field to repopulate
        # -- same UX as today, but consistent).
        for combo_key in ("initial_state.anomaly_type",
                           "initial_state.reference_body"):
            combo = self._widgets.get(combo_key)
            if isinstance(combo, QComboBox):
                combo.currentTextChanged.connect(
                    lambda _t: self._invalidate_ic_cache())
        return g

    def _build_cr3bp(self) -> QGroupBox:
        """[cr3bp]: pick a primary pair from the curated table. The
        combos cascade: primary_2 is filtered to the pairs registered
        with the currently-selected primary_1, so a user cannot pick a
        pair the engine would reject. Visible only when
        dynamics_model = 'cr3bp'."""
        g = QGroupBox("[cr3bp]")
        f = QFormLayout(g)

        primaries_1 = sorted({p1 for p1, _ in CR3BP_PAIRS})
        self._add_enum(f, "cr3bp.primary_1", "primary_1", tuple(primaries_1))
        # primary_2 starts wide; _on_cr3bp_primary_1_changed narrows it.
        self._add_enum(f, "cr3bp.primary_2", "primary_2",
                       tuple(sorted({p2 for _, p2 in CR3BP_PAIRS})))
        p1_combo = self._widgets["cr3bp.primary_1"]
        p2_combo = self._widgets["cr3bp.primary_2"]
        p1_combo.currentTextChanged.connect(self._on_cr3bp_primary_1_changed)
        # primary_2 also feeds the altitude-crossing valid-body list;
        # _on_cr3bp_primary_1_changed already refreshes it for p1
        # changes (and for the cascaded p2 narrow-down inside that
        # handler), so we only need a separate hook for a direct p2 pick.
        p2_combo.currentTextChanged.connect(
            lambda _t: self._refresh_altcross_body_options())
        self._on_cr3bp_primary_1_changed(p1_combo.currentText())
        return g

    def _on_cr3bp_primary_1_changed(self, p1: str) -> None:
        """Refresh primary_2 options to only those that form a
        registered pair with the just-selected primary_1."""
        valid = [p2 for q1, p2 in CR3BP_PAIRS if q1 == p1]
        combo = self._widgets.get("cr3bp.primary_2")
        if not isinstance(combo, QComboBox):
            return
        prev = combo.currentText()
        combo.blockSignals(True)
        combo.clear()
        for name in valid:
            combo.addItem(name)
        idx = combo.findText(prev)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.blockSignals(False)
        # Primary change flows into the altitude-crossing body combo's
        # valid-name set under CR3BP (the only two bodies the engine
        # can resolve are the two primaries).
        self._refresh_altcross_body_options()

    def _build_force_model(self) -> QGroupBox:
        g = QGroupBox("[force_model]")
        f = QFormLayout(g)
        self._add_enum (f, "force_model.central_body",     "central_body", CENTRAL_BODIES)
        # Harmonics combo: filtered by central_body so once Earth /
        # Mars / etc. land as supported central bodies, the dropdown
        # only ever shows the gravity models that apply to the
        # currently-selected body.
        self._add_asset_combo(
            f, "force_model.harmonics_file", "harmonics_file",
            category="harmonics", body_key="force_model.central_body",
        )
        # Custom row: int field + a sticky 'max num / max model' info
        # label + a 'Suggest...' button that shells out to the new C
        # CLI `spody maxhgdegree`. The CLI reads the actual Cnm/Snm
        # from the harmonics file and walks degree-by-degree against
        # the ULP noise floor seeded by the J2 contribution -- way
        # more accurate than a Kaula-rule estimate (which was the
        # first cut and got bounced for predicting too low a degree
        # on rough fields like GRGM1200B over the Moon).
        hd_edit = QLineEdit()
        # Cap matches the engine's TOML-schema ceiling. The per-body
        # effective max (e.g. 1200 for GRGM1200B Moon, 2190 for
        # EIGEN-6C4 Earth) is enforced by the engine at load time
        # against the actual harmonics file. Suggest... helper queries
        # the file directly via `spody maxhgdegree`.
        hd_edit.setValidator(QIntValidator(2, 2200, hd_edit))
        hd_edit.textChanged.connect(self._touch)
        hd_edit.textChanged.connect(
            lambda _t: self._validate_field("force_model.harmonics_degree"))
        self._widgets["force_model.harmonics_degree"] = hd_edit
        # Sticky info label, populated on each Suggest run. Hidden
        # before the first run since the numbers depend on the chosen
        # harmonics file AND the current initial state -- showing a
        # value before the user has filled either would be misleading.
        self._hd_info = QLabel("")
        self._hd_info.setStyleSheet("color: gray;")
        self._hd_info.setVisible(False)
        hd_suggest = QPushButton("Suggest...")
        hd_suggest.setToolTip(
            "Run the C engine to compute the largest harmonics degree "
            "whose Cnm/Snm contribution at the current orbit altitude "
            "still rises above double-precision noise. Going beyond it "
            "costs CPU (per-step work is O(N^2)) with no measurable "
            "accuracy gain.")
        hd_suggest.clicked.connect(self._suggest_harmonics_degree)
        hd_row = QHBoxLayout()
        hd_row.setContentsMargins(0, 0, 0, 0)
        hd_row.addWidget(hd_edit, 1)
        hd_row.addWidget(self._hd_info)
        hd_row.addWidget(hd_suggest)
        f.addRow("harmonics_degree", hwrap(hd_row))

        # Earth-only fields. Visible (and written to TOML) only when
        # central_body == "Earth"; for the Moon (and any other body
        # without an EOP / IAU 2006 dependency) the engine schema
        # forbids them. We keep them in [force_model] for proximity
        # with the rotation provider they configure.
        self._fm_eop_row = self._add_path(
            f, "force_model.eop_file", "eop_file",
            "IERS EOP (*.all *.dat *.txt);;All Files (*)",
        )
        self._fm_iau_row = self._add_path(
            f, "force_model.iau2006_dir", "iau2006_dir",
            "", pick_dir=True,
        )

        self._add_strlist_checks(f, "force_model.third_bodies", "third_bodies",
                                 THIRD_BODIES_ALL)
        self._add_bool (f, "force_model.srp", "srp")

        # Hook visibility of the Earth-only rows to the central_body
        # combo. Initial sync uses the combo's current value (set by
        # the enum widget's default selection -- Moon).
        self._fm_force_form = f
        cb_combo = self._widgets["force_model.central_body"]
        cb_combo.currentTextChanged.connect(self._on_central_body_changed)
        cb_combo.currentTextChanged.connect(
            lambda _t: self._invalidate_ic_cache())
        self._on_central_body_changed(cb_combo.currentText())
        return g

    def _on_central_body_changed(self, body: str) -> None:
        """Show / hide the Earth-only [force_model] fields, and
        auto-fill the path widgets with their canonical data-dir
        defaults the first time the user switches to Earth.

        When the engine sees `central_body = "Earth"` it requires
        `eop_file` and `iau2006_dir`; for any other body those fields
        must be absent. We mirror that by hiding the rows so the user
        never fills them for a non-Earth run.

        Defaults come from `<data_dir>/eop/finals2000A.all` and
        `<data_dir>/iau2006/` (the relpaths defined by the wizard's
        EOP_FILE + IAU2006_TAB_* assets). We only seed empty widgets:
        a user-typed override survives a Moon -> Earth -> Moon -> Earth
        round-trip.
        """
        if not hasattr(self, "_fm_force_form"):
            return  # called too early during construction
        is_earth = (body.strip().lower() == "earth")
        for row in (self._fm_eop_row, self._fm_iau_row):
            self._fm_force_form.setRowVisible(row, is_earth)

        if is_earth and self._store is not None:
            data_root = self._store.data_dir()
            eop_w = self._widgets.get("force_model.eop_file")
            iau_w = self._widgets.get("force_model.iau2006_dir")
            if isinstance(eop_w, QLineEdit) and not eop_w.text().strip():
                eop_w.setText(str(data_root / "eop" / "finals2000A.all"))
            if isinstance(iau_w, QLineEdit) and not iau_w.text().strip():
                iau_w.setText(str(data_root / "iau2006"))
        # Refresh the input_frame combo: the BF radio's label tracks the
        # central body's bf_frame_name (ITRS for Earth, PA for Moon).
        self._refresh_input_frame_availability()
        # Altitude-crossing rows reference body by name; central body
        # change is one of the inputs the valid-bodies list depends on.
        self._refresh_altcross_body_options()

    def _build_ephemeris(self) -> QGroupBox:
        g = QGroupBox("[ephemeris]")
        f = QFormLayout(g)
        # Ephemeris combo: body-agnostic. DE-series ephemerides cover
        # every planet at once, so the dropdown does not depend on
        # the central body.
        self._add_asset_combo(
            f, "ephemeris.file", "file",
            category="ephemeris", body_key=None,
        )
        # Lunar BF rotation reads from this ephemeris; swapping
        # ephemerides invalidates the cached BF representations.
        eph_combo = self._widgets.get("ephemeris.file")
        if isinstance(eph_combo, QComboBox):
            eph_combo.currentIndexChanged.connect(
                lambda _i: self._invalidate_ic_cache())
        return g

    def _build_integrator(self) -> QGroupBox:
        g = QGroupBox("[integrator]")
        f = QFormLayout(g)
        self._add_enum (f, "integrator.type",     "type", INTEGRATORS)
        self._add_float(f, "integrator.rel_tol",  "rel_tol")
        self._add_float(f, "integrator.h_init_s", "h_init_s")
        self._add_float(f, "integrator.h_min_s",  "h_min_s")
        self._add_float(f, "integrator.h_max_s",  "h_max_s")
        return g

    def _build_output(self) -> QGroupBox:
        g = QGroupBox("[output]")
        # Stash the QFormLayout + the row index of interval_s so we can
        # toggle its visibility via QFormLayout.setRowVisible when the
        # mode combo changes (interval_s only applies to mode='fixed').
        self._output_form = QFormLayout(g)
        f = self._output_form
        self._add_enum (f, "output.mode",               "mode", OUTPUT_MODES)
        self._add_float(f, "output.interval_s",         "interval_s")
        self._output_interval_row = f.rowCount() - 1

        # output_dir: directory that holds all enabled streams. Empty =
        # the TOML's own folder. The 5 file paths below are auto-derived
        # from this + [simulation].name + a standard suffix, so the
        # user only ever picks ONE directory (not five paths).
        self._add_path(f, "output.output_dir", "output_dir", "",
                       pick_dir=True)

        # Five on/off toggles for the optional output streams. The file
        # path that spody.exe sees is composed at Generate time as
        #     <output_dir>/<sim_name><suffix>
        # using OUTPUT_FILE_SUFFIX. Path fields are still emitted as
        # `output.csv_file` etc. -- the engine sees no schema change.
        for key in (
            "output.csv_file",
            "output.bin_file",
            "output.accelerations_file",
            "output.events_log",
            "output.log_file",
        ):
            cb = QCheckBox(OUTPUT_CHECK_LABEL[key])
            cb.toggled.connect(self._touch)
            cb.toggled.connect(lambda _checked: self._refresh_output_preview())
            self._widgets[key] = cb
            f.addRow("", cb)

        # Live preview of the paths the next Generate will emit. Sits
        # below the checkboxes so the user can sanity-check the
        # combination of output_dir + sim_name + which streams are on.
        self._output_preview_label = QLabel("")
        self._output_preview_label.setStyleSheet("color: gray;")
        self._output_preview_label.setWordWrap(True)
        f.addRow("paths preview", self._output_preview_label)
        # Auto-refresh when output_dir or simulation.name change.
        self._widgets["output.output_dir"].textChanged.connect(
            lambda _t: self._refresh_output_preview())
        # `simulation.name` is wired later (when _build_simulation has
        # registered it); the deferred connection happens at the bottom
        # of __init__ via _wire_output_preview_dependencies.

        # Wire interval_s visibility to the mode combo. Note: the
        # field's value is also stripped from to_dict when mode='step'
        # so a stale value left over from a prior 'fixed' run does
        # not leak into the emitted TOML.
        mode_combo = self._widgets["output.mode"]
        mode_combo.currentTextChanged.connect(self._on_output_mode_changed)
        self._on_output_mode_changed(mode_combo.currentText())
        return g

    def _on_output_mode_changed(self, mode: str) -> None:
        if not hasattr(self, "_output_form"):
            return   # called too early during construction
        self._output_form.setRowVisible(self._output_interval_row,
                                        mode == "fixed")

    # ------------------------------------------------------------------
    # [initial_state] kind swap (cartesian <-> keplerian)
    # ------------------------------------------------------------------
    def _on_init_kind_changed(self, kind: str) -> None:
        """Swap visibility between the cart / kep blocks. Tries the
        IC cache first (the four-representation snapshot kept in
        sync by `_on_ic_field_finished`): a cache hit writes the
        destination block verbatim, with NO spopy conversion at
        toggle time -- so back-and-forth flips no longer accumulate
        ULP drift. Falls back to the legacy on-the-fly conversion
        when the cache is empty for the destination view (first
        toggle of the session, or after an invalidating change).

        During load the conversion is skipped (the loader populates
        the destination block from the TOML directly); only the
        visibility toggle still runs so the right block is on screen."""
        is_kep = (kind == "keplerian")
        if not self._loading:
            # Toggle handlers NEVER re-seed the cache (re-seeding
            # here would re-derive the destination view through
            # cart_inertial every time, defeating the whole point
            # of the cache -- BF<->ICRF<->BF would still drift two
            # conversions per round-trip). The cache is filled by
            # editingFinished only; if there's no cached entry for
            # the destination view we fall back to legacy
            # conversion + repopulate the cache once from that.
            _, current_frame = self._ic_current_view()
            if not self._apply_ic_cache_to_widgets(kind, current_frame):
                if is_kep:
                    self._convert_cart_to_kep()
                else:
                    self._convert_kep_to_cart()
                # Capture the freshly-converted destination block
                # so subsequent toggles round-trip cleanly. The
                # visibility flip below has not happened yet -- we
                # use an explicit view rather than _ic_current_view
                # because the just-written widgets ARE the new
                # destination block.
                self._seed_ic_cache_from_view((kind, current_frame))
        self._init_cart_block.setVisible(not is_kep)
        self._init_kep_block.setVisible(is_kep)
        self._touch()

    # ------------------------------------------------------------------
    # [initial_state] four-representation cache
    # ------------------------------------------------------------------
    # Toggles between (cartesian / keplerian) x (central_inertial /
    # central_body_fixed) used to chain spopy conversions on every
    # click, which accumulated ULP noise after a few back-and-forth
    # flips. With the cache, every finalised edit re-derives all four
    # representations once and the toggle handlers just look up the
    # destination view -- no further conversions, no compounding drift.

    _IC_VARIANTS: tuple[tuple[str, str], ...] = (
        ("cartesian", "central_inertial"),
        ("cartesian", "central_body_fixed"),
        ("keplerian", "central_inertial"),
        ("keplerian", "central_body_fixed"),
    )

    _IC_CART_KEYS = (
        "initial_state.position_km",
        "initial_state.velocity_kms",
    )
    _IC_KEP_KEYS = (
        "initial_state.semi_major_axis_km",
        "initial_state.eccentricity",
        "initial_state.inclination_deg",
        "initial_state.raan_deg",
        "initial_state.arg_periapsis_deg",
        "initial_state.anomaly_deg",
    )

    def _invalidate_ic_cache(self) -> None:
        """Drop every cached IC representation. Triggered by changes
        to settings the cached values depend on (et_start_s,
        central_body, dynamics_model, anomaly_type, reference_body).
        The next finalised edit repopulates."""
        self._ic_cache.clear()

    def _on_ic_field_finished(self) -> None:
        """User finished editing a cart / kep field (focus loss or
        Enter): the currently-visible block is the ground truth, so
        re-derive every other representation from it and snapshot
        all four into `_ic_cache`. No-op during load (the legacy
        load path writes the widgets directly and we don't want a
        partial intermediate state to overwrite the loaded values
        in the cache)."""
        if self._loading:
            return
        self._seed_ic_cache_from_visible()

    def _ic_current_view(self) -> tuple[str, str]:
        """Return the (kind, frame) of the currently visible block,
        defaulting to the legacy ('cartesian', 'central_inertial')
        when the combos are not yet built."""
        kind_combo  = self._widgets.get("initial_state.kind")
        frame_combo = self._widgets.get("initial_state.frame")
        kind  = (kind_combo.currentText()
                 if isinstance(kind_combo, QComboBox) else "cartesian")
        frame = (frame_combo.currentText()
                 if isinstance(frame_combo, QComboBox)
                 else "central_inertial")
        return kind, frame

    def _snapshot_ic_block(self, kind: str) -> dict | None:
        """Read the kep / cart widgets for `kind` and return a dict
        of `{widget_key: parsed_value}`, or None when any required
        field is empty / unparseable (the cache stays as it was)."""
        if kind == "cartesian":
            r = self._read_vec3("initial_state.position_km")
            v = self._read_vec3("initial_state.velocity_kms")
            if r is None or v is None:
                return None
            return {
                "initial_state.position_km":  [float(x) for x in r],
                "initial_state.velocity_kms": [float(x) for x in v],
            }
        # Keplerian
        out: dict[str, float] = {}
        for k in self._IC_KEP_KEYS:
            v = self._read_float(k)
            if v is None:
                return None
            out[k] = float(v)
        return out

    def _ic_block_to_cart_inertial(self, block: dict, kind: str, frame: str
                                    ) -> tuple[np.ndarray, np.ndarray] | None:
        """Convert `block` (in the given (kind, frame)) to canonical
        cartesian-inertial via spopy. Returns (r, v) or None on
        failure (missing mu, missing et_start, missing rotation
        provider, degenerate kep)."""
        import math
        import numpy as np
        if kind == "cartesian":
            r = np.asarray(block["initial_state.position_km"],  dtype=float)
            v = np.asarray(block["initial_state.velocity_kms"], dtype=float)
        else:
            ctx = self._resolve_kep_mu_and_synodic()
            if ctx is None:
                return None
            mu_ref, _synodic = ctx
            anom_combo = self._widgets.get("initial_state.anomaly_type")
            anom_type = (anom_combo.currentText()
                         if isinstance(anom_combo, QComboBox) else "true")
            try:
                from spopy import keplerian_to_cartesian, mean_to_true_anom
                nu = math.radians(block["initial_state.anomaly_deg"])
                if anom_type == "mean":
                    nu = mean_to_true_anom(nu, block["initial_state.eccentricity"])
                r, v = keplerian_to_cartesian(
                    block["initial_state.semi_major_axis_km"],
                    block["initial_state.eccentricity"],
                    math.radians(block["initial_state.inclination_deg"]),
                    math.radians(block["initial_state.raan_deg"]),
                    math.radians(block["initial_state.arg_periapsis_deg"]),
                    nu, mu_ref)
                r = np.asarray(r, dtype=float)
                v = np.asarray(v, dtype=float)
            except (ValueError, ZeroDivisionError):
                return None
        if frame == "central_body_fixed":
            et = self._form_et_start()
            if et is None:
                return None
            R_icrf_to_bf = self._resolve_bf_rotation(et)
            if R_icrf_to_bf is None:
                return None
            R_bf_to_icrf = R_icrf_to_bf.T
            r = R_bf_to_icrf @ r
            v = R_bf_to_icrf @ v
        return r, v

    def _ic_block_from_cart_inertial(self, r: "np.ndarray", v: "np.ndarray",
                                      kind: str, frame: str) -> dict | None:
        """Inverse of `_ic_block_to_cart_inertial`: build the
        (kind, frame) block dict from canonical cartesian-inertial.
        None when prerequisites are missing for the destination
        (mu / et_start / rotation provider) or kep extraction
        fails."""
        import math
        import numpy as np
        if frame == "central_body_fixed":
            et = self._form_et_start()
            if et is None:
                return None
            R_icrf_to_bf = self._resolve_bf_rotation(et)
            if R_icrf_to_bf is None:
                return None
            r_dst = R_icrf_to_bf @ r
            v_dst = R_icrf_to_bf @ v
        else:
            r_dst = np.asarray(r, dtype=float)
            v_dst = np.asarray(v, dtype=float)
        if kind == "cartesian":
            return {
                "initial_state.position_km":  [float(x) for x in r_dst],
                "initial_state.velocity_kms": [float(x) for x in v_dst],
            }
        ctx = self._resolve_kep_mu_and_synodic()
        if ctx is None:
            return None
        mu_ref, _synodic = ctx
        try:
            from spopy import cartesian_to_keplerian, true_to_mean_anom
            el = cartesian_to_keplerian(r_dst, v_dst, mu_ref)
        except (ValueError, ZeroDivisionError):
            return None
        anom_combo = self._widgets.get("initial_state.anomaly_type")
        anom_type = (anom_combo.currentText()
                     if isinstance(anom_combo, QComboBox) else "true")
        nu = float(el["true_anom_rad"])
        anom = (true_to_mean_anom(nu, float(el["ecc"]))
                if anom_type == "mean" else nu)
        return {
            "initial_state.semi_major_axis_km": float(el["sma_km"]),
            "initial_state.eccentricity":       float(el["ecc"]),
            "initial_state.inclination_deg":    math.degrees(float(el["inc_rad"])),
            "initial_state.raan_deg":           math.degrees(float(el["raan_rad"])),
            "initial_state.arg_periapsis_deg":  math.degrees(float(el["argp_rad"])),
            "initial_state.anomaly_deg":        math.degrees(anom),
        }

    def _seed_ic_cache_from_visible(self) -> None:
        """Re-derive all four IC representations from the currently
        visible block (defined by `_ic_current_view`) and store them
        in `_ic_cache`. Use the explicit-view variant
        `_seed_ic_cache_from_view` when the caller knows the source
        view is NOT what `_ic_current_view` would report (typically
        a toggle handler where the combo has already moved to the
        destination)."""
        self._seed_ic_cache_from_view(self._ic_current_view())

    def _seed_ic_cache_from_view(self, view: tuple[str, str]) -> None:
        """Snapshot the `view = (kind, frame)` block (visible or
        not -- we read the kind's widgets directly), then derive
        the other three representations from it via spopy and cache
        all four. Silent on incomplete / unparseable / degenerate
        source: the cache stays as it was."""
        kind, frame = view
        src = self._snapshot_ic_block(kind)
        if src is None:
            return
        # Wipe so a stale entry doesn't survive a re-seed where the
        # other-3 conversions fail.
        self._ic_cache.clear()
        self._ic_cache[(kind, frame)] = src
        ri_vi = self._ic_block_to_cart_inertial(src, kind, frame)
        if ri_vi is None:
            return
        ri, vi = ri_vi
        for (k, f) in self._IC_VARIANTS:
            if (k, f) == (kind, frame):
                continue
            block = self._ic_block_from_cart_inertial(ri, vi, k, f)
            if block is not None:
                self._ic_cache[(k, f)] = block

    def _apply_ic_cache_to_widgets(self, kind: str, frame: str) -> bool:
        """Write the cached (kind, frame) representation into the
        cart / kep widgets without going through spopy. Returns True
        on hit + write, False when the cache has no entry for that
        view (the caller then falls back to the legacy convert-on-
        toggle path). Signal-blocking is done at the widget level
        so the textChanged-driven `_touch` still fires (the user's
        intent IS modification)."""
        block = self._ic_cache.get((kind, frame))
        if block is None:
            return False
        if kind == "cartesian":
            r = block.get("initial_state.position_km")
            v = block.get("initial_state.velocity_kms")
            if r is None or v is None:
                return False
            self._write_vec3("initial_state.position_km",  r)
            self._write_vec3("initial_state.velocity_kms", v)
        else:
            for k in self._IC_KEP_KEYS:
                v = block.get(k)
                if v is None:
                    return False
                self._write_float(k, float(v))
        return True

    def _resolve_kep_mu_and_synodic(self) -> tuple[float, dict] | None:
        """Look up the reference-body mu (+ CR3BP geometry when needed)
        for the current dynamics_model + reference_body combo. Returns
        `(mu_km3_s2, synodic_ctx)` where synodic_ctx is either None
        (HF) or a dict {mu1, mu2, L, primary_index} that the two
        conversion helpers feed into spopy.synodic_to_inertial /
        inertial_to_synodic. Returns None when anything cannot be
        resolved (unknown body, missing CR3BP pair, ...)."""
        dm_combo = self._widgets.get("simulation.dynamics_model")
        dyn_model = (dm_combo.currentText()
                     if isinstance(dm_combo, QComboBox) else "high_fidelity")
        if dyn_model == "cr3bp":
            ref_combo = self._widgets.get("initial_state.reference_body")
            ref = ref_combo.currentText() if isinstance(ref_combo, QComboBox) else ""
            if ref not in ("primary_1", "primary_2"):
                return None
            p1_combo = self._widgets.get("cr3bp.primary_1")
            p2_combo = self._widgets.get("cr3bp.primary_2")
            p1 = p1_combo.currentText() if isinstance(p1_combo, QComboBox) else ""
            p2 = p2_combo.currentText() if isinstance(p2_combo, QComboBox) else ""
            from ..central_bodies import resolve_central_body
            spec1 = resolve_central_body(p1)
            spec2 = resolve_central_body(p2)
            L = CR3BP_L_KM.get((p1, p2))
            if spec1 is None or spec2 is None or L is None:
                return None
            primary_index = 1 if ref == "primary_1" else 2
            mu_ref = spec1.mu_km3_s2 if primary_index == 1 else spec2.mu_km3_s2
            return mu_ref, {
                "mu1": spec1.mu_km3_s2, "mu2": spec2.mu_km3_s2,
                "L":   L,               "primary_index": primary_index,
            }
        # HF path: mu = central body's GM.
        cb_combo = self._widgets.get("force_model.central_body")
        cb_name = cb_combo.currentText() if isinstance(cb_combo, QComboBox) else ""
        from ..central_bodies import resolve_central_body
        spec = resolve_central_body(cb_name)
        if spec is None:
            return None
        return spec.mu_km3_s2, None

    def _read_vec3(self, key: str) -> "np.ndarray | None":
        import numpy as np  # local: avoid loading numpy at form-import time
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple) or len(widgets) != 3:
            return None
        out = []
        for w in widgets:
            txt = w.text().strip()
            if not txt:
                return None
            try:
                out.append(float(txt))
            except ValueError:
                return None
        return np.array(out)

    def _write_vec3(self, key: str, vals) -> None:
        widgets = self._widgets.get(key)
        if not isinstance(widgets, tuple) or len(widgets) != 3:
            return
        for w, v in zip(widgets, vals):
            w.setText(repr(float(v)))

    def _read_float(self, key: str) -> float | None:
        w = self._widgets.get(key)
        if not isinstance(w, QLineEdit):
            return None
        txt = w.text().strip()
        if not txt:
            return None
        try:
            return float(txt)
        except ValueError:
            return None

    def _write_float(self, key: str, v: float) -> None:
        w = self._widgets.get(key)
        if isinstance(w, QLineEdit):
            w.setText(repr(float(v)))

    def _convert_cart_to_kep(self) -> None:
        """Cartesian -> Keplerian. For CR3BP this first rotates the
        synodic state back into the reference primary's inertial frame
        before extracting the elements."""
        import math
        r = self._read_vec3("initial_state.position_km")
        v = self._read_vec3("initial_state.velocity_kms")
        if r is None or v is None:
            return
        ctx = self._resolve_kep_mu_and_synodic()
        if ctx is None:
            return
        mu_ref, synodic_ctx = ctx
        try:
            from spopy import (cartesian_to_keplerian, synodic_to_inertial,
                               true_to_mean_anom)
            if synodic_ctx is not None:
                r_in, v_in = synodic_to_inertial(
                    r, v, synodic_ctx["mu1"], synodic_ctx["mu2"],
                    synodic_ctx["L"], synodic_ctx["primary_index"])
            else:
                r_in, v_in = r, v
            el = cartesian_to_keplerian(r_in, v_in, mu_ref)
        except (ValueError, ZeroDivisionError):
            return
        self._write_float("initial_state.semi_major_axis_km", el["sma_km"])
        self._write_float("initial_state.eccentricity",       el["ecc"])
        self._write_float("initial_state.inclination_deg",    math.degrees(el["inc_rad"]))
        self._write_float("initial_state.raan_deg",           math.degrees(el["raan_rad"]))
        self._write_float("initial_state.arg_periapsis_deg",  math.degrees(el["argp_rad"]))
        # Honour the current anomaly_type so a round-trip keplerian ->
        # cartesian -> keplerian preserves the user's choice between
        # true and mean.
        anom_combo = self._widgets.get("initial_state.anomaly_type")
        anom_type = (anom_combo.currentText()
                     if isinstance(anom_combo, QComboBox) else "true")
        nu_rad = el["true_anom_rad"]
        if anom_type == "mean":
            anom_rad = true_to_mean_anom(nu_rad, el["ecc"])
        else:
            anom_rad = nu_rad
        self._write_float("initial_state.anomaly_deg", math.degrees(anom_rad))

    def _convert_kep_to_cart(self) -> None:
        """Keplerian -> Cartesian. For CR3BP, the primary-centered
        inertial state is then mapped into the synodic frame at t = 0."""
        import math
        sma = self._read_float("initial_state.semi_major_axis_km")
        ecc = self._read_float("initial_state.eccentricity")
        inc = self._read_float("initial_state.inclination_deg")
        raan = self._read_float("initial_state.raan_deg")
        argp = self._read_float("initial_state.arg_periapsis_deg")
        anom = self._read_float("initial_state.anomaly_deg")
        if (sma is None or ecc is None or inc is None or raan is None
                or argp is None or anom is None):
            return
        ctx = self._resolve_kep_mu_and_synodic()
        if ctx is None:
            return
        mu_ref, synodic_ctx = ctx
        anom_combo = self._widgets.get("initial_state.anomaly_type")
        anom_type = (anom_combo.currentText()
                     if isinstance(anom_combo, QComboBox) else "true")
        try:
            from spopy import (keplerian_to_cartesian, inertial_to_synodic,
                               mean_to_true_anom)
            anom_rad = math.radians(anom)
            if anom_type == "mean":
                anom_rad = mean_to_true_anom(anom_rad, ecc)
            r_in, v_in = keplerian_to_cartesian(
                sma, ecc, math.radians(inc), math.radians(raan),
                math.radians(argp), anom_rad, mu_ref)
            if synodic_ctx is not None:
                r, v = inertial_to_synodic(
                    r_in, v_in, synodic_ctx["mu1"], synodic_ctx["mu2"],
                    synodic_ctx["L"], synodic_ctx["primary_index"])
            else:
                r, v = r_in, v_in
        except (ValueError, ZeroDivisionError):
            return
        self._write_vec3("initial_state.position_km",  r)
        self._write_vec3("initial_state.velocity_kms", v)

    def _resolve_bf_rotation(self, et_s: float) -> "np.ndarray | None":
        """Return R_icrf_to_bf at ET `et_s` for the form's currently
        selected central body, or None on any failure (missing EOP /
        ephemeris, unsupported body, CR3BP run). Mirrors the
        orientation pipeline `central_bodies._earth_orientation` /
        `_moon_orientation` use at scene-render time, so the BF
        defined here matches the one the analysis tab shows."""
        dm_combo = self._widgets.get("simulation.dynamics_model")
        dyn_model = (dm_combo.currentText()
                     if isinstance(dm_combo, QComboBox) else "high_fidelity")
        if dyn_model == "cr3bp":
            return None
        cb_combo = self._widgets.get("force_model.central_body")
        cb_name = (cb_combo.currentText()
                   if isinstance(cb_combo, QComboBox) else "")
        from ..central_bodies import resolve_central_body
        spec = resolve_central_body(cb_name)
        if spec is None or spec.bf_orientation is None:
            return None
        # Earth orientation reads EOP from data_dir and ignores its
        # ephemeris argument; Moon orientation needs the ephemeris.
        # Build the latter lazily from the form's ephemeris.file
        # field; cache for subsequent calls in the same session so
        # switching frames many times does not re-load DE440 every
        # tick.
        eph = None
        if spec.name == "Moon":
            eph = self._cached_ephemeris_for_form()
            if eph is None:
                return None
        try:
            import numpy as np
            return np.asarray(spec.bf_orientation(float(et_s), eph), dtype=float)
        except Exception:
            return None

    def _cached_ephemeris_for_form(self):
        """Lazy + per-session cached spopy.Ephemeris built from the
        form's `ephemeris.file` widget value. Returns None when the
        path is empty or unreadable. Used only by the BF-rotation
        helper -- the engine loads its own copy at sim setup.

        `ephemeris.file` is an `AssetCombo` (QComboBox subclass)
        whose visible text is just the basename of the picked file;
        the absolute path lives in the item's userData and is what
        the rest of the form (and the TOML emitter) reads via
        `_widget_value`. Querying `.text()` here used to return the
        basename and silently fail to find the file on disk."""
        eph_w = self._widgets.get("ephemeris.file")
        if isinstance(eph_w, QComboBox):
            data = eph_w.currentData()
            path = str(data).strip() if data else ""
        elif isinstance(eph_w, QLineEdit):
            path = eph_w.text().strip()
        else:
            path = ""
        if not path:
            return None
        # Cache key is the path string; switching ephemeris files in
        # the form invalidates the cache so the new one is loaded on
        # the next call.
        cached = getattr(self, "_form_ephemeris_cache", None)
        if cached is not None and cached[0] == path:
            return cached[1]
        try:
            from spopy import Ephemeris
            eph = Ephemeris(path)
        except (OSError, ValueError, ImportError):
            self._form_ephemeris_cache = (path, None)
            return None
        self._form_ephemeris_cache = (path, eph)
        return eph

    def _form_et_start(self) -> float | None:
        """Read simulation.et_start_s from the form, or None when it
        is empty / unparseable. Used by the BF conversion paths to
        evaluate R_icrf_to_bf at the run's start epoch."""
        et_w = self._widgets.get("simulation.et_start_s")
        if not isinstance(et_w, QLineEdit):
            return None
        txt = et_w.text().strip()
        if not txt:
            return None
        try:
            return float(txt)
        except ValueError:
            return None

    def _on_input_frame_changed(self, new_frame: str) -> None:
        """User flipped the [initial_state].frame combo: when the
        swap is between `central_inertial` and `central_body_fixed`,
        rotate the currently typed cart / kep values in place so the
        displayed numbers reflect the new basis. Other transitions
        (e.g. dynamics-model reflow that inserts `synodic_rotating`)
        do not rotate -- only the BF<->ICRF pair is a frame swap on
        the same physical state.

        When rotation prerequisites are missing (empty et_start_s,
        unreadable ephemeris for Moon, etc.) we still honour the
        user's combo flip and pop a one-shot warning explaining what
        was skipped -- the previous behaviour of silently reverting
        the combo to its old value made it look like the BF option
        was broken when actually et_start was just empty."""
        bf_pair = {"central_inertial", "central_body_fixed"}
        if (new_frame == self._input_frame_prev
                or new_frame not in bf_pair
                or self._input_frame_prev not in bf_pair):
            self._input_frame_prev = new_frame
            return
        # Toggle handlers NEVER re-seed the cache: each re-seed
        # would re-derive the destination via cart_inertial and
        # ICRF<->BF<->ICRF would still drift two conversions per
        # round-trip. The cache is filled by editingFinished (and
        # by the legacy-fallback path below); a cache hit here
        # writes the destination verbatim -- zero in-loop
        # conversions, lossless across repeated flips.
        kind, _ = self._ic_current_view()
        if self._apply_ic_cache_to_widgets(kind, new_frame):
            self._input_frame_prev = new_frame
            return
        # Cache miss: fall back to the legacy in-place rotation, and
        # if the prerequisites are missing surface a one-shot warning
        # so the user knows what to fix.
        et = self._form_et_start()
        if et is None:
            QMessageBox.warning(
                self, "Body-fixed rotation skipped",
                "Set simulation.et_start_s first so the form knows at "
                "which epoch to evaluate the body-fixed rotation. The "
                "frame selector was kept on '" + new_frame + "' but the "
                "values below have NOT been rotated.")
            self._input_frame_prev = new_frame
            return
        R_icrf_to_bf = self._resolve_bf_rotation(et)
        if R_icrf_to_bf is None:
            QMessageBox.warning(
                self, "Body-fixed rotation unavailable",
                "Could not evaluate the body-fixed rotation at the "
                "current et_start_s. For Moon: pick a valid DE-series "
                "ephemeris in [ephemeris].file. For Earth: make sure "
                "the EOP file exists under the wizard data dir. The "
                "frame selector was kept on '" + new_frame + "' but "
                "the values below have NOT been rotated.")
            self._input_frame_prev = new_frame
            return
        # icrf->bf when going inertial -> body_fixed; transpose for
        # the reverse. Pure rotation only (no omega x r correction);
        # matches the Slice-B plot semantics so what you see in BF
        # plots round-trips through this form unchanged.
        R = R_icrf_to_bf if new_frame == "central_body_fixed" else R_icrf_to_bf.T
        if kind == "cartesian":
            self._rotate_cart_inplace(R)
        else:
            self._rotate_kep_inplace(R)
        # The legacy fallback mutated the visible block; capture
        # the freshly-rotated destination view into the cache so a
        # subsequent toggle hits the lossless path. The combo
        # already moved to `new_frame`, so _ic_current_view() is
        # correct here.
        self._seed_ic_cache_from_visible()
        self._input_frame_prev = new_frame

    def _rotate_cart_inplace(self, R: "np.ndarray") -> None:
        """Rotate the cart widgets' (r, v) values by R. Silent if any
        component is empty / unparseable."""
        r = self._read_vec3("initial_state.position_km")
        v = self._read_vec3("initial_state.velocity_kms")
        if r is None or v is None:
            return
        self._write_vec3("initial_state.position_km",  R @ r)
        self._write_vec3("initial_state.velocity_kms", R @ v)

    def _rotate_kep_inplace(self, R: "np.ndarray") -> None:
        """Rotate Keplerian angles by R: kep -> cart -> rotate -> kep.
        Magnitudes (a, e, i) are basis-independent and round-trip
        unchanged; RAAN / AOP / ν are recomputed in the new basis.
        Silent on conversion failure."""
        import math
        sma = self._read_float("initial_state.semi_major_axis_km")
        ecc = self._read_float("initial_state.eccentricity")
        inc = self._read_float("initial_state.inclination_deg")
        raan = self._read_float("initial_state.raan_deg")
        argp = self._read_float("initial_state.arg_periapsis_deg")
        anom = self._read_float("initial_state.anomaly_deg")
        if (sma is None or ecc is None or inc is None or raan is None
                or argp is None or anom is None):
            return
        ctx = self._resolve_kep_mu_and_synodic()
        if ctx is None:
            return
        mu_ref, _synodic = ctx
        anom_combo = self._widgets.get("initial_state.anomaly_type")
        anom_type = (anom_combo.currentText()
                     if isinstance(anom_combo, QComboBox) else "true")
        try:
            from spopy import (cartesian_to_keplerian, keplerian_to_cartesian,
                               mean_to_true_anom, true_to_mean_anom)
            nu = math.radians(anom)
            if anom_type == "mean":
                nu = mean_to_true_anom(nu, ecc)
            r, v = keplerian_to_cartesian(
                sma, ecc, math.radians(inc), math.radians(raan),
                math.radians(argp), nu, mu_ref)
            r_new = R @ r
            v_new = R @ v
            el = cartesian_to_keplerian(r_new, v_new, mu_ref)
        except (ValueError, ZeroDivisionError):
            return
        self._write_float("initial_state.semi_major_axis_km", el["sma_km"])
        self._write_float("initial_state.eccentricity",       el["ecc"])
        self._write_float("initial_state.inclination_deg",
                          math.degrees(el["inc_rad"]))
        self._write_float("initial_state.raan_deg",
                          math.degrees(el["raan_rad"]))
        self._write_float("initial_state.arg_periapsis_deg",
                          math.degrees(el["argp_rad"]))
        nu_new = el["true_anom_rad"]
        anom_new = (true_to_mean_anom(nu_new, el["ecc"])
                    if anom_type == "mean" else nu_new)
        self._write_float("initial_state.anomaly_deg",
                          math.degrees(anom_new))

    def _refresh_input_frame_availability(self) -> None:
        """Single source of truth for the [initial_state].frame combo
        items. Rebuilds based on dynamics_model + central body:

          - CR3BP                          -> ("synodic_rotating",)
          - HF + body has BF orientation   -> ("central_inertial",
                                              "central_body_fixed")
          - HF + body without BF provider  -> ("central_inertial",)

        The previous selection survives the rebuild when it's still
        a valid item; otherwise the combo snaps to the first entry
        and `_input_frame_prev` is reset to match so a subsequent
        BF<->ICRF flip does not try to rotate from a stale state."""
        frame_combo = self._widgets.get("initial_state.frame")
        if not isinstance(frame_combo, QComboBox):
            return
        dm_combo = self._widgets.get("simulation.dynamics_model")
        dyn_model = (dm_combo.currentText()
                     if isinstance(dm_combo, QComboBox) else "high_fidelity")
        if dyn_model == "cr3bp":
            items: tuple[str, ...] = ("synodic_rotating",)
        else:
            cb_combo = self._widgets.get("force_model.central_body")
            cb_name = (cb_combo.currentText()
                       if isinstance(cb_combo, QComboBox) else "")
            from ..central_bodies import resolve_central_body
            spec = resolve_central_body(cb_name)
            bf_available = (spec is not None
                            and spec.bf_orientation is not None)
            items = (("central_inertial", "central_body_fixed")
                     if bf_available else ("central_inertial",))
        prev = frame_combo.currentText()
        frame_combo.blockSignals(True)
        frame_combo.clear()
        for v in items:
            frame_combo.addItem(v)
        idx = frame_combo.findText(prev)
        if idx >= 0:
            frame_combo.setCurrentIndex(idx)
        else:
            frame_combo.setCurrentIndex(0)
            self._input_frame_prev = items[0]
        frame_combo.blockSignals(False)

    def _on_dynamics_model_changed(self, model: str) -> None:
        """Reflow the per-model sections + filter the frame combo + grey
        out HF-only optional toggles. HF shows the legacy stack (object
        / force_model / ephemeris) and hides [cr3bp]; CR3BP swaps the
        visibility and disables the output / event toggles whose engine
        path rejects CR3BP runs. Called once after construction (sync
        with the default selection) and on every user change of the
        dynamics_model combo."""
        if not hasattr(self, "_object_group"):
            return   # called too early during construction
        is_hf = (model == "high_fidelity")
        self._object_group.setVisible(is_hf)
        self._force_model_group.setVisible(is_hf)
        self._ephemeris_group.setVisible(is_hf)
        self._cr3bp_group.setVisible(not is_hf)
        # Frame combo is owned exclusively by
        # `_refresh_input_frame_availability` (called below) -- having
        # this method ALSO rebuild it caused races where the BF entry
        # would oscillate in / out of the list across consecutive
        # dynamics-model and kind swaps.

        # reference_body combo: HF only ever uses "central" (implicit
        # central body); CR3BP exposes primary_1 / primary_2 with no
        # default ("--" placeholder forces an explicit pick).
        ref_combo = self._widgets.get("initial_state.reference_body")
        if isinstance(ref_combo, QComboBox):
            ref_combo.blockSignals(True)
            prev = ref_combo.currentText()
            ref_combo.clear()
            if is_hf:
                ref_combo.addItem("central")
            else:
                ref_combo.addItem("primary_1")
                ref_combo.addItem("primary_2")
            idx = ref_combo.findText(prev)
            ref_combo.setCurrentIndex(idx if idx >= 0 else 0)
            ref_combo.blockSignals(False)

        # Optional knobs the engine rejects under CR3BP -- grey out and
        # uncheck so the user cannot enable them and so a stale toggle
        # from a prior HF scenario doesn't carry over.
        acc_cb = self._widgets.get("output.accelerations_file")
        if isinstance(acc_cb, QCheckBox):
            acc_cb.setEnabled(is_hf)
            if not is_hf and acc_cb.isChecked():
                acc_cb.setChecked(False)
            acc_cb.setToolTip(
                TOOLTIPS.get("output.accelerations_file", "") if is_hf
                else "Disabled in CR3BP (per-force breakdown is HF-only)")
        if hasattr(self, "_events_check"):
            self._events_check.setEnabled(is_hf)
            if not is_hf and self._events_check.isChecked():
                self._events_check.setChecked(False)
            self._events_check.setToolTip(
                "" if is_hf
                else "Disabled in CR3BP (no Sun in this model -- "
                     "primary-impact events are wired automatically)")
        # Input-frame combo: BF only makes sense in HF with a registered
        # orientation provider; CR3BP runs and bare-central-body specs
        # silently fall back to inertial.
        self._refresh_input_frame_availability()
        # Altitude-crossing body combos: HF allows central + thirds,
        # CR3BP allows only the two primaries; rebuild every row.
        self._refresh_altcross_body_options()

    # ------------------------------------------------------------------
    # Output auto-naming
    # ------------------------------------------------------------------
    def _wire_output_preview_dependencies(self) -> None:
        """Connect [simulation].name -> output preview. Called once at
        the end of __init__ because the simulation widgets are built
        after the output ones."""
        sim_name_w = self._widgets.get("simulation.name")
        if isinstance(sim_name_w, QLineEdit):
            sim_name_w.textChanged.connect(
                lambda _t: self._refresh_output_preview())
        self._refresh_output_preview()

    def _resolved_output_paths(self) -> dict[str, str]:
        """Map each enabled `output.<stream>` key to the file path that
        the next Generate would emit. Disabled streams are absent from
        the returned dict. Used by both _refresh_output_preview (label
        text) and to_dict (TOML emission)."""
        sim_name_w = self._widgets.get("simulation.name")
        sim_name = sim_name_w.text().strip() if isinstance(sim_name_w, QLineEdit) else ""
        out_dir = self._widgets["output.output_dir"].text().strip()

        out: dict[str, str] = {}
        for key, suffix in OUTPUT_FILE_SUFFIX.items():
            cb = self._widgets.get(key)
            if not isinstance(cb, QCheckBox) or not cb.isChecked():
                continue
            # Empty sim_name -> fall back to "output" so the file still
            # has a coherent name; the validator will surface the empty
            # simulation.name separately.
            stem = sim_name if sim_name else "output"
            name = f"{stem}{suffix}"
            out[key] = f"{out_dir}/{name}" if out_dir else name
        return out

    def _refresh_output_preview(self) -> None:
        if not hasattr(self, "_output_preview_label"):
            return
        paths = self._resolved_output_paths()
        if not paths:
            self._output_preview_label.setText("(no streams enabled)")
            return
        lines = [f"{key.rsplit('.', 1)[1]:>19}: {p}"
                 for key, p in paths.items()]
        self._output_preview_label.setText("\n".join(lines))

    def _build_events(self) -> QGroupBox:
        g = QGroupBox("[events]  (optional)")
        v = QVBoxLayout(g)

        # Eclipse sub-section -- gated by the legacy `_events_check` so
        # round-trip with TOMLs that only set eclipse_threshold stays
        # bit-identical. Altitude crossings live alongside, ungated:
        # the engine accepts them independently of eclipse (CR3BP even
        # rejects eclipse but allows altitude crossings).
        self._events_check = QCheckBox("Enable eclipse detection")
        self._events_check.toggled.connect(self._on_events_toggled)
        self._events_check.toggled.connect(lambda _: self._touch())
        v.addWidget(self._events_check)

        self._events_box = QWidget()
        f = QFormLayout(self._events_box)
        self._add_float(f, "events.eclipse_threshold", "eclipse_threshold (0..1)")
        v.addWidget(self._events_box)
        self._events_box.setVisible(False)

        # Altitude crossings sub-section, collapsible. One row per
        # `[[events.altitude_crossing]]` entry; empty table = nothing
        # emitted. Body combo options auto-refresh when the central
        # body / third bodies / CR3BP primaries change so a stale row
        # is spotted before Validate complains.
        self._altcross_check = QCheckBox("Enable altitude crossings")
        self._altcross_check.toggled.connect(self._on_altcross_toggled)
        self._altcross_check.toggled.connect(lambda _: self._touch())
        v.addWidget(self._altcross_check)

        self._altcross_box = QWidget()
        ac_v = QVBoxLayout(self._altcross_box)
        ac_v.setContentsMargins(0, 0, 0, 0)
        ac_v.addWidget(QLabel(
            "[[events.altitude_crossing]]  -- fires on ascending AND descending:"))
        self._altcross_table = QTableWidget(0, 4)
        self._altcross_table.setHorizontalHeaderLabels(
            ["body", "altitude_km", "action", "refined"])
        self._altcross_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self._altcross_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch)
        self._altcross_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self._altcross_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents)
        self._altcross_table.verticalHeader().setVisible(False)
        self._altcross_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._altcross_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._altcross_table.setMinimumHeight(120)
        ac_v.addWidget(self._altcross_table)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 0, 0, 0)
        self._altcross_add_btn = QPushButton("+ Add crossing")
        self._altcross_add_btn.clicked.connect(self._on_altcross_add_row)
        self._altcross_del_btn = QPushButton("- Remove selected")
        self._altcross_del_btn.clicked.connect(self._on_altcross_remove_row)
        btn_row.addWidget(self._altcross_add_btn)
        btn_row.addWidget(self._altcross_del_btn)
        btn_row.addStretch(1)
        ac_v.addLayout(btn_row)
        v.addWidget(self._altcross_box)
        self._altcross_box.setVisible(False)
        return g

    def _build_batch(self) -> QGroupBox:
        """Batch section + a CSV-aware [batch.columns] mapping table.

        The columns table is populated from the cases_file's CSV
        header (excluding the optional `id` column); each row gets a
        target dropdown (filtered by the current object schema) and a
        mode dropdown (override / delta). Heuristic pre-matching
        picks the obvious target if the column name matches the last
        segment of a known path (e.g. `mass_kg` -> `spacecraft.mass_kg`)."""
        g = QGroupBox("[batch]  (optional)")
        v = QVBoxLayout(g)

        self._batch_check = QCheckBox("Enable [batch]  (multi-case sweep)")
        self._batch_check.toggled.connect(self._on_batch_toggled)
        self._batch_check.toggled.connect(lambda _: self._touch())
        v.addWidget(self._batch_check)

        self._batch_box = QWidget()
        f = QFormLayout(self._batch_box)
        # `batch.name` and `batch.output_dir` used to live here as
        # separate string / path widgets, doubling up the name and
        # output-folder fields the user had already filled under
        # [simulation] and [output]. They are now derived in to_dict
        # from `simulation.name` and `output.output_dir`, so the form
        # exposes exactly one name field and one output folder field
        # regardless of whether the user is running a single
        # propagation or a batch sweep.
        # Cap thread_number to the actual logical-CPU count of the
        # host. spody is CPU-bound numerical work, so oversubscribing
        # past the available cores is only ever slower; the validator
        # blocks input above the cap as the user types it.
        # os.cpu_count() can return None on exotic systems; default to
        # 1 in that case so the cap is always meaningful.
        cpu_n = os.cpu_count() or 1
        self._add_int   (f, "batch.thread_number", "thread_number",
                         minimum=1, maximum=cpu_n,
                         hint=f"(1..{cpu_n} cores available on this machine)")

        # The single user-facing path field. spody.exe natively only
        # accepts ICRF state, so the path's role depends on cases_frame:
        #   icrf -> passed verbatim to spody.exe as cases_file
        #   ric  -> the GUI rotates this file at Generate and writes
        #           <stem>_wrt_icrf.csv next to it; spody.exe reads the
        #           rotated copy.
        # Internally the widget is mapped to "batch.cases_source_file";
        # the actual `cases_file` key written to the TOML is computed in
        # to_dict() so the two paths can't drift out of sync.
        self._batch_cases_edit = QLineEdit()
        self._batch_cases_edit.textChanged.connect(self._touch)
        self._batch_cases_edit.textChanged.connect(
            lambda _: self._update_cases_frame_status())
        self._batch_cases_edit.textChanged.connect(
            lambda _: self._update_ric_preview())
        cases_browse = QPushButton("Browse...")
        cases_browse.clicked.connect(self._on_browse_cases_file)
        cases_reread = QPushButton("Re-read columns")
        cases_reread.clicked.connect(self._refresh_batch_columns)
        cases_row = QHBoxLayout()
        cases_row.setContentsMargins(0, 0, 0, 0)
        cases_row.addWidget(self._batch_cases_edit, 1)
        cases_row.addWidget(cases_browse)
        cases_row.addWidget(cases_reread)
        f.addRow("cases_file", hwrap(cases_row))
        self._widgets["batch.cases_source_file"] = self._batch_cases_edit

        # Status line just under the cases_file row -- tells the user
        # whether the CSV was readable and how many columns came back.
        self._batch_cases_status = QLabel("")
        self._batch_cases_status.setStyleSheet("color: gray;")
        f.addRow("", self._batch_cases_status)

        # Frame selector for the state-vector columns in the cases file.
        # spody.exe only knows ICRF; this combo tells the GUI whether
        # the user's CSV is already in ICRF (used as-is) or in RIC
        # (rotated at Generate-TOML using [initial_state] as the
        # reference orbit). The cases_frame key is only emitted when
        # not the default.
        self._batch_cases_frame_combo = QComboBox()
        for frame_name in ("icrf", "ric", "lvlh"):
            self._batch_cases_frame_combo.addItem(frame_name)
        self._batch_cases_frame_combo.currentTextChanged.connect(
            self._on_cases_frame_changed)
        self._batch_cases_frame_combo.currentTextChanged.connect(
            lambda _: self._touch())
        self._widgets["batch.cases_frame"] = self._batch_cases_frame_combo
        f.addRow("cases_frame", self._batch_cases_frame_combo)

        # Status line under the frame combo: shows the path spody.exe
        # will actually read (== source for icrf, == rotated copy for ric).
        self._batch_frame_status = QLabel("")
        self._batch_frame_status.setStyleSheet("color: gray;")
        self._batch_frame_status.setWordWrap(True)
        f.addRow("", self._batch_frame_status)

        # The column-mapping table.
        self._batch_columns_table = QTableWidget(0, 3)
        self._batch_columns_table.setHorizontalHeaderLabels(
            ["CSV column", "Target", "Mode"])
        self._batch_columns_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents)
        self._batch_columns_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch)
        self._batch_columns_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents)
        self._batch_columns_table.verticalHeader().setVisible(False)
        self._batch_columns_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._batch_columns_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._batch_columns_table.setMinimumHeight(120)
        f.addRow(QLabel("Column mapping  ([batch.columns]):"))
        f.addRow(self._batch_columns_table)

        # CSV data preview: first N rows of the cases_file verbatim, so
        # the user can sanity-check the column-target mapping against
        # the actual numbers spody will see. Read-only, fixed-height
        # so it doesn't dominate the form even with many columns.
        self._batch_preview_status = QLabel("")
        self._batch_preview_status.setStyleSheet("color: gray;")
        f.addRow(self._batch_preview_status)

        self._batch_preview_table = QTableWidget(0, 0)
        self._batch_preview_table.verticalHeader().setDefaultSectionSize(20)
        self._batch_preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._batch_preview_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._batch_preview_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._batch_preview_table.setMinimumHeight(140)
        # Monospace cells so columns of numbers line up visually.
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._batch_preview_table.setFont(mono)
        f.addRow(self._batch_preview_table)

        # Rotated preview: visible only when cases_frame == "ric". Shows
        # the first N rows of what spody.exe would actually read once
        # the RIC->ICRF rotation is applied. Auto-refreshed on source /
        # frame / column-re-read; a Refresh button covers edits to the
        # column mapping or [initial_state].
        self._batch_rotated_preview_container = QWidget()
        rp_v = QVBoxLayout(self._batch_rotated_preview_container)
        rp_v.setContentsMargins(0, 0, 0, 0)

        rp_header = QHBoxLayout()
        rp_header.setContentsMargins(0, 0, 0, 0)
        # Label is a member so _update_ric_preview can patch it to the
        # actual frame ("post LVLH -> ICRF", "post RIC -> ICRF", ...).
        self._batch_rotated_preview_header = QLabel(
            "Rotated preview (post RIC -> ICRF):")
        rp_header.addWidget(self._batch_rotated_preview_header, 1)
        rp_refresh = QPushButton("Refresh preview")
        rp_refresh.clicked.connect(self._update_ric_preview)
        rp_header.addWidget(rp_refresh)
        rp_v.addLayout(rp_header)

        self._batch_rotated_preview_status = QLabel("")
        self._batch_rotated_preview_status.setStyleSheet("color: gray;")
        rp_v.addWidget(self._batch_rotated_preview_status)

        self._batch_rotated_preview_table = QTableWidget(0, 0)
        self._batch_rotated_preview_table.verticalHeader().setDefaultSectionSize(20)
        self._batch_rotated_preview_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        self._batch_rotated_preview_table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers)
        self._batch_rotated_preview_table.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection)
        self._batch_rotated_preview_table.setMinimumHeight(140)
        self._batch_rotated_preview_table.setFont(mono)
        rp_v.addWidget(self._batch_rotated_preview_table)

        f.addRow(self._batch_rotated_preview_container)
        self._batch_rotated_preview_container.setVisible(False)

        v.addWidget(self._batch_box)
        self._batch_box.setVisible(False)
        return g
