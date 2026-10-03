
# -*- coding: utf-8 -*-
"""
slr_ui/command_palette.py
----------------------------
A Ctrl+K "jump to" command palette: type to filter a flat list of pages
and actions, Enter or click to run one. A small feature, but one of the
strongest "this is a professional tool" signals in modern desktop and
web apps -- and cheap to build on top of Qt's existing widgets.
"""

from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem


class CommandPalette(QDialog):
    def __init__(self, actions, parent=None):
        """`actions` is a list of (label, callback) tuples, callback takes
        no arguments and is invoked when that entry is chosen."""
        super().__init__(parent)
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("commandPalette")
        self.setFixedWidth(460)
        self._all_actions = actions
        self._filtered = list(actions)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.input = QLineEdit()
        self.input.setObjectName("paletteInput")
        self.input.setPlaceholderText("Jump to a page or action\u2026")
        self.input.textChanged.connect(self._on_filter_changed)
        self.input.installEventFilter(self)
        lay.addWidget(self.input)

        self.list = QListWidget()
        self.list.setObjectName("paletteList")
        self.list.itemActivated.connect(self._activate_item)
        lay.addWidget(self.list)

        self._populate(self._filtered)

    def showEvent(self, event):
        super().showEvent(event)
        self.input.setFocus()
        self.input.clear()
        self._filtered = list(self._all_actions)
        self._populate(self._filtered)

    def _populate(self, actions):
        self.list.clear()
        for label, _ in actions:
            self.list.addItem(QListWidgetItem(label))
        if self.list.count():
            self.list.setCurrentRow(0)

    def _on_filter_changed(self, text):
        text = text.strip().lower()
        self._filtered = [(label, cb) for label, cb in self._all_actions if text in label.lower()]
        self._populate(self._filtered)

    def _activate_item(self, item):
        row = self.list.row(item)
        if 0 <= row < len(self._filtered):
            _, callback = self._filtered[row]
            self.accept()
            callback()

    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                row = self.list.currentRow()
                new_row = row + 1 if key == Qt.Key_Down else row - 1
                new_row = max(0, min(self.list.count() - 1, new_row))
                self.list.setCurrentRow(new_row)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                item = self.list.currentItem()
                if item is not None:
                    self._activate_item(item)
                return True
            if key == Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)
