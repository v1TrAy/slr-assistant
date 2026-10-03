
# -*- coding: utf-8 -*-
"""
slr_ui/icons.py
------------------
A small set of consistent, hand-drawn line icons (QPainter primitives,
no bundled image/SVG assets) used in the sidebar, toasts, and command
palette -- replacing generic OS stock icons with one consistent visual
language. This was one of the concrete "doesn't feel like a real app"
gaps identified in the redesign review: mixed Unicode glyphs / mixed
stock icon styles read as "hand-built" rather than "designed."

Each function returns a QIcon rendered at the given size/color. Icons
are regenerated on theme change (not cached) since "muted" vs. "accent"
color depends on the current theme and whether the item is active --
see Sidebar.refresh_nav_icons().
"""

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QPolygonF


def _new(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen()
    pen.setWidthF(max(1.4, size * 0.09))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pm, p, pen


def _finish(pm, p):
    p.end()
    return QIcon(pm)


def dashboard_icon(color_hex, size=20):
    pm, p, pen = _new(size)
    pen.setColor(QColor(color_hex))
    p.setPen(pen)
    s = size
    p.drawRoundedRect(QRectF(s * 0.12, s * 0.12, s * 0.34, s * 0.40), s * 0.06, s * 0.06)
    p.drawRoundedRect(QRectF(s * 0.54, s * 0.12, s * 0.34, s * 0.22), s * 0.06, s * 0.06)
    p.drawRoundedRect(QRectF(s * 0.54, s * 0.46, s * 0.34, s * 0.42), s * 0.06, s * 0.06)
    p.drawRoundedRect(QRectF(s * 0.12, s * 0.60, s * 0.34, s * 0.28), s * 0.06, s * 0.06)
    return _finish(pm, p)


def import_icon(color_hex, size=20):
    pm, p, pen = _new(size)
    pen.setColor(QColor(color_hex))
    p.setPen(pen)
    s = size
    p.drawLine(QPointF(s * 0.5, s * 0.10), QPointF(s * 0.5, s * 0.58))
    p.drawPolyline(QPolygonF([QPointF(s * 0.30, s * 0.40), QPointF(s * 0.5, s * 0.60), QPointF(s * 0.70, s * 0.40)]))
    p.drawPolyline(QPolygonF([QPointF(s * 0.16, s * 0.66), QPointF(s * 0.16, s * 0.84),
                              QPointF(s * 0.84, s * 0.84), QPointF(s * 0.84, s * 0.66)]))
    return _finish(pm, p)


def screening_icon(color_hex, size=20):
    pm, p, pen = _new(size)
    pen.setColor(QColor(color_hex))
    p.setPen(pen)
    s = size
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.18, s * 0.62, s * 0.64), s * 0.08, s * 0.08)
    p.drawPolyline(QPolygonF([QPointF(s * 0.28, s * 0.50), QPointF(s * 0.42, s * 0.64), QPointF(s * 0.70, s * 0.32)]))
    return _finish(pm, p)


def duplicates_icon(color_hex, size=20):
    pm, p, pen = _new(size)
    pen.setColor(QColor(color_hex))
    p.setPen(pen)
    s = size
    p.drawRoundedRect(QRectF(s * 0.36, s * 0.36, s * 0.50, s * 0.50), s * 0.07, s * 0.07)
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.14, s * 0.50, s * 0.50), s * 0.07, s * 0.07)
    return _finish(pm, p)


def reports_icon(color_hex, size=20):
    pm, p, pen = _new(size)
    pen.setColor(QColor(color_hex))
    p.setPen(pen)
    s = size
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.08, s * 0.56, s * 0.84), s * 0.05, s * 0.05)
    p.drawLine(QPointF(s * 0.34, s * 0.40), QPointF(s * 0.66, s * 0.40))
    p.drawLine(QPointF(s * 0.34, s * 0.56), QPointF(s * 0.66, s * 0.56))
    p.drawLine(QPointF(s * 0.34, s * 0.72), QPointF(s * 0.56, s * 0.72))
    return _finish(pm, p)


def search_icon(color_hex, size=16):
    pm, p, pen = _new(size)
    pen.setColor(QColor(color_hex))
    p.setPen(pen)
    s = size
    p.drawEllipse(QRectF(s * 0.10, s * 0.10, s * 0.52, s * 0.52))
    p.drawLine(QPointF(s * 0.58, s * 0.58), QPointF(s * 0.88, s * 0.88))
    return _finish(pm, p)


def check_icon(color_hex, size=14):
    pm, p, pen = _new(size)
    pen.setColor(QColor(color_hex))
    p.setPen(pen)
    s = size
    p.drawPolyline(QPolygonF([QPointF(s * 0.16, s * 0.52), QPointF(s * 0.40, s * 0.76), QPointF(s * 0.86, s * 0.22)]))
    return _finish(pm, p)


ICON_FUNCS = {
    "dashboard": dashboard_icon,
    "import": import_icon,
    "screening": screening_icon,
    "duplicates": duplicates_icon,
    "reports": reports_icon,
}
