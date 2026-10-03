
# -*- coding: utf-8 -*-
"""
slr_ui/pages/duplicates.py
-----------------------------
The Manage Duplicates page, now with a designed empty state instead of
a bare empty table when nothing is flagged.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QStackedWidget, QTableWidget, QMessageBox,
    QAbstractItemView, QStyle, QTableWidgetItem, QPushButton,
)

from ..widgets import page_header, configure_table_sizing, NumericTableWidgetItem, EmptyState
from ..icons import duplicates_icon


class ManageDuplicatesPage(QWidget):
    COLUMN_WIDTHS = {0: 55, 1: 400, 2: 65, 3: 130, 4: 55, 5: 300}

    def __init__(self, get_store, set_status, push_undo):
        super().__init__()
        self.get_store = get_store
        self.set_status = set_status
        self.push_undo = push_undo

        style = self.style()
        self.btn_refresh = QPushButton(" Refresh")
        self.btn_refresh.setObjectName("secondary")
        self.btn_refresh.setIcon(style.standardIcon(QStyle.SP_BrowserReload))
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_restore = QPushButton(" Restore as unique")
        self.btn_restore.setObjectName("success")
        self.btn_restore.setIcon(style.standardIcon(QStyle.SP_DialogResetButton))
        self.btn_restore.clicked.connect(self.restore_selected)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(14)
        outer.addLayout(page_header(
            "Manage Duplicates",
            "Every record flagged as a duplicate, and what it was matched against. "
            "If a match looks wrong, select it and restore it as unique.",
            actions=[self.btn_refresh, self.btn_restore]))

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["ID", "TITLE", "YEAR", "DATABASE", "MATCHED WITH ID", "MATCHED WITH TITLE"])
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        configure_table_sizing(self.table, stretch_col=1, widths=self.COLUMN_WIDTHS)

        self.empty_state = EmptyState(
            duplicates_icon("#8b8e9c", 40).pixmap(40, 40),
            "No duplicates flagged right now",
            "When you import new files, anything that looks like a "
            "duplicate will show up here for you to confirm or restore.",
        )
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty_state)
        outer.addWidget(self.stack, 1)

    def refresh(self):
        store = self.get_store()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        if store is None:
            self.table.setSortingEnabled(True)
            self.stack.setCurrentWidget(self.empty_state)
            return
        df = store.duplicates_df()
        self.table.setRowCount(len(df))
        for row, (_, r) in enumerate(df.iterrows()):
            pos_item = NumericTableWidgetItem(str(r["Pos"]))
            pos_item.setData(Qt.UserRole, int(r["DBID"]))
            self.table.setItem(row, 0, pos_item)
            self.table.setItem(row, 1, QTableWidgetItem(str(r["Title"])))
            self.table.setItem(row, 2, NumericTableWidgetItem(str(r["Year"])))
            self.table.setItem(row, 3, QTableWidgetItem(str(r["Database"])))
            self.table.setItem(row, 4, NumericTableWidgetItem(str(r["Matched With (Pos)"])))
            self.table.setItem(row, 5, QTableWidgetItem(str(r["Matched With (Title)"])))
        self.table.resizeRowsToContents()
        self.table.setSortingEnabled(True)
        self.stack.setCurrentWidget(self.empty_state if len(df) == 0 else self.table)

    def on_shown(self):
        self.refresh()

    def restore_selected(self):
        store = self.get_store()
        if store is None:
            return
        ids = sorted({it.data(Qt.UserRole) for it in self.table.selectedItems() if it.data(Qt.UserRole) is not None})
        if not ids:
            QMessageBox.information(self, "Nothing selected", "Select one or more rows first.")
            return
        reply = QMessageBox.question(
            self, "Confirm restore",
            f"Mark {len(ids)} records as NOT duplicates? They will reappear in the "
            f"Screening tab. This can be undone with Ctrl+Z.")
        if reply != QMessageBox.Yes:
            return
        self.push_undo()
        for rid in ids:
            store.unmark_duplicate(rid)
        self.set_status(f"Restored {len(ids)} records as unique.")
        self.refresh()
