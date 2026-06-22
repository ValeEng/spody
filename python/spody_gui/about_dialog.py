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
"""About dialog: app version + spody-core library version + credits.

Layout:

    +-----------------------------------------+
    |   SpOdy                                 |
    |   v0.1.3-beta                           |
    |   Desktop frontend for the SpOdy        |
    |   orbital propagator.                   |
    |                                         |
    |   (c) 2026 ValeEng - Apache License 2.0 |
    +-----------------------------------------+
    | App:    0.1.3-beta                      |
    | Core:   1.2.0 (git c5c0dd9,             |
    |         built 2026-06-22T00:00:00Z)     |
    +-----------------------------------------+
    | Built on (collapsible credits):         |
    | - Data: JPL DE440, GRGM1200B,           |
    |         EIGEN-6C4, IERS EOP,            |
    |         IAU 2006 conventions,           |
    |         NASA Blue Marble / SVS LROC     |
    | - Libs: pyerfa, numpy, matplotlib,      |
    |         PySide6 / Qt, VTK               |
    +-----------------------------------------+
    |                                  [Close]|
    +-----------------------------------------+

The Core row is filled in by shelling out to `spody.exe info`
once and parsing the `spody-core : ...` line. If the engine is
not configured in Settings, the row reads `(spody binary not set)`
in grey -- the rest of the dialog still works.

The credits block is rendered as a click-to-expand box so the
default About fits a small dialog; expanding it shows the canonical
references for every externally-maintained data set + every
non-trivial scientific dependency the user might want to cite when
publishing SpOdy results.
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
    QGroupBox,
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

        # The copyright line uses rich text so the license link is
        # clickable (Qt's QLabel auto-opens external links when
        # `openExternalLinks=True`). Kept as a single line so the
        # default About is still compact.
        copyright = QLabel(
            "&copy; 2026 ValeEng &mdash; "
            "<a href='https://www.apache.org/licenses/LICENSE-2.0'>"
            "Apache License 2.0</a>"
        )
        copyright.setTextFormat(Qt.TextFormat.RichText)
        copyright.setOpenExternalLinks(True)
        copyright.setStyleSheet("color: #444; padding-top: 4px;")

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

        # ---- Credits group -------------------------------------------
        credits = _build_credits_group()

        # ---- Close button --------------------------------------------
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)

        outer = QVBoxLayout(self)
        outer.addWidget(title)
        outer.addWidget(version)
        outer.addWidget(blurb)
        outer.addWidget(copyright)
        outer.addSpacing(8)
        outer.addWidget(sep)
        outer.addLayout(form)
        outer.addSpacing(8)
        outer.addWidget(credits)
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
# Credits group
# ----------------------------------------------------------------------
# Two short bullet lists pinned in a QGroupBox: external data sources
# (cite when publishing SpOdy results) and key scientific libraries
# (use the standard pip-cited references when reproducing the build).
# Rendered as rich text so links to the canonical project pages are
# clickable; the user's default browser opens them.
_CREDITS_HTML = (
    "<p style='margin: 0 0 4px 0;'><b>Data sources</b></p>"
    "<ul style='margin: 0 0 6px 14px; padding: 0;'>"
    "<li><b>JPL DE440</b> planetary ephemeris &mdash; "
    "Park, Folkner, Williams, Boggs (2021), "
    "<a href='https://ssd.jpl.nasa.gov/planets/eph_export.html'>"
    "ssd.jpl.nasa.gov</a></li>"
    "<li><b>GRGM1200B</b> lunar gravity model &mdash; "
    "Goossens et al. (2016), "
    "<a href='https://pgda.gsfc.nasa.gov/products/50'>"
    "pgda.gsfc.nasa.gov</a></li>"
    "<li><b>EIGEN-6C4</b> Earth gravity model &mdash; "
    "F&ouml;rste et al. (2014), "
    "<a href='http://icgem.gfz-potsdam.de/'>icgem.gfz-potsdam.de</a></li>"
    "<li><b>IERS EOP</b> (finals2000A.all) &mdash; "
    "<a href='https://www.iers.org/'>iers.org</a></li>"
    "<li><b>IAU 2006 / 2000A_R06</b> precession-nutation conventions"
    " &mdash; Petit &amp; Luzum, IERS Technical Note 36 (2010)</li>"
    "<li><b>NASA Visible Earth Blue Marble</b> (December 2004) &mdash; "
    "<a href='https://visibleearth.nasa.gov/collection/1484/blue-marble'>"
    "visibleearth.nasa.gov</a></li>"
    "<li><b>NASA SVS LROC color Moon</b> &mdash; "
    "<a href='https://svs.gsfc.nasa.gov/4720'>svs.gsfc.nasa.gov/4720</a></li>"
    "</ul>"
    "<p style='margin: 4px 0;'><b>Scientific libraries</b></p>"
    "<ul style='margin: 0 0 0 14px; padding: 0;'>"
    "<li><b>pyerfa</b> (Hohenkerk et al.) &mdash; SOFA C library Python bindings"
    " (Earth-orientation rotation in <code>spopy</code>)</li>"
    "<li><b>numpy</b> (Harris et al. 2020) "
    "&middot; <b>matplotlib</b> (Hunter 2007) "
    "&middot; <b>VTK</b> (Schroeder, Martin, Lorensen)</li>"
    "<li><b>PySide6 / Qt</b> &mdash; GUI toolkit (LGPL v3)</li>"
    "</ul>"
)


def _build_credits_group() -> QGroupBox:
    g = QGroupBox("Built on")
    g.setCheckable(True)
    g.setChecked(False)   # collapsed by default; user clicks to expand
    body = QLabel(_CREDITS_HTML)
    body.setTextFormat(Qt.TextFormat.RichText)
    body.setOpenExternalLinks(True)
    body.setWordWrap(True)
    body.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextBrowserInteraction
    )
    body.setVisible(False)
    g.toggled.connect(body.setVisible)
    lay = QVBoxLayout(g)
    lay.setContentsMargins(8, 6, 8, 6)
    lay.addWidget(body)
    return g


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
