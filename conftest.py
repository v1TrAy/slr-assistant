# -*- coding: utf-8 -*-
"""Shared pytest configuration.

A single offscreen QApplication is created for the whole session. Qt
allows only one application object per process, and creating a bare
QCoreApplication in one test makes widget construction crash in another.

Tests must NEVER touch the user's real QSettings (registry on Windows):
use the `isolated_settings` fixture and pass it to MainWindow(settings=...).
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp():
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setOrganizationName("SLRAssistantTest")
    app.setApplicationName("SLRAssistantTest")
    return app


@pytest.fixture
def isolated_settings(tmp_path, qapp):
    from PySide6.QtCore import QSettings

    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
