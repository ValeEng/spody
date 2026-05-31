"""TOML editor widget: monospace QPlainTextEdit with a minimal highlighter."""
from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextDocument,
)
from PySide6.QtWidgets import QPlainTextEdit

# Color palette tuned for a light editor background. Kept conservative so
# the highlighting reads correctly on both light and dark system themes.
_COLOR_HEADER  = QColor("#0033b3")  # [section] and [[arrays]]
_COLOR_KEY     = QColor("#7f0055")  # key =
_COLOR_STRING  = QColor("#1c8042")  # "string" 'string'
_COLOR_NUMBER  = QColor("#1750eb")  # 12, 3.14, 1e6
_COLOR_BOOL    = QColor("#7f0055")  # true / false
_COLOR_COMMENT = QColor("#808080")  # # comment


def _make_format(color: QColor, bold: bool = False) -> QTextCharFormat:
    fmt = QTextCharFormat()
    fmt.setForeground(color)
    if bold:
        fmt.setFontWeight(QFont.Weight.Bold)
    return fmt


class TomlHighlighter(QSyntaxHighlighter):
    """Minimal TOML highlighter: headers, keys, strings, numbers, bools, comments.

    Not a full TOML parser -- a handful of regexes that catch the common
    constructs and degrade gracefully on multi-line strings (which we do
    not highlight specially).
    """

    def __init__(self, document: QTextDocument) -> None:
        super().__init__(document)
        # Order matters: later rules overwrite earlier ones on overlap.
        self._rules: list[tuple[QRegularExpression, QTextCharFormat]] = [
            (QRegularExpression(r'\b(true|false)\b'),               _make_format(_COLOR_BOOL,   bold=True)),
            (QRegularExpression(r'\b-?\d+(\.\d+)?([eE][+-]?\d+)?\b'), _make_format(_COLOR_NUMBER)),
            (QRegularExpression(r'"[^"\n]*"'),                       _make_format(_COLOR_STRING)),
            (QRegularExpression(r"'[^'\n]*'"),                       _make_format(_COLOR_STRING)),
            (QRegularExpression(r'^\s*[A-Za-z_][\w\-]*\s*(?==)'),    _make_format(_COLOR_KEY)),
            (QRegularExpression(r'^\s*\[\[?[^\]]+\]\]?'),            _make_format(_COLOR_HEADER, bold=True)),
            (QRegularExpression(r'#.*$'),                            _make_format(_COLOR_COMMENT)),
        ]

    def highlightBlock(self, text: str) -> None:  # noqa: N802 -- Qt naming
        for regex, fmt in self._rules:
            it = regex.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)


class TomlEditor(QPlainTextEdit):
    """QPlainTextEdit specialised for editing TOML: monospace font,
    line wrap disabled, soft tab = 2 spaces, modified flag exposed via the
    inherited document().isModified() and modificationChanged() signal."""

    # Re-export the document's modificationChanged for convenience.
    modificationChanged = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.setFont(font)
        # 2-space tabs render at the right width given the monospace font.
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 2)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._highlighter = TomlHighlighter(self.document())
        # Forward the document's signal so MainWindow can connect without
        # reaching into self.document() explicitly.
        self.document().modificationChanged.connect(self.modificationChanged)

    # Convenience accessors --------------------------------------------
    def set_text(self, text: str) -> None:
        self.setPlainText(text)

    def text(self) -> str:
        return self.toPlainText()
