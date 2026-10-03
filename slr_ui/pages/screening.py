
# -*- coding: utf-8 -*-
"""
slr_ui/pages/screening.py
----------------------------
The Screening page: a bulk-edit table view (with a designed empty state
when there's nothing to screen yet) and a keyboard-driven Focus view.
Every decision persists immediately via store.update_decision().
"""

import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton,
    QLineEdit, QComboBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QProgressBar, QMessageBox, QAbstractItemView, QStackedWidget, QStyle,
    QApplication, QPlainTextEdit,
)

from slr_core import normalize_doi

from ..theme import DECISION_TA_OPTIONS, DECISION_FT_OPTIONS, DECISION_COLORS_DARK, DECISION_COLORS_LIGHT
from ..widgets import page_header, configure_table_sizing, NumericTableWidgetItem, ComboBoxDelegate, EmptyState
from ..icons import screening_icon


class ScreeningTableView(QWidget):
    COLUMN_WIDTHS = {0: 55, 1: 400, 2: 65, 3: 130, 4: 150, 5: 130, 6: 220, 7: 220}
    REASON_COLUMNS = {6: "ta_reason", 7: "ft_reason"}

    def __init__(self, get_store, set_status, push_undo):
        super().__init__()
        self.get_store = get_store
        self.set_status = set_status
        self.push_undo = push_undo
        self.dark_mode = True
        self.df = None
        self.loading = False
        self.session_clean = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        panel = QFrame()
        panel.setObjectName("panel")
        pl = QHBoxLayout(panel)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(12)
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search by title or database\u2026")
        # Debounced: apply_filter re-renders every matching row, which is far
        # too slow to run on each keystroke with tens of thousands of records.
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(250)
        self._filter_timer.timeout.connect(self.apply_filter)
        self.search_box.textChanged.connect(lambda _text: self._filter_timer.start())
        pl.addWidget(self.search_box, 2)
        self.filter_combo = QComboBox()
        self.filter_combo.addItems(["All", "Not decided", "Include", "Exclude", "Maybe"])
        self.filter_combo.currentTextChanged.connect(self.apply_filter)
        pl.addWidget(self.filter_combo)
        pl.addStretch()
        style = self.style()
        self.btn_delete = QPushButton(" Delete selected")
        self.btn_delete.setObjectName("danger")
        self.btn_delete.setIcon(style.standardIcon(QStyle.SP_TrashIcon))
        self.btn_delete.clicked.connect(self.delete_selected)
        pl.addWidget(self.btn_delete)
        outer.addWidget(panel)

        self.progress = QProgressBar()
        self.progress.setFormat("Screened %v / %m")
        outer.addWidget(self.progress)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["ID", "TITLE", "YEAR", "DATABASE", "TITLE/ABSTRACT", "FULL-TEXT",
             "TA REASON", "FT REASON"])
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.cellClicked.connect(self.on_cell_clicked)
        configure_table_sizing(self.table, stretch_col=1, widths=self.COLUMN_WIDTHS)

        self.ta_delegate = ComboBoxDelegate(
            DECISION_TA_OPTIONS, on_committed=lambda dbid, v: self.on_decision_committed(dbid, "ta_decision", v))
        self.ft_delegate = ComboBoxDelegate(
            DECISION_FT_OPTIONS, on_committed=lambda dbid, v: self.on_decision_committed(dbid, "ft_decision", v))
        self.table.setItemDelegateForColumn(4, self.ta_delegate)
        self.table.setItemDelegateForColumn(5, self.ft_delegate)
        self.table.itemChanged.connect(self.on_item_changed)

        # Designed empty state instead of a bare table when there's
        # nothing to screen yet (no project, or everything is a
        # duplicate / not yet imported).
        self.empty_state = EmptyState(
            screening_icon("#8b8e9c", 40).pixmap(40, 40),
            "Nothing to screen yet",
            "Import some database export files first, then come back "
            "here to work through title/abstract and full-text decisions.",
        )
        self.stack = QStackedWidget()
        self.stack.addWidget(self.table)
        self.stack.addWidget(self.empty_state)
        outer.addWidget(self.stack, 1)

        del_sc = QShortcut(QKeySequence(Qt.Key_Delete), self.table)
        del_sc.setContext(Qt.WidgetWithChildrenShortcut)
        del_sc.activated.connect(self.delete_selected)

    def _dbid_at(self, row):
        item = self.table.item(row, 0)
        return item.data(Qt.UserRole) if item is not None else None

    def _before_edit(self):
        if self.session_clean:
            self.push_undo()
            self.session_clean = False

    def on_cell_clicked(self, row, col):
        if col in (4, 5):
            item = self.table.item(row, col)
            if item:
                self.table.editItem(item)

    def set_theme(self, dark):
        self.dark_mode = dark
        for row in range(self.table.rowCount()):
            self._colorize_row(row)

    def refresh(self):
        store = self.get_store()
        if store is None:
            self.df = None
            self._render_empty()
            return
        self.df = store.all_records_df(exclude_duplicates=True)
        self._render(self.df)
        self._update_progress()

    def _render_empty(self):
        self.loading = True
        try:
            self.table.setRowCount(0)
        finally:
            self.loading = False
        self.stack.setCurrentWidget(self.empty_state)

    def _render(self, df):
        self.loading = True
        try:
            self.table.setSortingEnabled(False)
            self.table.setUpdatesEnabled(False)
            self.table.setRowCount(len(df))
            for row, (_, r) in enumerate(df.iterrows()):
                dbid = int(r["ID"])
                pos_item = NumericTableWidgetItem(str(r["Pos"]))
                pos_item.setData(Qt.UserRole, dbid)
                pos_item.setFlags(pos_item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, 0, pos_item)

                for col, val in [(1, r["Title"]), (3, r["Database"])]:
                    item = QTableWidgetItem(str(val))
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.table.setItem(row, col, item)

                year_item = NumericTableWidgetItem(str(r["Year"]))
                year_item.setFlags(year_item.flags() & ~Qt.ItemIsEditable)
                self.table.setItem(row, 2, year_item)

                self.table.setItem(row, 4, QTableWidgetItem(r["TA Decision"] or ""))
                self.table.setItem(row, 5, QTableWidgetItem(r["FT Decision"] or ""))
                self.table.setItem(row, 6, QTableWidgetItem(str(r["TA Reason"] or "")))
                self.table.setItem(row, 7, QTableWidgetItem(str(r["FT Reason"] or "")))
                self._colorize_row(row)
            self.table.resizeRowsToContents()
            self.table.setUpdatesEnabled(True)
            self.table.setSortingEnabled(True)
        finally:
            self.loading = False
            self.session_clean = True
        self.stack.setCurrentWidget(self.empty_state if len(df) == 0 else self.table)

    def _colorize_row(self, row):
        ta_item = self.table.item(row, 4)
        ft_item = self.table.item(row, 5)
        ta_text = ta_item.text() if ta_item else ""
        ft_text = ft_item.text() if ft_item else ""
        decision = ft_text if ft_text else ta_text
        palette = DECISION_COLORS_DARK if self.dark_mode else DECISION_COLORS_LIGHT
        color = palette.get(decision, palette[""])
        for col in range(6):
            item = self.table.item(row, col)
            if item:
                item.setBackground(color)

    def apply_filter(self):
        if getattr(self, "df", None) is None:
            return
        text = self.search_box.text().strip().lower()
        stage = self.filter_combo.currentText()
        df = self.df
        if text:
            mask = (df["Title"].astype(str).str.lower().str.contains(text, regex=False) |
                    df["Database"].astype(str).str.lower().str.contains(text, regex=False))
            df = df[mask]
        if stage == "Not decided":
            df = df[(df["TA Decision"] == "") & (df["FT Decision"] == "")]
        elif stage in ("Include", "Exclude", "Maybe"):
            df = df[(df["TA Decision"] == stage) | (df["FT Decision"] == stage)]
        self._render(df)

    def _update_progress(self):
        df = getattr(self, "df", None)
        if df is None:
            self.progress.setMaximum(1)
            self.progress.setValue(0)
            return
        total = len(df)
        decided = int((df["TA Decision"] != "").sum()) if total else 0
        self.progress.setMaximum(max(total, 1))
        self.progress.setValue(decided)

    def on_decision_committed(self, dbid, field, value):
        store = self.get_store()
        if dbid is None or store is None:
            return
        self._before_edit()
        store.update_decision(dbid, field, value)
        if self.df is not None:
            df_col = "TA Decision" if field == "ta_decision" else "FT Decision"
            self.df.loc[self.df["ID"] == dbid, df_col] = value
        self._update_progress()
        for row in range(self.table.rowCount()):
            if self._dbid_at(row) == dbid:
                self._colorize_row(row)
                break

    def on_item_changed(self, item):
        if self.loading:
            return
        field = self.REASON_COLUMNS.get(item.column())
        if field is None:
            return
        dbid = self._dbid_at(item.row())
        store = self.get_store()
        if dbid is None or store is None:
            return
        self._before_edit()
        store.update_decision(dbid, field, item.text())
        if self.df is not None:
            df_col = "TA Reason" if field == "ta_reason" else "FT Reason"
            self.df.loc[self.df["ID"] == dbid, df_col] = item.text()

    def selected_dbids(self):
        return sorted({it.data(Qt.UserRole) for it in self.table.selectedItems() if it.data(Qt.UserRole) is not None})

    def delete_selected(self):
        store = self.get_store()
        if store is None:
            return
        if self.table.state() == QAbstractItemView.EditingState:
            return
        ids = self.selected_dbids()
        if not ids:
            QMessageBox.information(self, "Nothing selected", "Select one or more rows first.")
            return
        reply = QMessageBox.question(
            self, "Confirm delete", f"Delete {len(ids)} selected records? This can be undone with Ctrl+Z.")
        if reply != QMessageBox.Yes:
            return
        self.push_undo()
        store.delete_records(ids)
        self.set_status(f"Deleted {len(ids)} records.")
        self.refresh()


class FocusView(QWidget):
    def __init__(self, get_store, set_status, push_undo):
        super().__init__()
        self.get_store = get_store
        self.set_status = set_status
        self.push_undo = push_undo
        self.rows = []
        self.i = 0
        self.session_clean = True

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)

        top = QHBoxLayout()
        self.counter = QLabel("0 of 0")
        self.counter.setObjectName("pageSubtitle")
        top.addWidget(self.counter)
        self.progress = QProgressBar()
        self.progress.setFormat("Screened %v / %m")
        top.addWidget(self.progress, 1)
        self.auto_advance = QCheckBox("Auto-advance")
        self.auto_advance.setChecked(True)
        top.addWidget(self.auto_advance)
        outer.addLayout(top)

        self.card = QFrame()
        self.card.setObjectName("focusCard")
        cl = QVBoxLayout(self.card)
        cl.setContentsMargins(24, 24, 24, 24)
        cl.setSpacing(10)

        # Imported text is untrusted and routinely contains '<' ("p < 0.05",
        # "<i>E. coli</i>"); QLabel's default AutoText would render it as HTML.
        self.lbl_pos = QLabel()
        self.lbl_pos.setObjectName("pageSubtitle")
        self.lbl_pos.setTextFormat(Qt.PlainText)
        cl.addWidget(self.lbl_pos)

        self.lbl_title = QLabel()
        self.lbl_title.setObjectName("focusTitle")
        self.lbl_title.setTextFormat(Qt.PlainText)
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setTextInteractionFlags(Qt.TextSelectableByMouse)
        cl.addWidget(self.lbl_title)

        self.lbl_authors = QLabel()
        self.lbl_authors.setObjectName("pageSubtitle")
        self.lbl_authors.setTextFormat(Qt.PlainText)
        self.lbl_authors.setWordWrap(True)
        cl.addWidget(self.lbl_authors)

        self.lbl_meta = QLabel()
        self.lbl_meta.setObjectName("pageSubtitle")
        self.lbl_meta.setTextFormat(Qt.PlainText)
        self.lbl_meta.setWordWrap(True)
        cl.addWidget(self.lbl_meta)

        self.lbl_doi = QLabel()
        self.lbl_doi.setObjectName("pageSubtitle")
        self.lbl_doi.setTextFormat(Qt.RichText)
        self.lbl_doi.setWordWrap(True)
        self.lbl_doi.setOpenExternalLinks(True)
        cl.addWidget(self.lbl_doi)

        # Read-only and unfocusable so the single-letter screening hotkeys
        # (I/E/M/N) are never swallowed by a text widget.
        self.txt_abstract = QPlainTextEdit()
        self.txt_abstract.setReadOnly(True)
        self.txt_abstract.setFocusPolicy(Qt.NoFocus)
        self.txt_abstract.setMinimumHeight(110)
        self.txt_abstract.setPlaceholderText("No abstract in the imported record.")
        cl.addWidget(self.txt_abstract, 1)

        cl.addSpacing(4)
        pills = QHBoxLayout()
        self.pill_ta = QLabel()
        self.pill_ta.setObjectName("pill")
        self.pill_ft = QLabel()
        self.pill_ft.setObjectName("pill")
        pills.addWidget(self.pill_ta)
        pills.addWidget(self.pill_ft)
        pills.addStretch()
        cl.addLayout(pills)

        cl.addSpacing(6)
        self.reason_stage = QLabel("Reason (title/abstract)")
        self.reason_stage.setObjectName("pageSubtitle")
        cl.addWidget(self.reason_stage)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Why excluded? (optional)")
        self.reason.editingFinished.connect(self.save_reason)
        cl.addWidget(self.reason)

        cl.addSpacing(8)
        ta_row = QHBoxLayout()
        self.btn_ta_inc = QPushButton("Include (I)")
        self.btn_ta_inc.setObjectName("success")
        self.btn_ta_inc.clicked.connect(lambda: self.decide("ta_decision", "Include"))
        self.btn_ta_exc = QPushButton("Exclude (E)")
        self.btn_ta_exc.setObjectName("danger")
        self.btn_ta_exc.clicked.connect(lambda: self.decide("ta_decision", "Exclude"))
        self.btn_ta_may = QPushButton("Maybe (M)")
        self.btn_ta_may.setObjectName("secondary")
        self.btn_ta_may.clicked.connect(lambda: self.decide("ta_decision", "Maybe"))
        for b in (self.btn_ta_inc, self.btn_ta_exc, self.btn_ta_may):
            b.setMinimumHeight(36)
            ta_row.addWidget(b)
        cl.addLayout(ta_row)

        self.ft_widget = QWidget()
        ft_row = QHBoxLayout(self.ft_widget)
        ft_row.setContentsMargins(0, 0, 0, 0)
        ft_row.setSpacing(10)
        ft_lbl = QLabel("Full-text:")
        ft_lbl.setObjectName("pageSubtitle")
        self.btn_ft_inc = QPushButton("Include (Shift+I)")
        self.btn_ft_inc.setObjectName("success")
        self.btn_ft_inc.clicked.connect(lambda: self.decide("ft_decision", "Include"))
        self.btn_ft_exc = QPushButton("Exclude (Shift+E)")
        self.btn_ft_exc.setObjectName("danger")
        self.btn_ft_exc.clicked.connect(lambda: self.decide("ft_decision", "Exclude"))
        ft_row.addWidget(ft_lbl)
        ft_row.addWidget(self.btn_ft_inc)
        ft_row.addWidget(self.btn_ft_exc)
        ft_row.addStretch()
        cl.addWidget(self.ft_widget)

        outer.addWidget(self.card, 1)

        nav = QHBoxLayout()
        self.btn_prev = QPushButton("\u2190 Prev")
        self.btn_prev.setObjectName("secondary")
        self.btn_prev.clicked.connect(self.prev)
        self.btn_next = QPushButton("Next \u2192")
        self.btn_next.clicked.connect(self.next)
        nav.addWidget(self.btn_prev)
        nav.addStretch()
        self.btn_next_undecided = QPushButton("Next undecided (N)")
        self.btn_next_undecided.setObjectName("secondary")
        self.btn_next_undecided.clicked.connect(self.next_undecided)
        nav.addWidget(self.btn_next_undecided)
        nav.addWidget(self.btn_next)
        outer.addLayout(nav)

        hint = QLabel("I include \u2022 E exclude \u2022 M maybe \u2022 Shift+I/E full-text \u2022 \u2190\u2192 move \u2022 N next undecided")
        hint.setObjectName("pageSubtitle")
        outer.addWidget(hint, 0, Qt.AlignCenter)

        self.empty = QLabel()
        self.empty.setObjectName("emptyState")
        self.empty.setAlignment(Qt.AlignCenter)
        outer.addWidget(self.empty, 1)

        self._add_shortcut("I", lambda: self._hk(lambda: self.decide("ta_decision", "Include")))
        self._add_shortcut("E", lambda: self._hk(lambda: self.decide("ta_decision", "Exclude")))
        self._add_shortcut("M", lambda: self._hk(lambda: self.decide("ta_decision", "Maybe")))
        self._add_shortcut("Shift+I", lambda: self._hk(lambda: self.decide("ft_decision", "Include")))
        self._add_shortcut("Shift+E", lambda: self._hk(lambda: self.decide("ft_decision", "Exclude")))
        self._add_shortcut("Left", lambda: self._hk(self.prev))
        self._add_shortcut("Right", lambda: self._hk(self.next))
        self._add_shortcut("N", lambda: self._hk(self.next_undecided))

    def _add_shortcut(self, key, fn):
        sc = QShortcut(QKeySequence(key), self)
        sc.setContext(Qt.WidgetWithChildrenShortcut)
        sc.activated.connect(fn)

    def _hk(self, fn):
        if isinstance(QApplication.focusWidget(), QLineEdit):
            return
        fn()

    @staticmethod
    def _doi_link_html(raw):
        """Rich-text link for a DOI/URL field. The value comes from an
        imported file, so it is HTML-escaped and only http(s) targets are
        ever turned into a clickable link."""
        raw = str(raw or "").strip()
        doi = normalize_doi(raw)
        if doi:
            href = "https://doi.org/" + doi
            return '<a href="{}">{}</a>'.format(html.escape(href, quote=True), html.escape(doi))
        if raw.lower().startswith(("http://", "https://")):
            return '<a href="{}">{}</a>'.format(html.escape(raw, quote=True), html.escape(raw))
        return html.escape(raw)

    def set_pill(self, lbl, value, stage):
        from ..theme import repolish
        lbl.setText(f"{stage}: {value or 'not set'}")
        lbl.setProperty("state", value)
        repolish(lbl)

    def reload(self):
        store = self.get_store()
        self.rows, self.i = [], 0
        self.session_clean = True
        if store is not None:
            self.rows = store.all_records_df(exclude_duplicates=True).to_dict("records")
        self.render()

    def render(self):
        has = bool(self.rows)
        self.card.setVisible(has)
        for w in (self.btn_prev, self.btn_next, self.btn_next_undecided):
            w.setVisible(has)
        self.empty.setVisible(not has)
        if not has:
            self.counter.setText("0 of 0")
            self.progress.setMaximum(1)
            self.progress.setValue(0)
            self.empty.setText("Nothing to screen. Import records first.")
            return

        r = self.rows[self.i]
        self.counter.setText(f"{self.i + 1} of {len(self.rows)}")
        decided = sum(1 for x in self.rows if x["TA Decision"])
        self.progress.setMaximum(max(len(self.rows), 1))
        self.progress.setValue(decided)

        self.lbl_pos.setText(f"Record #{r['Pos']} \u2022 {r['Database']}")
        self.lbl_title.setText(str(r["Title"]))
        self.lbl_authors.setText(str(r["Authors"]) or "")
        self.lbl_meta.setText(f"{r['Year'] or ''} \u2022 {r['Journal/Source'] or ''}")

        self.lbl_doi.setText(self._doi_link_html(r["DOI/Link"]))
        store = self.get_store()
        self.txt_abstract.setPlainText(store.get_abstract(int(r["ID"])) if store is not None else "")

        self.set_pill(self.pill_ta, r["TA Decision"], "Title/Abstract")
        self.set_pill(self.pill_ft, r["FT Decision"], "Full-text")

        ft_active = r["TA Decision"] == "Include"
        self.ft_widget.setVisible(ft_active)
        self.reason_stage.setText("Reason (full-text)" if ft_active else "Reason (title/abstract)")
        self.reason.setText(str(r["FT Reason"]) if ft_active else (r["TA Reason"] or ""))

    def decide(self, field, value):
        store = self.get_store()
        if not self.rows or store is None:
            return
        r = self.rows[self.i]
        if field == "ft_decision" and r["TA Decision"] != "Include":
            # The FT buttons are hidden for such records, but the Shift+I /
            # Shift+E hotkeys are not; without this guard they record a
            # full-text decision for a paper that never passed title/abstract.
            self.set_status("Full-text decisions need a title/abstract 'Include' first.")
            return
        if self.session_clean:
            self.push_undo()
            self.session_clean = False
        store.update_decision(r["ID"], field, value)
        df_col = "TA Decision" if field == "ta_decision" else "FT Decision"
        r[df_col] = value
        self.set_status(f"Record #{r['Pos']}: {field.replace('_', ' ')} = {value or 'cleared'}")
        self.render()
        if self.auto_advance.isChecked():
            if (field == "ta_decision" and value in ("Include", "Exclude")) or field == "ft_decision":
                self.next_undecided()

    def save_reason(self):
        store = self.get_store()
        if not self.rows or store is None:
            return
        r = self.rows[self.i]
        ft_active = r["TA Decision"] == "Include"
        field = "ft_reason" if ft_active else "ta_reason"
        if self.session_clean:
            self.push_undo()
            self.session_clean = False
        store.update_decision(r["ID"], field, self.reason.text())
        r["FT Reason" if ft_active else "TA Reason"] = self.reason.text()

    def prev(self):
        if self.rows:
            self.i = (self.i - 1) % len(self.rows)
            self.render()

    def next(self):
        if self.rows:
            self.i = (self.i + 1) % len(self.rows)
            self.render()

    def next_undecided(self):
        n = len(self.rows)
        if not n:
            return
        for k in range(1, n + 1):
            j = (self.i + k) % n
            if not self.rows[j]["TA Decision"]:
                self.i = j
                self.render()
                return
        self.render()


class ScreeningPage(QWidget):
    def __init__(self, get_store, set_status, push_undo):
        super().__init__()
        self.table_view = ScreeningTableView(get_store, set_status, push_undo)
        self.focus_view = FocusView(get_store, set_status, push_undo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Table view", "Focus view"])
        self.mode_combo.currentIndexChanged.connect(self.mode_changed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(14)
        outer.addLayout(page_header(
            "Screening",
            "Focus view is for speed (keyboard-driven, one record at a time); "
            "table view is for bulk edits and filtering.", actions=[self.mode_combo]))

        self.stack = QStackedWidget()
        self.stack.addWidget(self.table_view)
        self.stack.addWidget(self.focus_view)
        outer.addWidget(self.stack, 1)

    def mode_changed(self, index):
        self.stack.setCurrentIndex(index)
        self.refresh()

    def refresh(self):
        if self.stack.currentIndex() == 0:
            self.table_view.refresh()
        else:
            self.focus_view.reload()

    def on_shown(self):
        self.refresh()

    def set_theme(self, dark):
        self.table_view.set_theme(dark)
