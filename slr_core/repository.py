# -*- coding: utf-8 -*-
"""
slr_core/repository.py
------------------------
RecordRepository: the ONLY class in this codebase that talks to SQLite.

Thread safety: the connection is opened with check_same_thread=False so
that a background worker thread (e.g. an Excel export/sync worker) can
read from the same repository the GUI thread wrote to, without
recreating a whole new connection. sqlite3's own per-connection guard is
disabled by that flag, so every public method here acquires `self._lock`
for the duration of its SQL work.

Schema history
--------------
v1  records table (no constraints)
v2  CHECK on is_duplicate + indexes
v3  record_abstracts (abstract text kept OUT of `records` so the in-memory
    undo snapshots stay small), decision_log (append-only audit trail of
    every screening/duplicate change), meta (key/value)

Migrations run inside ONE explicit transaction (SQLite DDL is
transactional) after the database file has been backed up, so an
interrupted migration leaves the original data untouched.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

RECORD_COLUMNS = [
    "id", "title", "authors", "year", "journal", "database_name", "doi",
    "is_duplicate", "duplicate_of", "ta_decision", "ta_reason",
    "ft_decision", "ft_reason", "study_design", "population", "variables",
    "findings", "limitations", "notes",
]

SCHEMA_VERSION = 3

_V2_STATEMENTS = [
    """CREATE TABLE records__v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, authors TEXT, year TEXT, journal TEXT, database_name TEXT, doi TEXT,
        is_duplicate TEXT NOT NULL DEFAULT 'No' CHECK (is_duplicate IN ('Yes', 'No')),
        duplicate_of INTEGER,
        ta_decision TEXT DEFAULT '', ta_reason TEXT DEFAULT '',
        ft_decision TEXT DEFAULT '', ft_reason TEXT DEFAULT '',
        study_design TEXT DEFAULT '', population TEXT DEFAULT '', variables TEXT DEFAULT '',
        findings TEXT DEFAULT '', limitations TEXT DEFAULT '', notes TEXT DEFAULT ''
    )""",
    """INSERT INTO records__v2
        SELECT id, title, authors, year, journal, database_name, doi,
               CASE WHEN is_duplicate = 'Yes' THEN 'Yes' ELSE 'No' END,
               duplicate_of, ta_decision, ta_reason, ft_decision, ft_reason,
               study_design, population, variables, findings, limitations, notes
        FROM records""",
    "DROP TABLE records",
    "ALTER TABLE records__v2 RENAME TO records",
    "CREATE INDEX IF NOT EXISTS idx_records_is_duplicate ON records(is_duplicate)",
    "CREATE INDEX IF NOT EXISTS idx_records_ta_decision ON records(ta_decision)",
    "CREATE INDEX IF NOT EXISTS idx_records_ft_decision ON records(ft_decision)",
    "CREATE INDEX IF NOT EXISTS idx_records_duplicate_of ON records(duplicate_of)",
    "CREATE INDEX IF NOT EXISTS idx_records_doi ON records(doi)",
]

_V3_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS record_abstracts (
        record_id INTEGER PRIMARY KEY,
        abstract TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS decision_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
        record_id INTEGER,
        field TEXT NOT NULL,
        old_value TEXT,
        new_value TEXT,
        source TEXT NOT NULL DEFAULT 'ui'
    )""",
    "CREATE INDEX IF NOT EXISTS idx_decision_log_record ON decision_log(record_id)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
]

# Current shape, used for brand-new databases. Keep in sync with the
# migration statements above.
DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT, authors TEXT, year TEXT, journal TEXT, database_name TEXT, doi TEXT,
    is_duplicate TEXT NOT NULL DEFAULT 'No' CHECK (is_duplicate IN ('Yes', 'No')),
    duplicate_of INTEGER,
    ta_decision TEXT DEFAULT '', ta_reason TEXT DEFAULT '',
    ft_decision TEXT DEFAULT '', ft_reason TEXT DEFAULT '',
    study_design TEXT DEFAULT '', population TEXT DEFAULT '', variables TEXT DEFAULT '',
    findings TEXT DEFAULT '', limitations TEXT DEFAULT '', notes TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_records_is_duplicate ON records(is_duplicate);
CREATE INDEX IF NOT EXISTS idx_records_ta_decision ON records(ta_decision);
CREATE INDEX IF NOT EXISTS idx_records_ft_decision ON records(ft_decision);
CREATE INDEX IF NOT EXISTS idx_records_duplicate_of ON records(duplicate_of);
CREATE INDEX IF NOT EXISTS idx_records_doi ON records(doi);
""" + ";\n".join(_V3_STATEMENTS) + ";\n"

MIGRATIONS = {2: _V2_STATEMENTS, 3: _V3_STATEMENTS}

DECISION_FIELDS = ("ta_decision", "ta_reason", "ft_decision", "ft_reason")
ALLOWED_DECISIONS = {
    "ta_decision": ("", "Include", "Exclude", "Maybe"),
    "ft_decision": ("", "Include", "Exclude"),
}

# Stay well under SQLite's bound-variable limit (999 on old builds).
_CHUNK = 500


def _chunks(seq: Sequence, n: int = _CHUNK):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _validate_path(path: str) -> None:
    if path == ":memory:":
        return
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, "rb") as fh:
            if fh.read(16) != b"SQLite format 3\x00":
                raise ValueError("'{}' is not a valid SLR project database.".format(path))


def clean_dup_of(rid, dup_of):
    return None if dup_of == rid else dup_of


class RecordRepository:
    """Thin, transaction-safe wrapper around the `records` SQLite table."""

    def __init__(self, path: str):
        _validate_path(path)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.conn.execute("PRAGMA busy_timeout = 5000")
            # WAL lets a background worker thread run read queries (export,
            # tracker sync) concurrently with the GUI thread. No-op on an
            # in-memory database (silently kept as "memory").
            self.conn.execute("PRAGMA journal_mode = WAL")
            self.conn.execute("PRAGMA synchronous = NORMAL")
            self._ensure_schema()
            self._purge_orphan_abstracts()
        except Exception:
            self.conn.close()
            raise

    # -- schema ----------------------------------------------------------

    def _table_exists(self, name: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

    def _run_in_transaction(self, statement_groups: Iterable[Sequence[str]], version: int) -> None:
        """Run statements as ONE atomic unit and stamp user_version.

        sqlite3.Connection.executescript() COMMITs first and then runs in
        autocommit mode, so it cannot be rolled back; statements are
        therefore executed one by one inside an explicit transaction."""
        self.conn.commit()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for group in statement_groups:
                for stmt in group:
                    self.conn.execute(stmt)
            self.conn.execute("PRAGMA user_version = {}".format(int(version)))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _ensure_schema(self) -> None:
        with self._lock:
            version = self.conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    "Project database was created by a newer version of the app "
                    "(schema v{} > supported v{}).".format(version, SCHEMA_VERSION))

            has_records = self._table_exists("records")
            if not has_records:
                other = self.conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'").fetchone()[0]
                if other:
                    raise ValueError(
                        "'{}' is an SQLite database but not an SLR project "
                        "(no 'records' table).".format(self.path))
                stmts = [s.strip() for s in DB_SCHEMA.split(";") if s.strip()]
                self._run_in_transaction([stmts], SCHEMA_VERSION)
                return

            if version >= SCHEMA_VERSION:
                return

            self._backup_before_migration(version)
            steps = [MIGRATIONS[v] for v in range(max(version, 1) + 1, SCHEMA_VERSION + 1)
                     if v in MIGRATIONS]
            if version == 0:
                # Unversioned legacy file: apply every step from v2 on.
                steps = [MIGRATIONS[v] for v in sorted(MIGRATIONS)]
            self._run_in_transaction(steps, SCHEMA_VERSION)

    def _backup_before_migration(self, from_version: int) -> None:
        if self.path == ":memory:":
            return
        dest = "{}.pre-v{}-migration.bak".format(self.path, SCHEMA_VERSION)
        if os.path.exists(dest):
            return
        self.backup(dest)

    def backup(self, dest_path: str) -> None:
        """Write a consistent copy of the project database (online backup
        API: safe while the database is open and in WAL mode)."""
        with self._lock:
            self.conn.commit()
            target = sqlite3.connect(dest_path)
            try:
                self.conn.backup(target)
            finally:
                target.close()

    def _purge_orphan_abstracts(self) -> None:
        """Abstracts of deleted records are kept while the app runs so that
        Undo can restore them; they are dropped when a project is opened."""
        with self._lock, self.conn:
            self.conn.execute(
                "DELETE FROM record_abstracts WHERE record_id NOT IN (SELECT id FROM records)")

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # -- record numbering ----------------------------------------------------

    def positions_stale(self) -> bool:
        """Deprecated: exported IDs are the stable database ids (never
        renumbered), so exports can no longer drift out of sync after a
        delete. Kept so existing callers keep working."""
        return False

    def mark_positions_fresh(self) -> None:
        """Deprecated no-op, see positions_stale()."""

    # -- reads -----------------------------------------------------------

    def existing_titles(self) -> List[Tuple[int, str]]:
        with self._lock:
            return [(row[0], row[1]) for row in self.conn.execute("SELECT id, title FROM records")]

    def existing_dois(self) -> List[Tuple[int, str]]:
        """Return (id, normalized_doi) pairs for every record with a
        DOI-shaped value, matching ImportBatch(existing_dois=...)'s
        contract.

        When a record has been flagged as a duplicate of another record,
        its DOI is attributed to the ORIGINAL record's id instead of its
        own, so a brand-new import record with that DOI resolves straight
        to the surviving root record.
        """
        from .dedup import normalize_doi
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, doi, is_duplicate, duplicate_of FROM records").fetchall()
        doi_to_id: Dict[str, int] = {}
        for rid, doi, is_dup, dup_of in rows:
            nd = normalize_doi(doi)
            if nd is None:
                continue
            effective_id = clean_dup_of(rid, dup_of) if (is_dup == "Yes" and dup_of) else rid
            if effective_id is None:
                effective_id = rid
            doi_to_id.setdefault(nd, effective_id)
        return [(rid, doi) for doi, rid in doi_to_id.items()]

    def all_titles_map(self) -> Dict[int, str]:
        with self._lock:
            return {row[0]: row[1] for row in self.conn.execute("SELECT id, title FROM records")}

    def id_to_pos_map(self) -> Dict[int, int]:
        """Maps a record's database id to the number shown/exported as its
        'ID'. This is the identity mapping: the id is assigned once
        (AUTOINCREMENT, never reused) and never renumbered, so IDs written
        into earlier exports or trackers keep pointing at the same paper
        after records are deleted. (Previously this was the 1-based rank
        by id, which silently shifted after every delete.)"""
        with self._lock:
            return {row[0]: row[0] for row in self.conn.execute("SELECT id FROM records")}

    def get_abstract(self, rid: int) -> str:
        with self._lock:
            row = self.conn.execute(
                "SELECT abstract FROM record_abstracts WHERE record_id=?", (rid,)).fetchone()
        return row[0] if row else ""

    def abstract_count(self) -> int:
        with self._lock:
            return self.conn.execute(
                "SELECT COUNT(*) FROM record_abstracts WHERE record_id IN (SELECT id FROM records)"
            ).fetchone()[0]

    def fetch_all_for_export(self):
        with self._lock:
            return self.conn.execute(
                "SELECT id,title,authors,year,journal,database_name,doi,is_duplicate,duplicate_of FROM records"
                " ORDER BY id").fetchall()

    def fetch_screening(self):
        with self._lock:
            return self.conn.execute(
                "SELECT id,title,ta_decision,ta_reason,ft_decision,ft_reason FROM records "
                "WHERE is_duplicate!='Yes' ORDER BY id").fetchall()

    def fetch_extraction(self):
        with self._lock:
            return self.conn.execute(
                "SELECT id,title,study_design,population,variables,findings,limitations,notes "
                "FROM records WHERE is_duplicate!='Yes' AND ta_decision='Include' "
                "AND ft_decision='Include' ORDER BY id").fetchall()

    def fetch_for_tracker_sync(self):
        with self._lock:
            return self.conn.execute(
                "SELECT id, database_name, title, authors, year, journal, doi, is_duplicate, duplicate_of "
                "FROM records ORDER BY id").fetchall()

    def fetch_decisions_for_tracker_sync(self):
        with self._lock:
            return self.conn.execute(
                "SELECT id, is_duplicate, ta_decision, ta_reason, ft_decision, ft_reason "
                "FROM records ORDER BY id").fetchall()

    def fetch_decision_log(self, record_id: Optional[int] = None, limit: Optional[int] = None):
        q = "SELECT id, ts, record_id, field, old_value, new_value, source FROM decision_log"
        args: list = []
        if record_id is not None:
            q += " WHERE record_id=?"
            args.append(record_id)
        q += " ORDER BY id"
        if limit is not None:
            q += " LIMIT ?"
            args.append(int(limit))
        with self._lock:
            return self.conn.execute(q, args).fetchall()

    def all_records_df(self, exclude_duplicates: bool = False):
        import pandas as pd
        id_to_pos = self.id_to_pos_map()
        q = ("SELECT id,title,authors,year,journal,database_name,doi,is_duplicate,duplicate_of,"
             "ta_decision,ta_reason,ft_decision,ft_reason FROM records")
        if exclude_duplicates:
            q += " WHERE is_duplicate != 'Yes'"
        q += " ORDER BY id"
        cols = ["ID", "Title", "Authors", "Year", "Journal/Source", "Database", "DOI/Link",
                "Is Duplicate?", "Duplicate of ID", "TA Decision", "TA Reason", "FT Decision", "FT Reason"]
        with self._lock:
            rows = self.conn.execute(q).fetchall()
        df = pd.DataFrame([tuple(row) for row in rows], columns=cols)
        if not df.empty:
            df.insert(0, "Pos", df["ID"].map(id_to_pos))
            df["Duplicate of ID"] = df["Duplicate of ID"].map(lambda v: id_to_pos.get(v, "") if v else "")
        else:
            df.insert(0, "Pos", [])
        return df

    def duplicates_df(self):
        import pandas as pd
        id_to_pos = self.id_to_pos_map()
        titles = self.all_titles_map()
        with self._lock:
            rows = self.conn.execute(
                "SELECT id, title, authors, year, database_name, duplicate_of "
                "FROM records WHERE is_duplicate='Yes' ORDER BY id").fetchall()
        out = []
        for rid, title, authors, year, dbn, dup_of in rows:
            self_ref = (dup_of == rid)
            effective_dup_of = clean_dup_of(rid, dup_of)
            matched_pos = id_to_pos.get(effective_dup_of, "") if effective_dup_of else ""
            if self_ref:
                matched_title = "\u26a0 DATA ERROR: matched with itself (please report this)"
            elif effective_dup_of:
                matched_title = titles.get(effective_dup_of, "(original record not found)")
            else:
                matched_title = "(no match recorded)"
            out.append({
                "DBID": rid, "Pos": id_to_pos.get(rid, ""), "Title": title, "Authors": authors,
                "Year": year, "Database": dbn, "Matched With (Pos)": matched_pos,
                "Matched With (Title)": matched_title,
            })
        return pd.DataFrame(out, columns=["DBID", "Pos", "Title", "Authors", "Year", "Database",
                                           "Matched With (Pos)", "Matched With (Title)"])

    def prisma_counts(self) -> Dict[str, int]:
        """Flow counts that always reconcile.

        Every screening stage counts only records that survived
        de-duplication, and a record only counts at the full-text stage if
        it passed title/abstract screening. (The table view lets a user
        enter an FT decision on any row, and a duplicate flag can be set
        after a record was screened; counting those made 'screened' differ
        from 'excluded + assessed' and 'included' exceed 'assessed'.)"""
        with self._lock:
            row = self.conn.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN is_duplicate='Yes' THEN 1 ELSE 0 END) AS dups,
                    SUM(CASE WHEN is_duplicate!='Yes' AND ta_decision!='' THEN 1 ELSE 0 END) AS screened,
                    SUM(CASE WHEN is_duplicate!='Yes' AND ta_decision='Exclude' THEN 1 ELSE 0 END) AS ta_excluded,
                    SUM(CASE WHEN is_duplicate!='Yes' AND ta_decision='Include' THEN 1 ELSE 0 END) AS ft_assessed,
                    SUM(CASE WHEN is_duplicate!='Yes' AND ta_decision='Include'
                              AND ft_decision='Exclude' THEN 1 ELSE 0 END) AS ft_excluded,
                    SUM(CASE WHEN is_duplicate!='Yes' AND ta_decision='Include'
                              AND ft_decision='Include' THEN 1 ELSE 0 END) AS included
                FROM records
                """
            ).fetchone()
        total = row["total"] or 0
        dups = row["dups"] or 0
        return {
            "Records identified through database searching": total,
            "Duplicates removed": dups,
            "Records after duplicates removed": total - dups,
            "Records screened (title/abstract)": row["screened"] or 0,
            "Records excluded (title/abstract)": row["ta_excluded"] or 0,
            "Full-text articles assessed": row["ft_assessed"] or 0,
            "Full-text articles excluded (with reasons)": row["ft_excluded"] or 0,
            "Studies included in final synthesis": row["included"] or 0,
        }

    def database_counts(self) -> List[Tuple[str, int, int]]:
        """(database, records identified, duplicates removed) per source
        database -- PRISMA 2020 asks for records identified per source."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT COALESCE(NULLIF(database_name,''),'(unknown)'), COUNT(*), "
                "SUM(CASE WHEN is_duplicate='Yes' THEN 1 ELSE 0 END) "
                "FROM records GROUP BY 1 ORDER BY 1").fetchall()
        return [(r[0], r[1], r[2] or 0) for r in rows]

    def ft_exclusion_reasons(self) -> List[Tuple[str, int]]:
        """Full-text exclusions grouped by reason (PRISMA 2020 item 16b)."""
        with self._lock:
            rows = self.conn.execute(
                "SELECT COALESCE(NULLIF(TRIM(ft_reason),''),'(no reason given)'), COUNT(*) FROM records "
                "WHERE is_duplicate!='Yes' AND ta_decision='Include' AND ft_decision='Exclude' "
                "GROUP BY 1 ORDER BY 2 DESC, 1").fetchall()
        return [(r[0], r[1]) for r in rows]

    def consistency_warnings(self) -> List[str]:
        """Human-readable problems that make the PRISMA numbers unreliable."""
        with self._lock:
            row = self.conn.execute(
                """
                SELECT
                  SUM(CASE WHEN is_duplicate!='Yes' AND ft_decision!='' AND ta_decision!='Include'
                           THEN 1 ELSE 0 END),
                  SUM(CASE WHEN is_duplicate!='Yes' AND ta_decision='Maybe' THEN 1 ELSE 0 END),
                  SUM(CASE WHEN is_duplicate='Yes' AND (ta_decision!='' OR ft_decision!='')
                           THEN 1 ELSE 0 END),
                  SUM(CASE WHEN is_duplicate!='Yes' AND ta_decision='Include' AND ft_decision='Exclude'
                           AND TRIM(COALESCE(ft_reason,''))='' THEN 1 ELSE 0 END)
                FROM records
                """).fetchone()
        ft_no_ta, maybe, dup_dec, no_reason = [(v or 0) for v in row]
        out = []
        if ft_no_ta:
            out.append("{} record(s) have a full-text decision but were not included at "
                       "title/abstract; they are left out of the PRISMA counts.".format(ft_no_ta))
        if maybe:
            out.append("{} record(s) are still 'Maybe' at title/abstract; resolve them before "
                       "reporting.".format(maybe))
        if dup_dec:
            out.append("{} duplicate record(s) carry screening decisions that are ignored."
                       .format(dup_dec))
        if no_reason:
            out.append("{} full-text exclusion(s) have no reason (PRISMA 2020 requires one)."
                       .format(no_reason))
        return out

    # -- writes (each one is its own transaction) -------------------------

    def _log(self, rows: Sequence[Tuple], source: str = "ui") -> None:
        """Append (record_id, field, old, new) rows to the audit trail. Must
        be called inside the caller's transaction."""
        if rows:
            self.conn.executemany(
                "INSERT INTO decision_log(record_id, field, old_value, new_value, source) "
                "VALUES (?,?,?,?,?)", [(*r, source) for r in rows])

    @staticmethod
    def _check_decision(field: str, value) -> str:
        value = "" if value is None else str(value)
        allowed = ALLOWED_DECISIONS.get(field)
        if allowed is not None and value not in allowed:
            raise ValueError("Invalid value {!r} for {} (allowed: {}).".format(
                value, field, ", ".join(repr(a) for a in allowed)))
        return value

    def update_decision(self, rid: int, field: str, value) -> None:
        if field not in DECISION_FIELDS:
            raise ValueError("Unknown decision field: {!r}".format(field))
        value = self._check_decision(field, value)
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT {} FROM records WHERE id = ?".format(field), (rid,)).fetchone()
            if row is None:
                return
            old = row[0] or ""
            if old == value:
                return
            self.conn.execute(
                "UPDATE records SET {} = ? WHERE id = ?".format(field), (value, rid))
            self._log([(rid, field, old, value)])

    def _insert_abstract(self, rid: int, rec: dict) -> None:
        abstract = (rec.get("abstract") or "").strip()
        if abstract:
            self.conn.execute(
                "INSERT OR REPLACE INTO record_abstracts(record_id, abstract) VALUES (?,?)",
                (rid, abstract))

    def insert_record(self, rec: dict, is_dup: str = "No", dup_of: Optional[int] = None) -> int:
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO records(title,authors,year,journal,database_name,doi,is_duplicate,duplicate_of) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (rec["title"], rec["authors"], rec["year"], rec["journal"], rec["database"], rec["doi"],
                 is_dup, dup_of))
            new_id = cur.lastrowid
            if dup_of == new_id:
                self.conn.execute("UPDATE records SET duplicate_of=NULL WHERE id=?", (new_id,))
            self._insert_abstract(new_id, rec)
        return new_id

    def insert_records_bulk(self, items: Sequence[Tuple[dict, str, Optional[int]]]) -> List[int]:
        with self._lock, self.conn:
            cur = self.conn.cursor()
            inserted_ids = [None] * len(items)
            for idx, (rec, is_dup, dup_ref) in enumerate(items):
                cur.execute(
                    "INSERT INTO records(title,authors,year,journal,database_name,doi,is_duplicate,duplicate_of) "
                    "VALUES (?,?,?,?,?,?,?,NULL)",
                    (rec["title"], rec["authors"], rec["year"], rec["journal"], rec["database"], rec["doi"], is_dup))
                inserted_ids[idx] = cur.lastrowid

            abstracts = [(rid, (rec.get("abstract") or "").strip())
                         for rid, (rec, _, _) in zip(inserted_ids, items)]
            abstracts = [(rid, a) for rid, a in abstracts if a]
            if abstracts:
                cur.executemany(
                    "INSERT OR REPLACE INTO record_abstracts(record_id, abstract) VALUES (?,?)", abstracts)

            updates = []
            for idx, (rec, is_dup, dup_ref) in enumerate(items):
                if dup_ref is None:
                    continue
                if isinstance(dup_ref, int) and dup_ref < 0:
                    ref_idx = -dup_ref - 1
                    resolved_id = inserted_ids[ref_idx] if 0 <= ref_idx < len(inserted_ids) else None
                else:
                    resolved_id = dup_ref
                own_id = inserted_ids[idx]
                resolved_id = clean_dup_of(own_id, resolved_id)
                if resolved_id is not None:
                    updates.append((resolved_id, own_id))
            if updates:
                cur.executemany("UPDATE records SET duplicate_of=? WHERE id=?", updates)
            if items:
                n_dup = sum(1 for _, is_dup, _ in items if is_dup == "Yes")
                self._log([(None, "__import__", None,
                            "{} records ({} flagged duplicate)".format(len(items), n_dup))],
                          source="import")
        return inserted_ids

    def delete_record(self, rid: int) -> None:
        self.delete_records([rid])

    def delete_records(self, ids: Iterable[int]) -> None:
        ids = list(ids)
        if not ids:
            return
        with self._lock, self.conn:
            log_rows = []
            for chunk in _chunks(ids):
                ph = ",".join("?" * len(chunk))
                for rid, title in self.conn.execute(
                        "SELECT id, title FROM records WHERE id IN ({})".format(ph), chunk):
                    log_rows.append((rid, "__deleted__", title, None))
                self.conn.execute("DELETE FROM records WHERE id IN ({})".format(ph), chunk)
                self.conn.execute(
                    "UPDATE records SET is_duplicate='No', duplicate_of=NULL "
                    "WHERE duplicate_of IN ({})".format(ph), chunk)
            self._log(log_rows)

    @staticmethod
    def _blank(v) -> str:
        """None / NaN (pandas missing value) -> '', anything else -> str."""
        if v is None or (isinstance(v, float) and v != v):
            return ""
        return str(v)

    def update_decisions_from_df(self, df) -> None:
        params = []
        for _, row in df.iterrows():
            ta = self._check_decision("ta_decision", self._blank(row.get("TA Decision")))
            ft = self._check_decision("ft_decision", self._blank(row.get("FT Decision")))
            params.append((ta, self._blank(row.get("TA Reason")), ft,
                           self._blank(row.get("FT Reason")), int(row["ID"])))
        with self._lock, self.conn:
            log_rows = []
            for chunk in _chunks(params):
                ph = ",".join("?" * len(chunk))
                old = {r[0]: r for r in self.conn.execute(
                    "SELECT id, ta_decision, ta_reason, ft_decision, ft_reason FROM records "
                    "WHERE id IN ({})".format(ph), [p[4] for p in chunk])}
                for ta, ta_r, ft, ft_r, rid in chunk:
                    prev = old.get(rid)
                    if prev is None:
                        continue
                    for name, before, after in (("ta_decision", prev[1], ta), ("ta_reason", prev[2], ta_r),
                                                ("ft_decision", prev[3], ft), ("ft_reason", prev[4], ft_r)):
                        if (before or "") != after:
                            log_rows.append((rid, name, before or "", after))
            self.conn.executemany(
                "UPDATE records SET ta_decision=?, ta_reason=?, ft_decision=?, ft_reason=? WHERE id=?", params)
            self._log(log_rows, source="bulk")

    def set_duplicate_flag(self, rid: int, is_dup: str, dup_of: Optional[int] = None) -> None:
        dup_of = clean_dup_of(rid, dup_of)
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT is_duplicate, duplicate_of FROM records WHERE id=?", (rid,)).fetchone()
            self.conn.execute(
                "UPDATE records SET is_duplicate=?, duplicate_of=? WHERE id=?", (is_dup, dup_of, rid))
            if row is not None and (row[0], row[1]) != (is_dup, dup_of):
                self._log([(rid, "is_duplicate", row[0], is_dup),
                           (rid, "duplicate_of", row[1], dup_of)])

    def unmark_duplicate(self, rid: int) -> None:
        self.set_duplicate_flag(rid, "No", None)

    def snapshot(self) -> dict:
        cols = RECORD_COLUMNS
        with self._lock:
            rows = self.conn.execute("SELECT {} FROM records".format(",".join(cols))).fetchall()
        return {"cols": cols, "rows": [tuple(r) for r in rows]}

    def restore_snapshot(self, snap: dict) -> None:
        cols = list(snap.get("cols") or [])
        if not cols or set(cols) - set(RECORD_COLUMNS):
            raise ValueError("Snapshot has missing or unknown columns.")
        placeholders = ",".join(["?"] * len(cols))
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM records")
            if snap["rows"]:
                self.conn.executemany(
                    "INSERT INTO records ({}) VALUES ({})".format(",".join(cols), placeholders),
                    snap["rows"])
            # Never let the id counter move backwards: ids that were already
            # handed out (and may be printed in an export) must not be reused.
            maxid = self.conn.execute("SELECT MAX(id) FROM records").fetchone()[0] or 0
            cur = self.conn.execute(
                "UPDATE sqlite_sequence SET seq = MAX(seq, ?) WHERE name='records'", (maxid,))
            if cur.rowcount == 0:
                self.conn.execute(
                    "INSERT INTO sqlite_sequence(name, seq) VALUES ('records', ?)", (maxid,))
            self._log([(None, "__restore__", None, "{} records".format(len(snap["rows"])))],
                      source="undo")
