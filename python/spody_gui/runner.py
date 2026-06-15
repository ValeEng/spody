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
"""QProcess wrapper that launches spody.exe and streams stdout/stderr.

Exposes line_received/started/finished/error signals so the main window
can update the terminal pane and status bar without knowing about Qt's
QProcess specifics. Buffers partial lines so callers always see whole
ones, even when the C process writes in arbitrary-sized chunks.
"""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal


class SpodyRunner(QObject):
    line_received = Signal(str)   # emitted once per completed output line
    started       = Signal()      # emitted right after the process launches
    finished      = Signal(int)   # emitted with exit code (>=0) or -1 on crash
    error         = Signal(str)   # emitted on launch failure

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._buffer = ""
        self._start_time: float = 0.0
        self._end_time: float = 0.0
        # Most-recent non-empty line emitted to line_received -- exposed
        # so consumers (e.g. MainWindow when stamping the per-run notes
        # block) can read what the engine reported as its final status
        # without having to subscribe and hand-track it themselves.
        self._last_line: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def run(self, spody_bin: str, subcommand: str, toml_path: Path) -> None:
        """Launch `spody_bin <subcommand> <toml_path>` with the working
        directory set to the TOML's parent (so relative paths inside the
        TOML resolve the same way the CLI does)."""
        if self.is_running():
            self.error.emit("a spody process is already running")
            return

        self._buffer = ""
        self._last_line = ""
        self._start_time = time.monotonic()
        self._end_time = 0.0

        self._proc = QProcess(self)
        self._proc.setWorkingDirectory(str(toml_path.parent))
        # Merge stderr into stdout so a single signal handles both streams.
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._proc.readyReadStandardOutput.connect(self._on_ready_read)
        self._proc.finished.connect(self._on_finished)
        self._proc.errorOccurred.connect(self._on_error)

        self._proc.start(spody_bin, [subcommand, str(toml_path)])
        if not self._proc.waitForStarted(3000):
            self.error.emit(f"failed to start: {spody_bin}")
            self._proc = None
            return
        self.started.emit()

    def stop(self) -> None:
        """Terminate the running process (graceful, then kill after 2 s)."""
        if not self.is_running():
            return
        assert self._proc is not None
        self._proc.terminate()
        if not self._proc.waitForFinished(2000):
            self._proc.kill()
            self._proc.waitForFinished(1000)

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def elapsed_seconds(self) -> float:
        if self._start_time == 0.0:
            return 0.0
        end = self._end_time if self._end_time > 0.0 else time.monotonic()
        return end - self._start_time

    def last_line(self) -> str:
        """Most-recent non-empty stdout line the engine produced.
        Empty before the first line arrives and reset on each new
        run; meaningful only after the `finished` signal fires."""
        return self._last_line

    # ------------------------------------------------------------------
    # Qt signal handlers
    # ------------------------------------------------------------------
    def _on_ready_read(self) -> None:
        assert self._proc is not None
        # readAllStandardOutput returns a QByteArray; decode permissively.
        chunk = bytes(self._proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        self._buffer += chunk
        # Emit complete lines; keep any trailing partial line for next call.
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._last_line = line
            self.line_received.emit(line)

    def _on_finished(self, exit_code: int, _exit_status) -> None:
        # Flush any trailing partial line that did not end with newline.
        if self._buffer:
            if self._buffer.strip():
                self._last_line = self._buffer
            self.line_received.emit(self._buffer)
            self._buffer = ""
        self._end_time = time.monotonic()
        self.finished.emit(exit_code)
        self._proc = None

    def _on_error(self, err: QProcess.ProcessError) -> None:
        # QProcess emits errorOccurred for FailedToStart, Crashed, Timedout,
        # etc. The finished signal usually follows; just surface the reason.
        if self._proc is not None:
            self.error.emit(self._proc.errorString())
