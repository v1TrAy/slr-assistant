# SLR Assistant

Desktop tool for the record-handling part of a systematic literature review:
import database exports (CSV / RIS / XLSX), de-duplicate (DOI first, then fuzzy
title), screen on title/abstract and full text, and produce PRISMA counts, a
flow diagram and an Excel report / tracker sync.

## Setup

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt          # add requirements-dev.txt for tests
python app_pyside.py
```

## Tests

```
pip install -r requirements-dev.txt
python -m pytest
python tools/bench_dedup.py 1000 6000     # optional dedup benchmark
```

## Project files

A project is one `*.slrdb` SQLite file (schema v3). Opening an older project
migrates it in a single transaction and first writes
`<project>.slrdb.pre-v3-migration.bak`. Keep `*.slrdb*` out of version control
(see `.gitignore`).

## Layout

| Path | Role |
|---|---|
| `slr_core/` | UI-free logic: `parsers`, `dedup`, `staging`, `repository` (all SQL), `excel_io` |
| `slr_workers.py` | background QThreads (import, Excel jobs) |
| `slr_ui/` | Qt shell and pages |
| `tests/` | unit, integration and a headless UI smoke test |
| `AUDIT.md` | audit findings, roadmap, migration notes, QA checklist |
