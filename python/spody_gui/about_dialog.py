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
"""About dialog: app version + spody-core library version.

Layout:

    +-----------------------------------------+
    |   SpOdy                                 |
    |   v0.1.0-alpha                          |
    |   Desktop frontend for the SpOdy        |
    |   orbital propagator.                   |
    +-----------------------------------------+
    | App:    0.1.0-alpha                     |
    | Core:   1.0.0 (git 0d01f3b,             |
    |         built 2026-05-21T06:41:53Z)     |
    +-----------------------------------------+
    |                                  [Close]|
    +-----------------------------------------+

Two rows by design: the user only cares about which SpOdy app they
have and which spody-core library is inside it. Python / PySide6 /
Qt versions are bundled and the user does not pick them, so they
add noise without adding actionable information.

The Core row is filled in by shelling out to `spody.exe info`
once and parsing the `spody-core : ...` line. If the engine is
not configured in Settings, the row reads `(spody binary not set)`
in grey -- the rest of the dialog still works.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import __version__ as APP_VERSION
from .settings import SettingsStore


_UNCONFIGURED = "(spody binary not set in Settings > Paths)"
_INFO_TIMEOUT_S = 5


def show_about(store: SettingsStore, parent: QWidget | None = None) -> None:
    """Pop the modal About dialog. Returns when the user closes it."""
    dlg = _AboutDialog(store, parent)
    dlg.exec()


class _AboutDialog(QDialog):
    def __init__(self, store: SettingsStore, parent: QWidget | None) -> None:
        super().__init__(parent)
        self._store = store
        self.setWindowTitle("About SpOdy")
        self.setMinimumWidth(480)

        # ---- Header banner: name + version + one-line description -----
        title = QLabel("SpOdy")
        title_font = QFont(); title_font.setPointSize(20); title_font.setBold(True)
        title.setFont(title_font)
        version = QLabel(f"v{APP_VERSION}")
        version.setStyleSheet("color: gray;")
        blurb = QLabel(
            "Desktop frontend for the SpOdy orbital propagator.\n"
            "Patran-style: fills a TOML form, runs the binary, "
            "displays the output."
        )
        blurb.setWordWrap(True)
        blurb.setStyleSheet("color: #444; padding-top: 6px;")

        # ---- Version table --------------------------------------------
        core_text = self._query_core_version()

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(6)
        form.addRow(_label_b("App"),  _label(APP_VERSION))
        form.addRow(_label_b("Core"), _label(core_text, mono=True, wrap=True))

        sep = QFrame(); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)

        # ---- Close button --------------------------------------------
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        outer = QVBoxLayout(self)
        outer.addWidget(title)
        outer.addWidget(version)
        outer.addWidget(blurb)
        outer.addSpacing(8)
        outer.addWidget(sep)
        outer.addLayout(form)
        outer.addSpacing(4)
        outer.addWidget(buttons)

    # ------------------------------------------------------------------
    # Core probe
    # ------------------------------------------------------------------
    def _query_core_version(self) -> str:
        """Run `spody.exe info` and return the spody-core version
        line. Returns a placeholder when the engine is not set, or
        an error marker when the invocation fails -- never raises,
        because About should never crash."""
        path = self._store.spody_binary()
        if not path or not Path(path).is_file():
            return _UNCONFIGURED
        try:
            r = subprocess.run([path, "info"], capture_output=True,
                               text=True, timeout=_INFO_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"(launch failed: {exc})"
        if r.returncode != 0:
            return f"(spody info returned exit {r.returncode})"
        return _parse_core_line(r.stdout)


def _parse_core_line(stdout: str) -> str:
    """Pull the `spody-core : ...` line out of the engine's info
    output. The engine prints two lines:

        SpOdy app  : <app-ver>
        spody-core : <core-ver>  (git <hash>, built <timestamp>)

    We want just the second one -- App is already shown in the row
    above."""
    for line in stdout.splitlines():
        m = re.match(r"^\s*spody-core\s*:\s*(.+)$", line)
        if m:
            return m.group(1).strip()
    return "(no spody-core line in info output)"


# ----------------------------------------------------------------------
# Tiny label helpers -- keep the form construction above readable.
# ----------------------------------------------------------------------
def _label_b(text: str) -> QLabel:
    """Bold form-label."""
    w = QLabel(text)
    f = w.font(); f.setBold(True); w.setFont(f)
    return w


def _label(text: str, mono: bool = False, wrap: bool = False) -> QLabel:
    w = QLabel(text)
    if mono:
        f = QFont("Consolas")
        f.setStyleHint(QFont.StyleHint.Monospace)
        w.setFont(f)
    if wrap:
        w.setWordWrap(True)
    w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if text.startswith("(") and text.endswith(")"):
        # Placeholder / error -- render in grey so it reads as "unset".
        w.setStyleSheet("color: gray;")
    return w
