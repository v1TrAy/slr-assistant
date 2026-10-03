
# -*- coding: utf-8 -*-
"""
slr_ui/pages/dashboard.py
----------------------------
The PRISMA Dashboard page. "Diagram saved" now reports through a toast
(if a show_toast callback is provided) instead of a blocking dialog.
"""

import textwrap

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton,
    QGridLayout, QScrollArea, QFileDialog, QMessageBox, QStyle,
)

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from ..theme import _DARK, _LIGHT
from ..widgets import page_header


class DashboardPage(QWidget):
    STAGES = [
        "Records identified through database searching",
        "Duplicates removed",
        "Records after duplicates removed",
        "Records screened (title/abstract)",
        "Records excluded (title/abstract)",
        "Full-text articles assessed",
        "Full-text articles excluded (with reasons)",
        "Studies included in final synthesis",
    ]

    STAGE_BAR_ROLE = {
        "Records identified through database searching": "accent",
        "Duplicates removed": "maybe_fg",
        "Records after duplicates removed": "accent",
        "Records screened (title/abstract)": "accent",
        "Records excluded (title/abstract)": "no_fg",
        "Full-text articles assessed": "accent",
        "Full-text articles excluded (with reasons)": "no_fg",
        "Studies included in final synthesis": "ok_fg",
    }

    def __init__(self, get_store, show_toast=None):
        super().__init__()
        self.get_store = get_store
        self.show_toast = show_toast
        self.dark_mode = True

        style = self.style()
        self.btn_refresh = QPushButton(" Refresh")
        self.btn_refresh.setObjectName("secondary")
        self.btn_refresh.setIcon(style.standardIcon(QStyle.SP_BrowserReload))
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_save = QPushButton(" Save diagram as image")
        self.btn_save.setObjectName("secondary")
        self.btn_save.setIcon(style.standardIcon(QStyle.SP_DialogSaveButton))
        self.btn_save.clicked.connect(self.save_chart)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        scroll.setWidget(content)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        outer = QVBoxLayout(content)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(16)
        outer.addLayout(page_header(
            "PRISMA Dashboard",
            "Live counts and a PRISMA-style flow diagram (database-only, PRISMA 2009 layout). "
            "Per-database counts and full-text exclusion reasons are in the Excel export.",
            actions=[self.btn_refresh, self.btn_save]))

        cards = QFrame()
        cards.setObjectName("card")
        grid = QGridLayout(cards)
        grid.setSpacing(14)
        self.card_labels = {}
        self.card_bars = {}
        for i, stage in enumerate(self.STAGES):
            card = QFrame()
            card.setObjectName("panel")
            card_row = QHBoxLayout(card)
            card_row.setContentsMargins(0, 0, 0, 0)
            card_row.setSpacing(10)
            bar = QFrame()
            bar.setObjectName("statBar")
            bar.setFixedWidth(3)
            card_row.addWidget(bar)
            v = QVBoxLayout()
            v.setSpacing(4)
            val_lbl = QLabel("0")
            val_lbl.setObjectName("cardValue")
            title_lbl = QLabel(stage.upper())
            title_lbl.setObjectName("cardTitleCaps")
            title_lbl.setWordWrap(True)
            v.addWidget(val_lbl)
            v.addWidget(title_lbl)
            card_row.addLayout(v, 1)
            grid.addWidget(card, i // 4, i % 4)
            self.card_labels[stage] = val_lbl
            self.card_bars[stage] = bar
        for col in range(4):
            grid.setColumnStretch(col, 1)
        outer.addWidget(cards)
        self._recolor_bars()

        self.warn_label = QLabel()
        self.warn_label.setObjectName("pageSubtitle")
        self.warn_label.setTextFormat(Qt.PlainText)
        self.warn_label.setWordWrap(True)
        self.warn_label.setVisible(False)
        outer.addWidget(self.warn_label)

        flow = QFrame()
        flow.setObjectName("card")
        fl = QVBoxLayout(flow)
        self.figure = Figure(figsize=(7.6, 5.0))
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setMinimumHeight(380)
        fl.addWidget(self.canvas)
        outer.addWidget(flow)
        outer.addStretch()

    def set_theme(self, dark):
        self.dark_mode = dark
        self._recolor_bars()
        self.refresh()

    def _recolor_bars(self):
        palette = _DARK if self.dark_mode else _LIGHT
        for stage, bar in self.card_bars.items():
            role = self.STAGE_BAR_ROLE.get(stage, "accent")
            bar.setStyleSheet("background-color: {}; border-radius: 2px;".format(palette[role]))

    def on_shown(self):
        self.refresh()

    def refresh(self):
        store = self.get_store()
        if store is None:
            return
        counts = store.prisma_counts()
        for stage, lbl in self.card_labels.items():
            lbl.setText(str(counts.get(stage, 0)))
        self._draw_flow(counts)
        warnings = store.consistency_warnings()
        self.warn_label.setText("\n".join("\u26a0 " + w for w in warnings))
        self.warn_label.setVisible(bool(warnings))

    def _draw_flow(self, counts):
        from matplotlib.patches import FancyBboxPatch
        bg = "#1a1b22" if self.dark_mode else "#ffffff"
        fg = "#e6e8ee" if self.dark_mode else "#1b1d24"
        box_fc = "#202129" if self.dark_mode else "#f0f2f8"
        edge = "#2a2b36" if self.dark_mode else "#d5d9e4"
        muted = "#8b8e9c" if self.dark_mode else "#666a78"

        self.figure.clear()
        self.figure.patch.set_facecolor(bg)
        ax = self.figure.add_subplot(111)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis("off")

        danger = "#ff6b6b" if self.dark_mode else "#c22c42"
        danger_bg = "#3d2023" if self.dark_mode else "#fbe1e2"

        def box(x, y, w, text, n, excluded=False):
            label = textwrap.fill(text, 30) + f"\nn = {n}"
            face = danger_bg if excluded else box_fc
            line = danger if excluded else edge
            style = (0, (5, 3)) if excluded else "solid"
            patch = FancyBboxPatch(
                (x - w / 2, y - 0.047), w, 0.094,
                boxstyle="round,pad=0.010", fc=face, ec=line, lw=1.4, linestyle=style)
            ax.add_patch(patch)
            ax.text(x, y, label, ha="center", va="center", fontsize=8.5,
                    color=(danger if excluded else fg))

        def arrow(x1, y1, x2, y2):
            ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                        arrowprops=dict(arrowstyle="-|>", color=fg, lw=1.3))

        def phase(y, text):
            ax.text(0.015, y, text, rotation=90, va="center",
                    fontsize=8, color=muted, fontweight="bold")

        lx, lw = 0.38, 0.40
        rx, rw = 0.79, 0.26
        box(lx, 0.93, lw, "Records identified through database searching",
            counts["Records identified through database searching"])
        box(lx, 0.78, lw, "Records after duplicates removed",
            counts["Records after duplicates removed"])
        box(lx, 0.63, lw, "Records screened (title/abstract)",
            counts["Records screened (title/abstract)"])
        box(rx, 0.63, rw, "Records excluded (title/abstract)",
            counts["Records excluded (title/abstract)"], excluded=True)
        box(lx, 0.48, lw, "Full-text articles assessed",
            counts["Full-text articles assessed"])
        box(rx, 0.48, rw, "Full-text articles excluded (with reasons)",
            counts["Full-text articles excluded (with reasons)"], excluded=True)
        box(lx, 0.31, lw, "Studies included in final synthesis",
            counts["Studies included in final synthesis"])

        arrow(lx, 0.883, lx, 0.827)
        arrow(lx, 0.733, lx, 0.677)
        arrow(lx, 0.583, lx, 0.527)
        arrow(lx, 0.433, lx, 0.357)
        arrow(lx + lw / 2, 0.63, rx - rw / 2, 0.63)
        arrow(lx + lw / 2, 0.48, rx - rw / 2, 0.48)

        phase(0.855, "IDENTIFICATION")
        phase(0.555, "SCREENING")
        phase(0.395, "INCLUDED")

        self.figure.tight_layout()
        self.canvas.draw()

    def save_chart(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PRISMA flow diagram", "prisma_flow.png", "PNG Image (*.png)")
        if not path:
            return
        self.figure.savefig(path, facecolor=self.figure.get_facecolor(), dpi=200)
        if self.show_toast:
            self.show_toast("Diagram saved", path)
        else:
            QMessageBox.information(self, "Saved", f"Diagram saved to:\n{path}")
