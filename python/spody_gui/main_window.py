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
"""Main application window: TOML form, terminal pane, menus, status bar."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

import os
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from . import assets
from .about_dialog import show_about
from .analysis_panel import AnalysisPanel
from .rerun_panel import RerunPanel
from .runner import SpodyRunner
from .settings import SettingsDialog, SettingsStore
from .setup_wizard import SetupWizard, require_data_ready
from .terminal import TerminalView
from .toml_form import TomlForm

# How many entries to keep in the File > Recent menu.
RECENT_FILES_MAX = 8

# Folder names skipped during the working-dir TOML scan. Common
# build / VCS / venv noise that has no business in the combo. Note
# that `output/` is INTENTIONALLY NOT in this list: per-run snapshots
# inside output folders are valid load targets so the user can re-run
# them. The WIP-save mechanism in `_action_save` protects those
# snapshots from accidental overwrite.
_TOML_SCAN_SKIP_DIRS: frozenset[str] = frozenset({
    "__pycache__", ".git", ".venv", "venv",
    "build", "dist", "node_modules",
})


def _project_root_for_toml(toml_path: Path) -> Path:
    """Walk up from `toml_path.parent` looking for the project root:
    the closest ancestor that contains both an `output/` subdir and
    at least one `.toml` file. Used to auto-adopt the working dir
    when the user opens a TOML, so loading a snapshot deep inside
    `output/<ts>/` still surfaces the sibling source + every other
    scenario in the project root.

    Falls back to `toml_path.parent` when no such ancestor exists
    (e.g. a brand-new TOML with no run history yet)."""
    for parent in (toml_path.parent, *toml_path.parent.parents):
        try:
            has_output = (parent / "output").is_dir()
            has_toml = False
            for p in parent.iterdir():
                if p.is_file() and p.suffix.lower() == ".toml":
                    has_toml = True
                    break
        except OSError:
            continue
        if has_output and has_toml:
            return parent
    return toml_path.parent


def _path_is_under(path: Path, root: Path) -> bool:
    """True iff `path` resolves to `root` or any descendant. Used to
    decide whether opening / running a TOML should retarget the
    working dir: only when the file lives OUTSIDE the current
    working dir. Files already inside leave the working dir alone
    so the user's broader scope (e.g. an `examples/` browse covering
    many scenarios) doesn't silently shrink to a single sub-folder."""
    try:
        resolved_path = path.resolve()
        resolved_root = root.resolve()
    except OSError:
        return False
    try:
        resolved_path.relative_to(resolved_root)
        return True
    except ValueError:
        return False


# --- WIP TOML helpers -------------------------------------------------
# A TOML is "runnata" iff its parent folder contains at least one .bin
# file -- snapshots live next to their output bins, so the .bin sibling
# test pins down both "the snapshot itself" and "any source TOML the
# engine has already produced a run for, in a layout that puts outputs
# next to the input". Saving a runnata TOML would clobber a file the
# user (or an earlier run) is depending on; we divert to a WIP file
# instead.
#
# WIPs use a `.wip.toml` suffix so they're easy to spot in directory
# listings and in the TOML combo. Always overwritable.

def _toml_is_runnata(toml_path: Path) -> bool:
    parent = toml_path.parent
    try:
        for p in parent.iterdir():
            if p.is_file() and p.suffix.lower() == ".bin":
                return True
    except OSError:
        pass
    return False


def _is_wip_toml(toml_path: Path) -> bool:
    return (toml_path.suffix.lower() == ".toml"
            and toml_path.stem.endswith(".wip"))


def _wip_path_for(toml_path: Path) -> Path:
    """The WIP filename next to `toml_path`. Single WIP per source
    file; subsequent saves overwrite the same WIP."""
    stem = toml_path.stem
    if stem.endswith(".wip"):
        return toml_path
    return toml_path.with_name(f"{stem}.wip.toml")


class MainWindow(QMainWindow):
    """Single-window UI. Run tab: structured TOML form on the left,
    terminal output on the right. Analysis tab: file browser + plots."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SpOdy")
        self.resize(1280, 800)

        self._store = SettingsStore()

        # Working dir is a single shared concept across every tab: the
        # Form tab lists *.toml under it and the Analysis tab scans
        # *.bin under it. Opening any TOML auto-sets it to the TOML's
        # parent so the rest of the UI follows along without manual
        # synchronisation.
        self._working_dir: Path | None = None

        # Central layout: top-level mode switch between Run (form +
        # terminal) and Analysis (file picker + plots). The two modes
        # are completely independent widgets; the menu bar stays shared
        # but Run-only actions are no-ops while the Analysis tab is up.
        self._form = TomlForm(self._store)
        self._terminal = TerminalView()

        # Form column: TOML picker row sits ABOVE the form widget so
        # it visually belongs to the form (same width as the parameter
        # area) and doesn't sprawl above the terminal. The row is
        # built by MainWindow because it drives shared state (combo
        # listings + Load/Save actions), but it physically lives
        # alongside the form widgets the user is editing.
        form_column = QWidget()
        form_col_lay = QVBoxLayout(form_column)
        form_col_lay.setContentsMargins(0, 0, 0, 0)
        form_col_lay.setSpacing(4)
        form_col_lay.addWidget(self._build_toml_row())
        form_col_lay.addWidget(self._form, 1)

        run_tab = QSplitter(Qt.Orientation.Horizontal)
        run_tab.addWidget(form_column)
        run_tab.addWidget(self._terminal)
        run_tab.setStretchFactor(0, 1)
        run_tab.setStretchFactor(1, 1)
        run_tab.setSizes([640, 640])

        self._analysis = AnalysisPanel(self._store)
        self._rerun    = RerunPanel(self._store)
        # The rerun tab generates a new input.toml + cases.csv subset
        # and asks us to launch `spody batch` on it; we forward to the
        # existing Run path so the user sees the same form/terminal UI.
        self._rerun.runRequested.connect(self._on_rerun_requested)

        self._tabs = QTabWidget()
        self._tabs.addTab(run_tab,         "Run")
        self._tabs.addTab(self._analysis,  "Analysis")
        self._tabs.addTab(self._rerun,     "Re-run")

        # Top bar sits above the tabs and only carries the working-dir
        # field + Browse -- that IS shared between Run (lists TOMLs)
        # and Analysis (scans bins), so it deserves the global slot.
        # The TOML picker + Load / Save / Save As live inside the Run
        # tab (see `_build_toml_row`).
        top_bar = self._build_top_bar()

        central = QWidget()
        central_lay = QVBoxLayout(central)
        central_lay.setContentsMargins(6, 4, 6, 0)
        central_lay.setSpacing(4)
        central_lay.addWidget(top_bar)
        central_lay.addWidget(self._tabs, 1)
        self.setCentralWidget(central)

        # Runner: QProcess wrapper. Wired to the terminal and status bar.
        self._runner = SpodyRunner(self)
        self._runner.line_received.connect(self._terminal.append_line)
        self._runner.started.connect(self._on_run_started)
        self._runner.finished.connect(self._on_run_finished)
        self._runner.error.connect(self._on_run_error)

        # Status bar shows the file on the left and run status on the right.
        self._status_path = QLabel("(no file)")
        self._status_run = QLabel("idle")
        self.statusBar().addWidget(self._status_path, 1)
        self.statusBar().addPermanentWidget(self._status_run)

        # 1 Hz tick to keep the elapsed-time readout live while running.
        self._status_timer = QTimer(self)
        self._status_timer.setInterval(1000)
        self._status_timer.timeout.connect(self._refresh_run_status)

        # Form tells us when its content has been edited (so we can
        # mark the window title dirty) and when the RUN button is
        # clicked (so we share the save-before-run flow with the menu
        # actions). Post-IO sync (recents + working dir + analysis)
        # is driven by Save / Save As going through _save_to ->
        # _on_form_loaded_or_saved.
        self._form.modificationChanged.connect(self._refresh_title)
        self._form.runRequested.connect(self._action_run)
        self._form.stopRequested.connect(self._action_stop)
        self._form.calibrateRequested.connect(self._action_calibrate)
        # Calibrate bookkeeping: while a `spody calibrate` run is in
        # flight we watch the streamed lines for the `nodes :` report
        # row so the form's density_scale_file can be auto-filled on
        # success. `_calibrate_run_cwd` anchors the engine's
        # CWD-relative path back to an absolute one.
        self._calibrate_active = False
        self._calibrate_nodes_path: str | None = None
        self._calibrate_run_cwd: Path | None = None
        self._runner.line_received.connect(self._on_calibrate_line)

        self._build_menus()
        self._refresh_title()
        self._refresh_recent_menu()

        # Auto-pop the Setup wizard the first time the window is shown
        # if any required data file is missing. Done via a 0-ms single
        # shot so the main window is visible underneath the modal.
        QTimer.singleShot(0, self._maybe_pop_setup_wizard)
        # Separately: if the data is complete but the IERS EOP file is
        # stale (Bulletin A weekly, Bulletin B monthly), offer a
        # one-click re-download. Earth-centered propagation falls back
        # to predictions past the observed horizon -- still works but
        # noticeably less accurate, so a heads-up at launch is worth
        # the one extra dialog.
        QTimer.singleShot(0, self._maybe_warn_eop_stale)

    # ------------------------------------------------------------------
    # Top bar (working dir) + Run-tab TOML row (combo + Load/Save)
    # ------------------------------------------------------------------
    def _build_top_bar(self) -> QWidget:
        """Always-visible strip above the tabs. Carries only the
        working-dir field + Browse -- the one concept that BOTH the
        Run tab (TOML listing) and the Analysis tab (bin scanning)
        consume. TOML-specific controls live inside the Run tab,
        built by `_build_toml_row`."""
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(0, 0, 0, 0)

        lay.addWidget(QLabel("Working dir:"))
        self._dir_edit = QLineEdit()
        self._dir_edit.setReadOnly(True)
        self._dir_edit.setPlaceholderText(
            "(no working dir -- pick a folder or open a TOML)")
        # Greedy stretch so the full path stays readable; the top bar
        # is on its own row above the tabs so a wide field here does
        # NOT compete with the TOML combo or the form column. Tooltip
        # also carries the path so users can hover for the canonical
        # form regardless of the line edit's display state.
        lay.addWidget(self._dir_edit, 1)
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self._action_browse_working_dir)
        lay.addWidget(btn_browse)

        return bar

    def _build_toml_row(self) -> QWidget:
        """Top row inside the Run tab. Hosts the *.toml combo populated
        from the working dir + Load / Save / Save As buttons. Lives
        here (not in the global bar) because the Analysis tab does
        not consume TOMLs -- it works on .bin output. The combo state
        is still owned by MainWindow so all the open/save flows route
        through the existing actions and stay synchronised with the
        File menu."""
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)

        lay.addWidget(QLabel("TOML:"))
        self._toml_combo = QComboBox()
        self._toml_combo.setMinimumWidth(240)
        self._toml_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToContents)
        # `activated` (not `currentIndexChanged`) fires only on a real
        # user click, AND it fires even when the user re-clicks the
        # already-selected entry -- both are required so a freshly-
        # populated combo lets the user load the visible item without
        # first picking a different one.
        self._toml_combo.activated.connect(self._on_toml_combo_activated)
        lay.addWidget(self._toml_combo, 1)

        # Load... duplicates the File menu's Open... but stays reachable
        # from the row so the user does not need a menu trip for the
        # most common action. Save / Save As likewise mirror Ctrl+S /
        # Ctrl+Shift+S so the menu actions and the buttons stay
        # synchronised.
        btn_load = QPushButton("Load TOML...")
        btn_load.clicked.connect(self._action_open)
        btn_save = QPushButton("Save")
        btn_save.clicked.connect(lambda: self._action_save())
        btn_save_as = QPushButton("Save As...")
        btn_save_as.clicked.connect(lambda: self._action_save_as())
        lay.addWidget(btn_load)
        lay.addWidget(btn_save)
        lay.addWidget(btn_save_as)

        return row

    def _set_working_dir(self, path: Path | None) -> None:
        """Single point of truth for changing the shared working dir.
        Updates the top-bar field (truncated for display, full path
        in tooltip), rescans the TOML combo, and pushes the same dir
        into the Analysis tab so its file tree mirrors whatever the
        Form sees."""
        self._working_dir = Path(path) if path is not None else None
        text = str(self._working_dir) if self._working_dir else ""
        self._dir_edit.setText(text)
        # Cursor at the END so when the path is longer than the
        # display, the last component (the most informative tail) is
        # visible without scrolling.
        self._dir_edit.setCursorPosition(len(text))
        self._dir_edit.setToolTip(text)
        self._refresh_toml_combo()
        self._analysis.set_working_dir(self._working_dir)

    def _refresh_toml_combo(self) -> None:
        """Populate the TOML combo with the *.toml files under the
        current working dir, scanning all subdirectories. Snapshots
        inside `output/<ts>/` are listed too so the user can re-load
        them and re-run; the WIP-save mechanism keeps them safe from
        accidental overwrite. Subtrees in `_TOML_SCAN_SKIP_DIRS`
        (build / venv / VCS noise) are pruned. The currently-loaded
        form path stays selected (or auto-selected when present in
        the list); items outside the working dir show up as a
        `(external)` label so the user is not confused by an apparent
        absence.

        Always seeds a `-- pick a TOML to load --` placeholder at
        index 0 (data = None) so the visible selection never silently
        implies a loaded file. Without it, the combo would default to
        item 0 of the scan, falsely suggesting that TOML is active
        when the form is still empty."""
        self._toml_combo.blockSignals(True)
        try:
            self._toml_combo.clear()
            self._toml_combo.addItem("-- pick a TOML to load --", None)
            entries: list[tuple[str, Path]] = []
            if self._working_dir is not None and self._working_dir.is_dir():
                root = self._working_dir
                # Manual walk instead of rglob so we can prune entire
                # subtrees by directory name (rglob still enters them
                # before filtering -- expensive on huge build trees).
                seen: list[Path] = []
                stack: list[Path] = [root]
                while stack:
                    cur = stack.pop()
                    try:
                        children = list(cur.iterdir())
                    except OSError:
                        continue
                    for p in children:
                        try:
                            is_dir  = p.is_dir()
                            is_file = p.is_file()
                        except OSError:
                            continue
                        if is_dir:
                            if p.name in _TOML_SCAN_SKIP_DIRS:
                                continue
                            stack.append(p)
                        elif is_file and p.suffix.lower() == ".toml":
                            seen.append(p)
                seen.sort(key=lambda q: str(q.relative_to(root)).lower())
                for p in seen:
                    full_rel = str(p.relative_to(root)).replace("\\", "/")
                    # Compact display: keep just `<parent>/<file>` (or
                    # the bare filename when the TOML sits at the
                    # working-dir root). Deep paths like
                    # `output/<ts>/<ts>_input.toml` would otherwise
                    # blow the combo wide and need their own scroll;
                    # the full relative path stays one hover away via
                    # the tooltip set on the combo item below.
                    if p.parent == root:
                        label = p.name
                    else:
                        label = f"{p.parent.name}/{p.name}"
                    if _is_wip_toml(p):
                        label = f"{label}  (draft)"
                        full_rel = f"{full_rel}  (draft)"
                    entries.append((label, p, full_rel))
            current = self._form.current_path()
            # If the loaded form path is outside the working dir, append
            # it as an `(external)` entry so the combo still reflects
            # the active file.
            if current is not None and all(p != current for _, p, _ in entries):
                entries.append((f"{current.name}  (external)",
                                 current, str(current)))
            for label, p, tooltip in entries:
                self._toml_combo.addItem(label, str(p))
                self._toml_combo.setItemData(
                    self._toml_combo.count() - 1, tooltip,
                    Qt.ItemDataRole.ToolTipRole)
            # Match the active file when it appears in the list;
            # otherwise leave the placeholder visible.
            if current is not None:
                for i in range(self._toml_combo.count()):
                    if self._toml_combo.itemData(i) == str(current):
                        self._toml_combo.setCurrentIndex(i)
                        break
        finally:
            self._toml_combo.blockSignals(False)

    def _on_toml_combo_activated(self, idx: int) -> None:
        """User clicked a combo entry. Loads the picked TOML through
        the same gate File > Open uses (unsaved-edits prompt etc).
        Index 0 is the `-- pick a TOML to load --` placeholder; data
        is None there so we no-op. Re-clicking the already-loaded
        entry is also a no-op (no point re-reading the same file)."""
        if idx < 0:
            return
        data = self._toml_combo.itemData(idx)
        if not data:
            return
        target = Path(data)
        current = self._form.current_path()
        if current is not None and target == current:
            return
        if not self._maybe_save():
            # User cancelled -- snap the combo back to whatever the
            # form actually has loaded so the UI stays consistent.
            self._refresh_toml_combo()
            return
        self._open_path(target)

    def _action_browse_working_dir(self) -> None:
        start = (str(self._working_dir)
                 if self._working_dir is not None else "")
        path = QFileDialog.getExistingDirectory(
            self, "Pick working directory", start)
        if not path:
            return
        self._set_working_dir(Path(path))

    # ------------------------------------------------------------------
    # Menus
    # ------------------------------------------------------------------
    def _build_menus(self) -> None:
        mb = self.menuBar()

        # File ---------------------------------------------------------
        m_file = mb.addMenu("&File")
        m_file.addAction(self._make_action("&New",        self._action_new,    QKeySequence.StandardKey.New))
        m_file.addAction(self._make_action("&Open...",    self._action_open,   QKeySequence.StandardKey.Open))
        self._recent_menu = m_file.addMenu("Open &Recent")
        m_file.addSeparator()
        m_file.addAction(self._make_action("&Save",       self._action_save,   QKeySequence.StandardKey.Save))
        m_file.addAction(self._make_action("Save &As...", self._action_save_as, QKeySequence.StandardKey.SaveAs))
        m_file.addSeparator()
        m_file.addAction(self._make_action("&Quit",       self.close,          QKeySequence.StandardKey.Quit))

        # Run ----------------------------------------------------------
        m_run = mb.addMenu("&Run")
        self._a_validate  = self._make_action("&Validate",  lambda: self._action_run("validate"),  QKeySequence("Ctrl+T"))
        self._a_propagate = self._make_action("&Propagate", lambda: self._action_run("propagate"), QKeySequence("Ctrl+R"))
        self._a_batch     = self._make_action("&Batch",     lambda: self._action_run("batch"),     QKeySequence("Ctrl+B"))
        self._a_stop      = self._make_action("S&top",      self._action_stop,                     QKeySequence("Ctrl+."))
        self._a_stop.setEnabled(False)
        m_run.addAction(self._a_validate)
        m_run.addAction(self._a_propagate)
        m_run.addAction(self._a_batch)
        m_run.addSeparator()
        m_run.addAction(self._a_stop)

        # Settings -----------------------------------------------------
        m_set = mb.addMenu("&Settings")
        m_set.addAction(self._make_action("&Paths...",       self._action_settings))
        m_set.addAction(self._make_action("Setup &wizard...", self._action_setup_wizard))

        # Help ---------------------------------------------------------
        # About moved here from Settings -- conventional place for it
        # and gives the user-manual entry a natural home.
        m_help = mb.addMenu("&Help")
        m_help.addAction(self._make_action("&User manual",  self._action_user_manual))
        m_help.addSeparator()
        m_help.addAction(self._make_action("&About",         self._action_about))

    def _make_action(self, text: str, slot, shortcut: QKeySequence | None = None) -> QAction:
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut is not None:
            a.setShortcut(shortcut)
        return a

    def _refresh_recent_menu(self) -> None:
        self._recent_menu.clear()
        recents = self._store.recent_files()
        if not recents:
            empty = QAction("(empty)", self)
            empty.setEnabled(False)
            self._recent_menu.addAction(empty)
            return
        for p in recents:
            a = QAction(p, self)
            a.triggered.connect(lambda _checked=False, path=p: self._open_path(Path(path)))
            self._recent_menu.addAction(a)
        self._recent_menu.addSeparator()
        clear = QAction("Clear list", self)
        clear.triggered.connect(lambda: (self._store.clear_recent_files(), self._refresh_recent_menu()))
        self._recent_menu.addAction(clear)

    # ------------------------------------------------------------------
    # File actions
    # ------------------------------------------------------------------
    def _action_new(self) -> None:
        if not self._maybe_save():
            return
        self._form.reset_to_blank()
        self._refresh_title()

    def _action_open(self) -> None:
        if not self._maybe_save():
            return
        current = self._form.current_path()
        start = str(current.parent) if current else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open TOML", start, "TOML files (*.toml);;All files (*)"
        )
        if path:
            self._open_path(Path(path))

    def _open_path(self, path: Path) -> None:
        if not self._form.load_path(path):
            return   # form already showed a message box on failure
        self._on_form_loaded_or_saved(path)

    def _action_save(self) -> bool:
        current = self._form.current_path()
        if current is None:
            return self._action_save_as()
        # WIP TOMLs (*.wip.toml) are always overwritable -- they ARE
        # the editing target.
        if _is_wip_toml(current):
            return self._save_to(current)
        # If the current TOML has output bins next to it (snapshot, or
        # source TOML with runs in the same dir), saving would clobber
        # something the user is depending on. Divert to a `.wip.toml`
        # sidecar; the first divert pops a one-time info dialog so the
        # user understands what just happened, subsequent saves are
        # silent (the WIP exists now and we route straight to it).
        if _toml_is_runnata(current):
            wip = _wip_path_for(current)
            first_divert = not wip.is_file()
            if first_divert:
                QMessageBox.information(
                    self, "Save -> draft",
                    f"'{current.name}' has output bins next to it -- it's "
                    "either a snapshot or a source with associated runs. "
                    "Overwriting it would invalidate those outputs.\n\n"
                    f"Saving as draft:\n  {wip.name}\n\n"
                    "Subsequent Save clicks on this draft will overwrite "
                    "it silently. Use Save As if you want a different path.")
            return self._save_to(wip)
        return self._save_to(current)

    def _action_save_as(self) -> bool:
        current = self._form.current_path()
        start = str(current) if current else ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save TOML", start, "TOML files (*.toml);;All files (*)"
        )
        if not path:
            return False
        return self._save_to(Path(path))

    def _save_to(self, path: Path) -> bool:
        if not self._form.write_to(path):
            return False
        self._on_form_loaded_or_saved(path)
        return True

    def _on_form_loaded_or_saved(self, path: Path) -> None:
        """Shared post-IO sync: update Recent list, window title, the
        top-bar working-dir (when it needs to change), and the
        Sun-arrow epoch hint. Called by Open and Save.

        Working-dir rule:
          * If the path is already INSIDE the current working dir,
            leave it alone -- the user's broader scope (e.g. browsing
            to `examples/`) must not silently shrink to a sub-folder
            just because they opened one scenario from it.
          * Otherwise, auto-adopt via `_project_root_for_toml`:
            walking up the path to the closest ancestor that has
            both `output/` and a TOML keeps the working dir at the
            scenario root even when the user opens a deep snapshot
            inside `output/<ts>/`.
        Combo is refreshed in either branch so newly-arrived files
        surface immediately."""
        self._store.add_recent_file(str(path), RECENT_FILES_MAX)
        self._refresh_recent_menu()
        self._refresh_title()
        if (self._working_dir is None
                or not _path_is_under(path, self._working_dir)):
            target = _project_root_for_toml(path)
            self._set_working_dir(target)
        else:
            self._refresh_toml_combo()
        # Pre-fill the Sun-arrow epoch in the Analysis tab from the
        # loaded form, so the user does not have to retype the number.
        data = self._form.to_dict()
        et = data.get("simulation", {}).get("et_start_s")
        if isinstance(et, (int, float)):
            self._analysis.set_default_epoch(float(et))

    def _maybe_save(self) -> bool:
        """Prompt to save if the form has unsaved edits. Returns False
        if the user cancels (the caller should abort whatever it was
        about to do)."""
        if not self._form.is_modified():
            return True
        resp = QMessageBox.question(
            self, "Unsaved changes",
            "The form has unsaved edits. Generate the TOML before continuing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if resp == QMessageBox.StandardButton.Save:
            return self._action_save()
        return resp == QMessageBox.StandardButton.Discard

    # ------------------------------------------------------------------
    # Run actions
    # ------------------------------------------------------------------
    def _action_run(self, subcommand: str) -> None:
        spody_bin = self._store.spody_binary()
        if not spody_bin or not Path(spody_bin).exists():
            QMessageBox.warning(
                self, "spody binary not set",
                "Set the path to spody.exe in Settings > Paths first."
            )
            return
        # Hard guard: never launch spody if any required data file is
        # missing. The runner would crash with a cryptic error; better
        # to surface this here and offer the wizard one click away.
        if not self._require_data_ready("Cannot run"):
            return
        # spody.exe needs a file on disk plus a working directory; if
        # the form is dirty or has never been saved, generate first.
        if self._form.current_path() is None or self._form.is_modified():
            if not self._maybe_save():
                return
        current = self._form.current_path()
        if current is None:
            return  # user cancelled the save prompt
        self._terminal.clear()
        self._terminal.append_line(
            f"$ {Path(spody_bin).name} {subcommand} {current.name}"
        )
        # CWD = scenario root, not the TOML's literal parent. Matters
        # for snapshots / WIPs deep inside `output/<ts>/`: running them
        # from their literal parent would resolve `output_dir = "output"`
        # into another nested `output/<new-ts>/`, blowing the path
        # length every iteration. The scenario root is the same place
        # the original source TOML would launch from.
        run_cwd = _project_root_for_toml(current)
        self._runner.run(spody_bin, subcommand, current, cwd=run_cwd)

    def _action_stop(self) -> None:
        self._runner.stop()

    def _action_calibrate(self, ref_path: str, window_h: float) -> None:
        """Launch `spody calibrate <toml> <ref> --window <h>` through
        the shared runner: same save-before-run gating as
        `_action_run`, report streamed line-by-line into the Run-tab
        console, Stop button live. The Calibrate... button flips to a
        disabled 'Calibrating...' for the duration; on success the
        engine's `nodes :` line (captured by `_on_calibrate_line`)
        auto-fills the form's density_scale_file row."""
        if self._runner.is_running():
            QMessageBox.warning(self, "Calibrate",
                "A spody process is already running; stop it first.")
            return
        spody_bin = self._store.spody_binary()
        if not spody_bin or not Path(spody_bin).exists():
            QMessageBox.warning(
                self, "spody binary not set",
                "Set the path to spody.exe in Settings > Paths first."
            )
            return
        if not self._require_data_ready("Cannot calibrate"):
            return
        if self._form.current_path() is None or self._form.is_modified():
            if not self._maybe_save():
                return
        current = self._form.current_path()
        if current is None:
            return  # user cancelled the save prompt
        self._terminal.clear()
        self._terminal.append_line(
            f"$ {Path(spody_bin).name} calibrate {current.name} "
            f"{Path(ref_path).name} --window {window_h:g}"
        )
        run_cwd = _project_root_for_toml(current)
        self._calibrate_active = True
        self._calibrate_nodes_path = None
        self._calibrate_run_cwd = run_cwd
        self._form.set_calibrate_busy(True)
        self._runner.run(spody_bin, "calibrate", current, cwd=run_cwd,
                         extra_args=[ref_path, "--window", f"{window_h:g}"])

    def _on_calibrate_line(self, line: str) -> None:
        """Capture the `  nodes      : <path>  (N nodes)` report row
        of a running calibration; ignored for every other subcommand
        (the flag is only set by `_action_calibrate`)."""
        if not self._calibrate_active:
            return
        stripped = line.strip()
        if not stripped.startswith("nodes"):
            return
        _, _, value = stripped.partition(":")
        # Drop the trailing "  (N nodes)" annotation; the path itself
        # contains no double-space + parenthesis sequence (run folders
        # are UTC timestamps).
        self._calibrate_nodes_path = value.split("  (")[0].strip()

    def _on_rerun_requested(self, toml_path: Path) -> None:
        """RerunPanel finalised a subset and wrote a new input.toml.
        Load it into the form (so the user sees what's about to run),
        switch to the Run tab, and kick off `spody batch`. Any failure
        leaves the user on the Re-run tab with a message; nothing
        partial gets launched."""
        if self._runner.is_running():
            QMessageBox.warning(self, "Re-run",
                "A spody process is already running; stop it first.")
            return
        if not self._maybe_save():
            return  # current form had unsaved edits and user cancelled
        if not self._form.load_path(toml_path):
            return  # form already showed a message box
        self._on_form_loaded_or_saved(toml_path)
        self._tabs.setCurrentIndex(0)  # Run tab
        self._action_run("batch")

    def _on_run_started(self) -> None:
        self._a_validate.setEnabled(False)
        self._a_propagate.setEnabled(False)
        self._a_batch.setEnabled(False)
        self._a_stop.setEnabled(True)
        self._form.set_running(True)
        self._status_timer.start()
        self._refresh_run_status()

    def _on_run_finished(self, exit_code: int) -> None:
        self._status_timer.stop()
        self._a_validate.setEnabled(True)
        self._a_propagate.setEnabled(True)
        self._a_batch.setEnabled(True)
        self._a_stop.setEnabled(False)
        self._form.set_running(False)
        elapsed = self._runner.elapsed_seconds()
        verdict = "OK" if exit_code == 0 else f"exit {exit_code}"
        self._status_run.setText(f"{verdict} ({elapsed:.1f}s)")
        self._terminal.append_line(f"[{verdict} in {elapsed:.1f}s]")
        # On a successful run, stamp the engine's final status line
        # into the notes block of the per-run input.toml snapshot so
        # a user reopening the snapshot later sees how the run went
        # alongside whatever they wrote before launching it. The
        # snapshot path is resolved once here: the WIP branch below
        # reuses it as its reload target.
        snapshot = self._latest_run_snapshot() if exit_code == 0 else None
        if snapshot is not None:
            self._stamp_run_notes(self._runner.last_line(), snapshot)

        # WIP cleanup + snapshot reload. Only the WIP branch gets an
        # auto-reload: a normal-source run leaves the form pointed at
        # the source the user just edited (which is still on disk and
        # current), so reloading would just be busywork. A WIP run
        # instead deletes its draft, so the form is repointed at the
        # per-run snapshot the engine just copied into `output/<ts>/`
        # -- the only surviving on-disk record of what actually ran.
        ran_path = self._form.current_path()
        if exit_code == 0 and ran_path is not None and _is_wip_toml(ran_path):
            # The WIP filename is `<origin_stem>.wip.toml`; the origin
            # is the source it was diverted from (fallback target when
            # the snapshot can't be located).
            origin = ran_path.with_name(
                ran_path.stem.removesuffix(".wip") + ".toml")
            # Detach the form FIRST so any open handle the editor was
            # holding is released before we try to delete. On Windows
            # an open handle blocks unlink with PermissionError; the
            # detach is cheap and avoids the race.
            self._form.set_current_path(None)
            self._form.clear_modified()
            self._refresh_title()
            # Now unlink the WIP. The run just snapshotted its content
            # into the new run folder, so the on-disk draft is no
            # longer needed. Surface failures into the terminal so a
            # silent file-lock doesn't get hidden.
            try:
                ran_path.unlink()
            except OSError as exc:
                self._terminal.append_line(
                    f"[WIP cleanup: could not unlink {ran_path.name}: {exc}]")
            except FileNotFoundError:
                pass
            # Load the run snapshot (fall back to the origin file when
            # the snapshot can't be found). _open_path drives
            # _on_form_loaded_or_saved which refreshes the working
            # dir + combo + analysis -- so we can return early.
            target = (snapshot if snapshot is not None and snapshot.is_file()
                      else origin)
            if target.is_file():
                self._open_path(target)
                # A calibrate launched from a WIP still needs its
                # completion pass (busy reset + node-file auto-fill on
                # the freshly reloaded snapshot) before this early exit.
                self._finish_calibrate(exit_code)
                return

        # Refresh analysis + combo so the new run's outputs (the
        # `output/<new-ts>/` folder + its `<new-ts>_input.toml`
        # snapshot) appear immediately. Working dir is only retargeted
        # when the form's TOML drifted OUTSIDE the current scope
        # (rare: after a Re-run or when the source moved); inside the
        # current scope we just rescan -- a run by itself must not
        # narrow the working dir down to the scenario folder when the
        # user had picked a wider one (e.g. `examples/`).
        current = self._form.current_path()
        if current is not None:
            if (self._working_dir is None
                    or not _path_is_under(current, self._working_dir)):
                target = _project_root_for_toml(current)
                self._set_working_dir(target)
            else:
                self._analysis.set_working_dir(self._working_dir)
                self._refresh_toml_combo()
        else:
            self._refresh_toml_combo()
        self._finish_calibrate(exit_code)

    def _finish_calibrate(self, exit_code: int) -> None:
        """Completion pass of a `spody calibrate` run: re-enable the
        form's Calibrate... button and, on success, point the form's
        density_scale_file row at the emitted k_nodes.csv (relative to
        the TOML when possible, so the scenario stays relocatable).
        No-op unless `_action_calibrate` armed the flag; idempotent,
        so it is safe to call from both exit paths of
        `_on_run_finished` and from `_on_run_error`."""
        if not self._calibrate_active:
            return
        self._calibrate_active = False
        self._form.set_calibrate_busy(False)
        nodes   = self._calibrate_nodes_path
        run_cwd = self._calibrate_run_cwd
        self._calibrate_nodes_path = None
        self._calibrate_run_cwd = None
        if exit_code != 0:
            return
        if not nodes or run_cwd is None:
            self._terminal.append_line(
                "[calibrate: no nodes line found in the report]")
            return
        abs_nodes = (run_cwd / nodes).resolve()
        if not abs_nodes.is_file():
            self._terminal.append_line(
                f"[calibrate: nodes file not found at {abs_nodes}]")
            return
        current = self._form.current_path()
        target = str(abs_nodes)
        if current is not None:
            try:
                target = os.path.relpath(abs_nodes, current.parent)
            except ValueError:
                pass  # different drive on Windows -> keep absolute
        self._form.set_density_scale_file(target)
        self._terminal.append_line(
            f"[calibrate: density_scale_file set to {target} -- "
            f"save the TOML to keep it]")

    def _latest_run_snapshot(self) -> Path | None:
        """Path of the per-run `input.toml` snapshot of the most
        recent run under the form's configured `output_dir`, or None
        when it can't be located (missing output_dir, no run folders,
        unparseable form, ...).

        The snapshot lives at `<output_dir>/<UTC-ISO8601>/`; we take
        the most recently-modified subdir of `output_dir` so we don't
        have to parse engine stdout for the folder name."""
        try:
            data = self._form.to_dict()
        except ValueError:
            return None
        out_dir_raw = (data.get("output", {}) or {}).get("output_dir", "")
        if not isinstance(out_dir_raw, str) or not out_dir_raw:
            return None
        # Resolve relative to the source TOML's directory, matching
        # what spody.exe does when reading the same key.
        out_dir = Path(out_dir_raw)
        current = self._form.current_path()
        if not out_dir.is_absolute() and current is not None:
            out_dir = (current.parent / out_dir).resolve()
        if not out_dir.is_dir():
            return None
        # Latest UTC-ISO8601 subfolder by mtime == the run we just
        # finished. Using mtime over name-parsing keeps us robust to
        # whatever timestamp pattern spody happens to use today.
        try:
            subdirs = [p for p in out_dir.iterdir() if p.is_dir()]
        except OSError:
            return None
        if not subdirs:
            return None
        snapshot_dir = max(subdirs, key=lambda p: p.stat().st_mtime)
        # Modern snapshots are named `<ts>_input.toml`; legacy ones
        # are plain `input.toml`. Try modern first, then fall back.
        snapshot = snapshot_dir / f"{snapshot_dir.name}_input.toml"
        if not snapshot.is_file():
            snapshot = snapshot_dir / "input.toml"
        if not snapshot.is_file():
            return None
        return snapshot

    def _stamp_run_notes(self, last_engine_line: str,
                         snapshot: Path) -> None:
        """Append the engine's final stdout line to the notes block
        of the per-run `input.toml` snapshot.

        Modifies only the snapshot, NOT the source TOML the user is
        editing -- the source must stay re-usable across many runs
        without accumulating timing tails.

        Silent on every failure mode (unreadable snapshot, ...): the
        goal is a nice-to-have stamp, not a guaranteed contract. The
        run already succeeded; we are not going to flag a 'failed'
        status on a cosmetic post-step.
        """
        if not last_engine_line.strip():
            return
        from .toml_io import read_toml, write_toml
        try:
            snap_data = read_toml(snapshot)
        except Exception:  # noqa: BLE001 -- best-effort, swallow
            return
        existing = snap_data.get("notes", "")
        if not isinstance(existing, str):
            existing = ""
        # Append the engine line as a new paragraph. A blank line
        # between the user's notes and the stamped line keeps the
        # rendering clean when the snapshot is opened in any editor.
        stamped = (existing.rstrip() + "\n\n" + last_engine_line.strip()
                   if existing.strip()
                   else last_engine_line.strip())
        snap_data["notes"] = stamped
        try:
            write_toml(snapshot, snap_data)
        except Exception:  # noqa: BLE001
            return

    def _on_run_error(self, message: str) -> None:
        self._terminal.append_line(f"[runner error: {message}]")
        # A calibrate that failed to launch never reaches finished;
        # clear the button's busy state here (idempotent when the
        # finished signal does follow). The RUN/Stop pair resyncs to
        # the actual runner state: error also fires for non-fatal
        # conditions while a process is still in flight.
        self._finish_calibrate(-1)
        self._form.set_running(self._runner.is_running())

    def _refresh_run_status(self) -> None:
        if self._runner.is_running():
            self._status_run.setText(f"running {self._runner.elapsed_seconds():.0f}s")

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------
    def _action_settings(self) -> None:
        dlg = SettingsDialog(self._store, self)
        dlg.exec()

    def _action_setup_wizard(self) -> None:
        """User-triggered open: same dialog whether anything is missing
        or not (the user might want to add extra chunks / re-download)."""
        self._open_setup_wizard()

    def _open_setup_wizard(self) -> SetupWizard:
        dlg = SetupWizard(self._store, self)
        dlg.exec()
        # If the wizard changed anything on disk, give the Analysis tab
        # a chance to pick up newly-arrived files. The Run tab doesn't
        # need a refresh -- the run-guard re-checks on each launch.
        if dlg.was_changed():
            # Asset combos in the Run form (ephemeris / harmonics
            # dropdowns) read directly from the data dir on construction
            # -- nudge them so a freshly-downloaded file shows up
            # without restarting the GUI.
            self._form.refresh_asset_combos()
            current = self._form.current_path()
            if current is not None:
                # Same lockstep policy as _on_run_finished: only
                # retarget the working dir when the form's TOML
                # lives outside the current scope.
                if (self._working_dir is None
                        or not _path_is_under(current, self._working_dir)):
                    target = _project_root_for_toml(current)
                    self._set_working_dir(target)
                else:
                    self._analysis.set_working_dir(self._working_dir)
        return dlg

    def _maybe_pop_setup_wizard(self) -> None:
        """Called once on first show. Opens the wizard only when the
        data root is missing files we need; silent otherwise so re-use
        of the GUI is undisturbed."""
        root = self._store.data_dir()
        if assets.all_required_present(root):
            return
        # Friendly heads-up so the wizard does not appear unexplained.
        QMessageBox.information(
            self, "Setup needed",
            "Some required data files are missing.\n\n"
            f"Data dir: {root}\n\n"
            "Opening the Setup wizard.")
        self._open_setup_wizard()

    def _maybe_warn_eop_stale(self) -> None:
        """One-shot startup gate over the daily-updated remote tables:
        the IERS EOP file and the CelesTrak space weather CSV. For each
        one that is already on disk, HEAD its canonical URL and compare
        the server's Last-Modified with the local mtime; if the server
        has a fresher version (IERS pushes Bulletin A weekly, CelesTrak
        regenerates daily), offer a one-click re-download.

        The URLs come from the `assets` descriptors (the same field the
        wizard exposes for editing) -- never hard-coded here. Each probe
        is skipped silently when:
          - the file isn't downloaded yet (the wizard-pop covers that),
          - the HEAD request fails (offline, firewall, server outage),
          - the server is not newer than our local copy.

        Note: this REPLACES an earlier check that fired on the EOP
        file's `mjd_last_observed` age, which was misleading because
        Bulletin B always lags ~30 days behind real time regardless of
        how fresh the file actually is -- the dialog popped after every
        download.
        """
        for asset in (assets.EOP_FILE, assets.SPACE_WEATHER_FILE):
            self._warn_if_asset_stale(asset)

    def _warn_if_asset_stale(self, asset) -> None:
        root = self._store.data_dir()
        path = root / asset.relpath
        if not path.is_file():
            return

        url = asset.url
        try:
            import urllib.request
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=5) as resp:
                server_lm_str = resp.headers.get("Last-Modified")
                server_size_str = resp.headers.get("Content-Length")
        except Exception:
            # Offline / DNS failure / firewall: silently skip, the user
            # can still re-download manually from the wizard.
            return
        if not server_lm_str:
            return

        from datetime import datetime, timezone
        from email.utils import parsedate_to_datetime
        try:
            server_lm = parsedate_to_datetime(server_lm_str)
        except (TypeError, ValueError):
            return
        if server_lm.tzinfo is None:
            server_lm = server_lm.replace(tzinfo=timezone.utc)

        local_mtime = datetime.fromtimestamp(
            path.stat().st_mtime, tz=timezone.utc)
        local_size = path.stat().st_size
        try:
            server_size = int(server_size_str) if server_size_str else None
        except ValueError:
            server_size = None

        # Up-to-date iff the server has not modified the file since we
        # downloaded AND the byte count still matches. The size check
        # is belt-and-suspenders: both tables are effectively
        # append-only, so any new daily record changes the length.
        if server_lm <= local_mtime and (
                server_size is None or server_size == local_size):
            return

        choice = QMessageBox.question(
            self, f"{asset.name} update available",
            f"A newer {path.name} is available on the server.\n\n"
            f"  server : {server_lm.strftime('%Y-%m-%d %H:%M UTC')}"
            f"  ({server_size or '?'} B)\n"
            f"  local  : {local_mtime.strftime('%Y-%m-%d %H:%M UTC')}"
            f"  ({local_size} B)\n\n"
            f"Source: {url}\n\n"
            "Download the latest now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return
        # Open the wizard AND immediately trigger the asset row's
        # download. The wizard remains modal; the download progresses
        # in its row's progress bar while the user watches.
        dlg = SetupWizard(self._store, self)
        row = dlg._rows.get(asset.relpath)
        if row is not None:
            row.start_download()
        dlg.exec()
        if dlg.was_changed():
            self._form.refresh_asset_combos()

    def _require_data_ready(self, action_label: str) -> bool:
        """Thin wrapper around the shared `require_data_ready` helper
        so both menu/run paths and the form's Validate button use the
        same dialog text."""
        return require_data_ready(self._store, self, action_label)

    def _action_about(self) -> None:
        show_about(self._store, self)

    def _action_user_manual(self) -> None:
        """Locate spody-user-manual.pdf and hand it off to the OS
        default PDF viewer via QDesktopServices. The bundle puts it
        next to spody-gui.exe under docs/; in a dev checkout it lives
        at <repo>/docs/user-manual/spody-user-manual.pdf."""
        pdf = self._locate_user_manual()
        if pdf is None:
            QMessageBox.warning(
                self, "User manual not found",
                "Could not locate spody-user-manual.pdf.\n\n"
                "In a development checkout, rebuild it by running\n"
                "    python docs/user-manual/build_pdf.py\n"
                "from the repository root."
            )
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf)))

    @staticmethod
    def _locate_user_manual() -> Path | None:
        """Return the first existing manual path among the bundle and
        dev-checkout candidates, or None when neither is present.

        Bundle layout note: PyInstaller's one-folder mode (v6+) places
        every `datas` entry under `_internal/` -- so the PDF declared
        in spody_gui.spec as `(.., 'docs')` actually lands at
        `<exe>/_internal/docs/spody-user-manual.pdf`, NOT at
        `<exe>/docs/...`. We probe both for forward compatibility in
        case a later spec setting (contents_directory='.') moves it."""
        candidates = []
        if getattr(sys, "frozen", False):
            exe_dir = Path(sys.executable).parent
            # PyInstaller 6.x one-folder default:
            candidates.append(exe_dir / "_internal" / "docs"
                              / "spody-user-manual.pdf")
            # Fallback for contents_directory='.' / older layouts:
            candidates.append(exe_dir / "docs" / "spody-user-manual.pdf")
        # Dev checkout: spody_gui/main_window.py -> spody_gui/ ->
        # python/ -> <repo>/  ->  docs/user-manual/...
        candidates.append(
            Path(__file__).resolve().parents[2]
            / "docs" / "user-manual" / "spody-user-manual.pdf"
        )
        for p in candidates:
            if p.is_file():
                return p
        return None

    # ------------------------------------------------------------------
    # Title + close
    # ------------------------------------------------------------------
    def _refresh_title(self) -> None:
        current = self._form.current_path()
        label = str(current) if current else "(unsaved)"
        dirty = "*" if self._form.is_modified() else ""
        self.setWindowTitle(f"SpOdy -- {label}{dirty}")
        self._status_path.setText(label + dirty)

    def closeEvent(self, event) -> None:  # noqa: N802 -- Qt naming
        if self._runner.is_running():
            resp = QMessageBox.question(
                self, "spody is running",
                "A spody run is in progress. Stop it and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._runner.stop()
        if not self._maybe_save():
            event.ignore()
            return
        event.accept()
