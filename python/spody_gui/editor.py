"""TOML editor widget: monospace QPlainTextEdit with syntax highlighting,
context-aware autocompletion, and Tab-triggered snippet expansion."""
from __future__ import annotations

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import QPlainTextEdit

from . import schema
from .completer import TomlCompleter

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
    inherited document().isModified() and modificationChanged() signal.

    Adds:
      - context-aware autocompletion (Ctrl+Space and auto-trigger on
        certain printable characters)
      - Tab-triggered snippet expansion: typing a snippet name (e.g.
        "simulation") at the start of a line and pressing Tab inserts
        the full templated block.
    """

    # Re-export the document's modificationChanged for convenience.
    modificationChanged = Signal(bool)

    # Characters that should retrigger the completion popup after they
    # are inserted (alphanumerics, identifier chars, and the syntax
    # markers that change context).
    _AUTO_TRIGGER_CHARS = set("[]=._-\"")

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
        self._completer = TomlCompleter(self)
        # Forward the document's signal so MainWindow can connect without
        # reaching into self.document() explicitly.
        self.document().modificationChanged.connect(self.modificationChanged)

    # Convenience accessors --------------------------------------------
    def set_text(self, text: str) -> None:
        self.setPlainText(text)

    def text(self) -> str:
        return self.toPlainText()

    # ------------------------------------------------------------------
    # Snippet insertion (public so the Insert menu can call it too)
    # ------------------------------------------------------------------
    def insert_snippet(self, name: str) -> bool:
        """Insert SNIPPETS[name] at the cursor. Returns True if inserted."""
        body = schema.SNIPPETS.get(name)
        if body is None:
            return False
        cursor = self.textCursor()
        # Ensure the snippet starts on its own line: if the line up to
        # the cursor isn't blank, add a leading newline.
        col = cursor.positionInBlock()
        before = cursor.block().text()[:col]
        if before.strip():
            body = "\n" + body
        cursor.insertText(body)
        self.setTextCursor(cursor)
        return True

    def _try_expand_snippet_at_cursor(self) -> bool:
        """If the word immediately before the cursor (at line start) is
        a snippet name, replace it with the expanded template and
        return True. Otherwise return False."""
        cursor = self.textCursor()
        line = cursor.block().text()
        col = cursor.positionInBlock()
        before = line[:col]
        stripped = before.strip()
        if not stripped or stripped not in schema.SNIPPETS:
            return False
        # Replace the typed word (and any leading whitespace) with the
        # snippet body. We delete back to the start of the line.
        cursor.movePosition(QTextCursor.MoveOperation.StartOfLine,
                            QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
        cursor.insertText(schema.SNIPPETS[stripped])
        self.setTextCursor(cursor)
        return True

    # ------------------------------------------------------------------
    # Key handling: autocompletion + snippet Tab + popup forwarding
    # ------------------------------------------------------------------
    def keyPressEvent(self, e: QKeyEvent) -> None:  # noqa: N802 -- Qt naming
        # If the completion popup is up, let it handle navigation /
        # acceptance keys; insertion is performed by the completer's
        # activated signal.
        if self._completer.popup_visible:
            if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter,
                           Qt.Key.Key_Escape, Qt.Key.Key_Tab,
                           Qt.Key.Key_Backtab):
                e.ignore()
                return

        # Ctrl+Space: force-open the popup.
        is_shortcut = (e.modifiers() & Qt.KeyboardModifier.ControlModifier
                       and e.key() == Qt.Key.Key_Space)
        if is_shortcut:
            self._completer.trigger()
            return

        # Tab at line-start over a snippet keyword -> expand template.
        # Only when no modifier is held and the popup is hidden.
        if (e.key() == Qt.Key.Key_Tab
                and not e.modifiers()
                and not self._completer.popup_visible):
            if self._try_expand_snippet_at_cursor():
                return

        super().keyPressEvent(e)

        # Auto-trigger: after typing a printable character that could
        # affect the completion context, re-run analysis. Skip if a
        # modifier other than Shift was held (avoid firing on Ctrl+v
        # paste etc.).
        text = e.text()
        if not text:
            return
        unwanted = (Qt.KeyboardModifier.ControlModifier
                    | Qt.KeyboardModifier.AltModifier
                    | Qt.KeyboardModifier.MetaModifier)
        if e.modifiers() & unwanted:
            return
        ch = text[-1]
        if ch.isalnum() or ch in self._AUTO_TRIGGER_CHARS or ch == " ":
            self._completer.trigger()
