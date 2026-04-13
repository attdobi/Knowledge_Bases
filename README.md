# Knowledge Base

This repo currently packages the `financial_news/` ingestion tool plus a checked-in sample Obsidian vault snapshot used to validate note structure, attachments, and operator workflows.

## Where to start

- `financial_news/README.md` — package overview, install steps, CLI usage, and output format
- `financial_news/RUNBOOK.md` — operator handoff notes, safe run modes, recovery steps, and troubleshooting
- `financial_news/tests/` — unit tests covering parsing, Obsidian rendering, ingest orchestration, and state handling

## Repo hygiene notes

- Sample day notes, templates, source pages, and attachments under `financial_news/` are tracked intentionally as reference output.
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
