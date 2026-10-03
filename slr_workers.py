
# -*- coding: utf-8 -*-
"""
slr_workers.py
---------------
Background QThread workers so long-running I/O (multi-file parsing,
Excel export/tracker sync) never blocks the Qt GUI event loop.

Previously, ImportPage._process_files() and MainWindow.export_new_excel()/
sync_existing_tracker() ran parsing and openpyxl I/O directly on the GUI
thread (with only a wait cursor to show something was happening). For a
real systematic-review import -- several database exports of thousands of
rows each -- or a large tracker sync, that froze the whole application for
many seconds with no progress feedback and no way to cancel.

Design note: ImportParseWorker never touches the SQLite connection itself.
The caller reads `existing_titles`/`existing_dois` from the repository on
the GUI thread BEFORE starting the worker, and hands them over as plain
Python data. The worker thread only does file I/O and in-memory matching
(slr_core.ImportBatch), so RecordRepository's connection is never accessed
from two threads at the same time during an import. Export/sync DO need
repository reads from the worker thread (fetch_* methods); those go
through RecordRepository's own internal lock (see repository.py), so
concurrent access is still safe even though the GUI thread is idle
(modal progress dialog) for the duration of the job anyway.
"""

import os

from PySide6.QtCore import QThread, Signal

import slr_core


class ImportParseWorker(QThread):
    """Parses a list of files and stages them into an slr_core.ImportBatch
    on a background thread."""

    progress = Signal(int, int, str)          # done, total, current filename
    finished_ok = Signal(object, int, dict)   # ImportBatch, total_parsed, stats
    failed = Signal(str)                      # unexpected/fatal error
    file_error = Signal(str, str)             # filename, per-file error message
    cancelled = Signal()                      # user cancelled; no partial result is delivered

    def __init__(self, paths, existing_titles, existing_dois, threshold, doi_veto, parent=None):
        super().__init__(parent)
        self._paths = list(paths)
        self._existing_titles = existing_titles
        self._existing_dois = existing_dois
        self._threshold = threshold
        self._doi_veto = doi_veto
        self._cancelled = False

    def cancel(self):
        """Best-effort cooperative cancel: stops before starting the next
        file. A file already being parsed still finishes (parsing a single
        file is not itself interruptible mid-way). A cancelled run emits
        `cancelled` and NO result: handing back the half-built batch (as
        this worker used to) let a user merge a partial import believing it
        was complete."""
        self._cancelled = True

    def run(self):
        try:
            batch = slr_core.ImportBatch(self._existing_titles, self._existing_dois,
                                         threshold=self._threshold, doi_veto=self._doi_veto)
            total_parsed = 0
            stats = {}
            total_files = len(self._paths)
            for i, p in enumerate(self._paths, start=1):
                if self._cancelled:
                    break  # reported once, after the loop
                name = os.path.basename(p)
                try:
                    with open(p, "rb") as fh:
                        recs = slr_core.normalize_uploaded_file(name, fh, stats=stats)
                except Exception as e:
                    self.file_error.emit(p, str(e))
                    self.progress.emit(i, total_files, name)
                    continue
                total_parsed += len(recs)
                batch.add_records(recs)
                self.progress.emit(i, total_files, name)
            if self._cancelled:
                self.cancelled.emit()
                return
            self.finished_ok.emit(batch, total_parsed, stats)
        except Exception as e:
            self.failed.emit(str(e))


class CallableWorker(QThread):
    """Runs a single no-argument callable on a background thread and
    reports its return value or exception back to the GUI thread.

    Used for Excel export/tracker sync: the GUI thread builds a small
    closure over `self.store` and the chosen path(s), and this worker
    just calls it off-thread so openpyxl's (potentially slow) I/O never
    blocks the event loop.
    """

    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
        except Exception as e:
            self.failed.emit(str(e))
            return
        self.finished_ok.emit(result)
