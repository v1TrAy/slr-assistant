# -*- coding: utf-8 -*-
"""
app_pyside.py
--------------
SLR Assistant -- desktop GUI (PySide6/Qt). Thin entry point only.
All actual UI code lives in the slr_ui package.

Setup:
    pip install -r requirements.txt            # runtime
    pip install -r requirements-dev.txt        # + pytest

Run:
    python app_pyside.py
"""

import sys
import logging
import traceback

from PySide6.QtWidgets import QApplication, QMessageBox

from slr_ui.theme import ORG_NAME, APP_NAME, configure_logging
from slr_ui.main_window import MainWindow

logger = logging.getLogger(__name__)


def _install_excepthook():
    """Log every uncaught exception (with traceback) and tell the user.

    Without this, an exception raised inside a Qt slot is printed to a
    stderr that does not exist when the app is started without a console,
    i.e. the user just sees the app silently stop reacting."""
    def hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.critical("Unhandled exception", exc_info=(exc_type, exc, tb))
        try:
            QMessageBox.critical(
                None, "Unexpected error",
                "Something went wrong. Your data is saved automatically.\n\n"
                "{}: {}\n\nDetails were written to the log file.".format(exc_type.__name__, exc))
        except Exception:  # never let the handler itself raise
            traceback.print_exception(exc_type, exc, tb)
    sys.excepthook = hook


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName(ORG_NAME)
    app.setApplicationName(APP_NAME)
    configure_logging()
    _install_excepthook()
    logger.info("SLR Assistant starting.")
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
