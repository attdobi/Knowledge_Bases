# financial_news

`financial_news` is a small ingestion package that reads financial-news summary rows from Postgres and exports them into an Obsidian-friendly Markdown vault layout.

For an operator-facing checklist and recovery guide, see [RUNBOOK.md](RUNBOOK.md).

> Important: if you run the CLI without `--output-root`, it writes into this package directory itself. That is convenient for local demo/testing, but production operators should point `--output-root` at the real Obsidian vault so they do not modify the checked-in sample snapshot in this repo.

It is designed for the workflow already in use on the Mac mini today:

- read rows from the `summaries` table in the local `adobi` Postgres database
- parse nested JSON/text payloads into normalized headlines, insights, timestamps, and attachments
- append each summary into a day-based Markdown note under a month folder
- copy referenced local image files into an `attachments/` folder and embed them with Obsidian `![[...]]` syntax
- track the last processed row id in a state file so repeated runs are incremental

## What the tool does

For each new summary row, the ingester will:

1. connect to Postgres
2. discover the `summaries` table schema
3. fetch rows in ascending `id` order
4. parse summary content into a `SummaryRecord`
5. append a new block into `YYYY-MM/YYYY-MM-DD_summary.md`
6. optionally copy local attachments into `attachments/YYYY-MM-DD/`
7. update `.state/ingest_state.json` with the last processed row id

The output is intentionally **append-only**. Existing summary blocks are not rewritten or deduplicated after the fact; each ingested row is appended once, and incremental behavior is controlled by the saved `last_processed_id`.

## Repository / package layout

```text
financial_news/
├── README.md
├── pyproject.toml
├── src/
│   └── financial_news/
│       ├── __init__.py
│       ├── __main__.py
│       ├── config.py      # config + default output/state paths
│       ├── db.py          # Postgres connection, schema discovery, row fetch
│       ├── ingest.py      # CLI entry point / orchestration
│       ├── models.py      # SummaryRecord / TableSchema dataclasses
│       ├── obsidian.py    # markdown rendering + attachment copying
│       ├── parser.py      # payload normalization / extraction helpers
│       └── state.py       # simple JSON-backed state store
└── tests/
    ├── conftest.py
    ├── test_ingest.py
    ├── test_obsidian.py
    ├── test_parser.py
    └── test_state.py
```

## Prerequisites

- Python 3.11+
- Postgres access to the target database
- The `summaries` table available in the current schema
- Obsidian vault destination (or any directory where you want Markdown output)

The package depends on:

- `psycopg[binary]` for Postgres connectivity
- `pytest` for local test runs via the `dev` extra

## Create a venv and install locally

From `financial_news/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[dev]'
```

After that, you can use either form:

```bash
financial-news-ingest --help
# or
python -m financial_news --help
```

## Run tests

```bash
pytest
```

## Running against the local `adobi` DB on the Mac mini now

If Postgres is local and accessible with the default fallback connection logic, this is enough:

```bash
financial-news-ingest
```

When no `--dsn` is supplied, the package tries local candidates such as:

- `dbname=adobi host=127.0.0.1 port=5432`
- `dbname=adobi host=127.0.0.1 port=5432 user=<current-user>`
- `dbname=adobi`
- `dbname=adobi user=<current-user>`

If you want to be explicit on the Mac mini, you can run:

```bash
financial-news-ingest --dsn 'dbname=adobi host=127.0.0.1 port=5432'
```

By default, output is written into this package directory itself, and state is written to:

```text
financial_news/.state/ingest_state.json
```

For real operator runs, prefer passing `--output-root /path/to/your/obsidian/vault/financial_news` so live ingestion writes into the actual vault instead of the checked-in sample snapshot in this repository.

## Configuring a production Mac laptop later

For a remote or production database, provide a DSN either with an environment variable or a CLI flag.

### Environment variable

```bash
export FINANCIAL_NEWS_DSN='postgresql://username:password@db-host:5432/adobi'
financial-news-ingest --output-root /path/to/your/obsidian/vault/financial_news
```

`DATABASE_URL` is also accepted as a fallback.

### CLI override

```bash
financial-news-ingest \
  --dsn 'postgresql://username:password@db-host:5432/adobi' \
  --output-root /path/to/your/obsidian/vault/financial_news
```

`--dsn` wins over environment variables when both are present.

## CLI options

```text
--dsn           Postgres DSN override
--limit         Limit number of rows fetched this run
--since-id      Ignore saved state and start from rows with id > this value
--output-root   Destination root for markdown + attachments
--state-path    Override the JSON state file location
--reset-state   Delete the saved state before running
--dry-run       Parse and log rows without writing markdown or state
--log-level     Python logging level (default: INFO)
```

## Output structure

Given an output root such as `/vault/financial_news`, the ingester creates a structure like:

```text
/vault/financial_news/
├── .state/
│   └── ingest_state.json
├── 2026-03/
│   └── 2026-03-30_summary.md
└── attachments/
    └── 2026-03-30/
        ├── Agent_CNBC_1_<hash>.png
        └── Agent_CNBC_2_<hash>.png
```

### Markdown note format

Each day file starts with a single title line:

```markdown
# 2026-03-30 summary
```

Each ingested row appends a block similar to:

```markdown
---
<!-- source-summary-id: 123 -->
## 2026-03-30T16:00:00+00:00 — Agent CNBC

- Source row id: `123`
- Agent: `Agent CNBC`

### Headlines
- Treasuries steady after CPI

### Insights
- Rate-cut pricing held roughly flat.

### Attachments
![[attachments/2026-03-30/Agent_CNBC_1_ab12cd34ef.png]]
```

## State file behavior

The state store is a simple JSON file containing the last successfully processed id, for example:

```json
{
  "last_processed_id": 123
}
```

Important behavior:

- if the state file is missing, ingestion starts from the beginning
- after a successful non-dry-run ingest, the last processed id is updated
- `--since-id` overrides the saved state for that run
- `--reset-state` deletes the saved state before ingest begins
- `--dry-run` does **not** write or update the state file

## Attachment handling notes

- only paths that look like local filesystem paths are copied
- remote URLs such as `https://...` are not downloaded
- missing files are skipped with a warning
- copied files are renamed with a short hash suffix derived from the source path so names remain stable and collision-resistant
- copied attachments are embedded with Obsidian wiki-image syntax: `![[relative/path.png]]`

## Obsidian notes

This package targets standard Obsidian-friendly Markdown conventions:

- day files grouped under month folders like `2026-03/`
- image embeds written as `![[attachments/...]]`
- append-only blocks separated with `---`
- source row ids preserved in HTML comments for traceability

## Sample command and expected result

Example local run against the Mac mini database:

```bash
financial-news-ingest \
  --dsn 'dbname=adobi host=127.0.0.1 port=5432' \
  --output-root /Users/sacsimoto/GitHub/Knowledge_Bases/financial_news
```

Expected result:

- new rows from `summaries` are fetched in ascending `id` order
- `2026-03/2026-03-30_summary.md` is created if needed, otherwise appended to
- any local image attachments referenced by those rows are copied into `attachments/2026-03-30/`
- `.state/ingest_state.json` is updated with the newest processed row id
- rerunning the command only ingests rows with larger ids, unless you pass `--since-id` or `--reset-state`

## Development notes

The test suite covers:

- parser extraction / normalization behavior
- Obsidian markdown rendering
- attachment copy behavior
- JSON state file round-trips
- CLI ingest orchestration with mocked database calls and temp directories

That keeps unit tests fast and deterministic without requiring a live Postgres instance.
