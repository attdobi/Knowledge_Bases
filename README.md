# Knowledge Base

This repo packages the `financial_news/` ingestion tool plus the tracked vault structure needed to operate it: source profiles, topic MOCs, weekly-note templates, and operator documentation.

## Where to start

- `financial_news/README.md` — package overview, install steps, CLI usage, and output format
- `financial_news/RUNBOOK.md` — operator handoff notes, safe run modes, recovery steps, and troubleshooting
- `financial_news/tests/` — unit tests covering parsing, Obsidian rendering, ingest orchestration, and state handling

## Repo hygiene notes

- Tracked repo assets are code, docs, tests, templates, source profiles, weekly notes, and topic MOCs.
- Imported day-note output and screenshot attachments should live outside the repo checkout and remain untracked.
- Local runtime state stays in `financial_news/.state/` and should remain untracked.
- Local virtualenvs, pytest caches, coverage output, and macOS/editor noise are ignored at both repo and package level.

## Quick validation

```bash
cd financial_news
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[dev]'
pytest -q
```
