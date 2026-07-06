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

"""Conditional visibility (XOR groups) + the dynamic batch table.

Declarative where possible: which groups exist and which control
drives them lives in the methods below; a future schema change
(debris mode, new dynamics model) extends these instead of scattering
`setVisible` calls across the form.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .catalog import BATCH_TARGETS, UNASSIGNED


def read_csv_preview(path: Path, max_rows: int = 10
                       ) -> tuple[list[str], list[list[str]], int]:
    """Header + first `max_rows` data rows + total data-row count.

    Matches spody's own loose CSV reader: leading `#` lines are
    treated as comments, blank lines are skipped, fields are trimmed.
    The full file is scanned to count rows (needed for the
    'first N of M' status line); cases.csv files are typically
    small (~1000 rows max) so this is cheap."""
    header: list[str] = []
    rows:   list[list[str]] = []
    total = 0
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            cells = [c.strip() for c in stripped.split(",")]
            if not header:
                header = cells
                continue
            total += 1
            if len(rows) < max_rows:
                rows.append(cells)
    return header, rows, total


# Backward-compat shim: kept so any caller that only needed the header
# keeps working without an awkward signature change.
def read_csv_header(path: Path) -> list[str]:
    header, _, _ = read_csv_preview(path, max_rows=0)
    return header


def heuristic_target(col_name: str, available: list[str]) -> str | None:
    """Pre-match the column name to an available target when the last
    segment matches exactly. e.g. CSV column `mass_kg` ->
    `spacecraft.mass_kg`; `Cr` -> first match between `spacecraft.srp.Cr`
    and `debris.Cr` (filtered by mode at the caller). Returns None if
    nothing matches, leaving the row as (unassigned)."""
    for p in available:
        # Drop any [i] index suffix when comparing -- so a column
        # called `position_km` matches `position_km[0]` loosely. We
        # only use this as a hint; the user can always override.
        last = p.rsplit(".", 1)[-1].split("[", 1)[0]
        if last == col_name:
            return p
    return None


class VisibilityMixin:
    """XOR-group toggling + [batch.columns] table plumbing mixed into
    TomlForm."""

    # ==================================================================
    # Conditional visibility (XOR groups)
    # ==================================================================
    def _on_object_radio_toggled(self, _checked: bool) -> None:
        on_spc = self._radio_spc.isChecked()
        self._spc_box.setVisible(on_spc)
        self._dbr_box.setVisible(not on_spc)
        # Batch column targets depend on the object schema; refresh
        # the per-row combos to drop now-invalid options.
        self._refresh_batch_column_target_combos()

    def _on_events_toggled(self, checked: bool) -> None:
        self._events_box.setVisible(checked)

    def _on_altcross_toggled(self, checked: bool) -> None:
        self._altcross_box.setVisible(checked)

    # ------------------------------------------------------------------
    # [[events.altitude_crossing]] table
    # ------------------------------------------------------------------
    _ALTCROSS_ACTIONS = ("log", "stop", "log_and_stop")

    def _altcross_valid_bodies(self) -> list[str]:
        """Return the list of body names a [[events.altitude_crossing]]
        row may reference under the current dynamics_model. HF: central
        body + checked third bodies; CR3BP: the two primaries. Empty
        names are skipped (early in construction the combos may not
        have a selection yet)."""
        dm = self._widgets.get("simulation.dynamics_model")
        dyn_model = (dm.currentText()
                     if isinstance(dm, QComboBox) else "high_fidelity")
        if dyn_model == "cr3bp":
            out: list[str] = []
            for k in ("cr3bp.primary_1", "cr3bp.primary_2"):
                w = self._widgets.get(k)
                if isinstance(w, QComboBox):
                    name = w.currentText().strip()
                    if name and name not in out:
                        out.append(name)
            return out
        out = []
        cb = self._widgets.get("force_model.central_body")
        if isinstance(cb, QComboBox):
            name = cb.currentText().strip()
            if name:
                out.append(name)
        tb = self._widgets.get("force_model.third_bodies")
        if isinstance(tb, dict):
            for name, box in tb.items():
                if box.isChecked() and name not in out:
                    out.append(name)
        return out

    def _make_altcross_body_combo(self, current: str = "") -> QComboBox:
        combo = QComboBox()
        for b in self._altcross_valid_bodies():
            combo.addItem(b)
        if current:
            idx = combo.findText(current)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            else:
                # Keep the stale name in the list so a load of a TOML
                # whose body is no longer in the current model still
                # shows what was there (the validator will flag it).
                combo.addItem(current)
                combo.setCurrentIndex(combo.count() - 1)
        combo.currentTextChanged.connect(lambda _t: self._touch())
        return combo

    def _make_altcross_action_combo(self, current: str = "log") -> QComboBox:
        combo = QComboBox()
        for a in self._ALTCROSS_ACTIONS:
            combo.addItem(a)
        idx = combo.findText(current)
        combo.setCurrentIndex(idx if idx >= 0 else 0)
        combo.currentTextChanged.connect(lambda _t: self._touch())
        return combo

    def _make_altcross_refined_widget(self, current: bool = True) -> QWidget:
        # Center the checkbox in its cell. A bare QCheckBox docks to
        # the top-left of the cell which looks odd in a row of combos.
        wrap = QWidget()
        lay = QHBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setAlignment(Qt.AlignCenter)
        cb = QCheckBox()
        cb.setChecked(bool(current))
        cb.toggled.connect(lambda _b: self._touch())
        lay.addWidget(cb)
        wrap.setProperty("altcross_checkbox", cb)
        return wrap

    def _altcross_refined_value(self, wrap: QWidget) -> bool:
        cb = wrap.property("altcross_checkbox")
        return bool(cb.isChecked()) if isinstance(cb, QCheckBox) else True

    def _altcross_append_row(self, body: str = "", altitude_km: float = 0.0,
                              action: str = "log", refined: bool = True) -> None:
        """Append one configured row (combo bodies + altitude QLineEdit +
        action combo + refined checkbox). Defaults are picked so a
        fresh row is immediately editable: the body combo lands on the
        first valid body, action on `log`, refined on True."""
        row = self._altcross_table.rowCount()
        self._altcross_table.insertRow(row)
        self._altcross_table.setCellWidget(row, 0,
                self._make_altcross_body_combo(body))
        alt_edit = QLineEdit("" if altitude_km <= 0.0 else repr(float(altitude_km)))
        alt_edit.setPlaceholderText("km > 0")
        alt_edit.textChanged.connect(lambda _t: self._touch())
        self._altcross_table.setCellWidget(row, 1, alt_edit)
        self._altcross_table.setCellWidget(row, 2,
                self._make_altcross_action_combo(action))
        self._altcross_table.setCellWidget(row, 3,
                self._make_altcross_refined_widget(refined))

    def _on_altcross_add_row(self) -> None:
        self._altcross_append_row()
        self._touch()

    def _on_altcross_remove_row(self) -> None:
        idx = self._altcross_table.currentRow()
        if idx < 0:
            return
        self._altcross_table.removeRow(idx)
        self._touch()

    def _refresh_altcross_body_options(self) -> None:
        """Rebuild every body combo in the table to reflect the current
        valid-body list (central + third bodies under HF, primaries
        under CR3BP). Preserves each row's current text -- a name no
        longer in the list is added back as a stray item so loaded
        TOMLs that referenced a now-removed body still display what
        was there (the validator will reject it explicitly)."""
        if not hasattr(self, "_altcross_table"):
            return
        bodies = self._altcross_valid_bodies()
        for row in range(self._altcross_table.rowCount()):
            combo = self._altcross_table.cellWidget(row, 0)
            if not isinstance(combo, QComboBox):
                continue
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            for b in bodies:
                combo.addItem(b)
            if current:
                idx = combo.findText(current)
                if idx >= 0:
                    combo.setCurrentIndex(idx)
                elif bodies:
                    combo.addItem(current)
                    combo.setCurrentIndex(combo.count() - 1)
            combo.blockSignals(False)

    def _altcross_table_to_list(self) -> list[dict]:
        """Serialise table -> list of dicts for emission under
        `events.altitude_crossing`. Skips rows that have no body or
        an unparseable altitude_km (so a half-filled row never makes
        it into the TOML without flagging anything; the Validate
        action will catch missing rows separately)."""
        out: list[dict] = []
        for row in range(self._altcross_table.rowCount()):
            body_combo = self._altcross_table.cellWidget(row, 0)
            alt_edit   = self._altcross_table.cellWidget(row, 1)
            act_combo  = self._altcross_table.cellWidget(row, 2)
            ref_wrap   = self._altcross_table.cellWidget(row, 3)
            if not isinstance(body_combo, QComboBox): continue
            if not isinstance(alt_edit,   QLineEdit): continue
            if not isinstance(act_combo,  QComboBox): continue
            body = body_combo.currentText().strip()
            if not body:
                continue
            try:
                alt = float(alt_edit.text().strip())
            except ValueError:
                continue
            entry: dict = {
                "body":        body,
                "altitude_km": alt,
                "action":      act_combo.currentText(),
            }
            refined = self._altcross_refined_value(ref_wrap)
            # Only emit `refined` when it differs from the default
            # (true) so unchanged round-trips stay byte-identical with
            # legacy TOMLs that never wrote the key.
            if not refined:
                entry["refined"] = False
            out.append(entry)
        return out

    def _apply_loaded_altitude_crossings(self, entries: list) -> None:
        """Clear the table and repopulate from a loaded list of dicts
        (the engine schema). Silent on type mismatches inside an entry;
        invalid entries simply do not produce a row (the Validate
        action surfaces the missing/typo'd field next time)."""
        self._altcross_table.setRowCount(0)
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            body    = str(entry.get("body", "")).strip()
            try:
                alt = float(entry.get("altitude_km", 0.0))
            except (TypeError, ValueError):
                alt = 0.0
            action  = str(entry.get("action", "log")).strip() or "log"
            refined = bool(entry.get("refined", True))
            self._altcross_append_row(body, alt, action, refined)

    def _on_batch_toggled(self, checked: bool) -> None:
        self._batch_box.setVisible(checked)

    def _suggest_harmonics_degree(self) -> None:
        """Shell out to `spody maxhgdegree <file> <x> <y> <z>` and
        populate the harmonics_degree row's info label with the
        result. Shows a modal progress dialog during the subprocess
        (the harmonics-file load takes ~1-2 s on GRGM1200B) and a
        Yes/No confirm dialog with the recommendation when the CLI
        finishes.

        Going via the C engine instead of a Python-side estimate
        means we read the *actual* Cnm/Snm coefficient magnitudes
        from the file rather than rely on a Kaula-rule bound. The
        Kaula estimate (first cut) under-predicted the useful degree
        on rough fields like GRGM1200B over the Moon and produced
        runs that diverged from SPICE; the file-driven walk is the
        right answer.
        """
        from PySide6.QtCore import QProcess
        from PySide6.QtWidgets import QProgressDialog

        store = self._store
        if store is None:
            QMessageBox.warning(
                self, "Suggest max harmonics degree",
                "Settings store not wired (running in a standalone form?).")
            return
        spody_bin = store.spody_binary()
        if not spody_bin or not Path(spody_bin).is_file():
            QMessageBox.warning(
                self, "Suggest max harmonics degree",
                "Set the path to spody.exe in Settings > Paths first.")
            return

        # Harmonics file path -- the asset combo stores the absolute
        # path as currentData(); empty when the user has not picked
        # anything yet.
        hfile_w = self._widgets.get("force_model.harmonics_file")
        hfile_path = hfile_w.currentData() if hfile_w is not None else None
        if not hfile_path:
            QMessageBox.warning(
                self, "Suggest max harmonics degree",
                "Pick a harmonics file in [force_model] first.")
            return

        # Initial-state position triplet from the vec3 widget.
        pos_w = self._widgets.get("initial_state.position_km")
        if not isinstance(pos_w, tuple) or len(pos_w) != 3:
            QMessageBox.warning(
                self, "Suggest max harmonics degree",
                "[initial_state].position_km is not a vec3 widget.")
            return
        try:
            pos = [float(le.text()) for le in pos_w]
        except ValueError:
            QMessageBox.warning(
                self, "Suggest max harmonics degree",
                "Fill in [initial_state].position_km first.")
            return

        # Modal indeterminate progress (range 0,0): the spody.exe load
        # is one opaque step from our side. No cancel -- aborting
        # mid-load offers no benefit over waiting the second out.
        dlg = QProgressDialog(
            "Loading harmonics file and walking degrees...",
            "", 0, 0, self)
        dlg.setWindowTitle("Suggest max harmonics degree")
        dlg.setWindowModality(Qt.WindowModality.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setCancelButton(None)
        dlg.setValue(0)

        # QProcess kept on `self` for the duration so the lambda
        # closures see the right object; reset in on_finished.
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        out_buf = bytearray()

        def on_ready_read() -> None:
            out_buf.extend(bytes(proc.readAllStandardOutput()))

        def on_finished(exit_code: int, _exit_status) -> None:
            out_buf.extend(bytes(proc.readAllStandardOutput()))
            dlg.close()
            self._hd_suggest_proc = None
            text = out_buf.decode("utf-8", errors="replace")
            if exit_code != 0:
                QMessageBox.critical(
                    self, "Suggest max harmonics degree",
                    f"spody maxhgdegree exited with code {exit_code}.\n\n"
                    f"Output:\n{text}")
                return
            # Parse the `key: value` lines the CLI emits.
            parsed: dict[str, str] = {}
            for line in text.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    parsed[k.strip()] = v.strip()
            num_max = parsed.get("numerical_max")
            mod_max = parsed.get("model_max")
            if not num_max or not mod_max:
                QMessageBox.warning(
                    self, "Suggest max harmonics degree",
                    f"Could not parse spody maxhgdegree output:\n\n{text}")
                return
            # Sticky info label sits between the field and the button.
            self._hd_info.setText(
                f"  max num: {num_max}  |  max model: {mod_max}  ")
            self._hd_info.setVisible(True)
            current = self._widgets["force_model.harmonics_degree"].text()
            r_km    = parsed.get("r_km", "?")
            R_body  = parsed.get("R_body_km", "?")
            reply = QMessageBox.question(
                self, "Suggest max harmonics degree",
                f"Largest harmonics degree whose Cnm/Snm contribution at\n"
                f"the current orbit altitude rises above double-precision\n"
                f"noise (computed from the actual coefficients in the\n"
                f"harmonics file, not a Kaula estimate):\n\n"
                f"  numerical max: {num_max}\n"
                f"  model max:     {mod_max}\n\n"
                f"r = {r_km} km, R_body = {R_body} km\n"
                f"Current harmonics_degree: {current or '(empty)'}\n\n"
                f"Apply {num_max} to harmonics_degree?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if reply == QMessageBox.StandardButton.Yes:
                self._widgets["force_model.harmonics_degree"].setText(num_max)

        def on_error(_err) -> None:
            dlg.close()
            self._hd_suggest_proc = None
            QMessageBox.critical(
                self, "Suggest max harmonics degree",
                f"Failed to launch spody.exe:\n{proc.errorString()}")

        proc.readyReadStandardOutput.connect(on_ready_read)
        proc.finished.connect(on_finished)
        proc.errorOccurred.connect(on_error)
        self._hd_suggest_proc = proc
        proc.start(spody_bin, [
            "maxhgdegree", str(hfile_path),
            repr(pos[0]), repr(pos[1]), repr(pos[2]),
        ])

    def _build_notes(self) -> QGroupBox:
        """Freeform notes attached to this TOML, emitted as a comment
        block at the end of the file.

        Purely metadata: spody.exe never reads the comments, and the
        TOML schema itself stays clean (no synthetic `notes` key
        polluting the document). The value is preserved verbatim in
        the per-run input.toml snapshot inside each run folder, so a
        user revisiting a result months later can still find what
        they wrote when they launched it -- 'tuned h_max down 10x to
        chase the LRO regression', 'sweep #3 with thread_number 16
        after the OpenMP fix', etc.

        The toml_io reader scans the file for the BEGIN / END marker
        pair (`_NOTES_BEGIN` / `_NOTES_END`) and lifts the comment
        body back into a `notes` string on the parsed dict; the
        emitter does the inverse on save. A TOML edited by hand
        keeps round-tripping as long as the markers stay intact.
        """
        g = QGroupBox("Notes  (optional, attached to this TOML)")
        v = QVBoxLayout(g)
        self._notes_edit = QPlainTextEdit()
        self._notes_edit.setPlaceholderText(
            "Freeform notes about this scenario. Stored as a "
            "comment block at the end of the TOML; copied verbatim "
            "into the per-run input.toml snapshot.")
        self._notes_edit.setMinimumHeight(80)
        self._notes_edit.textChanged.connect(self._touch)
        v.addWidget(self._notes_edit)
        return g

    # ------------------------------------------------------------------
    # [batch.columns] helpers
    # ------------------------------------------------------------------
    def _available_batch_targets(self) -> list[str]:
        """Override-target paths valid under the current object mode."""
        mode = "spacecraft" if self._radio_spc.isChecked() else "debris"
        return [p for p, tag in BATCH_TARGETS if tag is None or tag == mode]

    def _on_browse_cases_file(self) -> None:
        start = self._batch_cases_edit.text() or ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Locate cases CSV", start,
            "Cases (*.csv);;All files (*)")
        if path:
            self._batch_cases_edit.setText(path)
            self._refresh_batch_columns()

    # ------------------------------------------------------------------
    # cases_frame (icrf | ric) handler + status label
    # ------------------------------------------------------------------
    def _on_cases_frame_changed(self, frame: str) -> None:
        """Refresh the status label + rotated preview after a frame
        switch. No other side effects -- the path field is always the
        user-picked source, and the actual cases_file written to the
        TOML is computed at Generate (to_dict)."""
        self._update_cases_frame_status()
        self._update_ric_preview()

    # Frames that require a GUI-side rotation to ICRF at Generate-TOML
    # (spody.exe only ingests ICRF state). Used by every dispatch helper
    # below as the single source of truth; add a new rotating frame here
    # + its (basis, rotation_fn) pair in `_rotation_helpers()` and the
    # rest plugs in automatically.
    _ROTATING_FRAMES = ("ric", "lvlh")

    @staticmethod
    def _rotation_helpers(frame: str):
        """Return `(basis_fn, rotate_csv_fn)` for a rotating frame.
        Both helpers come from `spody_gui.frames` and share the same
        contract; the dispatch maps the combo value to the right pair.
        Raises ValueError for an unrecognised frame."""
        from .. import frames as _frames
        if frame == "ric":
            return _frames.ric_basis,  _frames.rotate_state_csv_ric_to_icrf
        if frame == "lvlh":
            return _frames.lvlh_basis, _frames.rotate_state_csv_lvlh_to_icrf
        raise ValueError(f"no rotation helpers for frame {frame!r}")

    def _resolved_cases_file(self) -> str:
        """Compute the cases_file value that spody.exe will see, from
        the current source path + frame combo. Returns an empty string
        when the source field is empty."""
        src = self._batch_cases_edit.text().strip()
        if not src:
            return ""
        frame = self._batch_cases_frame_combo.currentText()
        if frame not in self._ROTATING_FRAMES:
            # icrf or unset: the source IS the file spody.exe reads.
            return src
        # ric / lvlh: rotated copy alongside the source. Preserve
        # relative-ness (cases_file in the TOML is interpreted relative
        # to the TOML file's directory). Same '_wrt_icrf' suffix for
        # every rotating frame -- the destination is always ICRF.
        p = Path(src)
        return str(p.with_name(f"{p.stem}_wrt_icrf.csv"))

    def _update_cases_frame_status(self) -> None:
        """Single-line help text under the frame combo telling the user
        what spody.exe will actually read. spody.exe only accepts ICRF
        natively; this status keeps that explicit."""
        derived = self._resolved_cases_file()
        frame = self._batch_cases_frame_combo.currentText()
        if not derived:
            self._batch_frame_status.setText(
                "Pick a cases CSV above.")
            return
        if frame in self._ROTATING_FRAMES:
            self._batch_frame_status.setText(
                f"{frame.upper()} source. At Generate the GUI rotates "
                f"the state columns and writes '{Path(derived).name}'; "
                f"spody.exe reads THAT file.")
        else:
            self._batch_frame_status.setText(
                f"ICRF source. spody.exe reads it directly "
                f"('{Path(derived).name}').")

    # ------------------------------------------------------------------
    # Rotated preview (visible only when cases_frame == "ric")
    # ------------------------------------------------------------------
    def _clear_rotated_preview(self) -> None:
        self._batch_rotated_preview_table.setRowCount(0)
        self._batch_rotated_preview_table.setColumnCount(0)

    def _update_ric_preview(self) -> None:
        """Recompute the first 10 rotated rows of the source CSV and
        push them into the rotated-preview table. No-op (and hidden)
        when frame is not a rotating one or batch is disabled. Silent
        on errors -- only the status line shows the reason. Despite
        the historical name, this handles both RIC and LVLH sources."""
        frame = self._batch_cases_frame_combo.currentText()
        is_rotating = (self._batch_check.isChecked() and
                       frame in self._ROTATING_FRAMES)
        self._batch_rotated_preview_container.setVisible(is_rotating)
        if not is_rotating:
            return

        # Keep the header label in sync with the actually-selected
        # rotating frame so messages don't lie about the convention.
        self._batch_rotated_preview_header.setText(
            f"Rotated preview (post {frame.upper()} -> ICRF):")

        try:
            data = self.to_dict()
        except ValueError as exc:
            self._batch_rotated_preview_status.setText(
                f"(preview unavailable: form has invalid values -- {exc})")
            self._clear_rotated_preview()
            return

        resolved = self._resolve_ric_inputs(self._current_path, data)
        if not resolved or resolved[0] != "ok":
            msg = resolved[1] if resolved else "unknown"
            self._batch_rotated_preview_status.setText(
                f"(preview unavailable: {msg})")
            self._clear_rotated_preview()
            return
        _, src, _out, r_ref, v_ref, pos_cols, vel_cols = resolved

        # Build R once; bail with a clean status if the reference orbit
        # is degenerate.
        basis_fn, _ = self._rotation_helpers(frame)
        import csv as csv_mod
        import numpy as np
        try:
            R = basis_fn(r_ref, v_ref)
        except ValueError as exc:
            self._batch_rotated_preview_status.setText(
                f"(preview unavailable: {exc})")
            self._clear_rotated_preview()
            return

        # Read first 10 data rows of the source.
        PREVIEW_N = 10
        try:
            with src.open(encoding="utf-8", newline="") as fp:
                data_lines = [
                    ln for ln in fp
                    if ln.strip() and not ln.lstrip().startswith("#")
                ]
        except OSError as exc:
            self._batch_rotated_preview_status.setText(
                f"(preview unavailable: {exc})")
            self._clear_rotated_preview()
            return
        if not data_lines:
            self._batch_rotated_preview_status.setText(
                f"(preview: {src.name} has a header but no data rows)")
            self._clear_rotated_preview()
            return

        reader = csv_mod.DictReader(data_lines, skipinitialspace=True)
        header = [h.strip() for h in (reader.fieldnames or [])]
        reader.fieldnames = header
        rows: list[dict[str, str]] = []
        for row_idx, row in enumerate(reader, start=1):
            if len(rows) >= PREVIEW_N:
                break
            out_row = dict(row)
            try:
                if pos_cols is not None:
                    r_ric = np.array([float(row[c]) for c in pos_cols])
                    r_eci = R @ r_ric
                    for c, v in zip(pos_cols, r_eci):
                        out_row[c] = repr(float(v))
                if vel_cols is not None:
                    v_ric = np.array([float(row[c]) for c in vel_cols])
                    v_eci = R @ v_ric
                    for c, v in zip(vel_cols, v_eci):
                        out_row[c] = repr(float(v))
            except (KeyError, TypeError, ValueError) as exc:
                self._batch_rotated_preview_status.setText(
                    f"(preview: row {row_idx} skipped -- {exc})")
                continue
            rows.append(out_row)

        self._batch_rotated_preview_table.setRowCount(0)
        self._batch_rotated_preview_table.setColumnCount(len(header))
        self._batch_rotated_preview_table.setHorizontalHeaderLabels(header)
        for r_idx, row in enumerate(rows):
            self._batch_rotated_preview_table.insertRow(r_idx)
            for c_idx, col in enumerate(header):
                self._batch_rotated_preview_table.setItem(
                    r_idx, c_idx,
                    QTableWidgetItem(row.get(col, "")))

        # Help line: cite which columns were rotated.
        bits = []
        if pos_cols: bits.append(f"pos={list(pos_cols)}")
        if vel_cols: bits.append(f"vel={list(vel_cols)}")
        self._batch_rotated_preview_status.setText(
            f"(rotated preview: first {len(rows)} rows from {src.name}; "
            f"{', '.join(bits)})")

    def _refresh_batch_columns(self) -> None:
        """(Re-)read the CSV at cases_file and rebuild both the
        [batch.columns] mapping table and the data-preview table.
        Existing target/mode assignments survive a re-read when the
        column name reappears."""
        path_str = self._batch_cases_edit.text().strip()
        if not path_str:
            self._batch_cases_status.setText("")
            self._batch_columns_table.setRowCount(0)
            self._clear_preview()
            return

        p = Path(path_str)
        if not p.is_absolute() and self._current_path is not None:
            # Same resolution rule spody uses internally: paths in the
            # TOML are relative to the TOML file's directory.
            p = self._current_path.parent / p

        if not p.is_file():
            self._batch_cases_status.setText(f"(not found: {p})")
            self._batch_columns_table.setRowCount(0)
            self._clear_preview()
            return

        try:
            header, preview_rows, total_rows = read_csv_preview(p)
        except OSError as exc:
            self._batch_cases_status.setText(f"(read failed: {exc})")
            self._batch_columns_table.setRowCount(0)
            self._clear_preview()
            return

        # Drop the special `id` column from the *mapping* table (it's
        # used for case naming, not a spody override target). The
        # preview keeps every column so the user sees the full row.
        mapping_columns = [c for c in header if c.lower() != "id"]
        existing = self._snapshot_batch_columns()

        was_loading = self._loading
        self._loading = True
        try:
            self._batch_columns_table.setRowCount(0)
            for col in mapping_columns:
                self._add_batch_column_row(col, existing.get(col))
        finally:
            self._loading = was_loading

        self._batch_cases_status.setText(
            f"({len(mapping_columns)} non-id columns read from {p.name})")
        self._populate_preview(header, preview_rows, total_rows, p)
        # New columns may have changed which targets are mapped to
        # initial_state -- the rotated preview depends on that.
        self._update_ric_preview()

    def _clear_preview(self) -> None:
        """Empty the preview table + clear the 'first N of M' label."""
        self._batch_preview_status.setText("")
        self._batch_preview_table.setRowCount(0)
        self._batch_preview_table.setColumnCount(0)

    def _populate_preview(self, header: list[str], rows: list[list[str]],
                          total: int, src: Path) -> None:
        """Push the CSV preview into the read-only table. Cells are
        rendered verbatim (no parsing), so float vs int vs string is
        whatever the file says."""
        n_shown = len(rows)
        if total == 0:
            self._batch_preview_status.setText(
                f"(preview: file has a header but no data rows: {src.name})")
        elif n_shown < total:
            self._batch_preview_status.setText(
                f"(preview: first {n_shown} of {total} rows from {src.name})")
        else:
            self._batch_preview_status.setText(
                f"(preview: {n_shown} rows from {src.name})")

        self._batch_preview_table.setRowCount(0)
        self._batch_preview_table.setColumnCount(len(header))
        self._batch_preview_table.setHorizontalHeaderLabels(header)
        for r, row_cells in enumerate(rows):
            self._batch_preview_table.insertRow(r)
            for c in range(len(header)):
                text = row_cells[c] if c < len(row_cells) else ""
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self._batch_preview_table.setItem(r, c, item)

    def _add_batch_column_row(self, col_name: str,
                               existing: tuple[str, str] | None = None) -> None:
        """Append one row to the column-mapping table. `existing` is a
        previous `(target, mode)` selection that survived a reload."""
        row = self._batch_columns_table.rowCount()
        self._batch_columns_table.insertRow(row)

        name_item = QTableWidgetItem(col_name)
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._batch_columns_table.setItem(row, 0, name_item)

        target_combo = QComboBox()
        target_combo.addItem(UNASSIGNED)
        for path in self._available_batch_targets():
            target_combo.addItem(path)
        target_combo.currentIndexChanged.connect(self._touch)
        self._batch_columns_table.setCellWidget(row, 1, target_combo)

        mode_combo = QComboBox()
        mode_combo.addItems(["override", "delta"])
        mode_combo.currentIndexChanged.connect(self._touch)
        self._batch_columns_table.setCellWidget(row, 2, mode_combo)

        # Restore the previous selection if one survived a re-read;
        # otherwise try the obvious-name heuristic for a fresh row.
        if existing is not None:
            tgt, mode = existing
            idx = target_combo.findText(tgt)
            if idx >= 0:
                target_combo.setCurrentIndex(idx)
            mode_combo.setCurrentText(mode)
        else:
            tgt = heuristic_target(col_name, self._available_batch_targets())
            if tgt is not None:
                target_combo.setCurrentText(tgt)

    def _snapshot_batch_columns(self) -> dict[str, tuple[str, str]]:
        """Capture the current (target, mode) per column. Used so a
        Re-read columns press doesn't blow away the user's work."""
        out: dict[str, tuple[str, str]] = {}
        for row in range(self._batch_columns_table.rowCount()):
            item = self._batch_columns_table.item(row, 0)
            if item is None:
                continue
            target = self._batch_columns_table.cellWidget(row, 1).currentText()
            mode   = self._batch_columns_table.cellWidget(row, 2).currentText()
            out[item.text()] = (target, mode)
        return out

    def _refresh_batch_column_target_combos(self) -> None:
        """Rebuild each row's Target combo for a new object mode,
        preserving any selection that survives the mode change."""
        if not hasattr(self, "_batch_columns_table"):
            return   # called too early during __init__
        new_targets = self._available_batch_targets()
        for row in range(self._batch_columns_table.rowCount()):
            combo = self._batch_columns_table.cellWidget(row, 1)
            if combo is None:
                continue
            current = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(UNASSIGNED)
            for t in new_targets:
                combo.addItem(t)
            idx = combo.findText(current)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.blockSignals(False)

    def _batch_columns_to_dict(self) -> dict[str, Any]:
        """Serialise the column-mapping table to a TOML-ready dict for
        emission inside `[batch.columns]`. Rows left at "(unassigned)"
        are emitted as `<col> = ""` -- the metadata-column sentinel the
        C parser recognises (target == empty string -> read out of the
        CSV but not applied to any field). This lets the user keep
        bookkeeping columns like L_char_m or fragment_id in the CSV
        without spody validate complaining about a missing entry."""
        out: dict[str, Any] = {}
        for row in range(self._batch_columns_table.rowCount()):
            item = self._batch_columns_table.item(row, 0)
            if item is None:
                continue
            target = self._batch_columns_table.cellWidget(row, 1).currentText()
            mode   = self._batch_columns_table.cellWidget(row, 2).currentText()
            if target == UNASSIGNED:
                out[item.text()] = ""
                continue
            if mode == "delta":
                out[item.text()] = {"target": target, "mode": "delta"}
            else:
                out[item.text()] = target
        return out

    def _apply_loaded_batch_columns(self, cols_data: dict[str, Any]) -> None:
        """After a TOML load, push the loaded `[batch.columns]` entries
        onto matching rows of the column-mapping table. Rows whose
        column name has no entry in the TOML keep their heuristic /
        unassigned default."""
        for row in range(self._batch_columns_table.rowCount()):
            item = self._batch_columns_table.item(row, 0)
            if item is None:
                continue
            name = item.text()
            entry = cols_data.get(name)
            if entry is None:
                continue
            if isinstance(entry, dict):
                target = entry.get("target", "")
                mode   = entry.get("mode", "override")
            else:
                target = str(entry)
                mode   = "override"
            target_combo = self._batch_columns_table.cellWidget(row, 1)
            mode_combo   = self._batch_columns_table.cellWidget(row, 2)
            # Empty target == metadata-column sentinel (col = ""). Pin
            # the combo to (unassigned) so the round-trip preserves the
            # user's choice instead of letting the name-based heuristic
            # silently reassign it on load.
            if target == "":
                target_combo.setCurrentText(UNASSIGNED)
            else:
                idx = target_combo.findText(target)
                if idx >= 0:
                    target_combo.setCurrentIndex(idx)
            mode_combo.setCurrentText(mode)

    def _on_srp_toggled(self, checked: bool) -> None:
        self._srp_box.setVisible(checked)

    def _on_srp_param_toggled(self) -> None:
        """Greys out whichever of area_m2 / am_srp is not the selected
        parameter -- so the user can't accidentally fill both and hit
        the XOR error from the parser."""
        use_area = self._srp_radio_area.isChecked()
        self._widgets["spacecraft.srp.area_m2"].setEnabled(use_area)
        self._widgets["spacecraft.srp.am_srp"].setEnabled(not use_area)

    def _on_drag_toggled(self, checked: bool) -> None:
        self._drag_box.setVisible(checked)

    def _on_drag_param_toggled(self) -> None:
        """Same XOR grey-out as SRP, for area_m2 / am_drag."""
        use_area = self._drag_radio_area.isChecked()
        self._widgets["spacecraft.drag.area_m2"].setEnabled(use_area)
        self._widgets["spacecraft.drag.am_drag"].setEnabled(not use_area)

    def _on_debris_drag_toggled(self, checked: bool) -> None:
        self._dbr_drag_box.setVisible(checked)
