# -*- coding: utf-8 -*-
"""
slr_core/staging.py
---------------------
The import pipeline: stage parsed records in memory, match them against
the project AND against each other, let the user review and deselect
rows, then commit the survivors to the repository in one call.

Matching is DOI-first, in this priority order:

  1. DOI match -- if the record's DOI (after normalization) exactly
     matches a DOI already known (in the project or earlier in this
     batch), it is flagged as a duplicate with score 1.0 no matter how
     different the titles are. DOI identity is certain.
  2. Fuzzy title match -- only for records without a DOI match.
  3. DOI veto -- if the best title match carries a valid but DIFFERENT
     DOI, the two are provably different papers (a conference paper and
     its journal extension, say, share a title but never a DOI), so the
     record is staged as unique with match_source="doi_veto". Pass
     doi_veto=False to flag such pairs on title similarity alone.

`existing_dois` pairs (from RecordRepository.existing_dois) must already
be normalized; incoming record DOIs are normalized here.
"""

from dataclasses import dataclass
from typing import List, Optional

from .dedup import DuplicateIndex, normalize_doi, DEFAULT_DUP_THRESHOLD


@dataclass
class StagedRecord:
    index: int                    # position within the batch (0-based)
    rec: dict
    is_dup: str                   # "Yes" / "No"
    dup_ref: Optional[int]        # existing DB id (>0), -(i+1) batch ref, or None
    score: Optional[float]        # 1.0 for DOI matches; similarity for title matches
    matched_title: Optional[str]
    skipped: bool = False         # user unchecked this row in the import table
    match_source: str = ""        # "doi" | "title" | "doi_veto" | ""


class ImportBatch:
    """Stage records from parsed files and commit them to a repository."""

    def __init__(self, existing_titles=(), existing_dois=(),
                 threshold=DEFAULT_DUP_THRESHOLD, doi_veto=True):
        self.threshold = threshold
        self.doi_veto = doi_veto
        self._index = DuplicateIndex().build(existing_titles)
        # normalized DOI -> rid (real DB id, or -(i+1) for batch records)
        self._doi_to_rid = {d: rid for rid, d in existing_dois}
        # rids that carry any valid DOI (needed for the veto check)
        self._rids_with_doi = {rid for rid, _ in existing_dois}
        self.records: List[StagedRecord] = []

    def add_records(self, recs):
        """Stage parsed records; each is matched against the project's
        existing records AND the records staged so far in this batch."""
        for rec in recs:
            doi = normalize_doi(rec.get("doi"))
            dup_id, score, source, matched_title = None, None, "", None

            if doi is not None and doi in self._doi_to_rid:
                # 1) DOI-first: certain match, regardless of titles.
                dup_id = self._doi_to_rid[doi]
                score, source = 1.0, "doi"
            else:
                cand_id, cand_score = self._index.query(rec["title"], self.threshold)
                if cand_id is not None and doi is not None and self.doi_veto \
                        and cand_id in self._rids_with_doi:
                    # 3) Similar titles, provably different papers.
                    score, source = cand_score, "doi_veto"
                    matched_title = self._index.title_of(cand_id)
                elif cand_id is not None:
                    # 2) Fuzzy title match.
                    dup_id, score, source = cand_id, cand_score, "title"

            if dup_id is not None:
                matched_title = self._index.title_of(dup_id)

            staged = StagedRecord(
                index=len(self.records),
                rec=rec,
                is_dup="Yes" if dup_id is not None else "No",
                dup_ref=dup_id,
                score=score,
                matched_title=matched_title,
                match_source=source,
            )
            self.records.append(staged)

            # Batch-local placeholder id, same -(index+1) convention as
            # RecordRepository.insert_records_bulk.
            batch_ref = -(staged.index + 1)
            self._index.add(batch_ref, rec["title"])
            if doi is not None:
                self._doi_to_rid.setdefault(doi, batch_ref)
                self._rids_with_doi.add(batch_ref)

    @property
    def uncertain(self):
        """Flagged duplicates that were NOT certain matches (score < 1.0).
        DOI matches (always 1.0) and DOI-vetoed records are excluded --
        the review dialog is only about records actually flagged as
        duplicates."""
        return [(r.rec["title"], r.matched_title, r.score)
                for r in self.records
                if r.is_dup == "Yes" and r.score is not None and r.score < 1.0]

    def commit(self, store):
        """Insert all non-skipped records via store.insert_records_bulk().

        Duplicate references to SKIPPED batch records are dropped: the
        referencing record is still imported, but no longer marked as a
        duplicate, since its original is not being imported. Returns
        (n_inserted, n_flagged_duplicate).
        """
        kept = [r for r in self.records if not r.skipped]
        new_pos = {r.index: i for i, r in enumerate(kept)}
        items = []
        for r in kept:
            ref = r.dup_ref
            if ref is not None and ref < 0:
                target = -ref - 1
                ref = -(new_pos[target] + 1) if target in new_pos else None
            is_dup = r.is_dup if ref is not None else "No"
            items.append((r.rec, is_dup, ref))
        if items:
            store.insert_records_bulk(items)
        n_dup = sum(1 for _, is_dup, _ in items if is_dup == "Yes")
        return len(items), n_dup