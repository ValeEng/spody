"""Read-only output pane that displays the spody subprocess's stdout/stderr.

A plain QPlainTextEdit with a monospace font and dark colours, used as a
terminal-style log. spody emits plain ASCII (no ANSI escape codes), so
no special parsing is needed -- each line is appended verbatim.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPalette, QColor
from PySide6.QtWidgets import QPlainTextEdit

# Cap the number of lines retained to keep memory bounded over long runs.
# spody emits a few records per integration step; 50k lines easily covers
# multi-day propagations at fine output cadence.
MAX_LINES = 50_000


class TerminalView(QPlainTextEdit):
    def __init__(self) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setMaximumBlockCount(MAX_LINES)
        self.setUndoRedoEnabled(False)

        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.setFont(font)

        # Dark terminal look: light-grey-on-near-black, regardless of theme.
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Base,           QColor("#1e1e1e"))
        pal.setColor(QPalette.ColorRole.Text,           QColor("#d4d4d4"))
        pal.setColor(QPalette.ColorRole.Highlight,      QColor("#264f78"))
        pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
        self.setPalette(pal)

    def append_line(self, line: str) -> None:
        """Append one line of output. Trailing newlines are stripped because
        appendPlainText adds its own paragraph break."""
        self.appendPlainText(line.rstrip("\r\n"))
        # Auto-scroll to the bottom so the user sees the latest output.
        sb = self.verticalScrollBar()
        sb.setValue(sb.maximum())
