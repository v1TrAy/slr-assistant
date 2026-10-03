# -*- coding: utf-8 -*-
"""
slr_core
--------
Backend package for the SLR Assistant app, fully independent from the UI.

    parsers.py     -- CSV/RIS/XLSX -> normalized record dicts
    dedup.py       -- fuzzy duplicate-title matching (DuplicateIndex) and
                      DOI normalization (normalize_doi)
    staging.py     -- ImportBatch: staged import pipeline (parse -> DOI-first
                      match -> review/skip -> commit), UI-independent
    repository.py  -- RecordRepository: the only class that touches SQLite
    excel_io.py    -- PRISMA export + tracker sync (openpyxl only)

`Store`, defined below, is a thin facade that composes a RecordRepository
with the excel_io functions and exposes the same public method names and
signatures as the old monolithic Store class, so existing UI code keeps
working unmodified.
"""

from .parsers import (
    guess_db_name,
    normalize_uploaded_file,
    parse_csv_bytes,
    parse_xlsx_bytes,
    parse_ris_bytes,
    parse_dictrows,
    HEADER_ALIASES,
    DATABASE_HINTS,
    RIS_TAG_MAP,
    SUPPORTED_EXTENSIONS,
)
from .dedup import (
    DuplicateIndex,
    find_fuzzy_duplicate,
    title_key,
    normalize_doi,  # <- DOI
    DEFAULT_DUP_THRESHOLD,
)
from .repository import RecordRepository, RECORD_COLUMNS, DB_SCHEMA
from .staging import ImportBatch, StagedRecord
from .excel_io import (
    get_sheet_names,
    guess_sheet_name,
    find_or_create_column,
    export_prisma_xlsx as _export_prisma_xlsx,
    sync_to_tracker as _sync_to_tracker,
    check_tracker_warnings,
    SYNC_FIELD_ALIASES,
)

__all__ = [
    "Store", "RecordRepository", "DuplicateIndex", "find_fuzzy_duplicate",
    "title_key", "normalize_doi",  # <- DOI
    "guess_db_name", "normalize_uploaded_file", "parse_csv_bytes",
    "parse_xlsx_bytes", "parse_ris_bytes", "parse_dictrows", "get_sheet_names",
    "guess_sheet_name", "find_or_create_column", "check_tracker_warnings",
    "RECORD_COLUMNS", "DB_SCHEMA",
    "HEADER_ALIASES", "DATABASE_HINTS", "RIS_TAG_MAP", "SYNC_FIELD_ALIASES",
    "DEFAULT_DUP_THRESHOLD", "SUPPORTED_EXTENSIONS", "ImportBatch", "StagedRecord",
]


class Store:
    """Backward-compatible facade over RecordRepository + excel_io.

    Every method here just delegates to the repository or to a plain
    excel_io function. No SQL or openpyxl code lives in this class
    anymore -- it exists purely to keep the public API stable for the UI.
    """

    def __init__(self, path):
        self._repo = RecordRepository(path)

    @property
    def repo(self):
        return self._repo

    @property
    def path(self):
        return self._repo.path

    def close(self):
        self._repo.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # -- reads -------------------------------------------------------
    def existing_titles(self):
        return self._repo.existing_titles()

    def existing_dois(self):
        return self._repo.existing_dois()

    def get_abstract(self, rid):
        return self._repo.get_abstract(rid)

    def all_titles_map(self):
        return self._repo.all_titles_map()

    def id_to_pos_map(self):
        return self._repo.id_to_pos_map()

    def all_records_df(self, exclude_duplicates=False):
        return self._repo.all_records_df(exclude_duplicates=exclude_duplicates)

    def duplicates_df(self):
        return self._repo.duplicates_df()

    def prisma_counts(self):
        return self._repo.prisma_counts()

    def consistency_warnings(self):
        return self._repo.consistency_warnings()

    # -- writes --------------------------------------------------------
    def update_decision(self, rid, field, value):
        return self._repo.update_decision(rid, field, value)

    def insert_record(self, rec, is_dup="No", dup_of=None):
        return self._repo.insert_record(rec, is_dup=is_dup, dup_of=dup_of)

    def insert_records_bulk(self, items):
        return self._repo.insert_records_bulk(items)

    def delete_record(self, rid):
        return self._repo.delete_record(rid)

    def delete_records(self, ids):
        return self._repo.delete_records(ids)

    def update_decisions_from_df(self, df):
        return self._repo.update_decisions_from_df(df)

    def set_duplicate_flag(self, rid, is_dup, dup_of=None):
        return self._repo.set_duplicate_flag(rid, is_dup, dup_of=dup_of)

    def unmark_duplicate(self, rid):
        return self._repo.unmark_duplicate(rid)

    def snapshot(self):
        return self._repo.snapshot()

    def restore_snapshot(self, snap):
        return self._repo.restore_snapshot(snap)

    # -- Excel I/O ------------------------------------------------------
    def export_prisma_xlsx(self, out_path):
        return _export_prisma_xlsx(self._repo, out_path)

    def sync_to_tracker(self, tracker_path, raw_sheet, ta_sheet, ft_sheet, on_warning=None):
        return _sync_to_tracker(self._repo, tracker_path, raw_sheet, ta_sheet, ft_sheet,
                                on_warning=on_warning)