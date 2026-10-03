# -*- coding: utf-8 -*-
"""
slr_core/parsers.py
--------------------
Pure, side-effect-free (besides logging) functions that turn raw exported
files (CSV / RIS / XLSX) from bibliographic databases into a common record
shape:
    {"title", "authors", "year", "journal", "doi", "abstract", "database"}

Every parse_* function accepts an optional `stats` dict. When given:
  * stats["skipped"]         -- rows/records dropped for lacking a title,
  * stats["encoding_guesses"] -- list of "<db>: decoded as <codec>" notes
                                 when a legacy (non-UTF) encoding had to be
                                 guessed, so the UI can warn the user.
Passing no `stats` (the default) preserves the original return signature.
"""

from __future__ import annotations

import codecs
import csv
import io
import logging
import os
import re
from typing import Dict, List, Optional, Sequence

from .dedup import normalize_doi

logger = logging.getLogger(__name__)

# Abstracts can exceed the csv module's 128 KiB default field limit.
csv.field_size_limit(10 * 1024 * 1024)

HEADER_ALIASES = {
    "title": ["title", "document title", "article title", "ti", "t1", "paper title", "chapter title"],
    "authors": ["authors", "author", "author(s)", "au", "author full names", "creator"],
    "year": ["year", "publication year", "pub year", "py", "date", "year published", "publication date"],
    "journal": ["journal/source", "source title", "journal", "so", "jo", "jf", "t2",
                "conference name", "book title", "publisher", "proceedings title",
                "container title", "publication title"],
    "doi": ["doi", "doi link", "do", "digital object identifier", "link", "url", "pdf link", "article url"],
    "abstract": ["abstract", "abstract note", "ab", "n2"],
}

# Matched against the lower-cased file stem. A hint matches when it is not
# directly adjacent to another letter, so "ieee2024", "scopus_export" and
# "wos-1" match, while "acme-search" does not match "acm".
DATABASE_HINTS = {"ieee": "IEEE Xplore", "acm": "ACM Digital Library", "scopus": "Scopus",
                  "wos": "Web of Science", "webofscience": "Web of Science",
                  "savedrecs": "Web of Science",  # Web of Science's default export name
                  "sciencedirect": "ScienceDirect", "science direct": "ScienceDirect",
                  "springer": "SpringerLink", "springerlink": "SpringerLink",
                  "pubmed": "PubMed", "medline": "MEDLINE", "embase": "Embase",
                  "cinahl": "CINAHL", "psycinfo": "PsycINFO", "cochrane": "Cochrane Library",
                  "proquest": "ProQuest", "ebsco": "EBSCOhost", "openalex": "OpenAlex",
                  "dimensions": "Dimensions", "google scholar": "Google Scholar",
                  "googlescholar": "Google Scholar"}

# IEEE Xplore's default CSV export name: export2026.09.18-08.33.53.csv
_IEEE_DEFAULT_NAME_RE = re.compile(r"^export\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2}\.\d{2}(?:\s*\(\d+\))?$")

RIS_TAG_MAP = {"TI": "title", "T1": "title", "AU": "authors", "A1": "authors",
               "PY": "year", "Y1": "year", "JO": "journal", "JF": "journal", "T2": "journal", "SO": "journal",
               "DO": "doi", "UR": "doi", "AB": "abstract", "N2": "abstract"}

YEAR_RE = re.compile(r"(?<!\d)(?:18|19|20)\d{2}(?!\d)")

SUPPORTED_EXTENSIONS = (".ris", ".xlsx", ".csv")

# Fraction of bytes >= 0x80 above which a non-UTF-8 file is assumed to be in
# a right-to-left Windows code page (Persian/Arabic text is nearly all
# high bytes; Western-European cp1252 text is typically a few percent).
_RTL_LEGACY_HIGH_BYTE_RATIO = 0.15


def _bump_skipped(stats: Optional[Dict[str, int]], n: int) -> None:
    if stats is not None and n:
        stats["skipped"] = stats.get("skipped", 0) + n


def guess_db_name(path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    stem = base.lower()
    if _IEEE_DEFAULT_NAME_RE.match(stem):
        return "IEEE Xplore"
    for k, v in DATABASE_HINTS.items():
        if re.search(r"(?<![^\W\d_])" + re.escape(k) + r"(?![^\W\d_])", stem):
            return v
    return base


def _decode(data: bytes, filename: str, encoding: Optional[str] = None,
            stats: Optional[Dict] = None) -> str:
    """Decode raw bytes into text.

    Order: explicit `encoding` -> BOM (UTF-8/16/32) -> strict UTF-8 ->
    legacy single-byte guess. The previous chain tried cp1252 right after
    UTF-8; cp1252 decodes almost any byte string without error, so Persian
    or Arabic files saved as Windows-1256 (and UTF-16 files lacking a
    recognised BOM path) were silently turned into mojibake. Now:

      * a UTF-16/32 BOM is honoured,
      * a file with a high share of >=0x80 bytes that is not valid UTF-8 is
        decoded as cp1256 (Persian/Arabic) -- and the guess is reported,
      * otherwise cp1252, then latin-1 (never fails).
    """
    if encoding:
        return data.decode(encoding)
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return data.decode("utf-32")
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass

    codec_order = ["cp1252", "latin-1"]
    if data:
        high = sum(1 for b in data[:200000] if b >= 0x80)
        if high / min(len(data), 200000) > _RTL_LEGACY_HIGH_BYTE_RATIO:
            codec_order.insert(0, "cp1256")
    for codec in codec_order:
        try:
            text = data.decode(codec)
        except UnicodeDecodeError:
            continue
        logger.warning("'%s' is not valid UTF-8; decoded as %s (guess).", filename, codec)
        if stats is not None:
            stats.setdefault("encoding_guesses", []).append("{}: decoded as {}".format(filename, codec))
        return text
    return data.decode("latin-1", errors="replace")  # pragma: no cover - latin-1 cannot fail


def _norm(h) -> str:
    return re.sub(r"\s+", " ", str(h).strip().lower())


def _extract_year(val: Optional[str]) -> Optional[str]:
    """Find a plausible 4-digit year (18xx/19xx/20xx) anywhere in the
    string, not just at position 0."""
    m = YEAR_RE.search(val or "")
    return m.group(0) if m else val


def _prefer_doi(current: str, new: str) -> str:
    """Pick between two candidate values for the same `doi` field.

    The doi alias list also covers generic link/url columns, and RIS maps
    both DO and UR onto `doi`. Taking whichever appears first meant a
    leading publisher URL shadowed the real DOI that followed it. A value
    that actually contains a DOI always wins over one that does not."""
    if not current:
        return new
    if new and normalize_doi(current) is None and normalize_doi(new) is not None:
        return new
    return current


def _map_headers(fields: Sequence[str]) -> Dict[str, str]:
    m = {}
    for f in fields:
        n = _norm(f)
        for std, aliases in HEADER_ALIASES.items():
            if n in aliases:
                m[f] = std
                break
    return m


def _unique_headers(raw: Sequence) -> List[str]:
    """Make header names unique and non-empty."""
    seen: Dict[str, int] = {}
    out = []
    for i, h in enumerate(raw):
        name = str(h).strip() if h is not None else ""
        if not name:
            name = "column_{}".format(i + 1)
        count = seen.get(name, 0)
        seen[name] = count + 1
        if count:
            name = "{} ({})".format(name, count + 1)
        out.append(name)
    return out


def _empty_record(dbname: str) -> dict:
    return {"title": "", "authors": "", "year": "", "journal": "", "doi": "",
            "abstract": "", "database": dbname}


def parse_dictrows(fieldnames: Sequence[str], rows, dbname: str,
                   stats: Optional[Dict[str, int]] = None) -> List[dict]:
    """Shared core for CSV/XLSX: turn an iterable of dict-like rows plus
    their header list into normalized record dicts. Rows with no
    recognizable title are dropped; the count of dropped rows is logged
    and, if `stats` is given, added to stats["skipped"]."""
    mapping = _map_headers(fieldnames)
    out = []
    skipped = 0
    for row in rows:
        rec = _empty_record(dbname)
        for orig, std in mapping.items():
            val = str(row.get(orig, "") or "").strip()
            if std == "year":
                val = _extract_year(val)
            if std == "doi":
                rec["doi"] = _prefer_doi(rec["doi"], val)
            elif not rec[std]:
                rec[std] = val
        if rec["title"]:
            out.append(rec)
        else:
            skipped += 1
    if skipped:
        logger.warning("Skipped %d row(s) with no recognizable title for database '%s'.", skipped, dbname)
    _bump_skipped(stats, skipped)
    return out


def _sniff_delimiter(text: str) -> str:
    """Pick the delimiter from the header line (outside quotes).

    European-locale Excel writes ';'-separated CSV; with the default ','
    every column header collapses into one unrecognised name and every row
    is reported as 'no title'."""
    header = []
    in_quotes = False
    for ch in text[:20000]:
        if ch == '"':
            in_quotes = not in_quotes
        elif ch in "\r\n" and not in_quotes:
            break
        header.append(ch if not in_quotes else " ")
    line = "".join(header)
    counts = {d: line.count(d) for d in (",", ";", "\t", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else ","


def parse_csv_bytes(file_obj, dbname: str, stats: Optional[Dict[str, int]] = None,
                    encoding: Optional[str] = None) -> List[dict]:
    text = file_obj.read()
    if isinstance(text, bytes):
        text = _decode(text, dbname, encoding=encoding, stats=stats)
    # io.StringIO (not splitlines) so quoted fields containing newlines
    # survive, as the csv module expects.
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=_sniff_delimiter(text))
    return parse_dictrows(reader.fieldnames or [], reader, dbname, stats=stats)


def parse_xlsx_bytes(file_obj, dbname: str, stats: Optional[Dict[str, int]] = None) -> List[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(file_obj, data_only=True, read_only=True)
    try:
        ws = wb.active
        rows_iter = ws.iter_rows(values_only=True)
        try:
            headers = _unique_headers(next(rows_iter))
        except StopIteration:
            return []
        dict_rows = [dict(zip(headers, r)) for r in rows_iter]
        return parse_dictrows(headers, dict_rows, dbname, stats=stats)
    finally:
        wb.close()


_RIS_LINE_RE = re.compile(r"^([A-Z][A-Z0-9])\s*-\s*(.*)$")


def parse_ris_bytes(file_obj, dbname: str, stats: Optional[Dict[str, int]] = None,
                    encoding: Optional[str] = None) -> List[dict]:
    text = file_obj.read()
    if isinstance(text, bytes):
        text = _decode(text, dbname, encoding=encoding, stats=stats)

    records, skipped = [], 0
    cur: dict = {}
    last_std = None  # field of the previous tag, for untagged continuation lines

    def flush():
        nonlocal skipped
        if cur.get("title"):
            cur["authors"] = "; ".join(cur.get("authors") or [])
            for key in ("year", "journal", "doi", "abstract"):
                cur.setdefault(key, "")
            records.append(dict(cur))
        elif cur:
            skipped += 1

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        m = _RIS_LINE_RE.match(line)
        if not m:
            # Some exports wrap long abstracts onto untagged lines.
            if cur and last_std == "abstract" and line.strip():
                cur["abstract"] = (cur.get("abstract", "") + " " + line.strip()).strip()
            continue
        tag, val = m.group(1), m.group(2).strip()
        if tag == "TY":
            cur = {"title": "", "authors": [], "year": "", "journal": "", "doi": "",
                   "abstract": "", "database": dbname}
            last_std = None
        elif tag == "ER":
            flush()
            cur = {}
            last_std = None
        elif tag in RIS_TAG_MAP:
            if not cur:
                # Tolerate exports that omit the leading TY line.
                cur = {"title": "", "authors": [], "year": "", "journal": "", "doi": "",
                       "abstract": "", "database": dbname}
            std = RIS_TAG_MAP[tag]
            last_std = std
            if std == "authors":
                cur.setdefault("authors", []).append(val)
            elif std == "year":
                cur["year"] = _extract_year(val)
            elif std == "doi":
                cur["doi"] = _prefer_doi(cur.get("doi", ""), val)
            elif not cur.get(std):
                cur[std] = val
        else:
            last_std = None

    # A truncated export whose last record never saw an "ER" line used to
    # be dropped silently; flush it now.
    flush()

    if skipped:
        logger.warning("Skipped %d RIS record(s) with no title for database '%s'.", skipped, dbname)
    _bump_skipped(stats, skipped)
    return records


def normalize_uploaded_file(name: str, file_obj, stats: Optional[Dict[str, int]] = None) -> List[dict]:
    """Dispatch to the right parser based on file extension."""
    dbname = guess_db_name(name)
    ext = os.path.splitext(name)[1].lower()
    if ext == ".ris":
        return parse_ris_bytes(file_obj, dbname, stats=stats)
    if ext == ".xlsx":
        return parse_xlsx_bytes(file_obj, dbname, stats=stats)
    if ext == ".csv":
        return parse_csv_bytes(file_obj, dbname, stats=stats)
    logger.warning("Unsupported file extension '%s' for '%s'; no records parsed.", ext, name)
    return []
