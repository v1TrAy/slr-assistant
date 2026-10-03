# -*- coding: utf-8 -*-
"""
slr_core/dedup.py
------------------
Fuzzy title-matching and DOI normalization used to flag likely duplicate
records during import.

Candidate generation (why it changed)
-------------------------------------
The previous index blocked on three 4-character windows (prefix, suffix,
middle) of the space-stripped title and capped every bucket at 500 entries.
On real corpora those windows are dominated by very common strings
("thee", "tion", "ysis"), so (a) every query still ran hundreds of
difflib comparisons -- staging cost grew quadratically (1k records ~3 s,
6k records ~113 s measured) -- and (b) once a bucket was full, later
records silently stopped being indexed under that key, making recall
depend on import order.

The index now works like this:

  1. Exact path: titles whose normalized key is identical are found with a
     single dict lookup (O(1)). This is by far the most common kind of
     duplicate in multi-database imports.
  2. Fuzzy path: an inverted index of character 4-grams. A query looks up
     only its RAREST grams (the discriminating ones), counts how many of
     them each indexed title shares, and verifies only the top
     `max_candidates` titles with difflib. A typo, a leading article or a
     reworded tail leaves most grams intact, so recall does not depend on
     where the edit falls. Character n-grams also work for scripts without
     reliable word boundaries (CJK) and for Persian/Arabic.
  3. difflib verification uses real_quick_ratio()/quick_ratio() upper
     bounds to skip hopeless candidates, and autojunk=False so long
     titles (>= 200 chars) are not scored with the "popular element"
     heuristic that silently lowers similarity.

False-merge guard
-----------------
In a systematic review a falsely merged record disappears from screening
(a possibly relevant paper is lost), whereas a missed duplicate only costs
a second look. Titles that are near-identical but carry different numbers
("... Part 1" / "... Part 2", "cohort 2018" / "cohort 2019") are therefore
NOT matched unless the normalized titles are exactly equal. Disable with
numeric_guard=False.

DOI matching itself (DOI-first deduplication) lives in staging.py; this
module only provides the normalization that makes DOIs comparable.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from collections import Counter
from typing import Dict, Iterable, List, Optional, Tuple

DEFAULT_DUP_THRESHOLD = 0.90

# Length of the character shingles in the inverted index.
GRAM_LEN = 4
# How many of a query's rarest grams are used to find candidates.
RARE_GRAMS = 16
# Upper bound on candidates that get a full difflib comparison per query.
MAX_CANDIDATES = 40
# Soft cap on the total posting-list entries scanned per query (the 4
# rarest grams are always used, further grams only while under budget).
POSTING_BUDGET = 5000

# A DOI as used in the wild: "10." + registrant (4-9 digits) + "/" + suffix
# (Crossref's recommended pattern). Deliberately conservative: anything that
# doesn't match (publisher page URLs, PubMed IDs, ...) returns None from
# normalize_doi and the record simply falls back to title matching.
DOI_RE = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)
# Trailing citation punctuation that is never part of the DOI itself.
DOI_TRIM_CHARS = ".,;\"')<>"

_DIGITS_RE = re.compile(r"\d+")


# Arabic and Persian share a script but use different code points for the
# same letters, and Persian/Arabic databases mix them freely. NFKD does not
# unify them, so identical titles would otherwise never match.
_SCRIPT_FOLD = {
    0x064A: 0x06CC,  # Arabic yeh   -> Persian yeh
    0x0649: 0x06CC,  # alef maksura -> Persian yeh
    0x0643: 0x06A9,  # Arabic kaf   -> Persian keheh
}
_SCRIPT_FOLD.update({0x0660 + i: 0x30 + i for i in range(10)})  # Arabic-Indic digits -> ASCII
_SCRIPT_FOLD.update({0x06F0 + i: 0x30 + i for i in range(10)})  # Persian digits -> ASCII


def title_key(t: Optional[str]) -> str:
    """Normalize a title for comparison.

    - NFKD decomposition + casefold: accented and decomposed forms of the
      same letter compare equal, ligatures expand (fi-ligature == "fi"),
      and case-folding is consistent across scripts.
    - Arabic/Persian letter variants and digit forms are folded together.
    - Keeps Unicode word characters, so CJK/Arabic/Cyrillic titles keep
      their content (the old [^a-z0-9] filter blanked them out entirely).
      Combining marks (accents, Arabic harakat) are not word characters and
      are dropped.
    """
    s = unicodedata.normalize("NFKD", (t or "")).casefold().translate(_SCRIPT_FOLD)
    return re.sub(r"[^\w]+", "", s)


def normalize_doi(value) -> Optional[str]:
    """Extract a canonical bare DOI from a raw doi/link/url value.

    Returns the lowercase bare DOI ("10.registrant/suffix"), or None when
    the value contains no DOI (e.g. an IEEE document URL or a PubMed ID).
    """
    s = str(value or "").strip().lower()
    if "10." not in s:
        return None
    m = DOI_RE.search(s)
    if not m:
        return None
    doi = m.group(0).rstrip(DOI_TRIM_CHARS)
    if DOI_RE.fullmatch(doi):
        return doi
    return None


def _number_signature(key: str) -> Tuple[int, ...]:
    """The sequence of integers appearing in a normalized title key.

    int() accepts any Unicode decimal digits, so Arabic-Indic / Persian
    digits compare equal to ASCII digits."""
    out: List[int] = []
    for run in _DIGITS_RE.findall(key):
        try:
            out.append(int(run))
        except ValueError:  # pragma: no cover - \d guarantees decimal digits
            out.append(-1)
    return tuple(out)


def _grams(key: str, n: int = GRAM_LEN) -> set:
    if len(key) <= n:
        return {key}
    return {key[i:i + n] for i in range(len(key) - n + 1)}


class DuplicateIndex:
    """Inverted-index fuzzy matcher over record titles (see module docs)."""

    def __init__(self, max_candidates: int = MAX_CANDIDATES, rare_grams: int = RARE_GRAMS,
                 numeric_guard: bool = True):
        self.max_candidates = max_candidates
        self.rare_grams = rare_grams
        self.numeric_guard = numeric_guard
        self.titles: Dict[object, str] = {}      # rid -> original title
        self._keys: Dict[object, str] = {}       # rid -> normalized key
        self._exact: Dict[str, object] = {}      # normalized key -> first rid
        self._postings: Dict[str, List[object]] = {}

    def __len__(self) -> int:
        return len(self._keys)

    def add(self, rid, title: Optional[str]) -> None:
        k = title_key(title)
        if k:
            self._keys[rid] = k
            self._exact.setdefault(k, rid)
            for g in _grams(k):
                self._postings.setdefault(g, []).append(rid)
        self.titles[rid] = title

    def build(self, existing_titles: Iterable[Tuple[object, str]]) -> "DuplicateIndex":
        for rid, title in existing_titles:
            self.add(rid, title)
        return self

    def _candidates(self, nk: str) -> List[object]:
        present = []
        for g in _grams(nk):
            posting = self._postings.get(g)
            if posting:
                present.append((len(posting), g))
        if not present:
            return []
        present.sort()
        hits: Counter = Counter()
        scanned = 0
        for i, (size, g) in enumerate(present[: self.rare_grams]):
            if i >= 4 and scanned + size > POSTING_BUDGET:
                break
            hits.update(self._postings[g])
            scanned += size
        return [rid for rid, _ in hits.most_common(self.max_candidates)]

    def query(self, title: Optional[str], threshold: float = DEFAULT_DUP_THRESHOLD,
              exhaustive: bool = False) -> Tuple[Optional[object], float]:
        """Return (best_matching_rid, similarity) or (None, best_score_seen).

        The score of a non-match is only the best among candidates that
        were fully compared; hopeless candidates are skipped by the cheap
        upper-bound checks, so treat it as informational."""
        nk = title_key(title)
        if not nk:
            return None, 0.0

        exact = self._exact.get(nk)
        if exact is not None:
            return exact, 1.0

        candidates = list(self._keys) if exhaustive else self._candidates(nk)
        if not candidates:
            return None, 0.0

        sig = _number_signature(nk) if self.numeric_guard else None
        sm = difflib.SequenceMatcher(None, autojunk=False)
        sm.set_seq2(nk)  # seq2 is analysed once and reused for every candidate

        best_id, best_score = None, 0.0
        for rid in candidates:
            k = self._keys[rid]
            floor = max(threshold, best_score)
            sm.set_seq1(k)
            if sm.real_quick_ratio() < floor or sm.quick_ratio() < floor:
                continue
            score = sm.ratio()
            if score < threshold or score <= best_score:
                continue
            if sig is not None and _number_signature(k) != sig:
                continue
            best_id, best_score = rid, score
        if best_id is not None:
            return best_id, best_score
        return None, 0.0

    def title_of(self, rid) -> str:
        return self.titles.get(rid, "")


def find_fuzzy_duplicate(new_title: Optional[str], existing_titles: Iterable[Tuple[object, str]],
                         threshold: float = DEFAULT_DUP_THRESHOLD) -> Tuple[Optional[object], float]:
    idx = DuplicateIndex().build(existing_titles)
    return idx.query(new_title, threshold)
