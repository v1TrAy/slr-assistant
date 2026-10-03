
# -*- coding: utf-8 -*-
"""
slr_ui/pages/reports.py
--------------------------
The Reports & Export page and the sheet-mapping dialog used before
syncing into an existing tracker workbook.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QFrame, QLabel, QPushButton,
    QComboBox, QFormLayout, QDialogButtonBox,
)

import slr_core
from ..widgets import page_header


class SheetMappingDialog(QDialog):
    ROLES = [
        ("raw", "Raw import sheet", ["raw", "import"],
         "Where new records (ID, Database, Title, Authors, Year, Journal, DOI, Is Duplicate?, "
         "Duplicate of ID, Duplicate of Title) are listed."),
        ("ta", "Title/Abstract screening sheet", ["title", "abstract", "screen"],
         "Rows are matched by an ID column; Decision/Reason columns are found or created automatically."),
        ("ft", "Full-text screening sheet", ["full", "text", "screen"],
         "Rows are matched by an ID column; Decision/Reason columns are found or created automatically."),
    ]

    def __init__(self, sheet_names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Match sheets in your tracker file")
        self.setMinimumWidth(460)
        self.combos = {}

        layout = QVBoxLayout(self)
        info = QLabel(
            "Pick which existing sheet corresponds to each role, or type a new sheet name. "
            "Column names/order inside each sheet don't need to match ours - matching columns "
            "are found automatically, and missing ones are created.")
        info.setWordWrap(True)
        info.setObjectName("pageSubtitle")
        layout.addWidget(info)

        form = QFormLayout()
        form.setSpacing(10)
        for key, label, keywords, hint in self.ROLES:
            combo = QComboBox()
            combo.setEditable(True)
            combo.addItems(sheet_names)
            combo.setCurrentText(slr_core.guess_sheet_name(sheet_names, keywords))
            combo.setToolTip(hint)
            self.combos[key] = combo
            row_label = QLabel(label)
            row_label.setToolTip(hint)
            form.addRow(row_label, combo)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_selection(self):
        return (self.combos["raw"].currentText().strip(),
                self.combos["ta"].currentText().strip(),
                self.combos["ft"].currentText().strip())


class ReportsPage(QWidget):
    def __init__(self, get_store, on_export, on_sync):
        super().__init__()
        self.get_store = get_store
        self.on_export = on_export
        self.on_sync = on_sync

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(16)
        outer.addLayout(page_header(
            "Reports & Export",
            "Everything that leaves the app: PRISMA workbooks, tracker sync, and the "
            "current project summary."))

        c1 = QFrame()
        c1.setObjectName("card")
        l1 = QVBoxLayout(c1)
        l1.setContentsMargins(18, 16, 18, 16)
        t1 = QLabel("PRISMA workbook")
        t1.setObjectName("pageTitle")
        d1 = QLabel(
            "A fresh Excel report with All_Records, Screening, Data_Extraction and "
            "PRISMA_Summary sheets, generated from the current project state.")
        d1.setObjectName("pageSubtitle")
        d1.setWordWrap(True)
        b1 = QPushButton(" Export PRISMA workbook\u2026")
        b1.clicked.connect(on_export)
        l1.addWidget(t1)
        l1.addWidget(d1)
        l1.addWidget(b1, 0, Qt.AlignLeft)
        outer.addWidget(c1)

        c2 = QFrame()
        c2.setObjectName("card")
        l2 = QVBoxLayout(c2)
        l2.setContentsMargins(18, 16, 18, 16)
        t2 = QLabel("Sync into an existing tracker")
        t2.setObjectName("pageTitle")
        d2 = QLabel(
            "Overwrites the managed columns of your own tracker workbook \u2014 columns and "
            "sheets are matched by name and created if missing. A timestamped backup of the "
            "original is always kept.")
        d2.setObjectName("pageSubtitle")
        d2.setWordWrap(True)
        b2 = QPushButton(" Choose tracker workbook\u2026")
        b2.setObjectName("secondary")
        b2.clicked.connect(on_sync)
        l2.addWidget(t2)
        l2.addWidget(d2)
        l2.addWidget(b2, 0, Qt.AlignLeft)
        outer.addWidget(c2)

        c3 = QFrame()
        c3.setObjectName("card")
        l3 = QVBoxLayout(c3)
        l3.setContentsMargins(18, 16, 18, 16)
        t3 = QLabel("Project summary")
        t3.setObjectName("pageTitle")
        self.summary = QLabel()
        self.summary.setObjectName("pageSubtitle")
        self.summary.setWordWrap(True)
        l3.addWidget(t3)
        l3.addWidget(self.summary)
        outer.addWidget(c3)

        outer.addStretch()

    def refresh(self):
        store = self.get_store()
        if store is None:
            self.summary.setText("No project open.")
            return
        c = store.prisma_counts()
        self.summary.setText(" \u2022 ".join(f"{k}: {v}" for k, v in c.items()))

    def on_shown(self):
        self.refresh()
