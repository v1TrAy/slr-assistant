
# -*- coding: utf-8 -*-
"""
slr_ui/theme.py
-----------------
App-wide constants, the dark/light color token dicts, the QSS stylesheet
generator, and small theme-related helpers.
"""

import os
import logging
import logging.handlers

from PySide6.QtCore import Qt, QStandardPaths
from PySide6.QtGui import QColor, QPixmap, QPainter, QIcon

ORG_NAME = "SLRAssistant"
APP_NAME = "SLRAssistant"

ACCENT = "#6d8dff"
ACCENT_HOVER = "#5a79f0"
ACCENT_PRESS = "#4a66d9"

DECISION_TA_OPTIONS = ["", "Include", "Exclude", "Maybe"]
DECISION_FT_OPTIONS = ["", "Include", "Exclude"]

DECISION_COLORS_DARK = {"Include": QColor("#173829"), "Exclude": QColor("#3a1f22"),
                        "Maybe": QColor("#3a331a"), "": QColor("#1a1b21")}
DECISION_COLORS_LIGHT = {"Include": QColor("#dcf5e6"), "Exclude": QColor("#fbe1e2"),
                         "Maybe": QColor("#fcf1cf"), "": QColor("#ffffff")}

_DARK = dict(
    win="#131419", base="#1a1b22", raised="#202129", raised2="#262834",
    sidebar="#0e0f13", border="#262834", text="#e6e8ee", title="#ffffff",
    muted="#8b8e9c", accent=ACCENT, accent_h=ACCENT_HOVER, accent_p=ACCENT_PRESS,
    accent_text="#0d0e12", accent_soft="#272d45",
    sel="#31406b", alt="#1e1f27", disabled_bg="#24252e", disabled_fg="#565863",
    ok_bg="#16382a", ok_fg="#6ee7a8", no_bg="#3d2023", no_fg="#ff97a0",
    maybe_bg="#3a341d", maybe_fg="#ffd97a", pill_off_bg="#24252e",
    pill_off_fg="#8b8e9c", field="#1e1f27",
    scroll_track="#20212b", scroll="#4a4c5c", scroll_h="#5f6376", scroll_p="#6d8dff",
)

_LIGHT = dict(
    win="#f4f5f8", base="#ffffff", raised="#eef0f6", raised2="#e2e5ee",
    sidebar="#eceef4", border="#dfe3ec", text="#1b1d24", title="#101116",
    muted="#666a78",
    accent="#3f5bd9", accent_h="#3450c4", accent_p="#2c46ac", accent_text="#ffffff",
    accent_soft="#eceffb",
    sel="#d9e2ff", alt="#f6f7fb", disabled_bg="#e7e9f0", disabled_fg="#9aa0ad",
    ok_bg="#dcf5e6", ok_fg="#177245", no_bg="#fbe1e2", no_fg="#a1262d",
    maybe_bg="#fcf1cf", maybe_fg="#8a6d1a", pill_off_bg="#e9ebf2",
    pill_off_fg="#666a78", field="#ffffff",
    scroll_track="#e9ebf2", scroll="#b7bccb", scroll_h="#98a0b5", scroll_p="#3f5bd9",
)


def build_qss(c):
    return f"""
* {{ outline: none; }}
QMainWindow, QWidget {{ background-color: {c['win']}; color: {c['text']};
    font-family: "Segoe UI", "Inter", Arial; font-size: 13px; }}
QDialog {{ background-color: {c['base']}; }}
QToolTip {{ background-color: {c['base']}; color: {c['text']}; border: 1px solid {c['border']}; padding: 4px; }}
QLabel {{ background: transparent; }}
QCheckBox {{ background: transparent; spacing: 8px; }}
QScrollArea, QStackedWidget {{ background: transparent; border: none; }}

QFrame {{ background: transparent; }}
QFrame#sidebar {{ background-color: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
QFrame#card {{ background-color: {c['base']}; border: 1px solid {c['border']}; border-radius: 14px; }}
QFrame#panel {{ background-color: {c['base']}; border: 1px solid {c['border']}; border-radius: 12px; }}
QFrame#dropArea {{ background-color: {c['raised']}; border: 2px dashed {c['border']}; border-radius: 12px; }}
QFrame#dropArea[dragOver="true"] {{ border-color: {c['accent']}; background-color: {c['sel']}; }}
QFrame#focusCard {{ background-color: {c['base']}; border: 1px solid {c['border']}; border-radius: 14px; }}

QLabel#brand {{ font-size: 15px; font-weight: 800; color: {c['title']}; padding: 4px 2px; }}
QLabel#pageTitle {{ font-size: 18px; font-weight: 800; color: {c['title']}; }}
QLabel#pageSubtitle {{ font-size: 12px; color: {c['muted']}; }}
QLabel#heroTitle {{ font-size: 30px; font-weight: 800; color: {c['title']}; }}
QLabel#heroSub {{ font-size: 14px; color: {c['muted']}; }}
QLabel#emptyState {{ font-size: 15px; color: {c['muted']}; }}
QLabel#focusTitle {{ font-size: 17px; font-weight: 800; color: {c['title']}; }}
QLabel#cardValue {{ font-size: 28px; font-weight: 800; color: {c['accent']}; }}
QLabel#cardTitle {{ font-size: 11px; color: {c['muted']}; font-weight: 600; }}
QLabel#badge {{ background-color: {c['raised']}; border-radius: 9px; padding: 3px 10px; font-size: 11px; font-weight: 700; color: {c['text']}; }}
QLabel#pill {{ border-radius: 9px; padding: 3px 10px; font-size: 11px; font-weight: 700; }}
QLabel#pill[state="Include"] {{ background-color: {c['ok_bg']}; color: {c['ok_fg']}; }}
QLabel#pill[state="Exclude"] {{ background-color: {c['no_bg']}; color: {c['no_fg']}; }}
QLabel#pill[state="Maybe"] {{ background-color: {c['maybe_bg']}; color: {c['maybe_fg']}; }}
QLabel#pill[state=""] {{ background-color: {c['pill_off_bg']}; color: {c['pill_off_fg']}; }}

QPushButton {{ background-color: {c['accent']}; color: {c['accent_text']}; border: none; border-radius: 8px; padding: 8px 16px; font-weight: 600; }}
QPushButton:hover {{ background-color: {c['accent_h']}; }}
QPushButton:pressed {{ background-color: {c['accent_p']}; }}
QPushButton:disabled {{ background-color: {c['disabled_bg']}; color: {c['disabled_fg']}; }}
QPushButton#secondary {{ background-color: {c['raised']}; color: {c['text']}; border: 1px solid {c['border']}; }}
QPushButton#secondary:hover {{ background-color: {c['raised2']}; border-color: {c['accent']}; }}
QPushButton#danger {{ background-color: #e5484d; color: white; }}
QPushButton#danger:hover {{ background-color: #c3383e; }}
QPushButton#success {{ background-color: #2ea36f; color: white; }}
QPushButton#success:hover {{ background-color: #279061; }}
QPushButton#navButton {{ background: transparent; color: {c['muted']}; border: none; border-radius: 8px; padding: 10px 14px; text-align: left; font-weight: 600; }}
QPushButton#navButton:hover {{ background-color: {c['raised']}; color: {c['text']}; }}
QPushButton#navButton:checked {{ background-color: {c['accent_soft']}; color: {c['accent']}; font-weight: 700; }}
QPushButton#recentItem {{ background-color: {c['base']}; color: {c['text']}; border: 1px solid {c['border']}; border-radius: 10px; padding: 10px 14px; text-align: left; }}
QPushButton#recentItem:hover {{ border: 1px solid {c['accent']}; }}

QMenuBar {{ background-color: {c['win']}; color: {c['text']}; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 5px; }}
QMenuBar::item:selected {{ background-color: {c['accent']}; color: {c['accent_text']}; }}
QMenu {{ background-color: {c['base']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 20px; border-radius: 5px; }}
QMenu::item:selected {{ background-color: {c['accent']}; color: {c['accent_text']}; }}

QTableWidget {{ background-color: {c['base']}; alternate-background-color: {c['alt']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QTableWidget::item {{ padding: 6px; }}
QTableWidget::item:selected {{ background-color: {c['sel']}; color: {c['text']}; }}
QHeaderView::section {{ background-color: {c['raised']}; color: {c['muted']}; padding: 8px; border: none; border-right: 1px solid {c['border']}; border-bottom: 1px solid {c['border']}; font-weight: 700; }}

QLineEdit, QComboBox {{ background-color: {c['field']}; border: 1px solid {c['border']}; border-radius: 8px; padding: 6px 10px; min-height: 18px; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {c['accent']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background-color: {c['base']}; color: {c['text']}; selection-background-color: {c['accent']}; selection-color: white; }}

QProgressBar {{ background-color: {c['raised']}; border-radius: 8px; text-align: center; color: {c['text']}; min-height: 20px; border: 1px solid {c['border']}; font-weight: 600; }}
QProgressBar::chunk {{ background-color: {c['accent']}; border-radius: 8px; }}

QStatusBar {{ background-color: {c['sidebar']}; color: {c['muted']}; border-top: 1px solid {c['border']}; }}

QScrollBar:vertical {{
    background: {c['scroll_track']}; width: 14px; margin: 0px;
    border-radius: 7px; border: 1px solid {c['border']};
}}
QScrollBar::handle:vertical {{
    background: {c['scroll']}; border-radius: 6px; min-height: 36px; margin: 2px;
}}
QScrollBar::handle:vertical:hover {{ background: {c['scroll_h']}; }}
QScrollBar::handle:vertical:pressed {{ background: {c['scroll_p']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; background: transparent; border: none; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{
    background: {c['scroll_track']}; height: 14px; margin: 0px;
    border-radius: 7px; border: 1px solid {c['border']};
}}
QScrollBar::handle:horizontal {{
    background: {c['scroll']}; border-radius: 6px; min-width: 36px; margin: 2px;
}}
QScrollBar::handle:horizontal:hover {{ background: {c['scroll_h']}; }}
QScrollBar::handle:horizontal:pressed {{ background: {c['scroll_p']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; background: transparent; border: none; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

QAbstractScrollArea::corner {{ background: {c['scroll_track']}; border: none; }}

QHeaderView::section {{ font-size: 10.5px; letter-spacing: 0.5px; }}
QHeaderView::section:hover {{ background-color: {c['raised2']}; }}

QComboBox QAbstractItemView::item {{ padding: 5px 8px; min-height: 22px; }}
QComboBox QAbstractItemView::item:hover {{ background-color: {c['raised']}; }}

QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {c['border']}; background: {c['field']};
}}
QCheckBox::indicator:hover {{ border: 1px solid {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']}; border: 1px solid {c['accent']}; }}
QCheckBox::indicator:checked:hover {{ background: {c['accent_h']}; border: 1px solid {c['accent_h']}; }}

QSlider::groove:horizontal {{ height: 4px; background: {c['border']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::add-page:horizontal {{ background: {c['border']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
    background: {c['accent']}; border: 2px solid {c['base']};
}}
QSlider::handle:horizontal:hover {{ background: {c['accent_h']}; }}
QSlider::handle:horizontal:pressed {{ background: {c['accent_p']}; }}

QFrame#savedDot {{ background-color: {c['ok_fg']}; border-radius: 4px; border: none; }}
QLabel#savedLabel {{ color: {c['ok_fg']}; font-size: 11px; font-weight: 700; }}

QLabel#brandName {{ font-size: 15px; font-weight: 800; color: {c['title']}; }}
QLabel#brandMark {{ background-color: {c['accent']}; color: {c['accent_text']}; border-radius: 8px;
    font-weight: 800; font-size: 12px; }}

QLabel#cardTitleCaps {{ font-size: 10px; color: {c['muted']}; font-weight: 700; letter-spacing: 0.5px; }}

/* ---- Toast notifications (slr_ui/toast.py) ----
   Non-blocking success/info notices, replacing a blocking QMessageBox
   for routine actions (merge, delete, restore, export, sync succeeded). */
QWidget#toast {{
    background-color: {c['raised']}; border: 1px solid {c['border']};
    border-radius: 10px;
}}
QLabel#toastIcon {{ background-color: {c['ok_bg']}; color: {c['ok_fg']}; border-radius: 10px; }}
QLabel#toastTitle {{ color: {c['text']}; font-size: 12.5px; font-weight: 700; }}
QLabel#toastSubtitle {{ color: {c['muted']}; font-size: 11.5px; }}

/* ---- Command palette (slr_ui/command_palette.py) ----
   Ctrl+K "jump to a page or action" -- one of the strongest signals of
   a finished, professional desktop tool. */
QDialog#commandPalette {{
    background-color: {c['base']}; border: 1px solid {c['border']}; border-radius: 12px;
}}
QLineEdit#paletteInput {{
    background: transparent; border: none; border-bottom: 1px solid {c['border']};
    border-radius: 0px; padding: 14px 16px; font-size: 14px; color: {c['text']};
}}
QListWidget#paletteList {{
    background: transparent; border: none; padding: 6px; font-size: 13px; color: {c['muted']};
    outline: none;
}}
QListWidget#paletteList::item {{ padding: 8px 10px; border-radius: 7px; }}
QListWidget#paletteList::item:selected {{ background-color: {c['raised2']}; color: {c['text']}; }}
"""


def repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)


def configure_logging():
    try:
        log_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        os.makedirs(log_dir, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            os.path.join(log_dir, "slr-assistant.log"), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.basicConfig(level=logging.INFO, handlers=[handler])
    except OSError:
        logging.basicConfig(level=logging.INFO)


def dot_icon(color_hex, size=9):
    """Legacy plain-dot icon, kept for anything not yet migrated to the
    real line-icon set in slr_ui/icons.py."""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(color_hex))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(0, 0, size, size)
    painter.end()
    return QIcon(pm)
