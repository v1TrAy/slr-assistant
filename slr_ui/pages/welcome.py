
# -*- coding: utf-8 -*-
"""
slr_ui/pages/welcome.py
--------------------------
The landing page shown when no project is open.
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton


class WelcomePage(QWidget):
    def __init__(self, on_open, on_open_path, get_recents, parent=None):
        super().__init__(parent)
        self.on_open_path = on_open_path
        self.get_recents = get_recents
        outer = QVBoxLayout(self)
        outer.setContentsMargins(48, 48, 48, 48)
        outer.addStretch(2)

        title = QLabel("SLR Assistant")
        title.setObjectName("heroTitle")
        title.setAlignment(Qt.AlignCenter)
        outer.addWidget(title)

        sub = QLabel("Import, deduplicate, screen and report systematic literature reviews.")
        sub.setObjectName("heroSub")
        sub.setAlignment(Qt.AlignCenter)
        outer.addWidget(sub)

        feats = QLabel("Any database export \u2022 fuzzy + exact dedup \u2022 keyboard screening \u2022 PRISMA flow diagram")
        feats.setObjectName("pageSubtitle")
        feats.setAlignment(Qt.AlignCenter)
        outer.addWidget(feats)

        outer.addSpacing(18)
        btn = QPushButton("+ Open or create a project\u2026")
        btn.setMinimumWidth(260)
        btn.setMinimumHeight(42)
        btn.clicked.connect(on_open)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(btn)
        row.addStretch()
        outer.addLayout(row)

        outer.addStretch(1)
        reclbl = QLabel("Recent projects")
        reclbl.setObjectName("pageSubtitle")
        outer.addWidget(reclbl, 0, Qt.AlignHCenter)
        self.recent_box = QVBoxLayout()
        self.recent_box.setSpacing(6)
        outer.addLayout(self.recent_box)
        outer.addStretch(2)

    def refresh(self):
        while self.recent_box.count():
            item = self.recent_box.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        recents = self.get_recents()
        if not recents:
            empty = QLabel("No recent projects yet.")
            empty.setObjectName("pageSubtitle")
            self.recent_box.addWidget(empty, 0, Qt.AlignHCenter)
            return
        for p in recents[:5]:
            b = QPushButton(f"{os.path.basename(p)}")
            b.setObjectName("recentItem")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda checked=False, path=p: self.on_open_path(path))
            self.recent_box.addWidget(b, 0, Qt.AlignHCenter)
