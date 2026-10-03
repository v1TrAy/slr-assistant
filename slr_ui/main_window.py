
# -*- coding: utf-8 -*-
"""
slr_ui/main_window.py
------------------------
MainWindow: the application shell (sidebar + page stack + menu bar +
status bar), project open/close lifecycle, undo/redo, theme toggling,
the non-blocking Excel export/sync flows, toast notifications, and the
Ctrl+K command palette.
"""

import os
import logging

from PySide6.QtCore import Qt, QSettings, QSize
from PySide6.QtGui import QKeySequence, QShortcut, QAction
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QStackedWidget, QLabel, QStatusBar,
    QMenu, QStyle, QFileDialog, QMessageBox, QApplication, QProgressDialog,
)

import slr_core
import slr_workers
from .theme import ORG_NAME, APP_NAME, _DARK, _LIGHT, build_qss
from .sidebar import Sidebar
from .toast import ToastManager
from .command_palette import CommandPalette
from .pages.welcome import WelcomePage
from .pages.import_page import ImportPage
from .pages.screening import ScreeningPage
from .pages.duplicates import ManageDuplicatesPage
from .pages.dashboard import DashboardPage
from .pages.reports import ReportsPage, SheetMappingDialog

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    MAX_RECENT = 5
    MAX_UNDO = 20

    def __init__(self, settings=None):
        """`settings` can be injected (tests pass a throw-away INI file so they
        never read or overwrite the user's real recent-projects/last-project)."""
        super().__init__()
        self.settings = settings if settings is not None else QSettings(ORG_NAME, APP_NAME)
        self.setWindowTitle("SLR Assistant")
        self.setMinimumSize(QSize(1080, 660))
        self.store = None
        self.dark_mode = self.settings.value("dark_mode", True, type=bool)
        self.undo_stack = []
        self.redo_stack = []

        geometry = self.settings.value("window_geometry")
        if geometry:
            self.restoreGeometry(geometry)
        else:
            self.resize(1280, 820)

        self._build_menu()

        central = QWidget()
        self.setCentralWidget(central)
        shell = QHBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        self.sidebar = Sidebar(self.go, lambda: self.open_project(), self.toggle_theme)
        shell.addWidget(self.sidebar)

        self.stack = QStackedWidget()
        shell.addWidget(self.stack, 1)

        # Toast notifications float over the central widget (bottom-right),
        # used for routine "that worked" feedback instead of a blocking
        # QMessageBox -- see show_toast() below.
        self.toast_manager = ToastManager(central, accent_hex=_DARK["accent"])

        self.welcome_page = WelcomePage(
            lambda: self.open_project(), self.open_project, self.recent_list)
        self.dashboard_page = DashboardPage(self.get_store, show_toast=self.show_toast)
        self.import_page = ImportPage(self.get_store, self.set_status, self.push_undo,
                                      on_merged=self._on_import_merged)
        self.screening_page = ScreeningPage(self.get_store, self.set_status, self.push_undo)
        self.dup_page = ManageDuplicatesPage(self.get_store, self.set_status, self.push_undo)
        self.reports_page = ReportsPage(self.get_store, self.export_new_excel, self.sync_existing_tracker)

        for page in (self.welcome_page, self.dashboard_page, self.import_page,
                     self.screening_page, self.dup_page, self.reports_page):
            self.stack.addWidget(page)

        self.page_index = {"welcome": 0, "dashboard": 1, "import": 2,
                           "screening": 3, "duplicates": 4, "reports": 5}

        self.statusbar = QStatusBar()
        self.setStatusBar(self.statusbar)
        self.project_status_label = QLabel("No project open")
        self.statusbar.addPermanentWidget(self.project_status_label)

        QShortcut(QKeySequence("Ctrl+O"), self, activated=lambda: self.open_project())
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.screening_page.refresh)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=self.export_new_excel)
        QShortcut(QKeySequence("Ctrl+Z"), self, activated=self.undo)
        QShortcut(QKeySequence("Ctrl+Y"), self, activated=self.redo)
        QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self.redo)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self.open_command_palette)

        self.update_undo_redo_actions()
        self.sidebar.set_project(None)
        self.apply_theme()
        self.go_welcome()

        last = self.settings.value("last_project", "")
        if last and os.path.exists(last):
            self.open_project(path=last)
        else:
            self.set_status("Ready. Open or create a project to begin.")

    def _build_menu(self):
        style = self.style()
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        self.act_open = QAction(style.standardIcon(QStyle.SP_DialogOpenButton), "&Open / Create Project\u2026", self)
        self.act_open.triggered.connect(lambda: self.open_project())
        file_menu.addAction(self.act_open)
        self.recent_menu = QMenu("Recent Projects", self)
        file_menu.addMenu(self.recent_menu)
        file_menu.addSeparator()
        self.act_export_new = QAction(style.standardIcon(QStyle.SP_FileIcon), "&New PRISMA Excel Report\u2026", self)
        self.act_export_new.triggered.connect(self.export_new_excel)
        file_menu.addAction(self.act_export_new)
        self.act_sync_tracker = QAction(style.standardIcon(QStyle.SP_DriveFDIcon), "&Sync Into Existing Tracker\u2026", self)
        self.act_sync_tracker.triggered.connect(self.sync_existing_tracker)
        file_menu.addAction(self.act_sync_tracker)
        file_menu.addSeparator()
        act_exit = QAction("Exit", self)
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        edit_menu = menubar.addMenu("&Edit")
        self.act_undo = QAction(style.standardIcon(QStyle.SP_ArrowBack), "&Undo", self)
        self.act_undo.triggered.connect(self.undo)
        edit_menu.addAction(self.act_undo)
        self.act_redo = QAction(style.standardIcon(QStyle.SP_ArrowForward), "&Redo", self)
        self.act_redo.triggered.connect(self.redo)
        edit_menu.addAction(self.act_redo)

        view_menu = menubar.addMenu("&View")
        self.act_theme = QAction("Toggle Dark / Light Mode", self)
        self.act_theme.triggered.connect(self.toggle_theme)
        view_menu.addAction(self.act_theme)
        act_palette = QAction("Command Palette\u2026\tCtrl+K", self)
        act_palette.triggered.connect(self.open_command_palette)
        view_menu.addAction(act_palette)

    # -- navigation --------------------------------------------------------

    def go(self, key):
        if self.store is None and key != "welcome":
            return
        idx = self.page_index.get(key)
        if idx is None:
            return
        self.stack.setCurrentIndex(idx)
        self.sidebar.set_active("welcome" if key == "welcome" else key)
        page = self.stack.widget(idx)
        if hasattr(page, "on_shown"):
            page.on_shown()

    def go_welcome(self):
        self.stack.setCurrentIndex(self.page_index["welcome"])
        self.sidebar.set_active(None)
        self.welcome_page.refresh()

    def go_import(self):
        self.go("import")

    # -- command palette -----------------------------------------------------

    def open_command_palette(self):
        if self.store is None:
            return
        actions = [
            ("Go to Dashboard", lambda: self.go("dashboard")),
            ("Go to Import & Merge", lambda: self.go("import")),
            ("Go to Screening", lambda: self.go("screening")),
            ("Go to Manage Duplicates", lambda: self.go("duplicates")),
            ("Go to Reports & Export", lambda: self.go("reports")),
            ("Export PRISMA workbook\u2026", self.export_new_excel),
            ("Sync into existing tracker\u2026", self.sync_existing_tracker),
            ("Undo", self.undo),
            ("Redo", self.redo),
            ("Toggle dark / light mode", self.toggle_theme),
        ]
        palette = CommandPalette(actions, parent=self)
        center_x = self.geometry().center().x() - palette.width() // 2
        top_y = self.geometry().top() + 90
        palette.move(center_x, top_y)
        palette.exec()

    # -- toasts ------------------------------------------------------------

    def show_toast(self, title, subtitle=""):
        """Non-blocking success/info notice. Use for routine 'that worked'
        feedback; keep QMessageBox for errors and destructive-action
        confirmations, which genuinely need an explicit acknowledgment."""
        self.toast_manager.show_success(title, subtitle)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.toast_manager.reposition()

    def _on_import_merged(self, n_total, n_dup):
        self.show_toast(f"Merged {n_total} records", f"{n_dup} flagged as duplicates")

    # -- plumbing ------------------------------------------------------------

    def get_store(self):
        return self.store

    def set_status(self, msg):
        self.statusbar.showMessage(msg)

    def push_undo(self):
        if self.store is None:
            return
        self.undo_stack.append(self.store.snapshot())
        if len(self.undo_stack) > self.MAX_UNDO:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self.update_undo_redo_actions()

    def undo(self):
        if not self.undo_stack or self.store is None:
            return
        self.redo_stack.append(self.store.snapshot())
        self.store.restore_snapshot(self.undo_stack.pop())
        self._after_data_change("Undone.")

    def redo(self):
        if not self.redo_stack or self.store is None:
            return
        self.undo_stack.append(self.store.snapshot())
        self.store.restore_snapshot(self.redo_stack.pop())
        self._after_data_change("Redone.")

    def _after_data_change(self, msg):
        current = self.stack.currentWidget()
        if current is self.screening_page:
            self.screening_page.refresh()
        elif current is self.dup_page:
            self.dup_page.refresh()
        elif current is self.dashboard_page:
            self.dashboard_page.refresh()
        self.set_status(msg)
        self.update_undo_redo_actions()

    def update_undo_redo_actions(self):
        self.act_undo.setEnabled(bool(self.undo_stack))
        self.act_redo.setEnabled(bool(self.redo_stack))

    def recent_list(self):
        recents = self.settings.value("recent_projects", []) or []
        if isinstance(recents, str):
            recents = [recents]
        return recents

    def refresh_recent_menu(self):
        self.recent_menu.clear()
        recents = self.recent_list()
        if not recents:
            empty = QAction("(empty)", self)
            empty.setEnabled(False)
            self.recent_menu.addAction(empty)
            return
        for p in recents:
            action = QAction(os.path.basename(p), self)
            action.setToolTip(p)
            action.triggered.connect(lambda checked=False, path=p: self.open_project(path))
            self.recent_menu.addAction(action)

    def add_recent(self, path):
        recents = [path] + [p for p in self.recent_list() if p != path]
        self.settings.setValue("recent_projects", recents[:self.MAX_RECENT])
        self.refresh_recent_menu()

    def remove_recent(self, path):
        self.settings.setValue("recent_projects", [p for p in self.recent_list() if p != path])
        self.refresh_recent_menu()

    # -- theme / lifecycle -----------------------------------------------------

    def toggle_theme(self):
        self.dark_mode = not self.dark_mode
        self.settings.setValue("dark_mode", self.dark_mode)
        self.apply_theme()

    def apply_theme(self):
        palette = _DARK if self.dark_mode else _LIGHT
        QApplication.instance().setStyleSheet(build_qss(palette))
        self.screening_page.set_theme(self.dark_mode)
        self.dashboard_page.set_theme(self.dark_mode)
        self.sidebar.set_theme_text("Switch to {} mode".format(
            "light" if self.dark_mode else "dark"))
        self.sidebar.refresh_nav_icons(palette["accent"], palette["muted"])
        self.toast_manager.set_accent(palette["accent"])

    def closeEvent(self, event):
        if self.import_page.batch and any(not r.skipped for r in self.import_page.batch.records):
            answer = QMessageBox.question(
                self, "Unmerged import",
                "You have staged records that have not been merged into the project. Exit anyway?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                event.ignore()
                return
        self.settings.setValue("window_geometry", self.saveGeometry())
        if self.store is not None:
            self.store.close()
            self.store = None
        logger.info("SLR Assistant closing.")
        event.accept()

    # -- project management ------------------------------------------------------

    def open_project(self, path=None):
        if path is not None and not os.path.exists(path):
            QMessageBox.warning(self, "Project not found", f"The project file no longer exists:\n{path}")
            self.remove_recent(path)
            return
        if path is None:
            dlg = QFileDialog(self, "Open or create project")
            dlg.setAcceptMode(QFileDialog.AcceptSave)
            dlg.setOption(QFileDialog.DontConfirmOverwrite, True)
            dlg.setNameFilters(["SLR project (*.slrdb *.sqlite *.db)", "All files (*)"])
            if not dlg.exec():
                return
            path = dlg.selectedFiles()[0]
            if not os.path.splitext(path)[1]:
                path += ".slrdb"

        try:
            store = slr_core.Store(path)  # L8: validates SQLite header up front
        except ValueError as e:
            QMessageBox.critical(self, "Cannot open project", str(e))
            self.remove_recent(path)
            return
        except Exception as e:
            logger.exception("Failed to open project '%s'", path)
            QMessageBox.critical(self, "Cannot open project", str(e))
            return

        if self.store is not None:
            self.store.close()
        self.store = store
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.update_undo_redo_actions()
        self.project_status_label.setText(os.path.basename(path))
        self.sidebar.set_project(os.path.basename(path))
        self.settings.setValue("last_project", path)
        self.add_recent(path)
        self.import_page.batch = None
        self.import_page.table.setRowCount(0)
        self.welcome_page.refresh()
        self.go_import()
        self.set_status(f"Project open: {path}")
        logger.info("Opened project '%s'.", path)

    # -- export / sync -------------------------------------------------------------

    def export_new_excel(self):
        if self.store is None:
            QMessageBox.warning(self, "No project open", "Open or create a project first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PRISMA report", "prisma_report.xlsx", "Excel workbook (*.xlsx)")
        if not path:
            return
        self._run_excel_job(
            fn=lambda: self.store.export_prisma_xlsx(path),
            busy_text="Exporting PRISMA report\u2026",
            on_ok=lambda _result: self._on_export_done(path),
            on_fail=lambda msg: self._on_excel_job_failed("Export failed", msg,
                                                          "PRISMA export failed"),
        )

    def _on_export_done(self, path):
        logger.info("Exported PRISMA report to '%s'.", path)
        self.set_status(f"PRISMA report written to {path}")
        self.show_toast("Export complete", path)

    def sync_existing_tracker(self):
        if self.store is None:
            QMessageBox.warning(self, "No project open", "Open or create a project first.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Select tracker workbook", "", "Excel workbook (*.xlsx)")
        if not path:
            return
        try:
            sheets = slr_core.get_sheet_names(path)
        except Exception as e:
            QMessageBox.critical(self, "Cannot read tracker", str(e))
            return
        dlg = SheetMappingDialog(sheets, self)
        if not dlg.exec():
            return
        raw, ta, ft = dlg.get_selection()

        # The data-loss confirmation must be asked HERE, on the GUI thread.
        # It used to be a callback invoked from inside the worker thread,
        # which created a QMessageBox off the GUI thread (unsupported by Qt).
        try:
            issues = slr_core.check_tracker_warnings(path)
        except Exception as e:
            QMessageBox.critical(self, "Cannot read tracker", str(e))
            return
        if issues:
            answer = QMessageBox.question(
                self, "Tracker may lose formatting",
                "This tracker {}.\n\nRewriting the file cannot preserve these features. "
                "A timestamped backup of the original will be saved alongside it. "
                "Continue anyway?".format("; ".join(issues)),
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if answer != QMessageBox.Yes:
                return

        self._run_excel_job(
            fn=lambda: self.store.sync_to_tracker(path, raw, ta, ft),
            busy_text="Syncing tracker\u2026",
            on_ok=lambda n: self._on_sync_done(path, n),
            on_fail=lambda msg: self._on_excel_job_failed("Sync failed", msg,
                                                          "Tracker sync failed for '%s'" % path),
        )

    def _on_sync_done(self, path, n):
        logger.info("Synced %d record(s) into '%s'.", n, path)
        self.set_status(f"Synced {n} record(s) into {path}")
        self.show_toast(f"Synced {n} record(s)", "Backup saved alongside the tracker file")

    def _run_excel_job(self, fn, busy_text, on_ok, on_fail):
        """Run an Excel export/sync callable on a background QThread with a
        modal, indeterminate progress dialog, instead of blocking the GUI
        thread for the duration of the openpyxl I/O.
        """
        previous = getattr(self, "_excel_worker", None)
        if previous is not None and previous.isRunning():
            self.set_status("Another export/sync is still running.")
            return
        progress = QProgressDialog(busy_text, None, 0, 0, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setCancelButton(None)

        worker = slr_workers.CallableWorker(fn)
        self._excel_worker = worker

        def finish(fn_to_call, *args):
            progress.close()
            fn_to_call(*args)

        worker.finished_ok.connect(lambda result: finish(on_ok, result))
        worker.failed.connect(lambda msg: finish(on_fail, msg))
        worker.start()

    def _on_excel_job_failed(self, title, message, log_message):
        logger.error("%s: %s", log_message, message)
        QMessageBox.critical(self, title, message)
