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
"""Resolution of the *data root* -- the folder where the setup wizard
downloads ephemeris + harmonics files into.

By design the data root is **portable**: it sits next to the GUI
executable so that the whole bundle (gui exe + spody.exe + data/) can
be moved / zipped as a single unit. The location is therefore *not*
asked of the user; the wizard only displays it.

Resolution order (first match wins):
    1. QSettings override `paths/data_dir` -- power users / CI override.
    2. `<frozen-exe>/data/` when running inside a PyInstaller bundle.
    3. `<repo>/data/` when running from source (development).

The third branch puts the folder at `spody/data/` next to the `python/`
package, which keeps a dev clone self-contained. The folder is added
to `.gitignore` in the repo so the downloaded data never accidentally
ends up in a commit.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings

# QSettings key for the optional override. Mirrors the spelling used in
# settings.py (kept here too so paths.py stays standalone-importable).
KEY_DATA_DIR = "paths/data_dir"


def data_dir() -> Path:
    """Return the effective data root. Always returns an absolute Path;
    does NOT create the directory (the wizard handles creation just
    before writing, so a stale empty folder is never left behind)."""
    override = QSettings().value(KEY_DATA_DIR, "", type=str)
    if override:
        return Path(override).expanduser().resolve()
    return _default_data_dir()


def is_overridden() -> bool:
    """True iff the user (or a previous session) has stored an explicit
    `paths/data_dir`. Used by the Settings dialog to show whether the
    displayed path is the default or a user pick."""
    return bool(QSettings().value(KEY_DATA_DIR, "", type=str))


def set_data_dir(path: str) -> None:
    """Persist an explicit override (empty string clears it)."""
    qs = QSettings()
    if path:
        qs.setValue(KEY_DATA_DIR, str(Path(path).expanduser().resolve()))
    else:
        qs.remove(KEY_DATA_DIR)


def _default_data_dir() -> Path:
    """The non-overridden default: `<frozen-exe>/data` if running under
    PyInstaller, otherwise `<repo>/data` next to the python/ package."""
    if getattr(sys, "frozen", False):
        # PyInstaller sets sys.frozen and points sys.executable at the
        # unpacked launcher. data/ next to it gives a portable layout.
        return Path(sys.executable).parent.resolve() / "data"
    # Dev mode: spody_gui/paths.py -> spody_gui/ -> python/ -> <repo>/
    return Path(__file__).resolve().parents[2] / "data"
