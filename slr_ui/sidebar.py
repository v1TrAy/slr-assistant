
# -*- coding: utf-8 -*-
"""
slr_ui/sidebar.py
--------------------
The left navigation rail: brand mark, page nav buttons using the real
line-icon set (slr_ui.icons) instead of generic stock icons or plain
dots, project name, the "Saved" autosave badge, theme toggle, and the
open/create-project button.
"""

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QButtonGroup, QStyle,
)

from .icons import ICON_FUNCS
from .widgets import SavedIndicator


class Sidebar(QFrame):
    PAGES = [
        ("dashboard", "Dashboard"),
        ("import", "Import & Merge"),
        ("screening", "Screening"),
        ("duplicates", "Manage Duplicates"),
        ("reports", "Reports & Export"),
    ]

    def __init__(self, on_nav, on_open_project, on_toggle_theme, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebar")
        self.setFixedWidth(216)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 14, 12, 14)
        lay.setSpacing(6)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(9)
        brand_mark = QLabel("SL")
        brand_mark.setObjectName("brandMark")
        brand_mark.setFixedSize(26, 26)
        brand_mark.setAlignment(Qt.AlignCenter)
        brand_name = QLabel("SLR Assistant")
        brand_name.setObjectName("brandName")
        brand_row.addWidget(brand_mark)
        brand_row.addWidget(brand_name)
        brand_row.addStretch(1)
        lay.addLayout(brand_row)
        lay.addSpacing(10)

        self.buttons = {}
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        style = self.style()
        self._icon_muted = "#8b8e9c"
        self._icon_active = "#6d8dff"
        for key, label in self.PAGES:
            b = QPushButton(f"  {label}")
            b.setObjectName("navButton")
            b.setCheckable(True)
            b.setIcon(ICON_FUNCS[key](self._icon_muted))
            b.setIconSize(QSize(18, 18))
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda checked=False, k=key: on_nav(k))
            b.toggled.connect(lambda checked, btn=b, k=key: self._recolor_one(btn, k, checked))
            self.group.addButton(b)
            lay.addWidget(b)
            self.buttons[key] = b

        lay.addStretch(1)

        self.project_lbl = QLabel("No project open")
        self.project_lbl.setObjectName("pageSubtitle")
        self.project_lbl.setWordWrap(True)
        lay.addWidget(self.project_lbl)

        self.saved_indicator = SavedIndicator()
        self.saved_indicator.setVisible(False)
        lay.addWidget(self.saved_indicator)

        self.btn_theme = QPushButton("Switch theme")
        self.btn_theme.setObjectName("secondary")
        self.btn_theme.clicked.connect(on_toggle_theme)
        lay.addWidget(self.btn_theme)

        self.btn_open = QPushButton(" Open / create project\u2026")
        self.btn_open.setIcon(style.standardIcon(QStyle.SP_DialogOpenButton))
        self.btn_open.clicked.connect(on_open_project)
        lay.addWidget(self.btn_open)

    def _recolor_one(self, btn, key, checked):
        color = self._icon_active if checked else self._icon_muted
        btn.setIcon(ICON_FUNCS[key](color))

    def refresh_nav_icons(self, accent_hex, muted_hex):
        """Regenerate every nav icon for the current theme (called from
        MainWindow.apply_theme on every dark/light toggle)."""
        self._icon_muted = muted_hex
        self._icon_active = accent_hex
        for key, b in self.buttons.items():
            color = accent_hex if b.isChecked() else muted_hex
            b.setIcon(ICON_FUNCS[key](color))

    def set_project(self, name):
        self.project_lbl.setText(name or "No project open")
        enabled = name is not None
        for b in self.buttons.values():
            b.setEnabled(enabled)
        self.btn_open.setEnabled(True)
        self.saved_indicator.setVisible(enabled)

    def set_active(self, key):
        if key is None:
            self.group.setExclusive(False)
            for b in self.buttons.values():
                b.setChecked(False)
            self.group.setExclusive(True)
            return
        b = self.buttons.get(key)
        if b is not None:
            b.setChecked(True)  # programmatic; does not emit clicked

    def set_theme_text(self, text):
        self.btn_theme.setText(text)
