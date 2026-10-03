
# -*- coding: utf-8 -*-
"""
slr_ui/widgets.py
-------------------
Small, reusable widgets and layout helpers shared across multiple pages.
"""

import os

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QTableWidgetItem,
    QStyledItemDelegate, QComboBox, QMessageBox, QGraphicsOpacityEffect,
    QAbstractItemView, QSizePolicy, QHeaderView,
)

import slr_core
from .theme import repolish


def page_header(title, subtitle=None, actions=()):
    box = QVBoxLayout()
    box.setSpacing(2)
    row = QHBoxLayout()
    left = QVBoxLayout()
    left.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("pageTitle")
    left.addWidget(t)
    if subtitle:
        s = QLabel(subtitle)
        s.setObjectName("pageSubtitle")
        s.setWordWrap(True)
        left.addWidget(s)
    row.addLayout(left, 1)
    for a in actions:
        row.addWidget(a)
    box.addLayout(row)
    return box


def configure_table_sizing(table, stretch_col, widths):
    table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
    table.setShowGrid(False)
    table.verticalHeader().setDefaultSectionSize(34)
    table.verticalHeader().setVisible(False)
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(50)
    for col, width in widths.items():
        header.setSectionResizeMode(col, QHeaderView.Interactive)
        table.setColumnWidth(col, max(width, 160) if col == stretch_col else width)
    table.setWordWrap(True)
    table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)


class NumericTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        selfval = self._numeric_value()
        otherval = other._numeric_value() if isinstance(other, NumericTableWidgetItem) else None
        if selfval is not None and otherval is not None:
            return selfval < otherval
        return self.text() < other.text()

    def _numeric_value(self):
        text = self.text().strip().rstrip("%").replace(",", "")
        try:
            return float(text)
        except ValueError:
            return None


class ComboBoxDelegate(QStyledItemDelegate):
    ID_COLUMN = 0

    def __init__(self, options, on_committed=None, parent=None):
        super().__init__(parent)
        self.options = options
        self.on_committed = on_committed

    def createEditor(self, parent, option, index):
        combo = QComboBox(parent)
        combo.addItems(self.options)
        combo.activated.connect(lambda _: self.commit(combo))
        return combo

    def commit(self, combo):
        self.commitData.emit(combo)
        self.closeEditor.emit(combo)

    def setEditorData(self, editor, index):
        text = index.data(Qt.EditRole) or index.data(Qt.DisplayRole) or ""
        pos = editor.findText(text)
        editor.setCurrentIndex(pos if pos >= 0 else 0)
        editor.showPopup()

    def setModelData(self, editor, model, index):
        value = editor.currentText()
        dbid = None
        id_index = model.index(index.row(), self.ID_COLUMN)
        if id_index.isValid():
            dbid = id_index.data(Qt.UserRole)
        model.setData(index, value, Qt.EditRole)
        if self.on_committed:
            self.on_committed(dbid, value)

    def updateEditorGeometry(self, editor, option, index):
        editor.setGeometry(option.rect)


class DropArea(QFrame):
    def __init__(self, on_files, parent=None):
        super().__init__(parent)
        self.setObjectName("dropArea")
        self.on_files = on_files
        self.setAcceptDrops(True)
        self.setMinimumHeight(84)
        lay = QVBoxLayout(self)
        main = QLabel("Drag & drop database export files here")
        main.setObjectName("pageSubtitle")
        main.setAlignment(Qt.AlignCenter)
        sub = QLabel(".csv / .xlsx / .ris \u2014 or use \u201cSelect files\u2026\u201d")
        sub.setObjectName("pageSubtitle")
        sub.setAlignment(Qt.AlignCenter)
        lay.addWidget(main)
        lay.addWidget(sub)

    def _set_drag(self, on):
        self.setProperty("dragOver", on)
        repolish(self)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if any(os.path.splitext(p)[1].lower() in slr_core.SUPPORTED_EXTENSIONS for p in paths):
                event.acceptProposedAction()
                self._set_drag(True)
                return
        event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drag(False)

    def dropEvent(self, event):
        self._set_drag(False)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        valid = [p for p in paths if os.path.splitext(p)[1].lower() in slr_core.SUPPORTED_EXTENSIONS]
        if valid:
            self.on_files(valid)
        else:
            QMessageBox.warning(self, "Unsupported files",
                                "Only {} files are supported.".format(", ".join(slr_core.SUPPORTED_EXTENSIONS)))


class SavedIndicator(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 4, 2, 4)
        lay.setSpacing(6)

        self.dot = QFrame()
        self.dot.setObjectName("savedDot")
        self.dot.setFixedSize(7, 7)
        self._opacity_effect = QGraphicsOpacityEffect(self.dot)
        self._opacity_effect.setOpacity(1.0)
        self.dot.setGraphicsEffect(self._opacity_effect)

        self._anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._anim.setDuration(1600)
        self._anim.setStartValue(1.0)
        self._anim.setKeyValueAt(0.5, 0.35)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.InOutSine)
        self._anim.setLoopCount(-1)
        self._anim.start()

        label = QLabel("Saved")
        label.setObjectName("savedLabel")

        lay.addWidget(self.dot)
        lay.addWidget(label)
        lay.addStretch(1)
        self.setToolTip("All changes are saved automatically as you make them.")


class EmptyState(QWidget):
    """A designed 'nothing here yet' placeholder, replacing a bare empty
    table/list. One of the concrete "doesn't feel like a real app" gaps:
    a screen that's just blank until data exists reads as unfinished.
    """

    def __init__(self, icon_pixmap, title, subtitle, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 60, 40, 60)
        lay.setSpacing(8)
        lay.setAlignment(Qt.AlignCenter)

        icon_lbl = QLabel()
        icon_lbl.setPixmap(icon_pixmap)
        icon_lbl.setAlignment(Qt.AlignCenter)
        lay.addWidget(icon_lbl)

        title_lbl = QLabel(title)
        title_lbl.setObjectName("pageSubtitle")
        title_lbl.setAlignment(Qt.AlignCenter)
        title_lbl.setStyleSheet("font-weight: 700; font-size: 13px;")
        lay.addWidget(title_lbl)

        sub_lbl = QLabel(subtitle)
        sub_lbl.setObjectName("pageSubtitle")
        sub_lbl.setAlignment(Qt.AlignCenter)
        sub_lbl.setWordWrap(True)
        sub_lbl.setMaximumWidth(320)
        lay.addWidget(sub_lbl, 0, Qt.AlignCenter)
