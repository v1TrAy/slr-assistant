
"""
slr_core/excel_io.py
----------------------
Everything that talks to openpyxl / Excel files: sheet discovery, the
PRISMA export workbook, and syncing into an existing tracker workbook.

These functions take anything satisfying the RecordSource protocol (the
RecordRepository, or a test fake with no SQLite behind it at all) as a
plain argument, so the repository stays free of any openpyxl dependency.
"""

import os
import re
import shutil
import logging
import tempfile
from datetime import datetime
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


# Leading characters that Excel/LibreOffice/Google Sheets will interpret as
# the start of a formula if a cell's value begins with one of them. Titles,
# authors, journals and DOI/URL fields all come from THIRD-PARTY database
# exports (untrusted input), so any of those characters at position 0 is
# treated as attacker- or export-tool-controlled formula injection (CWE-1236)
# rather than legitimate data, and is neutralized before being written.
_FORMULA_TRIGGER_CHARS = ("=", "+", "-", "@", "\t", "\r")


def _xlsx_safe(value):
    """Return `value` coerced to a string that Excel cannot interpret as a
    formula, by prefixing a single quote when it starts with a formula
    trigger character. None/empty values pass through unchanged so numeric
    columns (IDs, years) are not accidentally stringified.
    """
    if value is None or value == "":
        return value
    s = str(value)
    if s[:1] in _FORMULA_TRIGGER_CHARS:
        return "'" + s
    return s


def _xlsx_year(value):
    """Coerce a year value to a real int for Excel.

    `year` is stored as TEXT in SQLite (parsers.py extracts it as a plain
    string, e.g. "2023"), so writing it straight through leaves openpyxl
    writing a Python str into the cell -- Excel then stores it as a TEXT
    cell, not a number. That breaks numeric sort/filter and shows Excel's
    green "Number Stored as Text" warning triangle on every row.

    Converts clean 4-digit-ish integer strings to a real int so the cell
    is genuinely numeric. Anything that isn't a clean integer (empty,
    "n.d.", a year range like "2019-2020", stray whitespace-only junk from
    a messy export) is left as-is rather than raised or silently dropped --
    better a text cell for a handful of odd rows than a crash on export.
    """
    if value is None or value == "":
        return value
    s = str(value).strip()
    try:
        return int(s)
    except (ValueError, TypeError):
        return value


class RecordSource(Protocol):
    """The read-only repository surface these functions need. Declared
    explicitly (instead of implicit duck typing) so the contract is
    checkable by type checkers and self-documenting for test fakes."""
    def id_to_pos_map(self): ...
    def all_titles_map(self): ...
    def fetch_all_for_export(self): ...
    def fetch_screening(self): ...
    def fetch_extraction(self): ...
    def fetch_for_tracker_sync(self): ...
    def fetch_decisions_for_tracker_sync(self): ...
    def prisma_counts(self): ...
    def database_counts(self): ...
    def ft_exclusion_reasons(self): ...
    def consistency_warnings(self): ...


SYNC_FIELD_ALIASES = {
    "database": ["database", "source database", "source", "db"],
    "title": ["title", "document title", "article title", "paper title", "chapter title"],
    "authors": ["authors", "author", "author(s)", "author names", "author full names"],
    "year": ["year", "publication year", "pub year", "year published"],
    "journal": ["journal/source", "source title", "journal", "publication title"],
    "doi": ["doi", "doi link", "digital object identifier", "link", "url"],
    "id": ["id", "record id"],
    "ta_decision": ["decision", "ta decision", "title/abstract decision", "screening decision"],
    "ft_decision": ["final decision", "ft decision", "full-text decision", "full text decision"],
    "reason": ["exclusion reason", "reason", "notes"],
    "is_duplicate": ["is duplicate?", "is duplicate", "duplicate?", "duplicate"],
    "duplicate_of": ["duplicate of id", "duplicate of", "duplicate id"],
    "duplicate_of_title": ["duplicate of title", "matched title", "duplicate title"],
}


def get_sheet_names(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def guess_sheet_name(names, keywords, fallback_idx=0):
    for kw in keywords:
        for n in names:
            if kw.lower() in n.lower():
                return n
    return names[fallback_idx] if names else ""


def _sheet_headers(ws):
    headers = {}
    for c in range(1, ws.max_column + 1):
        header = str(ws.cell(row=1, column=c).value or "").strip().lower()
        if header:
            headers[c] = header
    return headers


def _resolve_columns(ws, spec):
    """Map several logical fields onto the columns of one sheet, without
    letting two fields claim the same physical column.

    `spec` is an ordered list of (field_key, canonical_name); the order
    is the priority in which fields claim columns. For each field we rank
    candidate columns (exact alias match first, then word-boundary match;
    longer/more specific aliases first), and each field takes its best
    still-unclaimed column. Fields with no candidate get a fresh column
    with the canonical header.

    This fixes e.g. a "Source ID" header being word-matched by BOTH the
    `id` field ("id") and the `database` field ("source") and having two
    different values written into one column.
    """
    headers = _sheet_headers(ws)
    claimed = set()
    result = {}
    for field_key, canonical in spec:
        scored = []
        for c, header in headers.items():
            for alias in SYNC_FIELD_ALIASES[field_key]:
                a = alias.lower()
                if header == a:
                    scored.append((0, -len(a), c))
                elif re.search(r"\b{}\b".format(re.escape(a)), header):
                    scored.append((1, -len(a), c))
        scored.sort()
        col = next((c for _, _, c in scored if c not in claimed), None)
        if col is None:
            col = ws.max_column + 1
            ws.cell(row=1, column=col, value=canonical)
            headers[col] = canonical.lower()
        claimed.add(col)
        result[field_key] = col
    return result


def find_or_create_column(ws, field_key, canonical_name):
    """Find an existing column matching one of the known aliases for
    `field_key`, or create a new one.

    Matching is exact-header-first, then whole-word only -- never a raw
    substring check (a substring check on short aliases like "id" would
    happily match unrelated headers such as "Guide" or "Author Bio" and
    silently corrupt the wrong column).

    Note: when mapping SEVERAL fields onto the same sheet, prefer
    _resolve_columns(), which additionally prevents two fields from
    claiming the same column.
    """
    return _resolve_columns(ws, [(field_key, canonical_name)])[field_key]



def _row_is_empty(ws, r):
    return all(ws.cell(row=r, column=c).value in (None, "") for c in range(1, ws.max_column + 1))


def _upsert_rows(ws, cols, updates):
    """Write `updates` -- [(record_id, {field_key: value})] -- into `ws`,
    matching existing rows by the value in the ID column.

    * A record whose id is already in the sheet is updated IN PLACE, so any
      columns the user added next to ours (notes, second reviewer, colours)
      stay attached to the right paper. The previous implementation cleared
      the managed columns and rewrote them top-down, which shifted every
      managed value relative to the user's own columns whenever a record
      was added, removed or re-flagged.
    * A new record is appended on the first fully empty row below the data.
    * A row whose id is no longer part of the update set has only its
      managed cells cleared; the rest of the row is left alone.
    """
    id_col = cols["id"]
    by_id, last = _index_rows_by_id(ws, id_col)
    next_row = last + 1
    seen = set()
    for rec_id, values in updates:
        row = by_id.get(rec_id)
        if row is None:
            while not _row_is_empty(ws, next_row):
                next_row += 1
            row = next_row
            next_row += 1
            by_id[rec_id] = row
        seen.add(rec_id)
        for key, value in values.items():
            ws.cell(row=row, column=cols[key], value=value)
    managed = tuple(cols.values())
    for rec_id, row in by_id.items():
        if rec_id not in seen:
            for c in managed:
                # NB: ws.cell(..., value=None) is a no-op in openpyxl (it only
                # assigns non-None values); the old _clear_columns() relied on
                # it and so never actually cleared anything.
                ws.cell(row=row, column=c).value = None


def _as_int(value):
    """Cell value -> int, or None when it is not a whole number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else None
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _index_rows_by_id(ws, id_col):
    """({record id -> row number}, last_used_row) for the data rows of a
    sheet. The first row carrying an id wins; rows without a numeric id
    are left alone."""
    by_id = {}
    last = 1
    for r in range(2, ws.max_row + 1):
        rid = _as_int(ws.cell(row=r, column=id_col).value)
        if rid is not None:
            by_id.setdefault(rid, r)
            last = max(last, r)
    return by_id, last


def _data_loss_warnings(wb):
    """Detect workbook features openpyxl cannot round-trip."""
    issues = []
    if getattr(wb, "vba_archive", None) is not None:
        issues.append("contains VBA macros")
    if any(getattr(ws, "_images", None) or getattr(ws, "_charts", None) for ws in wb.worksheets):
        issues.append("contains images or charts on one or more sheets")
    if any(getattr(ws, "_pivots", None) for ws in wb.worksheets):
        issues.append("contains pivot tables")
    return issues


def check_tracker_warnings(tracker_path):
    """Return the list of workbook features that a rewrite would lose
    (macros, images/charts, pivot tables).

    Call this on the GUI thread BEFORE starting the sync worker and ask the
    user there. The old design invoked a confirmation callback from inside
    the worker thread, i.e. it created a QMessageBox off the GUI thread,
    which Qt does not support."""
    import openpyxl
    wb = openpyxl.load_workbook(tracker_path)
    try:
        return _data_loss_warnings(wb)
    finally:
        wb.close()


def _atomic_save_with_backup(wb, path, keep_backups: int = 5):
    """Save `wb` to `path` without risking the user's original file.

    1. write to a temp file in the same directory (same filesystem),
    2. copy the original to a TIMESTAMPED backup (path.YYYYmmdd-HHMMSS.bak),
    3. atomically replace the original with the temp file.
    If anything fails midway, the original file is left untouched.

    Each sync used to overwrite a single '.bak' file, so only the most
    recent pre-sync state was ever recoverable -- a second sync destroyed
    the only backup of the first. Timestamped backups keep a short history
    instead; `keep_backups` bounds how many are retained per tracker file
    so repeated syncs don't accumulate disk usage forever.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp = tempfile.mkstemp(prefix=".slr_sync_", suffix=".xlsx", dir=directory)
    os.close(fd)
    try:
        wb.save(tmp)
        if os.path.exists(path):
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            backup_path = "{}.{}.bak".format(path, stamp)
            shutil.copy2(path, backup_path)
            _prune_old_backups(path, keep_backups)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _prune_old_backups(path, keep_backups: int) -> None:
    """Keep only the `keep_backups` most recent timestamped backups for
    `path`, deleting older ones. Best-effort: a failure to delete an old
    backup (e.g. permissions) must never abort the sync itself."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    base = os.path.basename(path)
    prefix = base + "."
    try:
        candidates = sorted(
            f for f in os.listdir(directory) if f.startswith(prefix) and f.endswith(".bak")
        )
    except OSError:
        return
    stale = candidates[:-keep_backups] if keep_backups > 0 else candidates
    for name in stale:
        try:
            os.remove(os.path.join(directory, name))
        except OSError:
            pass


def export_prisma_xlsx(repo: RecordSource, out_path):
    """Write a fresh PRISMA workbook (All_Records / Screening /
    Data_Extraction / PRISMA_Summary) from the repository's current state."""
    import openpyxl
    from .repository import clean_dup_of

    id_to_pos = repo.id_to_pos_map()
    titles = repo.all_titles_map()
    wb = openpyxl.Workbook()

    ws1 = wb.active
    ws1.title = "All_Records"
    ws1.append(["ID", "Title", "Authors", "Year", "Journal/Source", "Database", "DOI/Link",
                "Is Duplicate?", "Duplicate of ID", "Duplicate of Title"])
    for row in repo.fetch_all_for_export():
        rid, title, authors, year, journal, dbn, doi, is_dup, dup_of = row
        dup_of = clean_dup_of(rid, dup_of)
        dup_pos = id_to_pos.get(dup_of, "") if dup_of else ""
        dup_title = titles.get(dup_of, "") if dup_of else ""
        ws1.append([id_to_pos[rid], _xlsx_safe(title), _xlsx_safe(authors), _xlsx_year(year), _xlsx_safe(journal),
                    _xlsx_safe(dbn), _xlsx_safe(doi), is_dup, dup_pos, _xlsx_safe(dup_title)])

    ws2 = wb.create_sheet("Screening")
    ws2.append(["ID", "Title", "TA Decision", "TA Reason", "FT Decision", "FT Reason"])
    for row in repo.fetch_screening():
        rid, title, ta_dec, ta_reason, ft_dec, ft_reason = row
        ws2.append([id_to_pos[rid], _xlsx_safe(title), ta_dec, _xlsx_safe(ta_reason),
                    ft_dec, _xlsx_safe(ft_reason)])

    ws3 = wb.create_sheet("Data_Extraction")
    ws3.append(["ID", "Title", "Study Design", "Population", "Variables", "Findings", "Limitations", "Notes"])
    for row in repo.fetch_extraction():
        rid = row[0]
        ws3.append([id_to_pos[rid]] + [_xlsx_safe(v) for v in row[1:]])

    ws4 = wb.create_sheet("PRISMA_Summary")
    ws4.append(["PRISMA Stage", "Count"])
    for k, v in repo.prisma_counts().items():
        ws4.append([k, v])

    ws5 = wb.create_sheet("PRISMA_Detail")
    ws5.append(["Records identified, by source database", "Identified", "Duplicates removed"])
    for name, n, n_dup in repo.database_counts():
        ws5.append([_xlsx_safe(name), n, n_dup])
    ws5.append([])
    ws5.append(["Full-text exclusions, by reason", "Count"])
    for reason, n in repo.ft_exclusion_reasons():
        ws5.append([_xlsx_safe(reason), n])
    warnings = repo.consistency_warnings()
    if warnings:
        ws5.append([])
        ws5.append(["Data-consistency warnings"])
        for w in warnings:
            ws5.append([w])

    wb.save(out_path)


def sync_to_tracker(repo: RecordSource, tracker_path, raw_sheet, ta_sheet, ft_sheet,
                    on_warning=None):
    """Overwrite the managed columns of an existing tracker workbook with
    the repository's current state, matching columns by header text and
    creating any that are missing. Sheets that do not exist are CREATED
    (the sheet-mapping dialog lets users type new sheet names).

    Safety: the original file is first copied to a timestamped backup and
    the replacement is atomic (temp file + os.replace), so an interrupted
    or failing sync can no longer destroy the tracker, and a second sync
    no longer overwrites the only backup of the first.

    `on_warning(issues) -> bool` (optional) is called BEFORE any mutation
    if the workbook contains features openpyxl cannot preserve (macros,
    images, charts, pivot tables); return False to abort. Returns the
    number of synced records, or 0 if the callback vetoed the sync.
    """
    import openpyxl
    from .repository import clean_dup_of

    wb = openpyxl.load_workbook(tracker_path)
    try:
        issues = _data_loss_warnings(wb)
        if issues:
            logger.warning("Tracker '%s' %s; openpyxl cannot preserve these on save.",
                           tracker_path, "; ".join(issues))
            if on_warning is not None and not on_warning(issues):
                return 0

        for needed in (raw_sheet, ta_sheet, ft_sheet):
            if needed not in wb.sheetnames:
                wb.create_sheet(needed)

        id_to_pos = repo.id_to_pos_map()
        titles = repo.all_titles_map()

        ws_raw = wb[raw_sheet]
        cols = _resolve_columns(ws_raw, [
            ("id", "ID"), ("database", "Database"), ("title", "Title"),
            ("authors", "Authors"), ("year", "Year"), ("journal", "Journal/Source"),
            ("doi", "DOI/Link"), ("is_duplicate", "Is Duplicate?"),
            ("duplicate_of", "Duplicate of ID"), ("duplicate_of_title", "Duplicate of Title"),
        ])

        rows = repo.fetch_for_tracker_sync()
        raw_updates = []
        for rec_id, dbn, title, authors, year, journal, doi, is_dup, dup_of in rows:
            dup_of = clean_dup_of(rec_id, dup_of)
            dup_pos = id_to_pos.get(dup_of) if dup_of else None
            raw_updates.append((id_to_pos[rec_id], {
                "id": id_to_pos[rec_id],
                "database": _xlsx_safe(dbn),
                "title": _xlsx_safe(title),
                "authors": _xlsx_safe(authors),
                "year": _xlsx_year(year),
                "journal": _xlsx_safe(journal),
                "doi": _xlsx_safe(doi),
                "is_duplicate": is_dup or "No",
                "duplicate_of": dup_pos if dup_pos else "",
                "duplicate_of_title": _xlsx_safe(titles.get(dup_of, "")) if dup_of else "",
            }))
        _upsert_rows(ws_raw, cols, raw_updates)

        ws_ta = wb[ta_sheet]
        ta_cols = _resolve_columns(ws_ta, [
            ("id", "ID"), ("ta_decision", "Decision"), ("reason", "Exclusion Reason")])

        ws_ft = wb[ft_sheet]
        ft_cols = _resolve_columns(ws_ft, [
            ("id", "ID"), ("ft_decision", "Final Decision"), ("reason", "Exclusion Reason")])

        ta_updates, ft_updates = [], []
        for rec_id, is_dup, ta_dec, ta_reason, ft_dec, ft_reason in repo.fetch_decisions_for_tracker_sync():
            if is_dup == "Yes":
                continue
            pos = id_to_pos[rec_id]
            ta_updates.append((pos, {"id": pos, "ta_decision": ta_dec or "",
                                     "reason": _xlsx_safe(ta_reason) or ""}))
            if ta_dec == "Include":
                ft_updates.append((pos, {"id": pos, "ft_decision": ft_dec or "",
                                          "reason": _xlsx_safe(ft_reason) or ""}))
        _upsert_rows(ws_ta, ta_cols, ta_updates)
        _upsert_rows(ws_ft, ft_cols, ft_updates)

        _atomic_save_with_backup(wb, tracker_path)
        return len(rows)
    finally:
        wb.close()
