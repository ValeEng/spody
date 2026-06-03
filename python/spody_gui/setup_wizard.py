"""First-launch / on-demand data setup dialog.

Single-pane layout (no QWizard step-flow): the user mostly wants to see
*everything that's missing* at a glance, fix one URL or two, hit
Download all, and let the conversion happen.

Layout (top -> bottom):

    Data dir:  <path>                       [Open folder] [Change...]
    Coverage:  (•) modern (1950-2050)
               ( ) full   (1550-2650)
    +-------------------------------------------------------+
    | Per-asset card (one each):                            |
    |   status icon | name (size) | URL editor              |
    |   [progress bar]           [Download]                 |
    +-------------------------------------------------------+
    Conversion (auto): <status text>
    -------------------------------------------------------- |
                          [Download all missing] [Refresh] [Close]

Downloads use `QNetworkAccessManager` so they integrate cleanly with
the Qt event loop -- no extra thread, no extra dependency.

Conversion shells out to `spody.exe convert ephemeris <folder> <de>
<date1> [date2 ...]`. It runs *automatically* whenever the raw DE440
chunks are complete and the derived `de440.spody` is missing or
older than the newest raw input -- the user never has to click it.
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
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
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
        self.setMinimumSize(820, 640)

        self._store = store
        self._nam   = QNetworkAccessManager(self)
        self._changed = False
        # One row widget per asset, rebuilt whenever the coverage
        # profile changes.
        self._rows: dict[str, _AssetRow] = {}
        # Track the active conversion process so we can cancel it cleanly.
        self._convert_proc: QProcess | None = None
        # Suppress auto-conversion during the user's destructive
        # operations (e.g. switching coverage rebuilds the row list).
        self._suspend_auto_convert = False

        self._build_ui()
        self._rebuild_rows()
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

        # ---- coverage profile selector ---------------------------------
        cov_row = QHBoxLayout()
        cov_row.addWidget(QLabel("DE440 coverage:"))
        self._rb_modern = QRadioButton("Modern era (1950-2050, ~30 MB)")
        self._rb_full   = QRadioButton("Full pack (1550-2650, ~340 MB)")
        # Default from QSettings; tooltips remind the user what each implies.
        if assets.coverage() == "full":
            self._rb_full.setChecked(True)
        else:
            self._rb_modern.setChecked(True)
        self._rb_modern.setToolTip(
            "One DE440 ASCII chunk (ascp01950.440). Right default for "
            "anyone running near-present epochs.")
        self._rb_full.setToolTip(
            "All 11 DE440 ASCII chunks (1550..2650). Needed only for "
            "historical / far-future scenarios.")
        cov_group = QButtonGroup(self)
        cov_group.addButton(self._rb_modern)
        cov_group.addButton(self._rb_full)
        self._rb_modern.toggled.connect(self._on_coverage_changed)
        cov_row.addWidget(self._rb_modern)
        cov_row.addWidget(self._rb_full)
        cov_row.addStretch(1)
        outer.addLayout(cov_row)

        intro = QLabel(
            "Spody needs an external planetary ephemeris (JPL DE440) and a "
            "lunar harmonic-gravity model (GRGM1200B). The wizard downloads "
            "the raw files into the data dir, then converts the DE440 ASCII "
            "chunks into spody's binary format (`de440.spody`) automatically.\n\n"
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
        self._rows_host = QWidget()
        self._rows_lay  = QVBoxLayout(self._rows_host)
        self._rows_lay.setSpacing(4)
        # A bottom stretch so cards stack at the top; the stretch index
        # stays as the last child after every _rebuild_rows.
        self._rows_lay.addStretch(1)
        scroll.setWidget(self._rows_host)
        outer.addWidget(scroll, 1)

        # ---- conversion status (no manual trigger button) --------------
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        outer.addWidget(sep)

        conv_lay = QHBoxLayout()
        conv_lay.addWidget(QLabel("Conversion (auto):"))
        self._convert_status = QLabel("")
        self._convert_status.setStyleSheet("color: gray;")
        conv_lay.addWidget(self._convert_status, 1)
        outer.addLayout(conv_lay)

        # ---- footer: download-all + refresh + close --------------------
        foot = QHBoxLayout()
        self._btn_download_all = QPushButton("Download all missing")
        self._btn_download_all.clicked.connect(self._on_download_all)
        btn_refresh = QPushButton("Refresh")
        btn_refresh.clicked.connect(self.refresh_status)
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)
        foot.addWidget(self._btn_download_all)
        foot.addStretch(1)
        foot.addWidget(btn_refresh)
        foot.addWidget(btn_close)
        outer.addLayout(foot)

    # ------------------------------------------------------------------
    # Row management
    # ------------------------------------------------------------------
    def _rebuild_rows(self) -> None:
        """Tear down the current asset rows and rebuild from the
        current coverage profile. Called on init and whenever the
        coverage radio changes."""
        # Pop everything but the trailing stretch.
        while self._rows_lay.count() > 1:
            item = self._rows_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._rows.clear()

        for a in assets.required_assets():
            row = _AssetRow(a, self._nam, self._store, self)
            row.downloaded.connect(self._on_asset_arrived)
            self._rows[a.relpath] = row
            # Insert before the stretch (index = count - 1).
            self._rows_lay.insertWidget(self._rows_lay.count() - 1, row)

    def _on_coverage_changed(self, _checked: bool) -> None:
        """Radio toggled. Persist + rebuild rows + refresh status."""
        new = "modern" if self._rb_modern.isChecked() else "full"
        if new == assets.coverage():
            return
        # While the rows are being torn down, no asset-arrived signal
        # should sneak in and trigger conversion against a transient
        # state.
        self._suspend_auto_convert = True
        try:
            assets.set_coverage(new)
            self._rebuild_rows()
            self.refresh_status()
        finally:
            self._suspend_auto_convert = False

    # ------------------------------------------------------------------
    # Status refresh
    # ------------------------------------------------------------------
    def refresh_status(self) -> None:
        """Recompute the per-asset 'present?' badge from disk and update
        the conversion status. Called on open, after each download, on
        coverage change, and from the Refresh button."""
        root = self._store.data_dir()
        self._data_dir_label.setText(str(root))
        for row in self._rows.values():
            row.refresh(root)
        self._refresh_convert_status(root)
        self._refresh_download_all_button(root)

    def _refresh_download_all_button(self, root: Path) -> None:
        """Enable Download-all iff there's at least one *raw* required
        asset that isn't present yet (and no download is in flight)."""
        any_missing = any(
            r.is_missing() and not r.is_busy()
            for r in self._rows.values()
            if r.asset.kind == "raw"
        )
        self._btn_download_all.setEnabled(any_missing)

    def _refresh_convert_status(self, root: Path) -> None:
        """Single status label for the conversion: pending / running /
        ok / stale. No buttons -- conversion is automatic."""
        if self._convert_proc is not None:
            return  # status was set when we launched
        out_file = root / "DE440" / "de440.spody"
        raw_required = [
            a for a in assets.required_assets()
            if a.kind == "raw" and a.relpath.startswith("DE440/")
        ]
        missing = [a.name for a in raw_required if not assets.is_present(a, root)]
        if missing:
            self._convert_status.setText(
                "waiting on raw DE440 (" + ", ".join(missing) + ")")
            return
        if not out_file.is_file():
            self._convert_status.setText("ready to convert (will run automatically)")
            return
        # Output present: check freshness against on-disk chunks (we
        # consider any chunk in the folder, not just the required ones,
        # so switching to "modern" after a full convert doesn't claim
        # the .spody is stale).
        de_folder = root / "DE440"
        chunks = list(de_folder.glob("ascp*.440"))
        newest = max((p.stat().st_mtime for p in chunks), default=0.0)
        mb = out_file.stat().st_size / (1024 * 1024)
        if out_file.stat().st_mtime < newest:
            self._convert_status.setText(
                f"de440.spody is stale ({mb:.1f} MB) -- new chunks present, "
                "will re-convert")
        else:
            self._convert_status.setText(f"de440.spody ready ({mb:.1f} MB)")

    # ------------------------------------------------------------------
    # Download callbacks
    # ------------------------------------------------------------------
    def _on_asset_arrived(self, relpath: str) -> None:
        """A row finished writing its file."""
        self._changed = True
        self.refresh_status()
        self.assets_changed.emit()
        # Maybe everything DE440-raw is now present and we can convert.
        self._maybe_auto_convert()

    def _on_download_all(self) -> None:
        """Kick off every missing raw download in one click. Rows handle
        the actual networking, including URL validation."""
        for row in self._rows.values():
            if row.asset.kind != "raw":
                continue
            if row.is_missing() and not row.is_busy():
                row.start_download()
        self._refresh_download_all_button(self._store.data_dir())

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
    # Conversion (automatic)
    # ------------------------------------------------------------------
    def _maybe_auto_convert(self) -> None:
        """Decide whether to fire the conversion subprocess. Trigger
        when all required raw chunks are present AND the derived
        de440.spody is either missing or older than the newest raw
        input. Bails out silently in any other case so this method is
        safe to call after every download."""
        if self._suspend_auto_convert:
            return
        if self._convert_proc is not None:
            return  # already running
        root = self._store.data_dir()
        # Need every REQUIRED raw DE440 chunk present (and the header).
        raw_required = [
            a for a in assets.required_assets()
            if a.kind == "raw" and a.relpath.startswith("DE440/")
        ]
        if any(not assets.is_present(a, root) for a in raw_required):
            return
        out_file = root / "DE440" / "de440.spody"
        if out_file.is_file():
            chunks = list((root / "DE440").glob("ascp*.440"))
            newest = max((p.stat().st_mtime for p in chunks), default=0.0)
            if out_file.stat().st_mtime >= newest:
                return  # up to date
        self._run_conversion()

    def _run_conversion(self) -> None:
        """Launch `spody.exe convert ephemeris <DE440 folder> 440
        <date1> [date2 ...]`. The list of dates is the union of every
        ascpXXXXX.440 actually present in the folder (not just the
        required ones), so a `.spody` produced here always covers as
        much epoch range as the user has downloaded."""
        spody_bin = self._store.spody_binary()
        if not spody_bin or not Path(spody_bin).exists():
            self._convert_status.setText(
                "auto-convert blocked: configure spody binary in Settings > Paths")
            return

        de_folder = self._store.data_dir() / "DE440"
        date_ids = sorted({
            p.stem[4:]                  # "ascp01950" -> "01950"
            for p in de_folder.glob("ascp*.440")
            if len(p.stem) == 9
        })
        if not date_ids:
            return  # nothing to convert

        argv = ["convert", "ephemeris", str(de_folder), "440", *date_ids]
        self._convert_status.setText(
            "converting... " + " ".join(date_ids))

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
        else:
            self._convert_status.setText(f"conversion failed (exit {exit_code})")
            QMessageBox.critical(self, "Conversion failed",
                                 out or f"spody convert exited with code {exit_code}")

    def _on_convert_error(self, err: QProcess.ProcessError) -> None:
        if self._convert_proc is None:
            return
        self._convert_status.setText(f"conversion launch failed ({err.name})")
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
        self.asset = asset
        self._nam = nam
        self._store = store
        self._reply: QNetworkReply | None = None
        # The on-disk file we stream the body into. Kept open for the
        # duration of the download and closed in _on_finished.
        self._sink: QFile | None = None
        self._dest: Path | None = None
        self._part: Path | None = None
        # Cached "present" state from the last refresh(); cheap source
        # of truth for is_missing() / wizard button gating.
        self._present = False

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
    # State queries (used by SetupWizard for the Download-all button)
    # ------------------------------------------------------------------
    def is_missing(self) -> bool:
        return not self._present

    def is_busy(self) -> bool:
        return self._reply is not None

    # ------------------------------------------------------------------
    # Refresh from disk
    # ------------------------------------------------------------------
    def refresh(self, root: Path) -> None:
        self._present = assets.is_present(self.asset, root)
        p = root / self.asset.relpath
        if self._present:
            self._icon.setText("✓")  # check mark
            self._icon.setStyleSheet("color: #1a7f37; font-weight: bold;")
            mb = p.stat().st_size / (1024 * 1024)
            self._size.setText(f"({mb:.1f} MB on disk)")
            self._btn.setText("Re-download")
            if self.asset.kind == "derived":
                self._btn.setText("(derived)")
        elif p.is_file():
            self._icon.setText("⚠")  # warning sign
            self._icon.setStyleSheet("color: #b58900; font-weight: bold;")
            self._size.setText(
                f"({p.stat().st_size} B; expected >= {self.asset.min_bytes:,})")
            self._btn.setText("Download" if self.asset.kind == "raw" else "(derived)")
        else:
            self._icon.setText("✗")  # cross
            self._icon.setStyleSheet("color: #cf222e; font-weight: bold;")
            self._size.setText("(missing)")
            self._btn.setText("Download" if self.asset.kind == "raw" else "(derived)")
        # Always re-enable so the user can retry; only derived stays disabled.
        if self.asset.kind == "raw":
            self._btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Download lifecycle
    # ------------------------------------------------------------------
    def _on_button(self) -> None:
        if self._reply is not None:
            self._reply.abort()             # acts as Cancel while in-flight
            return
        self.start_download()

    def start_download(self) -> None:
        """Begin a download for this row. No-op if already in flight or
        if this is a derived asset. Validates the URL field first."""
        if self._reply is not None or self.asset.kind != "raw":
            return
        url_text = self._url_edit.text().strip()
        if not url_text:
            return
        url = QUrl(url_text)
        if not url.isValid() or url.scheme() not in ("http", "https"):
            QMessageBox.warning(self, "Invalid URL",
                                f"Not a valid http(s) URL:\n{url_text}")
            return

        dest = self._store.data_dir() / self.asset.relpath
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
        err_text  = reply.errorString()
        reply.deleteLater()

        if err != QNetworkReply.NetworkError.NoError:
            # Cleanup the partial file so a re-try starts fresh.
            if self._part is not None:
                try:
                    self._part.unlink(missing_ok=True)
                except OSError:
                    pass
            self._bar.setRange(0, 100)
            self._bar.setValue(0)
            self._btn.setText("Download")
            QMessageBox.warning(
                self, "Download failed",
                f"{self.asset.name}\nURL: {url_final}\n\n"
                f"Error: {err.name}\n"
                f"Reply: {err_text}")
            return

        # Atomic-ish rename: drop any existing file first (Windows
        # refuses to rename onto an existing path).
        try:
            if self._dest is not None and self._dest.exists():
                self._dest.unlink()
            if self._part is not None and self._dest is not None:
                self._part.rename(self._dest)
        except OSError as exc:
            QMessageBox.critical(self, "Rename failed", f"{self._dest}\n{exc}")
            return

        self._bar.setRange(0, 100)
        self._bar.setValue(100)
        self._btn.setText("Re-download")
        self.downloaded.emit(self.asset.relpath)
