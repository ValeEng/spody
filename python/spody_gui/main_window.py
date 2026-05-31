"""Main application window: TOML editor, terminal pane, menus, status bar."""
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

from . import schema
from .analysis_panel import AnalysisPanel
from .editor import TomlEditor
from .runner import SpodyRunner
from .settings import SettingsDialog, SettingsStore
from .terminal import TerminalView

# How many entries to keep in the File > Recent menu.
RECENT_FILES_MAX = 8


class MainWindow(QMainWindow):
    """Single-window UI: TOML editor on the left, terminal output on the right."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SpOdy")
        self.resize(1280, 800)

        self._store = SettingsStore()
        self._current_path: Path | None = None

        # Central layout: top-level mode switch between Run (editor +
        # terminal) and Analysis (file picker + plots). The two modes
        # are completely independent widgets; the menu bar stays shared
        # but Run-only actions are no-ops while the Analysis tab is up.
        self._editor = TomlEditor()
        self._terminal = TerminalView()
        run_splitter = QSplitter(Qt.Orientation.Horizontal)
        run_splitter.addWidget(self._editor)
        run_splitter.addWidget(self._terminal)
        run_splitter.setStretchFactor(0, 1)
        run_splitter.setStretchFactor(1, 1)
        run_splitter.setSizes([640, 640])

        self._analysis = AnalysisPanel()

        self._tabs = QTabWidget()
        self._tabs.addTab(run_splitter,    "Run")
        self._tabs.addTab(self._analysis,  "Analysis")
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

        self._editor.modificationChanged.connect(self._refresh_title)

        self._build_menus()
        self._refresh_title()
        self._refresh_recent_menu()

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

        # Insert -------------------------------------------------------
        # One menu item per snippet template. Inserting also serves as a
        # discoverable list of the available top-level sections.
        m_ins = mb.addMenu("&Insert")
        for name in schema.SNIPPETS.keys():
            a = QAction(f"[{name}] template", self)
            a.triggered.connect(lambda _checked=False, n=name: self._editor.insert_snippet(n))
            m_ins.addAction(a)

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
        m_set.addAction(self._make_action("&Paths...", self._action_settings))
        m_set.addSeparator()
        m_set.addAction(self._make_action("&About",    self._action_about))

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
        self._editor.set_text("")
        self._current_path = None
        self._editor.document().setModified(False)
        self._refresh_title()

    def _action_open(self) -> None:
        if not self._maybe_save():
            return
        start = str(self._current_path.parent) if self._current_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open TOML", start, "TOML files (*.toml);;All files (*)"
        )
        if path:
            self._open_path(Path(path))

    def _open_path(self, path: Path) -> None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Open failed", str(exc))
            return
        self._editor.set_text(text)
        self._current_path = path
        self._editor.document().setModified(False)
        self._store.add_recent_file(str(path), RECENT_FILES_MAX)
        self._refresh_recent_menu()
        self._refresh_title()
        # The TOML's directory is the canonical "working dir" for outputs
        # (spody resolves relative paths there); seed the Analysis tab so
        # switching to it immediately shows any binaries already present.
        self._analysis.set_working_dir(path.parent)

    def _action_save(self) -> bool:
        if self._current_path is None:
            return self._action_save_as()
        return self._save_to(self._current_path)

    def _action_save_as(self) -> bool:
        start = str(self._current_path) if self._current_path else ""
        path, _ = QFileDialog.getSaveFileName(
            self, "Save TOML", start, "TOML files (*.toml);;All files (*)"
        )
        if not path:
            return False
        return self._save_to(Path(path))

    def _save_to(self, path: Path) -> bool:
        try:
            path.write_text(self._editor.text(), encoding="utf-8")
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))
            return False
        self._current_path = path
        self._editor.document().setModified(False)
        self._store.add_recent_file(str(path), RECENT_FILES_MAX)
        self._refresh_recent_menu()
        self._refresh_title()
        self._analysis.set_working_dir(path.parent)
        return True

    def _maybe_save(self) -> bool:
        """Prompt to save if buffer is dirty. Returns False if user cancels."""
        if not self._editor.document().isModified():
            return True
        resp = QMessageBox.question(
            self, "Unsaved changes",
            "The current TOML has unsaved changes. Save before continuing?",
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
        # Save-before-run: spody.exe reads from disk and resolves paths
        # relative to the TOML file's directory, so we always need a real
        # file on disk and a meaningful working directory.
        if self._current_path is None or self._editor.document().isModified():
            if not self._maybe_save():
                return
        if self._current_path is None:
            return  # user cancelled the save prompt
        self._terminal.clear()
        self._terminal.append_line(
            f"$ {Path(spody_bin).name} {subcommand} {self._current_path.name}"
        )
        self._runner.run(spody_bin, subcommand, self._current_path)

    def _action_stop(self) -> None:
        self._runner.stop()

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
        # Refresh the Analysis tree so any new outputs from this run
        # appear without the user having to hit Refresh manually. The
        # working dir was already seeded on Open / Save.
        if self._current_path is not None:
            self._analysis.set_working_dir(self._current_path.parent)

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

    def _action_about(self) -> None:
        QMessageBox.about(
            self, "About SpOdy",
            "SpOdy GUI -- desktop frontend for the spody propagator.\n"
            "PySide6 (Qt for Python).\n"
            "Patran-style: edits TOML, runs the binary, displays output."
        )

    # ------------------------------------------------------------------
    # Title + close
    # ------------------------------------------------------------------
    def _refresh_title(self) -> None:
        if self._current_path is None:
            label = "(unsaved)"
        else:
            label = str(self._current_path)
        dirty = "*" if self._editor.document().isModified() else ""
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
