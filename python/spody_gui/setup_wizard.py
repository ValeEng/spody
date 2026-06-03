"""First-launch / on-demand data setup dialog.

The dialog has a single pane (no QWizard step-flow) because the user
mostly wants to see *everything that's missing* at a glance, fix one
URL or two, hit Download, and then Convert. Step-by-step wizards
hide that state.

Layout (top -> bottom):

    Data dir:  <path>                          [Open folder]
    +-------------------------------------------------------+
    | Per-asset row (one card each):                        |
    |   status icon | name (size) | URL editor              |
    |   [progress bar]           [Download]                 |
    +-------------------------------------------------------+
    Conversion: DE440 ASCII -> de440.spody                  |
    [Run conversion]   status text                          |
    -------------------------------------------------------- |
                                          [Refresh] [Close]

Downloads use `QNetworkAccessManager` so they integrate cleanly with
the Qt event loop -- no extra thread, no extra dependency.

Conversion shells out to `spody.exe convert ephemeris <folder> <de>
<date1> [date2 ...]` (added in the same change set). If the spody
binary is not configured, the dialog tells the user to set it via
Settings > Paths first.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QFile,
    QIODevice,
    QProcess,
    QStandardPaths,
    QUrl,
    Qt,
    Signal,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import (
    QNetworkAccessManager,
    QNetworkReply,
    QNetworkRequest,
)
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import assets, paths
from .assets import Asset
from .settings import SettingsStore


def require_data_ready(store: SettingsStore, parent: QWidget,
                       action_label: str) -> bool:
    """Hard run-guard reused by every entry point that launches the
    spody binary. Returns True iff all required raw + derived data
    files are present in the data dir; otherwise pops a warning with
    the missing list and offers to open the Setup wizard one click
    away. Caller aborts on False."""
    root = store.data_dir()
    missing = assets.missing_required(root)
    if not missing:
        return True
    names = "\n  - ".join(a.name for a in missing)
    resp = QMessageBox.warning(
        parent, f"{action_label}: data not ready",
        f"Required files missing from {root}:\n  - {names}\n\n"
        "Open the Setup wizard now?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if resp == QMessageBox.StandardButton.Yes:
        dlg = SetupWizard(store, parent)
        dlg.exec()
    return False


class SetupWizard(QDialog):
    """Modal dialog that downloads + converts the required data files.

    Public surface:
        was_changed() -> bool   -- True if any file in the data dir
                                   was added / replaced this session.
    """

    # Emitted whenever a file lands on disk, so anyone holding a status
    # cache (the run-guard, for instance) can refresh.
    assets_changed = Signal()

    def __init__(self, store: SettingsStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("SpOdy -- Setup")
        self.setMinimumSize(820, 600)

        self._store = store
        self._nam   = QNetworkAccessManager(self)
        self._changed = False
        # One row widget per asset, keyed by relpath. Rows manage their
        # own URL edit + progress bar + Download button, and call back
        # into the wizard to start/cancel downloads through self._nam.
        self._rows: dict[str, _AssetRow] = {}
        # Track the active conversion process so we can cancel it cleanly.
        self._convert_proc: QProcess | None = None

        self._build_ui()
        self.refresh_status()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def was_changed(self) -> bool:
        return self._changed

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)

        # ---- header: data dir + open folder ----------------------------
        head = QHBoxLayout()
        self._data_dir_label = QLabel("")
        self._data_dir_label.setStyleSheet("font-family: Consolas, monospace;")
        self._data_dir_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        btn_open = QPushButton("Open folder")
        btn_open.clicked.connect(self._on_open_folder)
        btn_change = QPushButton("Change...")
        btn_change.clicked.connect(self._on_change_dir)
        head.addWidget(QLabel("Data dir:"))
        head.addWidget(self._data_dir_label, 1)
        head.addWidget(btn_open)
        head.addWidget(btn_change)
        outer.addLayout(head)

        intro = QLabel(
            "Spody needs an external planetary ephemeris (JPL DE440) and a "
            "lunar harmonic-gravity model (GRGM1200B). The wizard downloads "
            "the raw files into the data dir, then converts the DE440 ASCII "
            "chunks into spody's binary format (`de440.spody`).\n\n"
            "URLs below are editable: if a download fails, fix the URL and "
            "try again. The wizard does not store overrides -- once we know "
            "the right link we'll bake it into the next release."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color: gray;")
        outer.addWidget(intro)

        # ---- asset rows in a scroll area -------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_lay = QVBoxLayout(body)
        body_lay.setSpacing(4)
        for a in assets.ASSETS:
            row = _AssetRow(a, self._nam, self._store, self)
            row.downloaded.connect(self._on_asset_arrived)
            self._rows[a.relpath] = row
            body_lay.addWidget(row)
        body_lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # ---- conversion section ----------------------------------------
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        conv_lay = QHBoxLayout()
        self._btn_convert = QPushButton("Run conversion (DE440 ASCII -> de440.spody)")
        self._btn_convert.clicked.connect(self._on_convert)
        self._convert_status = QLabel("")
        self._convert_status.setStyleSheet("color: gray;")
        conv_lay.addWidget(self._btn_convert)
        conv_lay.addWidget(self._convert_status, 1)
        outer.addLayout(conv_lay)

        # ---- footer: refresh + close -----------------------------------
        foot = QHBoxLayout()
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_status)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        foot.addStretch(1)
        foot.addWidget(btn_refresh)
        foot.addWidget(btn_close)
        outer.addLayout(foot)

    # ------------------------------------------------------------------
    # Status refresh
    # ------------------------------------------------------------------
    def refresh_status(self) -> None:
        """Recompute the per-asset 'present?' badge from disk and update
        the conversion section. Called on open, after each download, and
        from the Refresh button."""
        root = self._store.data_dir()
        self._data_dir_label.setText(str(root))
        for relpath, row in self._rows.items():
            row.refresh(root)
        self._refresh_convert_status(root)

    def _refresh_convert_status(self, root: Path) -> None:
        """Enable the Run-conversion button only when the raw DE440 files
        are present, and tell the user what's missing otherwise."""
        out_file = root / "DE440" / "de440.spody"
        raw = [a for a in assets.ASSETS
               if a.kind == "raw" and a.relpath.startswith("DE440/")]
        missing = [a.name for a in raw if not assets.is_present(a, root)]
        if missing:
            self._btn_convert.setEnabled(False)
            self._convert_status.setText(
                "needs raw DE440 first: " + ", ".join(missing))
            return
        self._btn_convert.setEnabled(True)
        if out_file.is_file():
            mb = out_file.stat().st_size / (1024 * 1024)
            self._convert_status.setText(
                f"de440.spody present ({mb:.1f} MB) -- safe to re-run to overwrite")
        else:
            self._convert_status.setText("ready -- click to convert")

    # ------------------------------------------------------------------
    # Download callbacks
    # ------------------------------------------------------------------
    def _on_asset_arrived(self, relpath: str) -> None:
        """A row finished writing its file. Refresh the world and
        remember we changed something."""
        self._changed = True
        self.refresh_status()
        self.assets_changed.emit()

    # ------------------------------------------------------------------
    # Header buttons
    # ------------------------------------------------------------------
    def _on_open_folder(self) -> None:
        root = self._store.data_dir()
        root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))

    def _on_change_dir(self) -> None:
        current = str(self._store.data_dir())
        start = current if Path(current).is_dir() else \
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.HomeLocation)
        new_dir = QFileDialog.getExistingDirectory(
            self, "Choose data directory", start)
        if not new_dir:
            return
        paths.set_data_dir(new_dir)
        self.refresh_status()

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    def _on_convert(self) -> None:
        """Shell out to `spody.exe convert ephemeris <DE440 folder> 440
        <date1> [date2 ...]`. The list of dates is derived from the
        ascpXXXXX.440 files actually present in the folder, so re-runs
        with extra chunks Just Work."""
        spody_bin = self._store.spody_binary()
        if not spody_bin or not Path(spody_bin).exists():
            QMessageBox.warning(
                self, "spody binary not set",
                "Configure the spody binary in Settings > Paths first, "
                "then re-open this wizard.")
            return
        if self._convert_proc is not None:
            return  # already running

        de_folder = self._store.data_dir() / "DE440"
        date_ids = sorted({
            p.stem[4:]                  # "ascp01950" -> "01950"
            for p in de_folder.glob("ascp*.440")
            if len(p.stem) == 9
        })
        if not date_ids:
            QMessageBox.warning(self, "No DE440 chunks",
                                f"No ascpXXXXX.440 files found in {de_folder}.")
            return

        argv = ["convert", "ephemeris", str(de_folder), "440", *date_ids]
        self._convert_status.setText("running... " + " ".join(date_ids))
        self._btn_convert.setEnabled(False)

        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        proc.setWorkingDirectory(str(self._store.data_dir()))
        proc.finished.connect(self._on_convert_finished)
        proc.errorOccurred.connect(self._on_convert_error)
        self._convert_proc = proc
        proc.start(spody_bin, argv)

    def _on_convert_finished(self, exit_code: int, _exit_status) -> None:
        proc = self._convert_proc
        self._convert_proc = None
        out = ""
        if proc is not None:
            out = bytes(proc.readAllStandardOutput()).decode(
                "utf-8", errors="replace").strip()
        if exit_code == 0:
            self._changed = True
            self.refresh_status()
            self.assets_changed.emit()
            if out:
                # Print only the last line on the status label, full text
                # in a message box for the curious.
                tail = out.splitlines()[-1]
                self._convert_status.setText(f"OK -- {tail}")
            else:
                self._convert_status.setText("OK")
        else:
            self._convert_status.setText(f"failed (exit {exit_code})")
            QMessageBox.critical(self, "Conversion failed",
                                 out or f"spody convert exited with code {exit_code}")
            self._btn_convert.setEnabled(True)

    def _on_convert_error(self, err: QProcess.ProcessError) -> None:
        if self._convert_proc is None:
            return
        self._convert_status.setText(f"launch failed ({err.name})")
        self._btn_convert.setEnabled(True)
        self._convert_proc = None


class _AssetRow(QWidget):
    """One row of the wizard: status icon + name + editable URL +
    progress bar + Download button. The row owns the QNetworkReply
    while a download is in flight so cancel() is straightforward."""

    # relpath of the just-downloaded asset, so the wizard knows what
    # was added without scanning the disk.
    downloaded = Signal(str)

    def __init__(self, asset: Asset, nam: QNetworkAccessManager,
                 store: SettingsStore, parent: QWidget) -> None:
        super().__init__(parent)
        self._asset = asset
        self._nam = nam
        self._store = store
        self._reply: QNetworkReply | None = None
        # The on-disk file we stream the body into. Kept open for the
        # duration of the download and closed in _on_finished.
        self._sink: QFile | None = None

        # Layout: two rows -- header (icon | name | URL editor) and
        # action (progress bar | Download button).
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 4)
        outer.setSpacing(2)

        head = QHBoxLayout()
        self._icon = QLabel("?")
        self._icon.setFixedWidth(18)
        self._name = QLabel(asset.name)
        self._name.setStyleSheet("font-weight: bold;")
        self._size = QLabel("")
        self._size.setStyleSheet("color: gray;")
        head.addWidget(self._icon)
        head.addWidget(self._name)
        head.addWidget(self._size)
        head.addStretch(1)
        outer.addLayout(head)

        action = QHBoxLayout()
        self._url_edit = QLineEdit(asset.url)
        if asset.kind == "derived":
            self._url_edit.setText("(produced by conversion below)")
            self._url_edit.setEnabled(False)
        action.addWidget(QLabel("URL:" if asset.kind == "raw" else "src:"))
        action.addWidget(self._url_edit, 1)
        outer.addLayout(action)

        bar_row = QHBoxLayout()
        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setTextVisible(True)
        bar_row.addWidget(self._bar, 1)
        self._btn = QPushButton("Download")
        self._btn.clicked.connect(self._on_button)
        if asset.kind == "derived":
            self._btn.setEnabled(False)
            self._btn.setText("(derived)")
        bar_row.addWidget(self._btn)
        outer.addLayout(bar_row)

        # Hairline under each card so they read as separate items.
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

    # ------------------------------------------------------------------
    # Refresh from disk
    # ------------------------------------------------------------------
    def refresh(self, root: Path) -> None:
        present = assets.is_present(self._asset, root)
        p = root / self._asset.relpath
        if present:
            self._icon.setText("✓")  # check
            self._icon.setStyleSheet("color: #1a7f37; font-weight: bold;")
            mb = p.stat().st_size / (1024 * 1024)
            self._size.setText(f"({mb:.1f} MB on disk)")
            self._btn.setText("Re-download")
            if self._asset.kind == "derived":
                self._btn.setText("(derived)")
        elif p.is_file():
            self._icon.setText("⚠")  # warning
            self._icon.setStyleSheet("color: #b58900; font-weight: bold;")
            self._size.setText(
                f"({p.stat().st_size} B; expected >= {self._asset.min_bytes:,})")
            self._btn.setText("Download" if self._asset.kind == "raw" else "(derived)")
        else:
            self._icon.setText("✗")  # cross
            self._icon.setStyleSheet("color: #cf222e; font-weight: bold;")
            self._size.setText("(missing)")
            self._btn.setText("Download" if self._asset.kind == "raw" else "(derived)")
        # Always re-enable so the user can retry; only derived stays disabled.
        if self._asset.kind == "raw":
            self._btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Download lifecycle
    # ------------------------------------------------------------------
    def _on_button(self) -> None:
        if self._reply is not None:
            # In-flight: button acts as Cancel.
            self._reply.abort()
            return
        url_text = self._url_edit.text().strip()
        if not url_text:
            return
        url = QUrl(url_text)
        if not url.isValid() or url.scheme() not in ("http", "https"):
            QMessageBox.warning(self, "Invalid URL",
                                f"Not a valid http(s) URL:\n{url_text}")
            return

        # Prepare destination: <data dir>/<relpath>.
        dest = self._store.data_dir() / self._asset.relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Write to a .part file and rename on success so an aborted
        # download never leaves a corrupted "real" file behind.
        part = dest.with_suffix(dest.suffix + ".part")
        sink = QFile(str(part))
        if not sink.open(QIODevice.OpenModeFlag.WriteOnly):
            QMessageBox.critical(self, "Cannot write",
                                 f"Failed to open for writing:\n{part}")
            return
        self._sink = sink
        self._dest = dest
        self._part = part

        req = QNetworkRequest(url)
        req.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy,
        )
        # Identify ourselves so anti-scraping middleware lets us through.
        req.setHeader(
            QNetworkRequest.KnownHeaders.UserAgentHeader,
            "spody-gui-setup/0.1",
        )
        reply = self._nam.get(req)
        reply.readyRead.connect(self._on_ready_read)
        reply.downloadProgress.connect(self._on_progress)
        reply.finished.connect(self._on_finished)
        self._reply = reply
        self._btn.setText("Cancel")
        self._bar.setValue(0)
        self._bar.setRange(0, 0)  # busy-pulse until first progress signal

    def _on_progress(self, received: int, total: int) -> None:
        if total > 0:
            self._bar.setRange(0, 100)
            self._bar.setValue(int(received * 100 / total))
        # When total is unknown (chunked transfer), leave the bar in
        # busy-pulse mode (range 0..0).

    def _on_ready_read(self) -> None:
        if self._reply is None or self._sink is None:
            return
        self._sink.write(self._reply.readAll())

    def _on_finished(self) -> None:
        reply = self._reply
        sink  = self._sink
        self._reply = None
        self._sink  = None
        if sink is not None:
            sink.close()
        if reply is None:
            return

        err = reply.error()
        url_final = reply.url().toString()
        reply.deleteLater()

        if err != QNetworkReply.NetworkError.NoError:
            # Cleanup the partial file so a re-try starts fresh.
            try:
                self._part.unlink(missing_ok=True)
            except OSError:
                pass
            self._bar.setRange(0, 100)
            self._bar.setValue(0)
            self._btn.setText("Download")
            QMessageBox.warning(
                self, "Download failed",
                f"{self._asset.name}\nURL: {url_final}\n\n"
                f"Error: {err.name}\n"
                f"Reply: {reply.errorString() if hasattr(reply, 'errorString') else '(no message)'}")
            return

        # Atomic-ish rename: drop any existing file first (Windows
        # refuses to rename onto an existing path).
        try:
            if self._dest.exists():
                self._dest.unlink()
            self._part.rename(self._dest)
        except OSError as exc:
            QMessageBox.critical(self, "Rename failed", f"{self._dest}\n{exc}")
            return

        self._bar.setRange(0, 100)
        self._bar.setValue(100)
        self._btn.setText("Re-download")
        self.downloaded.emit(self._asset.relpath)
