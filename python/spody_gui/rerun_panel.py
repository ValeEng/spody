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
"""Re-run selettivo: re-launch a batch on a subset of its previous cases.

Workflow:

    1. User picks a per-run folder (one that contains `input.toml` +
       `<batch_name>_events.bin`).
    2. The panel parses the snapshot TOML to find the cases.csv, then
       reads the aggregated events file (SPDYEVTB) to build a per-case
       summary (impacted? eclipsed? survived?).
    3. User picks a subset via presets (survivors / crashed / any-event)
       or by toggling individual rows.
    4. On `Re-run selected`, the panel:
         - Creates a sibling folder `<run>_rerun_<UTC-ISO8601>/` next to
           the source run.
         - Writes the filtered cases.csv there.
         - Writes a new input.toml there, identical to the snapshot but
           with every file path (ephemeris, harmonics, cases_file,
           output_dir) rewritten to an absolute path. Absolute paths
           sidestep the trap that the snapshot's relatives were written
           against the ORIGINAL TOML's dir, not the snapshot's.
         - Emits `runRequested(new_toml_path)` which MainWindow handles
           by loading the file into the Run tab and kicking off
           `spody batch`.

The source folder is never modified -- the rerun is fully self-contained
in its own sibling directory, which makes it easy to delete or compare.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from spody_io import (
    EVENT_KIND_ECLIPSE,
    EVENT_KIND_IMPACT,
    read_events,
)

from .settings import SettingsStore
from .toml_io import format_toml, read_toml


# ----------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------
@dataclass
class _CaseRow:
    """One row in the cases table: identity + per-column values from
    cases.csv joined with whatever the events file says happened to it.
    `last_kind` is the kind of the LAST event recorded for this case
    (so survival = no events at all, IMPACT trumps ECLIPSE because
    impact is terminal). `last_t` is the sim time [s] of that event,
    None when there were no events."""
    case_id:   str
    columns:   dict[str, str]      # raw string values from cases.csv
    last_kind: int | None = None   # EVENT_KIND_* or None for survivors
    last_t:    float | None = None
    n_events:  int = 0


# ----------------------------------------------------------------------
# Filesystem helpers
# ----------------------------------------------------------------------
def _resolve_against_snapshot(raw: str, snapshot_dir: Path) -> Path | None:
    """Resolve a possibly-relative path string from the snapshot TOML
    against the snapshot dir's parent ladder. Mirrors the resolution
    logic in `analysis.context.resolve_ephemeris_path`: the snapshot is
    a verbatim copy of the source TOML, so its relative paths were
    written against the *original* file's dir, which is typically two
    levels above the snapshot folder."""
    if not raw:
        return None
    p = Path(raw)
    if p.is_absolute():
        return p if p.exists() else None
    for cand_base in (snapshot_dir,
                      snapshot_dir.parent,
                      snapshot_dir.parent.parent,
                      Path.cwd()):
        cand = cand_base / raw
        try:
            r = cand.resolve()
        except OSError:
            continue
        if r.exists():
            return r
    return None


def _read_cases_csv(path: Path) -> tuple[list[str], list[_CaseRow]]:
    """Read a cases.csv file the same way spody-core does
    (toml_input.c:load_cases_csv): skip blank/comment lines, first
    remaining line is the header; an `id` column (if present) provides
    case ids; otherwise ids are 1-based zero-padded row indices.

    Returns (header, rows). `header` is the original column order
    (with `id` kept in place if present); `rows[i].columns` excludes
    `id` because spody emits it as a separate slot in case_ids."""
    lines = [
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    if not lines:
        raise ValueError(f"{path}: empty (only comments?)")

    reader = csv.DictReader(lines, skipinitialspace=True)
    header = [h.strip() for h in (reader.fieldnames or [])]
    reader.fieldnames = header
    if not header:
        raise ValueError(f"{path}: header has no columns")

    has_id = "id" in header
    data_rows: list[dict[str, str]] = [
        {k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
        for row in reader
    ]
    n = len(data_rows)
    if n == 0:
        raise ValueError(f"{path}: no data rows after header")

    if has_id:
        ids = [row["id"] for row in data_rows]
    else:
        # Same zero-padding spody-core does: width = digits in n_cases.
        width = max(1, len(str(n)))
        ids = [str(i + 1).zfill(width) for i in range(n)]

    out: list[_CaseRow] = []
    for cid, row in zip(ids, data_rows):
        cols = {k: v for k, v in row.items() if k != "id"}
        out.append(_CaseRow(case_id=cid, columns=cols))
    return header, out


def _annotate_with_events(rows: list[_CaseRow], events_path: Path) -> None:
    """Read the aggregated events binary and stamp `last_kind` /
    `last_t` / `n_events` on every row whose case_idx appears.

    Per-run (SPDYEVT_) files have no `case_idx`, so they aren't usable
    here -- this view is batch-only. Raises ValueError if the file
    turns out to be the per-run format."""
    arr = read_events(events_path)
    if "case_idx" not in arr.dtype.names:
        raise ValueError(
            f"{events_path}: per-run events file (no case_idx field) -- "
            "re-run selettivo needs the aggregated batch events file "
            "(SPDYEVTB) written by `spody batch`")

    n = len(rows)
    for r in arr:
        i = int(r["case_idx"])
        if not (0 <= i < n):
            continue
        row = rows[i]
        row.n_events += 1
        # Keep the LAST event (in sim time) and prefer IMPACT over
        # ECLIPSE at equal time, since IMPACT terminates the run.
        kind = int(r["kind"])
        t    = float(r["t"])
        if row.last_t is None or t > row.last_t or (
            t == row.last_t and kind == EVENT_KIND_IMPACT):
            row.last_kind = kind
            row.last_t = t


def _kind_label(kind: int | None) -> str:
    if kind is None: return "(survived)"
    if kind == EVENT_KIND_IMPACT:  return "IMPACT"
    if kind == EVENT_KIND_ECLIPSE: return "ECLIPSE"
    return f"kind={kind}"


# ----------------------------------------------------------------------
# TOML / CSV emission for the rerun bundle
# ----------------------------------------------------------------------
def _utc_stamp() -> str:
    """Match spody-core's run-folder format (`YYYY-MM-DDTHHMMSSZ`)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")


def _absolutise_paths(cfg: dict[str, Any], snapshot_dir: Path) -> None:
    """Walk `cfg` (in place) and rewrite path-like string values to
    absolute paths, resolving relative entries against the snapshot's
    parent ladder. Skips keys whose value doesn't resolve to an
    existing file/dir on disk so non-path strings stay untouched.

    The list of path-bearing keys is hard-coded against spody's schema
    (see toml_input.h / SECTION_ORDER in toml_io.py). Anything new
    added there should be reflected here."""
    targets: list[tuple[str, str]] = [
        ("ephemeris",   "file"),
        ("force_model", "harmonics_file"),
        ("batch",       "output_dir"),
        ("output",      "output_dir"),
    ]
    for section, key in targets:
        sub = cfg.get(section)
        if not isinstance(sub, dict):
            continue
        raw = sub.get(key, "")
        if not isinstance(raw, str) or not raw:
            continue
        resolved = _resolve_against_snapshot(raw, snapshot_dir)
        if resolved is not None:
            # POSIX-style separators so the TOML reads the same on any
            # host (Windows accepts forward slashes everywhere).
            sub[key] = resolved.as_posix()


def _write_filtered_cases(src_header: list[str],
                          rows: list[_CaseRow],
                          selected_ids: set[str],
                          dest: Path) -> int:
    """Emit a filtered cases.csv at `dest` keeping the original column
    order (including `id` if it was there). Returns the number of rows
    written. Adds a one-line comment header so the source of the file
    is obvious when grepping later."""
    has_id = "id" in src_header
    with dest.open("w", encoding="utf-8", newline="") as f:
        f.write(f"# generated by spody_gui.rerun_panel at {_utc_stamp()}\n")
        f.write(f"# subset of {len(rows)} -> {len(selected_ids)} cases\n")
        writer = csv.DictWriter(f, fieldnames=src_header)
        writer.writeheader()
        n = 0
        for r in rows:
            if r.case_id not in selected_ids:
                continue
            row_out = dict(r.columns)
            if has_id:
                row_out["id"] = r.case_id
            writer.writerow(row_out)
            n += 1
    return n


# ----------------------------------------------------------------------
# The widget
# ----------------------------------------------------------------------
class RerunPanel(QWidget):
    """Tab content for re-launching a batch on a subset of its cases.
    Emits `runRequested(Path)` when the user finalises a re-run; the
    main window listens and forwards to the Run tab."""

    runRequested = Signal(Path)

    # Tree column indices -- single place so the slot bodies don't
    # carry magic numbers.
    _COL_ID     = 0
    _COL_EVENT  = 1
    _COL_TIME_S = 2
    _COL_NEV    = 3
    _N_FIXED_COLS = 4   # before per-column dynamic ones

    def __init__(self, store: SettingsStore,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._store = store

        # Loaded state -- all four are reset together when a new folder
        # is picked. `_rows` mirrors the on-screen tree; the tree owns
        # the checkbox state directly (QTreeWidgetItem.checkState).
        self._snapshot_path: Path | None = None
        self._cases_path:    Path | None = None
        self._events_path:   Path | None = None
        self._cfg:           dict[str, Any] | None = None
        self._csv_header:    list[str] = []
        self._rows:          list[_CaseRow] = []
        self._extra_cols:    list[str] = []   # cases.csv cols minus 'id'

        root = QVBoxLayout(self)

        # --- Source picker -------------------------------------------
        src_box = QGroupBox("Source batch run")
        src_lay = QVBoxLayout(src_box)
        pick_row = QHBoxLayout()
        self._lbl_folder = QLabel("(no run folder selected)")
        self._lbl_folder.setWordWrap(True)
        btn_pick = QPushButton("Pick run folder...")
        btn_pick.clicked.connect(self._on_pick_folder)
        pick_row.addWidget(self._lbl_folder, 1)
        pick_row.addWidget(btn_pick)
        src_lay.addLayout(pick_row)
        self._lbl_summary = QLabel("")
        self._lbl_summary.setStyleSheet("color: gray;")
        src_lay.addWidget(self._lbl_summary)
        root.addWidget(src_box)

        # --- Preset bar ----------------------------------------------
        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("Select:"))
        for label, slot in (
            ("All",         self._sel_all),
            ("None",        self._sel_none),
            ("Survivors",   self._sel_survivors),
            ("Crashed (IMPACT)", self._sel_crashed),
            ("Any event",   self._sel_any_event),
        ):
            b = QPushButton(label)
            b.clicked.connect(slot)
            preset_row.addWidget(b)
        preset_row.addStretch(1)
        preset_row.addWidget(QLabel("Filter id:"))
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("substring match...")
        self._filter.textChanged.connect(self._apply_filter)
        self._filter.setMaximumWidth(200)
        preset_row.addWidget(self._filter)
        root.addLayout(preset_row)

        # --- Cases tree ----------------------------------------------
        self._tree = QTreeWidget()
        self._tree.setRootIsDecorated(False)
        self._tree.setUniformRowHeights(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._tree.itemChanged.connect(self._on_item_changed)
        root.addWidget(self._tree, 1)

        # --- Re-run bar ----------------------------------------------
        run_row = QHBoxLayout()
        self._lbl_selection = QLabel("0 selected")
        run_row.addWidget(self._lbl_selection)
        run_row.addStretch(1)
        self._btn_rerun = QPushButton("Re-run selected")
        self._btn_rerun.clicked.connect(self._on_rerun)
        self._btn_rerun.setEnabled(False)
        run_row.addWidget(self._btn_rerun)
        root.addLayout(run_row)

        self._refresh_tree_columns()  # initial empty headers

    # ------------------------------------------------------------------
    # External API
    # ------------------------------------------------------------------
    def load_run_folder(self, folder: Path) -> None:
        """Programmatic entry point so MainWindow can preload the
        latest run after a batch finishes (or jump here from the
        Analysis tab). Errors raise a message box; the panel stays in
        its previous state on failure."""
        try:
            self._load_folder_impl(folder)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Re-run", str(exc))

    # ------------------------------------------------------------------
    # Folder loading
    # ------------------------------------------------------------------
    def _on_pick_folder(self) -> None:
        start = ""
        if self._snapshot_path is not None:
            start = str(self._snapshot_path.parent.parent)
        path = QFileDialog.getExistingDirectory(
            self, "Pick run folder (must contain input.toml)", start)
        if path:
            self.load_run_folder(Path(path))

    def _load_folder_impl(self, folder: Path) -> None:
        if not folder.is_dir():
            raise ValueError(f"not a directory: {folder}")
        # Modern snapshots are `<ts>_input.toml`; legacy runs use
        # plain `input.toml`. Accept either so old re-run workflows
        # keep working.
        snap = folder / f"{folder.name}_input.toml"
        if not snap.is_file():
            snap = folder / "input.toml"
        if not snap.is_file():
            raise ValueError(
                f"no input.toml in {folder}. Pick a per-run folder "
                "(spody.exe drops one snapshot per batch).")

        cfg = read_toml(snap)
        batch = cfg.get("batch")
        if not isinstance(batch, dict) or not batch.get("name"):
            raise ValueError(
                f"{snap}: [batch] section missing or has no `name`; this "
                "snapshot wasn't produced by `spody batch`.")

        cases_raw = batch.get("cases_file", "")
        cases = _resolve_against_snapshot(cases_raw, folder)
        if cases is None or not cases.is_file():
            raise ValueError(
                f"cases_file '{cases_raw}' from the snapshot could not "
                f"be located. Tried snapshot dir and its two parents.")

        # Aggregated events file lives next to the snapshot, named
        # `<batch.name>_events.bin` (see main.c:batch_events_path).
        events_name = f"{batch['name']}_events.bin"
        events = folder / events_name
        if not events.is_file():
            raise ValueError(
                f"no aggregated events file '{events_name}' next to the "
                "snapshot. Re-run selettivo needs the batch events log; "
                "enable [output].events_log on the source batch and re-run it.")

        header, rows = _read_cases_csv(cases)
        _annotate_with_events(rows, events)

        # Commit state only once everything parsed cleanly.
        self._snapshot_path = snap
        self._cases_path    = cases
        self._events_path   = events
        self._cfg           = cfg
        self._csv_header    = header
        self._rows          = rows
        self._extra_cols    = [c for c in header if c != "id"]

        self._lbl_folder.setText(f"Run folder: {folder}")
        self._lbl_summary.setText(
            f"snapshot: {snap.name}  |  cases: {cases} ({len(rows)} rows)  "
            f"|  events: {events.name}")

        self._refresh_tree_columns()
        self._populate_tree()
        self._update_selection_label()

    # ------------------------------------------------------------------
    # Tree management
    # ------------------------------------------------------------------
    def _refresh_tree_columns(self) -> None:
        cols = ["case_id", "last event", "last t [s]", "n events"] + self._extra_cols
        self._tree.setColumnCount(len(cols))
        self._tree.setHeaderLabels(cols)
        # Sensible default widths; user can resize.
        hdr = self._tree.header()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        if self._tree.columnCount():
            hdr.setSectionResizeMode(self._COL_ID, QHeaderView.ResizeMode.ResizeToContents)

    def _populate_tree(self) -> None:
        # Block signals while we batch-insert items to avoid N^2 work
        # from itemChanged firing per row.
        self._tree.blockSignals(True)
        self._tree.clear()
        for r in self._rows:
            fields = [
                r.case_id,
                _kind_label(r.last_kind),
                "" if r.last_t is None else f"{r.last_t:.3f}",
                str(r.n_events),
            ]
            for c in self._extra_cols:
                fields.append(r.columns.get(c, ""))
            it = QTreeWidgetItem(fields)
            it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(self._COL_ID, Qt.CheckState.Unchecked)
            self._tree.addTopLevelItem(it)
        self._tree.blockSignals(False)

    def _iter_items(self):
        for i in range(self._tree.topLevelItemCount()):
            yield self._tree.topLevelItem(i)

    def _selected_ids(self) -> set[str]:
        return {it.text(self._COL_ID) for it in self._iter_items()
                if it.checkState(self._COL_ID) == Qt.CheckState.Checked}

    def _on_item_changed(self, _item: QTreeWidgetItem, _col: int) -> None:
        self._update_selection_label()

    def _update_selection_label(self) -> None:
        n = len(self._selected_ids())
        self._lbl_selection.setText(f"{n} selected")
        self._btn_rerun.setEnabled(n > 0 and self._snapshot_path is not None)

    # ------------------------------------------------------------------
    # Preset slots
    # ------------------------------------------------------------------
    def _set_all(self, predicate) -> None:
        self._tree.blockSignals(True)
        for i, it in enumerate(self._iter_items()):
            target = Qt.CheckState.Checked if predicate(self._rows[i]) \
                else Qt.CheckState.Unchecked
            it.setCheckState(self._COL_ID, target)
        self._tree.blockSignals(False)
        self._update_selection_label()

    def _sel_all(self)         -> None: self._set_all(lambda _: True)
    def _sel_none(self)        -> None: self._set_all(lambda _: False)
    def _sel_survivors(self)   -> None: self._set_all(lambda r: r.last_kind is None)
    def _sel_crashed(self)     -> None: self._set_all(lambda r: r.last_kind == EVENT_KIND_IMPACT)
    def _sel_any_event(self)   -> None: self._set_all(lambda r: r.n_events > 0)

    def _apply_filter(self, text: str) -> None:
        """Hide rows whose case_id doesn't contain `text` (case-
        insensitive). Pure visibility -- hidden rows keep their
        checkbox state, so the preset+filter combination works
        intuitively (filter narrows; preset toggles only what's
        currently visible? -- no, presets ignore visibility for
        predictability)."""
        needle = text.strip().lower()
        for it in self._iter_items():
            visible = (needle in it.text(self._COL_ID).lower()) if needle else True
            it.setHidden(not visible)

    # ------------------------------------------------------------------
    # Re-run
    # ------------------------------------------------------------------
    def _on_rerun(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return  # button should be disabled, defensive
        assert self._snapshot_path is not None
        assert self._cfg is not None
        snap = self._snapshot_path
        src_folder = snap.parent

        # Destination: sibling folder, suffixed with timestamp so
        # repeat re-runs don't collide.
        stamp = _utc_stamp()
        dest_folder = src_folder.parent / f"{src_folder.name}_rerun_{stamp}"
        try:
            dest_folder.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            QMessageBox.warning(self, "Re-run",
                f"Destination folder already exists:\n{dest_folder}")
            return

        # 1. Filtered cases.csv
        dest_cases = dest_folder / "cases.csv"
        n_written = _write_filtered_cases(
            self._csv_header, self._rows, ids, dest_cases)

        # 2. New input.toml: deep-copy enough of the snapshot to safely
        # mutate, absolutise path fields, and point at our filtered CSV.
        # A shallow dict.copy() isn't enough because we mutate the
        # nested [batch] / [output] / etc. sub-dicts.
        new_cfg: dict[str, Any] = {
            k: (dict(v) if isinstance(v, dict) else v)
            for k, v in self._cfg.items()
        }
        _absolutise_paths(new_cfg, src_folder)
        new_cfg.setdefault("batch", {})
        new_cfg["batch"]["cases_file"] = dest_cases.as_posix()
        # Tag the rerun in batch.name so per-case output filenames are
        # distinguishable from the source run when both share an
        # output_dir.
        base_name = str(new_cfg["batch"].get("name", "batch")) or "batch"
        new_cfg["batch"]["name"] = f"{base_name}_rerun_{stamp}"
        # Park the rerun's outputs INSIDE the rerun folder regardless
        # of what the source had in [output].output_dir / [batch].output_dir
        # -- keeps every rerun self-contained.
        new_cfg.setdefault("output", {})
        local_out = (dest_folder / "output").as_posix()
        new_cfg["output"]["output_dir"] = local_out
        new_cfg["batch"]["output_dir"]  = local_out
        try:
            (dest_folder / "output").mkdir(exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Re-run",
                f"Cannot create output dir:\n{exc}")
            return

        # Append a notes line describing the provenance so the rerun is
        # self-documenting when reopened later (the toml_io notes block
        # round-trips through the form).
        notes_old = new_cfg.get("notes", "") if isinstance(new_cfg.get("notes"), str) else ""
        provenance = (f"Re-run of {snap}: {n_written}/{len(self._rows)} "
                      f"cases selected at {stamp}.")
        new_cfg["notes"] = (notes_old.rstrip() + "\n\n" + provenance).strip()

        dest_toml = dest_folder / "input.toml"
        try:
            dest_toml.write_text(format_toml(new_cfg), encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "Re-run",
                f"Cannot write {dest_toml}:\n{exc}")
            return

        # Confirm + emit. The main window will load it into the form
        # and launch `spody batch`.
        QMessageBox.information(self, "Re-run",
            f"Wrote {n_written} cases to:\n{dest_cases}\n\n"
            f"New input.toml:\n{dest_toml}\n\n"
            "Loading into Run tab and launching `spody batch`...")
        self.runRequested.emit(dest_toml)
