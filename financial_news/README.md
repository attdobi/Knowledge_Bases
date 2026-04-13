# financial_news

`financial_news` ingests financial-news summary rows from Postgres and exports them into an Obsidian-friendly vault.

This repo now tracks the **operator surface and curated vault structure** only:

- ingestion code
- operator docs / runbook
- tests
- source profiles under `Sources/`
- topic MOCs under `Topics/`
- weekly-note seeds under `Themes/`
- templates

It does **not** want checked-in imported screenshots or bulky generated day-note output.

For the step-by-step operator checklist, recovery notes, and cron example, see [RUNBOOK.md](RUNBOOK.md).

## What is tracked vs local-only

### Tracked in git

- Python package code under `src/`
- tests under `tests/`
- source profiles, topic MOCs, weekly notes, templates
- operator documentation

### Local-only / untracked

- imported daily notes such as `2026-04/2026-04-13_summary.md`
- copied screenshot attachments under `attachments/YYYY-MM-DD/`
- runtime state under `.state/ingest_state.json`
- local virtualenvs and caches

The checked-in repo should behave like an **operator control plane**, not the storage location for the imported vault payload.

## Vault structure

The intended vault structure is:

```text
financial_news/
├── README.md
├── RUNBOOK.md
├── scripts/
│   └── run_remote_import.sh
├── Sources/
│   ├── Home.md
│   ├── CNBC.md
│   └── ...
├── Topics/
│   ├── AI.md
│   ├── Tech.md
│   ├── Energy.md
│   ├── Utilities.md
│   ├── Trump.md
│   ├── War and Geopolitics.md
│   ├── Rates and Fed.md
│   ├── Financials.md
│   └── ...
├── Themes/
│   ├── Home.md
│   └── 2026-W14.md
├── Templates/
│   ├── Daily Summary Template.md
│   └── Weekly Theme Template.md
├── src/
└── tests/
```

Generated import output should live in the **real vault output path** outside the repo checkout, typically on the mounted share.

## Graph / note design

The graph is intentionally less hub-and-spoke than before.

### What changed

- generated daily imports no longer link back to generic `Home` notes
- imported summary blocks link directly to matching source profiles when recognized
- imported summary blocks link directly to topic MOCs when heuristic keyword matches fire
- weekly notes and topic MOCs remain manual / curated notes

### What is heuristic vs manual

**Heuristic / generated**
- summary-block topic MOC links
- summary-block source-profile links for recognized agent names

**Manual / curated**
- source profile notes in `Sources/`
- topic MOCs in `Topics/`
- weekly theme notes in `Themes/`
- any judgment about whether a narrative is durable or noise

## Install locally

From `financial_news/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[dev]'
```

After that, either form works:

```bash
financial-news-ingest --help
# or
python -m financial_news --help
```

## Run tests

```bash
pytest -q
```

## CLI options

```text
--dsn           Postgres DSN override
--limit         Limit number of rows fetched this run
--since-id      Ignore saved state and start from rows with id > this value
--output-root   Destination root for markdown + attachments
--state-path    Override the JSON state file location
--path-remap    Remap attachment paths before copying (repeatable FROM=TO)
--reset-state   Delete the saved state before running
--dry-run       Parse and log rows without writing markdown or state
--log-level     Python logging level (default: INFO)
```

## Real remote-operator workflow

For Attila's remote MacBook workflow, the recommended entrypoint is:

```bash
./financial_news/scripts/run_remote_import.sh
```

Run it from the repo root after setting the environment variables you actually need.

### Recommended mounted share root

The docs and script assume the mounted share root is:

```text
/Volumes/adobi/d-ai-trader
```

By default the script uses this output-root shape:

```text
/Volumes/adobi/d-ai-trader/Knowledge_Bases/financial_news
```

The script refuses to write imported output back into the repo checkout.

### Real DSN shape to prefer

For remote operation, prefer an explicit psycopg key/value DSN such as:

```bash
export FINANCIAL_NEWS_DSN='host=192.168.1.50 port=5432 dbname=adobi user=postgres password=REDACTED sslmode=disable connect_timeout=5'
```

That shape matches the package's current local-candidate style better than hand-wavy placeholder URIs.

A PostgreSQL URL also works, but the operator docs and examples use the key/value DSN above.

## Path remap concept

Rows can contain local attachment paths captured on another machine. The import job therefore supports a simple prefix remap contract:

```text
--path-remap FROM=TO
```

Example:

```bash
export FINANCIAL_NEWS_PATH_REMAP_FROM='/Users/attila/d-ai-trader'
export FINANCIAL_NEWS_PATH_REMAP_TO='/Volumes/adobi/d-ai-trader'
./financial_news/scripts/run_remote_import.sh --limit 10
```

Meaning:

- if a row references `/Users/attila/d-ai-trader/.../capture.png`
- the importer can rewrite it to `/Volumes/adobi/d-ai-trader/.../capture.png`
- then copy the file into the vault's `attachments/YYYY-MM-DD/` folder

### Implemented today

- one or more repeatable prefix remaps via `--path-remap FROM=TO`
- remap is applied before attachment existence checks
- copied attachments still land in the vault-local `attachments/YYYY-MM-DD/` folder

### Not implemented here

- regex remapping
- database-side path rewriting
- network file transfer or rsync

## Attachment behavior

### Implemented today

- local filesystem paths are copied into `attachments/YYYY-MM-DD/`
- remote URLs are not downloaded
- missing files are skipped with a warning
- filenames get a short hash suffix to avoid collisions
- if the exact destination file already exists, it is reused rather than recopied

### Compression / storage optimization behavior

Implemented behavior today is intentionally conservative:

- files are copied with `shutil.copy2`
- there is **no image recompression or format transcoding** in this repo
- the only storage optimization currently implemented is collision-safe naming and reusing an already-copied destination file

If X1 later adds richer compression/transcoding, that would be an extension beyond what this branch documents as implemented.

## Safe pruning behavior

There is **no automatic pruning job** in this branch.

That is deliberate.

- the importer appends notes and copies attachments
- it does not delete old note folders
- it does not delete old attachments
- `--reset-state` only clears the ingestion cursor; it does not prune vault content

Safe operator stance today: prune manually, with intent, outside the importer.

## Example remote run

```bash
cd /path/to/Knowledge_Bases
export FINANCIAL_NEWS_DSN='host=192.168.1.50 port=5432 dbname=adobi user=postgres password=REDACTED sslmode=disable connect_timeout=5'
export FINANCIAL_NEWS_OUTPUT_ROOT='/Volumes/adobi/d-ai-trader/Knowledge_Bases/financial_news'
export FINANCIAL_NEWS_PATH_REMAP_FROM='/Users/attila/d-ai-trader'
export FINANCIAL_NEWS_PATH_REMAP_TO='/Volumes/adobi/d-ai-trader'
./financial_news/scripts/run_remote_import.sh --log-level INFO
```

## Recommended daily cron shape

For a once-per-day weekday import on America/Los_Angeles time, a sensible default is shortly after the U.S. cash session finishes and the local network share should still be mounted:

```cron
CRON_TZ=America/Los_Angeles
20 14 * * 1-5 cd /path/to/Knowledge_Bases && FINANCIAL_NEWS_DSN='host=192.168.1.50 port=5432 dbname=adobi user=postgres password=REDACTED sslmode=disable connect_timeout=5' FINANCIAL_NEWS_OUTPUT_ROOT='/Volumes/adobi/d-ai-trader/Knowledge_Bases/financial_news' FINANCIAL_NEWS_PATH_REMAP_FROM='/Users/attila/d-ai-trader' FINANCIAL_NEWS_PATH_REMAP_TO='/Volumes/adobi/d-ai-trader' ./financial_news/scripts/run_remote_import.sh --log-level INFO >> ~/Library/Logs/financial_news_remote_import.log 2>&1
```

Why 14:20 PT?

- it is after the 13:00 PT market close
- it leaves time for same-day summaries/screenshots to finish landing
- it still runs during normal local-network uptime on the MacBook/share side

## Development / validation notes

The tests now cover:

- parser extraction / normalization behavior
- ingest state behavior
- operator-facing `--path-remap` parsing and handoff
- attachment remapping and copy behavior
- summary-block source-profile and topic-MOC links
- generated note headers staying free of generic `Home` graph attractors
