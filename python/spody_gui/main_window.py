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
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QTabWidget,
)

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


class MainWindow(QMainWindow):
    """Single-window UI. Run tab: structured TOML form on the left,
    terminal output on the right. Analysis tab: file browser + plots."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SpOdy")
        self.resize(1280, 800)

        self._store = SettingsStore()

        # Central layout: top-level mode switch between Run (form +
        # terminal) and Analysis (file picker + plots). The two modes
        # are completely independent widgets; the menu bar stays shared
        # but Run-only actions are no-ops while the Analysis tab is up.
        self._form = TomlForm(self._store)
        self._terminal = TerminalView()
        run_splitter = QSplitter(Qt.Orientation.Horizontal)
        run_splitter.addWidget(self._form)
        run_splitter.addWidget(self._terminal)
        run_splitter.setStretchFactor(0, 1)
        run_splitter.setStretchFactor(1, 1)
        run_splitter.setSizes([640, 640])

        self._analysis = AnalysisPanel(self._store)
        self._rerun    = RerunPanel(self._store)
        # The rerun tab generates a new input.toml + cases.csv subset
        # and asks us to launch `spody batch` on it; we forward to the
        # existing Run path so the user sees the same form/terminal UI.
        self._rerun.runRequested.connect(self._on_rerun_requested)

        self._tabs = QTabWidget()
        self._tabs.addTab(run_splitter,    "Run")
        self._tabs.addTab(self._analysis,  "Analysis")
        self._tabs.addTab(self._rerun,     "Re-run")
        self.setCentralWidget(self._tabs)

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
        # mark the window title dirty), when a Generate write succeeded
        # (so we refresh recents + Analysis working dir), and when the
        # RUN button is clicked (so we share the save-before-run flow
        # with the menu actions).
        self._form.modificationChanged.connect(self._refresh_title)
        self._form.requestRunCheck.connect(self._on_form_generated)
        self._form.runRequested.connect(self._action_run)

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

    def _on_form_generated(self) -> None:
        """The form's Generate TOML button finished writing. Sync the
        rest of the UI (recents, title, analysis dir, sun-arrow epoch)
        using the path the form now holds."""
        path = self._form.current_path()
        if path is not None:
            self._on_form_loaded_or_saved(path)

    def _on_form_loaded_or_saved(self, path: Path) -> None:
        """Shared post-IO sync: update Recent list, window title, the
        Analysis tab's working-dir + Sun-arrow epoch hint. Called by
        Open, Save, and the form's Generate button via requestRunCheck."""
        self._store.add_recent_file(str(path), RECENT_FILES_MAX)
        self._refresh_recent_menu()
        self._refresh_title()
        self._analysis.set_working_dir(path.parent)
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
        self._runner.run(spody_bin, subcommand, current)

    def _action_stop(self) -> None:
        self._runner.stop()

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
        self._status_timer.start()
        self._refresh_run_status()

    def _on_run_finished(self, exit_code: int) -> None:
        self._status_timer.stop()
        self._a_validate.setEnabled(True)
        self._a_propagate.setEnabled(True)
        self._a_batch.setEnabled(True)
        self._a_stop.setEnabled(False)
        elapsed = self._runner.elapsed_seconds()
        verdict = "OK" if exit_code == 0 else f"exit {exit_code}"
        self._status_run.setText(f"{verdict} ({elapsed:.1f}s)")
        self._terminal.append_line(f"[{verdict} in {elapsed:.1f}s]")
        # On a successful run, stamp the engine's final status line
        # into the notes block of the per-run input.toml snapshot so
        # a user reopening the snapshot later sees how the run went
        # alongside whatever they wrote before launching it.
        if exit_code == 0:
            self._stamp_run_notes(self._runner.last_line())
        # Refresh the Analysis tree so any new outputs from this run
        # appear without the user having to hit Refresh manually.
        current = self._form.current_path()
        if current is not None:
            self._analysis.set_working_dir(current.parent)

    def _stamp_run_notes(self, last_engine_line: str) -> None:
        """Append the engine's final stdout line to the notes block
        of the per-run `input.toml` snapshot.

        Modifies only the snapshot, NOT the source TOML the user is
        editing -- the source must stay re-usable across many runs
        without accumulating timing tails. The snapshot lives at
        `<output_dir>/<UTC-ISO8601>/input.toml`; we locate the most
        recently-modified subdir of the configured `output_dir` to
        find it without parsing engine stdout.

        Silent on every failure mode (missing output_dir, unreadable
        snapshot, ...): the goal is a nice-to-have stamp, not a
        guaranteed contract. The run already succeeded; we are not
        going to flag a 'failed' status on a cosmetic post-step.
        """
        if not last_engine_line.strip():
            return
        try:
            data = self._form.to_dict()
        except ValueError:
            return
        out_dir_raw = (data.get("output", {}) or {}).get("output_dir", "")
        if not isinstance(out_dir_raw, str) or not out_dir_raw:
            return
        # Resolve relative to the source TOML's directory, matching
        # what spody.exe does when reading the same key.
        out_dir = Path(out_dir_raw)
        current = self._form.current_path()
        if not out_dir.is_absolute() and current is not None:
            out_dir = (current.parent / out_dir).resolve()
        if not out_dir.is_dir():
            return
        # Latest UTC-ISO8601 subfolder by mtime == the run we just
        # finished. Using mtime over name-parsing keeps us robust to
        # whatever timestamp pattern spody happens to use today.
        try:
            subdirs = [p for p in out_dir.iterdir() if p.is_dir()]
        except OSError:
            return
        if not subdirs:
            return
        snapshot_dir = max(subdirs, key=lambda p: p.stat().st_mtime)
        snapshot = snapshot_dir / "input.toml"
        if not snapshot.is_file():
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
                self._analysis.set_working_dir(current.parent)
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
        """One-shot startup gate: HEAD the IERS finals2000A.all URL and
        compare the server's Last-Modified with our local file's mtime.
        If the server has a fresher version we missed (i.e. IERS pushed
        a Bulletin A update since the user last downloaded), offer a
        one-click re-download.

        The URL comes from `assets.EOP_FILE.url` (the same field the
        wizard exposes for editing) -- never hard-coded here. Skipped
        silently when:
          - the file isn't downloaded yet (the wizard-pop covers that),
          - the HEAD request fails (offline, firewall, IERS outage),
          - the server is not newer than our local copy.

        Note: this REPLACES an earlier check that fired on the file's
        `mjd_last_observed` age, which was misleading because Bulletin B
        always lags ~30 days behind real time regardless of how fresh
        the file actually is -- the dialog popped after every download.
        """
        root = self._store.data_dir()
        eop_path = root / "eop" / "finals2000A.all"
        if not eop_path.is_file():
            return

        url = assets.EOP_FILE.url
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
            eop_path.stat().st_mtime, tz=timezone.utc)
        local_size = eop_path.stat().st_size
        try:
            server_size = int(server_size_str) if server_size_str else None
        except ValueError:
            server_size = None

        # Up-to-date iff the server has not modified the file since we
        # downloaded AND the byte count still matches. The size check
        # is belt-and-suspenders: IERS finals2000A.all is append-only,
        # so any new daily record changes the length.
        if server_lm <= local_mtime and (
                server_size is None or server_size == local_size):
            return

        choice = QMessageBox.question(
            self, "IERS EOP update available",
            "A newer IERS finals2000A.all is available on the server.\n\n"
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
        # Open the wizard AND immediately trigger the EOP row's
        # download. The wizard remains modal; the download progresses
        # in its row's progress bar while the user watches.
        dlg = SetupWizard(self._store, self)
        row = dlg._rows.get("eop/finals2000A.all")
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
