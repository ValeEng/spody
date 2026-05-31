"""Context-aware TOML completer.

Owns a QCompleter and decides, on each trigger, which suggestion list
to feed it based on where the cursor is in the document:

  - inside `[...]`             -> section names
  - on a bare line in a section -> keys valid for that section
  - after `key = `              -> enum values for that key
                                   (or, in [batch.columns], the dotted
                                    override-target paths)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from PySide6.QtCore import QObject, QStringListModel, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QCompleter, QPlainTextEdit

from . import schema


# A small enum-ish triple so the trigger code doesn't juggle string tags.
@dataclass(frozen=True)
class Context:
    kind: str        # "section" | "key" | "value"
    section: str | None = None   # section the cursor is inside (for key/value)
    key: str | None = None       # key on the left of '=' (for value)
    prefix: str = ""             # text already typed; what the popup filters on


class TomlCompleter(QObject):
    """Glue between a QPlainTextEdit and a QCompleter. Call `trigger()`
    after relevant key presses; the completer hides itself if no match."""

    def __init__(self, editor: QPlainTextEdit) -> None:
        super().__init__(editor)
        self._editor = editor

        self._completer = QCompleter([], editor)
        self._completer.setWidget(editor)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setWrapAround(False)
        self._completer.activated.connect(self._insert_completion)

    # ------------------------------------------------------------------
    # Public API used by the editor
    # ------------------------------------------------------------------
    @property
    def popup_visible(self) -> bool:
        return self._completer.popup().isVisible()

    def hide_popup(self) -> None:
        self._completer.popup().hide()

    def popup_widget(self):
        return self._completer.popup()

    def trigger(self) -> None:
        """Analyse the cursor context and show the popup if there is at
        least one suggestion that matches the typed prefix."""
        ctx = self._analyze_context()
        if ctx is None:
            self.hide_popup()
            return

        suggestions = self._suggestions_for(ctx)
        if not suggestions:
            self.hide_popup()
            return

        model = QStringListModel(sorted(set(suggestions)), self._completer)
        self._completer.setModel(model)
        self._completer.setCompletionPrefix(ctx.prefix)
        if self._completer.completionCount() == 0:
            self.hide_popup()
            return

        # Position the popup at the cursor, sized to fit the longest entry.
        rect = self._editor.cursorRect()
        popup = self._completer.popup()
        width = (popup.sizeHintForColumn(0)
                 + popup.verticalScrollBar().sizeHint().width()
                 + 16)
        rect.setWidth(width)
        self._completer.complete(rect)

    # ------------------------------------------------------------------
    # Context detection
    # ------------------------------------------------------------------
    def _analyze_context(self) -> Context | None:
        cursor = self._editor.textCursor()
        line = cursor.block().text()
        col = cursor.positionInBlock()
        before = line[:col]

        # Section context: inside an open `[` not yet closed by `]`.
        idx_open = before.rfind("[")
        idx_close = before.rfind("]")
        if idx_open >= 0 and idx_open > idx_close:
            return Context(kind="section", prefix=before[idx_open + 1:])

        # Value context: there is an `=` to the left and the cursor is
        # past it.
        idx_eq = before.rfind("=")
        if idx_eq >= 0:
            key = before[:idx_eq].strip()
            value_part = before[idx_eq + 1:].lstrip()
            section = self._find_current_section()
            return Context(kind="value", section=section, key=key, prefix=value_part)

        # Key context: cursor on a bare identifier line (just whitespace
        # and word characters before it). This is what fires when typing
        # a new key inside a section.
        stripped = before.lstrip()
        if all(c.isalnum() or c in "_-" for c in stripped):
            section = self._find_current_section()
            if section is not None:
                return Context(kind="key", section=section, prefix=stripped)

        return None

    def _find_current_section(self) -> str | None:
        """Scan backwards from the cursor line for the last `[section]`
        header. The cursor's own line is included only if it already
        terminates with `]` (i.e. the user moved past it)."""
        block = self._editor.textCursor().block()
        while block.isValid():
            text = block.text().strip()
            if text.startswith("[") and "]" in text:
                end = text.index("]")
                inner = text[1:end].strip("[]")
                return inner
            block = block.previous()
        return None

    def _detect_object_mode(self) -> str | None:
        """Return 'spacecraft', 'debris', or None depending on which
        top-level object block (if any) is present in the document.
        Used to filter mode-specific batch targets and section
        suggestions."""
        for line in self._editor.toPlainText().splitlines():
            s = line.strip()
            if s == "[spacecraft]":
                return "spacecraft"
            if s == "[debris]":
                return "debris"
        return None

    # ------------------------------------------------------------------
    # Suggestion lists
    # ------------------------------------------------------------------
    def _suggestions_for(self, ctx: Context) -> Iterable[str]:
        if ctx.kind == "section":
            sections = schema.all_section_names_including_nested()
            mode = self._detect_object_mode()
            # Don't offer the alternate object block (e.g. don't suggest
            # [debris] in a file that already uses [spacecraft]).
            if mode == "spacecraft":
                sections = [s for s in sections if not s.startswith("debris")]
            elif mode == "debris":
                sections = [s for s in sections if not s.startswith("spacecraft")]
            return sections

        if ctx.kind == "key":
            return schema.keys_for_section(ctx.section or "")

        if ctx.kind == "value":
            if ctx.section == "batch.columns":
                mode = self._detect_object_mode()
                return [f'"{p}"' for p in schema.batch_target_paths(mode)]
            return schema.enum_for_key(ctx.section or "", ctx.key or "")

        return []

    # ------------------------------------------------------------------
    # Inserting the chosen completion
    # ------------------------------------------------------------------
    def _insert_completion(self, completion: str) -> None:
        cursor = self._editor.textCursor()
        prefix_len = len(self._completer.completionPrefix())
        cursor.movePosition(
            QTextCursor.MoveOperation.Left,
            QTextCursor.MoveMode.KeepAnchor,
            prefix_len,
        )
        cursor.insertText(completion)
        self._editor.setTextCursor(cursor)
