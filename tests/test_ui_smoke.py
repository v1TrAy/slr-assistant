# -*- coding: utf-8 -*-
"""Headless end-to-end smoke test of the Qt layer (offscreen platform)."""

import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402


CSV = (
    '"Document Title",Authors,"Publication Title","Publication Year",Abstract,DOI,"PDF Link"\n'
    '"Effect of <i>p</i> < 0.05 on X","A; B","Conf",2025,"We study X. Results follow.",10.1109/X.1,'
    'https://ieeexplore.ieee.org/stamp/1\n'
    '"Effect of p < 0.05 on X","A; B","Conf",2025,"",10.1109/X.1,\n'
    '"Another paper","C","Journal",2024,"Second abstract.",,\n'
)


def test_end_to_end_import_screen_report(qapp, isolated_settings, tmp_path):
    import slr_core
    import slr_workers
    from slr_ui.main_window import MainWindow

    csv_path = tmp_path / "export2026.01.01-00.00.00.csv"
    csv_path.write_text(CSV, encoding="utf-8")

    win = MainWindow(settings=isolated_settings)
    project = str(tmp_path / "smoke.slrdb")
    slr_core.Store(project).close()          # open_project(path) needs an existing file
    win.open_project(path=project)
    assert win.store is not None

    # -- import: run the worker synchronously, then drive the page as the UI would
    worker = slr_workers.ImportParseWorker([str(csv_path)], win.store.existing_titles(),
                                           win.store.existing_dois(), 0.9, True)
    result = {}
    worker.finished_ok.connect(lambda b, n, s: result.update(batch=b, n=n, stats=s))
    worker.run()
    batch = result["batch"]
    assert result["n"] == 3
    assert [r.is_dup for r in batch.records] == ["No", "Yes", "No"]   # DOI duplicate caught
    assert batch.records[0].rec["database"] == "IEEE Xplore"

    win.import_page.batch = batch
    win.import_page._render_batch()
    win.import_page.commit_batch()
    assert win.store.prisma_counts()["Records identified through database searching"] == 3
    assert win.store.repo.abstract_count() == 2

    # -- screening table + focus view
    win.go("screening")
    sp = win.screening_page
    assert sp.table_view.table.rowCount() == 2            # duplicate excluded
    sp.mode_combo.setCurrentIndex(1)                      # focus view
    fv = sp.focus_view
    assert len(fv.rows) == 2
    assert fv.txt_abstract.toPlainText() == "We study X. Results follow."
    assert fv.lbl_title.text() == "Effect of <i>p</i> < 0.05 on X"      # shown literally
    assert fv.lbl_title.textFormat() == Qt.PlainText
    assert "https://doi.org/10.1109/x.1" in fv.lbl_doi.text()

    first_id = fv.rows[0]["ID"]
    fv.decide("ta_decision", "Include")       # auto-advances to the next undecided record
    assert fv.i == 1
    fv.decide("ft_decision", "Include")       # hotkey on a record that is NOT TA-included: refused
    assert fv.rows[1]["FT Decision"] == ""
    fv.i = 0
    fv.render()
    fv.decide("ft_decision", "Exclude")       # now legitimate
    assert [r["field"] for r in win.store.repo.fetch_decision_log(first_id)] == ["ta_decision", "ft_decision"]

    # -- dashboard / reports / export
    win.go("dashboard")
    counts = win.store.prisma_counts()
    assert counts["Full-text articles assessed"] == 1
    assert win.dashboard_page.card_labels["Duplicates removed"].text() == "1"
    # the only FT exclusion has no reason yet -> exactly that warning, nothing else
    warnings = win.store.consistency_warnings()
    assert len(warnings) == 1 and "no reason" in warnings[0]
    win.go("reports")

    out = tmp_path / "prisma.xlsx"
    win.store.export_prisma_xlsx(str(out))
    assert out.exists()

    # -- undo restores the pre-screening state, and the abstract survives it
    before = win.store.repo.get_abstract(fv.rows[0]["ID"])
    win.undo()
    row = win.store.repo.conn.execute(
        "SELECT ta_decision, ft_decision FROM records WHERE id=?", (fv.rows[0]["ID"],)).fetchone()
    assert tuple(row) == ("", "")
    assert win.store.repo.get_abstract(fv.rows[0]["ID"]) == before != ""
    win.store.close()
    win.store = None
    # the injected settings absorbed everything; nothing leaked to the real store
    assert isolated_settings.value("last_project") == project
