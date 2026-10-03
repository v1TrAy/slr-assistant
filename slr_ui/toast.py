
# -*- coding: utf-8 -*-
"""
slr_ui/toast.py
------------------
Non-blocking "toast" notifications, replacing blocking QMessageBox
confirmations for routine, successful actions (merge, export, sync,
diagram saved). This was a concrete "doesn't feel like a real app" gap:
every routine action forced a modal dialog the user had to click
through. Real desktop apps reserve modal dialogs for things that need a
decision (destructive confirmations, errors) and use a transient,
self-dismissing notice for "this succeeded."

ToastManager owns a small stack of ToastWidget instances positioned in
the bottom-right corner of a host window, each auto-dismissing after a
few seconds with a fade animation. Errors/warnings still go through
QMessageBox (see main_window.py) -- toasts are for success/info only,
by design, so a failure is never something the user could miss because
it silently expired.
"""

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QGraphicsOpacityEffect

from .icons import check_icon


class ToastWidget(QWidget):
    DURATION_MS = 3200
    FADE_MS = 220

    def __init__(self, title, subtitle, accent_hex, parent=None):
        super().__init__(parent)
        self.setObjectName("toast")
        self.setFixedWidth(300)
        self.setAttribute(Qt.WA_StyledBackground, True)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 14, 10)
        lay.setSpacing(10)

        icon_badge = QLabel()
        icon_badge.setObjectName("toastIcon")
        icon_badge.setFixedSize(20, 20)
        icon_badge.setPixmap(check_icon(accent_hex, 11).pixmap(11, 11))
        icon_badge.setAlignment(Qt.AlignCenter)
        lay.addWidget(icon_badge)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title_lbl = QLabel(title)
        title_lbl.setObjectName("toastTitle")
        title_lbl.setWordWrap(True)
        text_col.addWidget(title_lbl)
        if subtitle:
            sub_lbl = QLabel(subtitle)
            sub_lbl.setObjectName("toastSubtitle")
            sub_lbl.setWordWrap(True)
            text_col.addWidget(sub_lbl)
        lay.addLayout(text_col, 1)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

    def play_in_then_schedule_out(self, on_finished):
        fade_in = QPropertyAnimation(self._opacity, b"opacity", self)
        fade_in.setDuration(self.FADE_MS)
        fade_in.setStartValue(0.0)
        fade_in.setEndValue(1.0)
        fade_in.setEasingCurve(QEasingCurve.OutCubic)
        fade_in.start()
        self._fade_in_anim = fade_in

        def start_fade_out():
            fade_out = QPropertyAnimation(self._opacity, b"opacity", self)
            fade_out.setDuration(self.FADE_MS)
            fade_out.setStartValue(1.0)
            fade_out.setEndValue(0.0)
            fade_out.setEasingCurve(QEasingCurve.InCubic)
            fade_out.finished.connect(on_finished)
            fade_out.start()
            self._fade_out_anim = fade_out

        QTimer.singleShot(self.DURATION_MS, start_fade_out)


class ToastManager:
    """Owns the stack of currently-visible toasts inside `host` (normally
    the MainWindow's central widget). Call show_success() from anywhere
    that used to pop a QMessageBox.information() for a routine, non-error
    outcome."""

    MARGIN = 16
    GAP = 8

    def __init__(self, host_widget, accent_hex="#6d8dff"):
        self.host = host_widget
        self.accent_hex = accent_hex
        self._toasts = []

    def set_accent(self, accent_hex):
        self.accent_hex = accent_hex

    def show_success(self, title, subtitle=""):
        toast = ToastWidget(title, subtitle, self.accent_hex, parent=self.host)
        toast.adjustSize()
        self._toasts.append(toast)
        self._reflow()
        toast.show()
        toast.raise_()

        def remove():
            if toast in self._toasts:
                self._toasts.remove(toast)
            toast.deleteLater()
            self._reflow()

        toast.play_in_then_schedule_out(remove)

    def _reflow(self):
        """Stack toasts bottom-up in the host's bottom-right corner."""
        if not self.host:
            return
        host_rect = self.host.rect()
        y = host_rect.height() - self.MARGIN
        for toast in reversed(self._toasts):
            toast.adjustSize()
            y -= toast.height()
            x = host_rect.width() - self.MARGIN - toast.width()
            toast.move(QPoint(max(0, x), max(0, y)))
            y -= self.GAP

    def reposition(self):
        """Call from the host's resizeEvent so toasts track the window."""
        self._reflow()
