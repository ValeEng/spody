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

"""Bottom-bar handlers (RUN / Validate / Save As)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


class HandlersMixin:
    """Bottom-bar button handlers mixed into TomlForm."""

    # ==================================================================
    # Bottom-bar handlers
    # ==================================================================
    def _on_run_clicked(self) -> None:
        """Pick the right spody subcommand based on the form contents
        and ask MainWindow to launch it (save-before-run logic stays
        in MainWindow so this button shares it with the menu actions)."""
        subcommand = "batch" if "batch" in self.to_dict() else "propagate"
        self.runRequested.emit(subcommand)

    def _on_validate_clicked(self) -> None:
        """Write the current form to a temp TOML next to the current
        file (or to the OS temp dir if there is no current file) and
        run `spody validate` synchronously. Show the verdict on the
        badge -- green '✓ OK' or red '✗ <error>' with the full
        message in the tooltip. Does NOT touch the terminal pane;
        this is a quick check without committing to a Run."""
        if self._store is None:
            self._set_badge("(no SettingsStore wired)", ok=False)
            return
        spody_bin = self._store.spody_binary()
        if not spody_bin or not Path(spody_bin).exists():
            self._set_badge("(spody binary not set)", ok=False,
                            tip="Set Settings > Paths > spody binary first.")
            return
        # Hard guard: same check the menu run uses. spody validate is
        # tolerant of missing files in some edge cases, but most TOML
        # inputs reference the harmonics / ephemeris paths, and the
        # parser stats them eagerly -- safer to refuse outright and
        # offer the wizard.
        from ..setup_wizard import require_data_ready
        if not require_data_ready(self._store, self, "Validate"):
            self._set_badge("(data not ready)", ok=False,
                            tip="Open Settings > Setup wizard...")
            return

        try:
            data = self.to_dict()
        except ValueError as exc:
            self._set_badge("✗ form has invalid values", ok=False,
                            tip=str(exc))
            return

        # Write next to the current file when possible so relative
        # paths inside the TOML (harmonics_file, ephemeris.file,
        # batch.cases_file) resolve the same way spody does at run time.
        if self._current_path is not None:
            tmp_dir = self._current_path.parent
            prefix  = ".spody_validate_"
        else:
            tmp_dir = Path(tempfile.gettempdir())
            prefix  = "spody_validate_"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".toml", prefix=prefix,
            dir=str(tmp_dir), delete=False, encoding="utf-8",
        ) as fp:
            tmp_path = Path(fp.name)
            from ..toml_io import format_toml
            fp.write(format_toml(data))

        try:
            r = subprocess.run(
                [spody_bin, "validate", str(tmp_path)],
                capture_output=True, text=True, timeout=30,
                cwd=str(tmp_dir),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._set_badge("✗ validate failed to launch", ok=False,
                            tip=str(exc))
            tmp_path.unlink(missing_ok=True)
            return
        finally:
            tmp_path.unlink(missing_ok=True)

        if r.returncode == 0:
            self._set_badge("✓ valid", ok=True,
                            tip=(r.stdout or "spody validate exit 0").strip())
        else:
            # spody writes one-line "error: ..." messages to stderr;
            # the last non-empty line is what we want as the short msg.
            err_lines = [
                ln for ln in (r.stderr or r.stdout).strip().splitlines() if ln
            ]
            short = err_lines[-1] if err_lines else f"exit {r.returncode}"
            # Strip a leading "error: <file>: " for the badge so it fits.
            badge_msg = short
            if ": " in badge_msg:
                badge_msg = "✗ " + badge_msg.split(": ", 2)[-1]
            else:
                badge_msg = "✗ " + badge_msg
            self._set_badge(badge_msg[:160], ok=False, tip=short)

    def _set_badge(self, text: str, *, ok: bool, tip: str = "") -> None:
        self._validate_badge.setText(text)
        self._validate_badge.setStyleSheet(self._BADGE_OK if ok else self._BADGE_BAD)
        self._validate_badge.setToolTip(tip)

    def _refresh_preview(self) -> None:
        """Update the read-only TOML preview to reflect the current
        form. Robust to in-progress invalid input: if to_dict raises,
        we show a one-line placeholder and the preview catches up on
        the next valid edit."""
        if not hasattr(self, "_preview"):
            return   # called during __init__ before the preview exists
        try:
            from ..toml_io import format_toml
            text = format_toml(self.to_dict())
        except ValueError as exc:
            text = f"# (form has invalid values: {exc})"
        # Preserve the user's scroll position so the preview doesn't
        # jump to the top on every keystroke.
        scrollbar = self._preview.verticalScrollBar()
        pos = scrollbar.value()
        self._preview.setPlainText(text)
        scrollbar.setValue(pos)
