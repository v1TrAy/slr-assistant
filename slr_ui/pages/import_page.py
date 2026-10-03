
# -*- coding: utf-8 -*-
"""
slr_ui/pages/import_page.py
------------------------------
The Import & Merge page. File parsing runs on a background QThread
(slr_workers) so the GUI stays responsive during large multi-file
imports. The final "merged" outcome is reported via a toast (set up by
MainWindow) instead of a blocking dialog -- see main_window.py.
"""

import os
import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QDialog, QVBoxLayout, QHBoxLayout, QFrame, QLabel, QPushButton,
    QSlider, QCheckBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QDialogButtonBox, QFileDialog, QMessageBox,
    QProgressDialog, QStyle,
)

import slr_core
import slr_workers
from ..widgets import page_header, configure_table_sizing, NumericTableWidgetItem, DropArea

logger = logging.getLogger(__name__)


class ImportPage(QWidget):
    COLUMN_WIDTHS = {0: 45, 1: 60, 2: 400, 3: 190, 4: 70, 5: 200, 6: 130, 7: 300}

    def __init__(self, get_store, set_status, push_undo, on_merged=None):
        super().__init__()
        self.get_store = get_store
        self.set_status = set_status
        self.push_undo = push_undo
        self.on_merged = on_merged  # optional callback(n_total, n_dup) -> toast, etc.
        self.batch = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(14)
        outer.addLayout(page_header(
            "Import & Merge",
            "Anything under 100% similarity may be a false match \u2014 review it, then "
            "uncheck rows you don't want. Fix mistakes later in Manage Duplicates."))

        panel = QFrame()
        panel.setObjectName("panel")
        pl = QHBoxLayout(panel)
        pl.setContentsMargins(12, 10, 12, 10)
        pl.setSpacing(12)

        style = self.style()
        self.btn_pick = QPushButton(" Select files\u2026")
        self.btn_pick.setIcon(style.standardIcon(QStyle.SP_DialogOpenButton))
        self.btn_pick.clicked.connect(self.pick_files)
        pl.addWidget(self.btn_pick)

        pl.addSpacing(8)
        pl.addWidget(QLabel("Sensitivity"))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(70)
        self.slider.setMaximum(100)
        self.slider.setValue(90)
        self.slider.setFixedWidth(140)
        self.slider.valueChanged.connect(lambda v: self.slider_label.setText(f"{v/100:.2f}"))
        pl.addWidget(self.slider)
        self.slider_label = QLabel("0.90")
        self.slider_label.setObjectName("badge")
        self.slider_label.setAlignment(Qt.AlignCenter)
        pl.addWidget(self.slider_label)

        self.doi_veto_check = QCheckBox("Different DOI overrides similar title")
        self.doi_veto_check.setChecked(True)
        self.doi_veto_check.setToolTip(
            "When a new record's title matches an existing record but their DOIs differ, "
            "keep both as unique records \u2014 they are different papers (e.g. a conference "
            "paper and its journal extension). Uncheck to flag such pairs as duplicates anyway.")
        pl.addWidget(self.doi_veto_check)

        pl.addStretch()
        self.btn_review = QPushButton(" Review uncertain matches")
        self.btn_review.setObjectName("secondary")
        self.btn_review.setIcon(style.standardIcon(QStyle.SP_FileDialogContentsView))
        self.btn_review.clicked.connect(self.open_review_dialog)
        pl.addWidget(self.btn_review)

        self.btn_commit = QPushButton(" Merge into project")
        self.btn_commit.setIcon(style.standardIcon(QStyle.SP_DialogApplyButton))
        self.btn_commit.clicked.connect(self.commit_batch)
        pl.addWidget(self.btn_commit)

        outer.addWidget(panel)

        self.drop_area = DropArea(self._process_files)
        outer.addWidget(self.drop_area)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["#", "IMPORT?", "TITLE", "AUTHORS", "YEAR", "JOURNAL/SOURCE", "DATABASE", "STATUS"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        configure_table_sizing(self.table, stretch_col=2, widths=self.COLUMN_WIDTHS)
        outer.addWidget(self.table, 1)

    def pick_files(self):
        if self.get_store() is None:
            QMessageBox.warning(self, "No project open", "Open or create a project first.")
            return
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select export files", "",
            "Export files (*.csv *.xlsx *.ris);;All files (*)")
        if paths:
            self._process_files(paths)

    def _set_busy(self, busy):
        for w in (self.btn_pick, self.btn_review, self.btn_commit, self.slider,
                  self.drop_area):
            w.setEnabled(not busy)

    def _process_files(self, paths):
        store = self.get_store()
        if store is None:
            QMessageBox.warning(self, "No project open", "Open or create a project first.")
            return
        running = getattr(self, "_import_worker", None)
        if running is not None and running.isRunning():
            return
        threshold = self.slider.value() / 100.0
        existing_titles = store.existing_titles()
        existing_dois = store.existing_dois()

        progress = QProgressDialog("Parsing files\u2026", "Cancel", 0, len(paths), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(300)
        progress.setValue(0)

        worker = slr_workers.ImportParseWorker(
            paths, existing_titles, existing_dois, threshold,
            self.doi_veto_check.isChecked())
        self._import_worker = worker
        file_errors = []

        def on_progress(done, total, name):
            progress.setLabelText(f"Parsing {name}\u2026 ({done}/{total})")
            progress.setValue(done)

        def on_file_error(path, message):
            logger.error("Failed to parse '%s': %s", path, message)
            file_errors.append((path, message))

        def on_failed(message):
            self._set_busy(False)
            progress.close()
            QMessageBox.critical(self, "Import failed", message)

        def on_cancelled():
            self._set_busy(False)
            progress.close()
            self.set_status("Import cancelled; nothing was staged.")

        def on_finished(batch, total_parsed, stats):
            self._set_busy(False)
            progress.close()
            self.batch = batch
            self._render_batch()

            for path, message in file_errors:
                QMessageBox.warning(self, "Error reading file",
                                    f"{os.path.basename(path)}:\n{message}")

            n_dup = sum(1 for r in self.batch.records if r.is_dup == "Yes")
            n_doi = sum(1 for r in self.batch.records
                        if r.is_dup == "Yes" and r.match_source == "doi")
            n_uncertain = len(self.batch.uncertain)
            n_veto = sum(1 for r in self.batch.records if r.match_source == "doi_veto")
            n_skipped = stats.get("skipped", 0)
            n_abs = sum(1 for r in self.batch.records if r.rec.get("abstract"))
            msg = (f"Parsed {total_parsed} record(s) from {len(paths)} file(s) - "
                   f"{n_dup} flagged as duplicates")
            if total_parsed and n_abs < total_parsed:
                msg += f" ({total_parsed - n_abs} without an abstract)"
            if n_doi:
                msg += f" ({n_doi} by exact DOI match)"
            if n_uncertain:
                msg += f" ({n_uncertain} uncertain, not 100% - review recommended)"
            if n_veto:
                msg += f" ({n_veto} similar titles kept unique \u2014 different DOIs)"
            if n_skipped:
                msg += f" ({n_skipped} row(s) skipped: no recognizable title)"
            msg += "."
            self.set_status(msg)
            guesses = stats.get("encoding_guesses") or []
            if guesses:
                QMessageBox.warning(
                    self, "Check text encoding",
                    "These files were not valid UTF-8, so the text encoding was guessed. "
                    "Check that titles (especially non-Latin ones) look right before "
                    "merging:\n\n" + "\n".join(guesses))

        worker.progress.connect(on_progress)
        worker.file_error.connect(on_file_error)
        worker.failed.connect(on_failed)
        worker.cancelled.connect(on_cancelled)
        worker.finished_ok.connect(on_finished)
        progress.canceled.connect(worker.cancel)

        self._set_busy(True)
        worker.start()

    def _render_batch(self):
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self.batch.records))
        for row, r in enumerate(self.batch.records):
            num_item = NumericTableWidgetItem(str(r.index + 1))
            num_item.setData(Qt.UserRole, r.index)
            num_item.setFlags(num_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 0, num_item)

            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk.setCheckState(Qt.Unchecked if r.skipped else Qt.Checked)
            chk.setData(Qt.UserRole, r.index)
            self.table.setItem(row, 1, chk)

            matched = r.matched_title or ""
            short = (matched[:45] + "\u2026") if len(matched) > 45 else matched
            if r.is_dup == "Yes" and r.match_source == "doi":
                status = f"Exact DOI match ({short})"
            elif r.is_dup == "Yes":
                status = f"Duplicate ({r.score:.0%}) ({short})"
            elif r.match_source == "doi_veto":
                status = f"Similar title but different DOI \u2014 kept unique ({short})"
            else:
                status = ""

            for col, val in enumerate([r.rec["title"], r.rec["authors"], r.rec["year"],
                                       r.rec["journal"], r.rec["database"], status], start=2):
                item = NumericTableWidgetItem(str(val)) if col == 4 else QTableWidgetItem(str(val))
                if col == 7 and r.is_dup == "Yes":
                    item.setForeground(QColor("#e0a94b") if (r.score or 0) < 1.0 else QColor("#8b8e9c"))
                elif col == 7 and r.match_source == "doi_veto":
                    item.setForeground(QColor("#2ea36f"))
                self.table.setItem(row, col, item)
        self.table.resizeRowsToContents()
        self.table.setUpdatesEnabled(True)
        self.table.setSortingEnabled(True)

    def open_review_dialog(self):
        if not self.batch or not self.batch.records:
            QMessageBox.information(self, "Nothing to review", "Import some files first.")
            return
        uncertain = self.batch.uncertain
        if not uncertain:
            QMessageBox.information(
                self, "No uncertain matches",
                "Every flagged duplicate in this batch was a 100% exact title match.")
            return
        DuplicateReviewDialog(uncertain, self).exec()

    def commit_batch(self):
        store = self.get_store()
        if store is None or not self.batch or not self.batch.records:
            QMessageBox.warning(self, "Nothing to merge", "Select or drop files first.")
            return
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 1)
            if chk is not None:
                idx = chk.data(Qt.UserRole)
                if idx is not None and 0 <= idx < len(self.batch.records):
                    self.batch.records[idx].skipped = (chk.checkState() == Qt.Unchecked)

        self.push_undo()
        n_total, n_dup = self.batch.commit(store)
        logger.info("Merged %d records (%d duplicates) into '%s'.", n_total, n_dup, store.path)
        self.batch = None
        self.table.setRowCount(0)
        self.set_status(f"Merged {n_total} records ({n_dup} flagged as duplicates).")
        if self.on_merged:
            self.on_merged(n_total, n_dup)


class DuplicateReviewDialog(QDialog):
    def __init__(self, uncertain_matches, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review uncertain duplicate matches")
        self.setMinimumSize(760, 420)
        layout = QVBoxLayout(self)

        info = QLabel(
            f"These {len(uncertain_matches)} matches below are NOT exact (100%) title matches. "
            "If they are actually different papers, uncheck the row in the import table "
            "(the \u201cImport?\u201d column) before merging. You can also lower the sensitivity "
            "and re-import.")
        info.setWordWrap(True)
        info.setObjectName("pageSubtitle")
        layout.addWidget(info)

        table = QTableWidget(len(uncertain_matches), 3)
        table.setHorizontalHeaderLabels(
            ["NEW RECORD (BEING IMPORTED)", "MATCHED EXISTING RECORD", "SIMILARITY"])
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        table.setColumnWidth(2, 100)
        table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        table.setWordWrap(True)

        uncertain_matches = sorted(uncertain_matches, key=lambda x: -x[2])
        for row, (new_t, matched_t, score) in enumerate(uncertain_matches):
            table.setItem(row, 0, QTableWidgetItem(new_t))
            table.setItem(row, 1, QTableWidgetItem(matched_t))
            score_item = NumericTableWidgetItem(f"{score:.0%}")
            score_item.setTextAlignment(Qt.AlignCenter)
            if score >= 0.93:
                score_item.setForeground(QColor("#e5484d"))
            elif score >= 0.97:
                score_item.setForeground(QColor("#e0a94b"))
            table.setItem(row, 2, score_item)
        table.resizeRowsToContents()
        layout.addWidget(table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)
