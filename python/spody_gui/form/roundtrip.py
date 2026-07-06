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

"""Round-trip between the widgets and the TOML-ready dict.

`to_dict` serializes the current form (omitting empty/disabled
fields); the load path explodes dotted keys back onto widgets. The
dotted-key helpers live at module level.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QComboBox, QMessageBox

from .catalog import OUTPUT_FILE_SUFFIX


def explode_dotted(flat: dict[str, Any]) -> dict[str, Any]:
    """Turn `{"spacecraft.srp.area_m2": 1.0}` into
    `{"spacecraft": {"srp": {"area_m2": 1.0}}}`. Used by `to_dict` so
    the emitter receives the same nested shape `tomli.load` produces."""
    out: dict[str, Any] = {}
    for key, val in flat.items():
        parts = key.split(".")
        cur = out
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = val
    return out


def flatten_dotted(nested: dict[str, Any]) -> dict[str, Any]:
    """Inverse of `explode_dotted` -- used by `load_from_dict` so we
    can address widgets by the same dotted keys the form uses."""
    out: dict[str, Any] = {}
    def walk(prefix: str, d: dict[str, Any]) -> None:
        for k, v in d.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, dict) and not is_inline_table(v):
                walk(path, v)
            else:
                out[path] = v
    walk("", nested)
    return out


def is_inline_table(d: dict[str, Any]) -> bool:
    """Same heuristic the emitter uses: an inline table value (kept as
    a leaf) has the `target` key. spody only emits this inside
    `[batch.columns]`."""
    return "target" in d


class RoundTripMixin:
    """dict <-> widgets round-trip methods mixed into TomlForm."""

    # ==================================================================
    # Round-trip: dict <-> widgets
    # ==================================================================
    def to_dict(self) -> dict[str, Any]:
        """Build the TOML-ready dict from the current widget values.
        Empty / disabled fields are omitted so the emitter does not
        write blank entries."""
        flat: dict[str, Any] = {}
        for key, w in self._widgets.items():
            v = self._widget_value(key, w)
            if v is None:
                continue
            flat[key] = v

        # Model dispatch: CR3BP strips every HF-only section so a stale
        # widget value left over from a Moon scenario doesn't leak into
        # the emitted TOML. HF strips [cr3bp] for the same reason.
        dyn_model = flat.get("simulation.dynamics_model", "high_fidelity")
        if dyn_model == "cr3bp":
            flat = {k: v for k, v in flat.items()
                    if not (k.startswith("spacecraft.")
                            or k.startswith("debris.")
                            or k.startswith("force_model.")
                            or k.startswith("ephemeris."))}
        else:
            flat = {k: v for k, v in flat.items()
                    if not k.startswith("cr3bp.")}

            # Apply object XOR by stripping the inactive branch (so
            # even if both have stale data the emitted TOML is
            # consistent). HF-only -- under CR3BP both sub-branches
            # were already wiped above.
            if self._radio_spc.isChecked():
                flat = {k: v for k, v in flat.items() if not k.startswith("debris.")}
                if not self._srp_check.isChecked():
                    flat = {k: v for k, v in flat.items() if not k.startswith("spacecraft.srp.")}
                else:
                    # XOR inside [spacecraft.srp]: drop whichever param is unselected.
                    if self._srp_radio_area.isChecked():
                        flat.pop("spacecraft.srp.am_srp", None)
                    else:
                        flat.pop("spacecraft.srp.area_m2", None)
                if not self._drag_check.isChecked():
                    flat = {k: v for k, v in flat.items() if not k.startswith("spacecraft.drag.")}
                else:
                    # XOR inside [spacecraft.drag]: same rule as SRP.
                    if self._drag_radio_area.isChecked():
                        flat.pop("spacecraft.drag.am_drag", None)
                    else:
                        flat.pop("spacecraft.drag.area_m2", None)
            else:
                flat = {k: v for k, v in flat.items()
                        if not (k.startswith("spacecraft.") or k == "spacecraft.mass_kg")}
                if not self._dbr_drag_check.isChecked():
                    flat.pop("debris.am_drag", None)
                    flat.pop("debris.Cd",      None)

        # Optional sections: drop their fields entirely when the gating
        # checkbox is off so an unchecked block isn't emitted half-filled.
        if not self._events_check.isChecked():
            flat = {k: v for k, v in flat.items() if not k.startswith("events.")}
        if not self._batch_check.isChecked():
            flat = {k: v for k, v in flat.items() if not k.startswith("batch.")}

        # Earth-only [force_model] fields: present in the engine schema
        # ONLY when central_body == "Earth". The widgets are hidden in
        # the form for any other body but may still hold stale text from
        # a previous Earth run; drop them on emit so the validator
        # never rejects the TOML on round-trip.
        cb = flat.get("force_model.central_body", "")
        if str(cb).strip().lower() != "earth":
            flat.pop("force_model.eop_file",    None)
            flat.pop("force_model.iau2006_dir", None)
            flat.pop("force_model.drag",        None)
            flat.pop("force_model.space_weather_file", None)
            flat.pop("force_model.density_scale",      None)
            flat.pop("force_model.density_scale_file", None)
        # The space weather table and the density calibration pair only
        # matter to the drag force; keep the emitted TOML minimal when
        # drag is off (the widgets still remember their values for the
        # next toggle-on). The density XOR itself is left to the engine
        # validator so the form and CLI verdicts stay identical.
        if not flat.get("force_model.drag", False):
            flat.pop("force_model.space_weather_file", None)
            flat.pop("force_model.density_scale",      None)
            flat.pop("force_model.density_scale_file", None)

        # output.interval_s only applies to mode == "fixed"; in step
        # mode the field is hidden in the UI but the underlying widget
        # may still hold a stale value -- drop it from the emitted TOML
        # so the file matches what the user sees.
        if flat.get("output.mode") == "step":
            flat.pop("output.interval_s", None)

        # Output stream paths: the form uses checkbox + auto-naming, so
        # the five `output.<stream>` keys are stored as booleans in
        # `flat`. Replace each True with the resolved path
        # `<output_dir>/<sim_name><suffix>`; drop the falses. The
        # auxiliary `output.output_dir` key is kept so the GUI can
        # round-trip the user's choice on the next Load (spody.exe
        # ignores it).
        paths = self._resolved_output_paths()
        for key in OUTPUT_FILE_SUFFIX:
            flat.pop(key, None)
            if key in paths:
                flat[key] = paths[key]

        # CR3BP-specific drops: the engine rejects the per-force
        # breakdown (no HF force model to break down) and the eclipse
        # event (no Sun in the model). Strip them regardless of the
        # checkbox state so a stale toggle from a HF scenario doesn't
        # turn into a validate error.
        if dyn_model == "cr3bp":
            flat.pop("output.accelerations_file", None)
            flat.pop("events.eclipse_threshold", None)

        # BF input: from spody 0.2.x the engine understands
        # `frame = "central_body_fixed"` natively and rotates the
        # parsed (position, velocity) into the integrator's
        # central_inertial frame at sim_setup via the central body's
        # bf_rotation provider. The GUI therefore writes the BF
        # values + the BF frame name unchanged -- no GUI-side
        # rotation, no frame override -- so the TOML preserves the
        # user's BF intent across save / load cycles.

        # [initial_state] kind dispatch: drop the inactive block's
        # keys so the emitted TOML matches what the user sees in the
        # form. 'cartesian' is the default; we also drop the `kind`
        # key itself in that case so legacy round-trips stay
        # byte-identical when the user never touched the new combo.
        init_kind = flat.get("initial_state.kind", "cartesian")
        if init_kind == "cartesian":
            for k in (
                "initial_state.kind",
                "initial_state.reference_body",
                "initial_state.semi_major_axis_km",
                "initial_state.eccentricity",
                "initial_state.inclination_deg",
                "initial_state.raan_deg",
                "initial_state.arg_periapsis_deg",
                "initial_state.anomaly_deg",
                "initial_state.anomaly_type",
            ):
                flat.pop(k, None)
        else:  # keplerian
            flat.pop("initial_state.position_km",  None)
            flat.pop("initial_state.velocity_kms", None)
            # HF: reference_body is implicit ("central"); omit so the
            # emitted TOML mirrors the schema docs (defaults are not
            # written).
            if dyn_model != "cr3bp":
                flat.pop("initial_state.reference_body", None)

        # Resolve cases_file from the source path + frame combo. The
        # form has a SINGLE path widget (always showing the user-picked
        # source); the TOML carries three batch keys whose contract is:
        #
        #   cases_source_file = the path the user chose (= widget text)
        #   cases_frame       = "icrf" | "ric" (what frame the source is in)
        #   cases_file        = what spody.exe actually reads
        #                       == cases_source_file when icrf
        #                       == <stem>_wrt_icrf.csv when ric (the
        #                          rotated copy the GUI writes at Generate)
        #
        # The triple is emitted regardless of mode so loading is
        # symmetric and the schema is self-describing: any reader can
        # tell from cases_frame alone whether cases_file is a direct
        # copy or a derived rotation. spody.exe ignores cases_frame and
        # cases_source_file today (parser only reads keys it knows);
        # they also reserve the schema for a future engine-side RIC
        # handler that would no longer need the GUI-side rotation.
        source = flat.pop("batch.cases_source_file", None)
        frame  = flat.pop("batch.cases_frame", "icrf")
        if source:
            flat["batch.cases_source_file"] = source
            flat["batch.cases_frame"]       = frame
            if frame in self._ROTATING_FRAMES:
                p = Path(source)
                flat["batch.cases_file"] = str(
                    p.with_name(f"{p.stem}_wrt_icrf.csv"))
            else:
                flat["batch.cases_file"] = source

        result = explode_dotted(flat)

        # [[events.altitude_crossing]] comes from the dynamic table,
        # not from a flat widget key. Emit only when the user has
        # explicitly enabled the section: skip both when the checkbox
        # is off (so a hidden table doesn't leak rows the user can't
        # see) and when the table happens to be empty. Bypasses the
        # `_events_check` gate -- altitude crossings are accepted by
        # the engine independently of eclipse_threshold (CR3BP even
        # forbids eclipse while still allowing alt crossings).
        if self._altcross_check.isChecked():
            ac_list = self._altcross_table_to_list()
            if ac_list:
                result.setdefault("events", {})["altitude_crossing"] = ac_list

        # [batch.columns] comes from the dynamic table, not from a flat
        # widget key; inject it only when batch is enabled and at least
        # one column has a target assigned.
        if self._batch_check.isChecked():
            cols = self._batch_columns_to_dict()
            if cols:
                result.setdefault("batch", {})["columns"] = cols
            # Mirror the form's single name + output-folder fields
            # into the batch section so spody.exe's batch dispatcher
            # finds the keys where it expects them. The form used to
            # show duplicate widgets here; the mirror keeps the on-
            # disk TOML schema unchanged while the UI surfaces one
            # field. Skip the mirror when the source field is empty
            # so a missing simulation.name surfaces as the same
            # validate error it always did, instead of being masked
            # by an empty string in batch.name.
            sim = result.get("simulation", {})
            if isinstance(sim, dict) and sim.get("name"):
                result.setdefault("batch", {})["name"] = sim["name"]
            out = result.get("output", {})
            if isinstance(out, dict) and out.get("output_dir"):
                result.setdefault("batch", {})["output_dir"] = out["output_dir"]

        # Pass-through for any top-level section we don't render at all.
        for k, v in self._passthrough.items():
            result.setdefault(k, v)

        # Notes: top-level string, not a table, so it sits at the
        # outermost scope rather than under any [section]. Emitted
        # only when non-empty -- an empty note would leave a
        # `notes = ""` artefact in every TOML the form produces.
        notes_text = self._notes_edit.toPlainText().strip()
        if notes_text:
            result["notes"] = notes_text
        return result

    def load_from_dict(self, data: dict[str, Any]) -> None:
        """Inverse of `to_dict`: takes a tomli-parsed dict and pushes
        the values back into the widgets, suppressing the modified
        flag while loading. Sections the form doesn't render are
        stashed in `_passthrough` so the next Generate preserves them."""
        self._passthrough = {
            k: v for k, v in data.items()
            if k not in self._FORM_OWNED_TOP and isinstance(v, dict)
        }
        self._loading = True
        try:
            self._reset_widgets()

            # Normalise the [batch] schema for the load path: the form
            # has ONE path widget (`batch.cases_source_file`) backing
            # both modes. For ICRF / legacy TOMLs the user's chosen
            # path lives in `cases_file`; redirect it so the source
            # widget gets populated. RIC TOMLs already carry the path
            # in `cases_source_file`; their `cases_file` is the derived
            # copy (recomputed by to_dict) and we just drop it from the
            # flat view so it doesn't try to find a non-existent widget.
            #
            # Back-compat for the name/output-folder consolidation:
            # legacy TOMLs (and hand-written ones) may carry
            # `batch.name` / `batch.output_dir` without populating
            # the matching `simulation.name` / `output.output_dir`.
            # The form no longer renders separate batch widgets, so
            # we promote the batch values into the unified slots
            # when those are empty -- otherwise simulation.name
            # would silently stay blank after a load.
            batch_in = data.get("batch")
            if isinstance(batch_in, dict):
                batch_in = dict(batch_in)
                if batch_in.get("cases_frame") not in self._ROTATING_FRAMES:
                    if ("cases_file" in batch_in
                            and "cases_source_file" not in batch_in):
                        batch_in["cases_source_file"] = batch_in["cases_file"]
                batch_in.pop("cases_file", None)

                sim_in = dict(data.get("simulation") or {})
                if batch_in.get("name") and not sim_in.get("name"):
                    sim_in["name"] = batch_in["name"]
                    data = {**data, "simulation": sim_in}
                out_in = dict(data.get("output") or {})
                if batch_in.get("output_dir") and not out_in.get("output_dir"):
                    out_in["output_dir"] = batch_in["output_dir"]
                    data = {**data, "output": out_in}

                # Drop name/output_dir from the batch view so the
                # flat-load pass doesn't try to find a widget that
                # no longer exists; the values are now resolved
                # through simulation/output and will be re-emitted
                # under batch by to_dict.
                batch_in.pop("name", None)
                batch_in.pop("output_dir", None)
                data = {**data, "batch": batch_in}

            # Normalise [output]: the five stream paths in the TOML
            # become booleans (presence -> True). If the TOML doesn't
            # carry `output.output_dir`, derive a best-guess from the
            # first non-empty stream path; if all paths are bare names
            # the output_dir stays empty (= TOML's own dir).
            output_in = data.get("output")
            if isinstance(output_in, dict):
                output_in = dict(output_in)
                derived_dir = output_in.get("output_dir", "")
                for key in ("csv_file", "bin_file", "accelerations_file",
                            "events_log", "log_file"):
                    val = output_in.get(key, "")
                    if isinstance(val, str) and val:
                        output_in[key] = True
                        if not derived_dir:
                            d = os.path.dirname(val)
                            if d:
                                derived_dir = d
                    else:
                        output_in[key] = False
                output_in["output_dir"] = derived_dir
                data = {**data, "output": output_in}

            flat = flatten_dotted(
                {k: v for k, v in data.items() if k in self._FORM_OWNED_TOP}
            )

            # Pick dynamics_model FIRST so HF/CR3BP visibility is right
            # before field values are pushed. Default to high_fidelity
            # when the TOML omits the key (legacy files).
            dm_value = flat.get("simulation.dynamics_model") or "high_fidelity"
            dm_combo = self._widgets.get("simulation.dynamics_model")
            if isinstance(dm_combo, QComboBox):
                idx = dm_combo.findText(str(dm_value))
                if idx >= 0:
                    dm_combo.setCurrentIndex(idx)
            self._on_dynamics_model_changed(str(dm_value))
            # TOML always stores inertial values so the loaded
            # `frame` will be central_inertial / synodic_rotating;
            # sync the rotation tracker accordingly so a subsequent
            # user-driven BF flip rotates from ICRF, not from a stale
            # in-memory state.
            self._input_frame_prev = (
                flat.get("initial_state.frame") or "central_inertial")

            # [initial_state].kind also drives visibility: pick it BEFORE
            # the field push so the right block is shown. Default to
            # cartesian (legacy TOMLs). The kind combo is wired through
            # _on_init_kind_changed which would otherwise try to convert
            # whatever is in the cartesian block right now (most likely
            # empty); _loading=True is already set so _touch is a no-op,
            # but we still want to skip the conversion attempt -- toggle
            # visibility manually here, _set_widget_value below will set
            # the combo to the right text.
            init_kind = (flat.get("initial_state.kind") or "cartesian")
            init_is_kep = (init_kind == "keplerian")
            if hasattr(self, "_init_cart_block"):
                self._init_cart_block.setVisible(not init_is_kep)
                self._init_kep_block.setVisible(init_is_kep)

            # Decide object mode FIRST so the XOR visibility is right
            # before fields populate. (No-op when dynamics_model = cr3bp
            # since the object group is hidden, but the radio still
            # tracks state for a later HF load.)
            if "debris.am_srp" in flat:
                self._radio_dbr.setChecked(True)
            else:
                self._radio_spc.setChecked(True)
            self._on_object_radio_toggled(True)

            # SRP gate.
            has_srp = any(k.startswith("spacecraft.srp.") for k in flat)
            self._srp_check.setChecked(has_srp)
            self._on_srp_toggled(has_srp)
            if has_srp:
                # XOR area_m2 / am_srp.
                if "spacecraft.srp.am_srp" in flat:
                    self._srp_radio_am.setChecked(True)
                else:
                    self._srp_radio_area.setChecked(True)
                self._on_srp_param_toggled()

            # Drag gates (spacecraft block / debris pair).
            has_drag = any(k.startswith("spacecraft.drag.") for k in flat)
            self._drag_check.setChecked(has_drag)
            self._on_drag_toggled(has_drag)
            if has_drag:
                if "spacecraft.drag.am_drag" in flat:
                    self._drag_radio_am.setChecked(True)
                else:
                    self._drag_radio_area.setChecked(True)
                self._on_drag_param_toggled()

            has_dbr_drag = "debris.am_drag" in flat
            self._dbr_drag_check.setChecked(has_dbr_drag)
            self._on_debris_drag_toggled(has_dbr_drag)

            # Optional-block gates: events, batch.
            has_events = any(k.startswith("events.") for k in flat)
            self._events_check.setChecked(has_events)
            self._on_events_toggled(has_events)

            has_batch = any(k.startswith("batch.") for k in flat)
            self._batch_check.setChecked(has_batch)
            self._on_batch_toggled(has_batch)

            # Now push field values.
            for key, value in flat.items():
                w = self._widgets.get(key)
                if w is None:
                    continue   # unknown key; emitter would round-trip via flatten too
                self._set_widget_value(key, w, value)

            # [[events.altitude_crossing]] is a dynamic array-of-tables;
            # the flat-load pass skips it (the array is not a single
            # widget value). Populate the table from the raw `data`
            # directly + auto-check the enable box so the user sees
            # the loaded rows. Missing / empty key -> stays disabled.
            ac_list = data.get("events", {}).get("altitude_crossing", [])
            if not isinstance(ac_list, list):
                ac_list = []
            self._apply_loaded_altitude_crossings(ac_list)
            self._altcross_check.setChecked(bool(ac_list))
            self._on_altcross_toggled(bool(ac_list))

            # [batch.columns] is dynamic: first scan the freshly-loaded
            # cases_file to populate the rows, then apply the loaded
            # column->target mappings on top.
            if has_batch:
                self._refresh_batch_columns()
                cols_data = data.get("batch", {}).get("columns", {})
                if isinstance(cols_data, dict):
                    self._apply_loaded_batch_columns(cols_data)

            # Top-level `notes` string: optional, no section. Loaded
            # outside the flat-section pass since flatten_dotted is
            # scoped to _FORM_OWNED_TOP sections and would lose it.
            notes_raw = data.get("notes", "")
            if isinstance(notes_raw, str):
                self._notes_edit.setPlainText(notes_raw)
        finally:
            self._loading = False
        self.clear_modified()
        # Preview was suppressed during _loading; sync it now.
        self._refresh_preview()
        # Seed the IC four-representation cache from the freshly-
        # loaded values so the first kind / frame toggle benefits
        # from the lossless lookup path (and we don't pay the spopy
        # conversion on a click that the user perceives as
        # navigation, not computation).
        self._invalidate_ic_cache()
        self._seed_ic_cache_from_visible()
        if self._validate_badge.text():
            self._validate_badge.setText("")

    def load_path(self, path: Path) -> bool:
        """Read a TOML from disk via tomli and populate the form."""
        from ..toml_io import read_toml
        try:
            data = read_toml(path)
        except (OSError, Exception) as exc:
            QMessageBox.critical(self, "Load failed", f"{path}\n{exc}")
            return False
        # Set the current path FIRST so load_from_dict can resolve any
        # relative paths inside the TOML (notably batch.cases_file)
        # against the right base directory, matching what spody does.
        self.set_current_path(path)
        self.load_from_dict(data)
        return True

    def reset_to_blank(self) -> None:
        """Clear every field, restore the default XOR selections, drop
        the current path AND any pass-through sections from a previous
        load. Used by File > New."""
        self._passthrough = {}
        self._loading = True
        try:
            self._reset_widgets()
            # Re-snap dynamics_model to the HF default so a previous
            # CR3BP session does not leak hidden HF widgets.
            dm_combo = self._widgets.get("simulation.dynamics_model")
            if isinstance(dm_combo, QComboBox):
                idx = dm_combo.findText("high_fidelity")
                if idx >= 0:
                    dm_combo.setCurrentIndex(idx)
            self._on_dynamics_model_changed("high_fidelity")
            self._radio_spc.setChecked(True)
            self._on_object_radio_toggled(True)
            self._srp_check.setChecked(False)
            self._on_srp_toggled(False)
            self._srp_radio_area.setChecked(True)
            self._on_srp_param_toggled()
            self._drag_check.setChecked(False)
            self._on_drag_toggled(False)
            self._drag_radio_area.setChecked(True)
            self._on_drag_param_toggled()
            self._dbr_drag_check.setChecked(False)
            self._on_debris_drag_toggled(False)
            self._events_check.setChecked(False)
            self._on_events_toggled(False)
            self._altcross_check.setChecked(False)
            self._on_altcross_toggled(False)
            self._altcross_table.setRowCount(0)
            self._batch_check.setChecked(False)
            self._on_batch_toggled(False)
            self._batch_columns_table.setRowCount(0)
            self._batch_cases_status.setText("")
            self._clear_preview()
        finally:
            self._loading = False
        self.set_current_path(None)
        self.clear_modified()
        self._refresh_preview()
        if self._validate_badge.text():
            self._validate_badge.setText("")

    def write_to(self, path: Path) -> bool:
        """Serialise the form via to_dict + write_toml. Returns True
        on success; surfaces I/O / value errors via a message box.

        When the cases_frame combo reads "ric", also performs the
        RIC -> ICRF rotation on the source CSV at this point: rotating
        at Generate (rather than at Run) keeps the side effect visible
        on disk and lets a downstream `spody batch <toml>` from the
        terminal work without re-opening the GUI."""
        from ..toml_io import write_toml
        try:
            data = self.to_dict()
        except ValueError as exc:
            QMessageBox.critical(self, "Generate failed", f"{path}\n{exc}")
            return False

        # Rotation is driven by the live combo state, not by the TOML
        # dict (the GUI deliberately no longer persists cases_frame /
        # cases_source_file -- see to_dict). If rotation fails the TOML
        # is also not written so the user sees one atomic failure.
        if (self._batch_check.isChecked()
                and self._batch_cases_frame_combo.currentText()
                    in self._ROTATING_FRAMES):
            if not self._rotate_ric_cases(path, data):
                return False

        try:
            write_toml(path, data)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "Generate failed", f"{path}\n{exc}")
            return False
        self.set_current_path(path)
        self.clear_modified()
        return True

    def _resolve_ric_inputs(self, toml_path: Path | None,
                            data: dict[str, Any]) -> tuple | None:
        """Shared resolver for the rotating-frame pipeline (used by
        both the Generate-time rotation and the live preview).

        Returns a tuple `(src_abs, out_abs, r_ref, v_ref, pos_cols,
        vel_cols)` on success, or `None` after a logged warning when
        any precondition fails. Warnings are surfaced through
        QMessageBox by `_rotate_ric_cases` (Generate path) and silenced
        in the preview path -- preview callers should branch on `None`
        and show a placeholder instead.

        `toml_path` may be None when the form has never been saved;
        in that case the cases path must already be absolute, otherwise
        we can't resolve it.

        Despite the historical name, this handles every entry in
        `_ROTATING_FRAMES` (RIC, LVLH, ...) -- error strings cite the
        actually-selected frame so the user isn't told "RIC" when the
        combo says LVLH.
        """
        import numpy as np

        frame_label = self._batch_cases_frame_combo.currentText().upper()

        src_raw = self._batch_cases_edit.text().strip()
        if not src_raw:
            return ("error", f"Pick a {frame_label}-frame source CSV in "
                             f"the cases_file field first.")
        src = Path(src_raw)
        if not src.is_absolute():
            if toml_path is None:
                return ("error",
                        "The cases_file path is relative but the TOML "
                        "has not been saved yet; save first or pick an "
                        "absolute path.")
            src = (toml_path.parent / src).resolve()
        if not src.is_file():
            return ("error", f"cases_file not found:\n  {src}")
        out = src.with_name(f"{src.stem}_wrt_icrf.csv")

        init = data.get("initial_state", {})
        r_ref = init.get("position_km")
        v_ref = init.get("velocity_kms")
        if not (isinstance(r_ref, list) and len(r_ref) == 3 and
                isinstance(v_ref, list) and len(v_ref) == 3):
            return ("error",
                    "[initial_state].position_km and velocity_kms must "
                    "be fully filled -- they define the reference orbit "
                    "whose axes the cases CSV uses.")

        cols_map: dict[str, Any] = data.get("batch", {}).get("columns", {})
        pos_by_idx: dict[int, str] = {}
        vel_by_idx: dict[int, str] = {}
        for csv_col, spec in cols_map.items():
            target = spec["target"] if isinstance(spec, dict) else spec
            for i in range(3):
                if target == f"initial_state.position_km[{i}]":
                    pos_by_idx[i] = csv_col
                elif target == f"initial_state.velocity_kms[{i}]":
                    vel_by_idx[i] = csv_col

        def _full_triplet(d: dict[int, str]) -> tuple[str, str, str] | None:
            if len(d) == 3 and all(i in d for i in range(3)):
                return (d[0], d[1], d[2])
            if not d:
                return None
            return ...   # partial sentinel

        pos_cols = _full_triplet(pos_by_idx)
        vel_cols = _full_triplet(vel_by_idx)
        if pos_cols is ... or vel_cols is ...:
            return ("error",
                    "Partial position or velocity triplet in "
                    "[batch.columns]: rotation needs all 3 components "
                    "of a vector or none.")
        if pos_cols is None and vel_cols is None:
            # Help the user see WHICH columns landed where: when a row
            # they thought was wired is actually emitted as the metadata
            # sentinel (target = ""), the listing makes the discrepancy
            # obvious without needing to inspect the generated TOML.
            mapped = []
            for csv_col, spec in cols_map.items():
                t = spec["target"] if isinstance(spec, dict) else spec
                mapped.append(f"{csv_col} -> {t!r}")
            mapped_str = ("\n  " + "\n  ".join(mapped)
                          if mapped else " (none)")
            return ("error",
                    f"No state columns wired in [batch.columns]. "
                    f"{frame_label} rotation has nothing to do -- "
                    f"either map position/velocity columns or switch "
                    f"frame to icrf.\nCurrent [batch.columns]:"
                    f"{mapped_str}")

        return ("ok", src, out,
                np.asarray(r_ref, dtype=float),
                np.asarray(v_ref, dtype=float),
                pos_cols, vel_cols)

    def _rotate_ric_cases(self, toml_path: Path, data: dict[str, Any]) -> bool:
        """Generate <stem>_wrt_icrf.csv from the user's source CSV in
        whichever rotating frame the combo currently shows (RIC or
        LVLH). Returns True on success or False after a message box.
        Reuses `_resolve_ric_inputs` for input validation -- the
        resolution rules (state-column triplets, reference orbit) are
        frame-agnostic; only the rotation kernel differs."""
        frame = self._batch_cases_frame_combo.currentText()
        try:
            _basis_fn, rotate_csv_fn = self._rotation_helpers(frame)
        except ValueError as exc:
            QMessageBox.warning(self, "Rotation",
                                f"unsupported cases_frame {frame!r}: {exc}")
            return False

        resolved = self._resolve_ric_inputs(toml_path, data)
        if resolved is None or resolved[0] == "error":
            QMessageBox.warning(self, f"{frame.upper()} rotation",
                                resolved[1] if resolved else "unknown error")
            return False
        _, src, out, r_ref, v_ref, pos_cols, vel_cols = resolved

        try:
            info = rotate_csv_fn(
                src, out,
                r_ref_km=r_ref, v_ref_kms=v_ref,
                pos_columns=pos_cols, vel_columns=vel_cols,
            )
        except (FileNotFoundError, ValueError) as exc:
            QMessageBox.critical(self, f"{frame.upper()} rotation failed",
                                 str(exc))
            return False

        # Brief on-the-fly status so the user gets immediate feedback
        # without an extra dialog.
        self._batch_cases_status.setText(
            f"({frame.upper()} -> ICRF: {info['n_rows']} rows written to "
            f"{out.name})")
        return True
