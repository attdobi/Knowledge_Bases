# financial_news operator runbook

This runbook is for the person who needs to install, validate, and operate the `financial_news` ingester without reverse-engineering the code.

## What this tool does

- Reads new rows from the Postgres `summaries` table
- Normalizes nested payloads into headlines, insights, timestamps, and local attachments
- Appends each summary to a day note in an Obsidian-friendly vault layout
- Stores the incremental cursor in `.state/ingest_state.json`

## Important safety note

If you do **not** pass `--output-root`, the tool writes into this package directory itself.

That is fine for local development and fixture generation, but it is **not** the recommended production mode because it will modify the checked-in sample vault snapshot in this repo.

For production or real operator use, point `--output-root` at the actual Obsidian vault location.

## First-time setup

From the `financial_news/` directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[dev]'
pytest -q
```

## Pre-flight checklist

Before running against a real database:

1. Confirm you are in the intended worktree/branch.
2. Activate the local virtualenv.
3. Confirm the destination vault path you want to write to.
4. Confirm your DSN source:
   - `--dsn 'postgresql://…'`
   - `FINANCIAL_NEWS_DSN`
   - `DATABASE_URL`
5. Decide whether you want:
   - incremental mode (normal run)
   - dry-run validation
   - a controlled backfill with `--since-id`
   - a full cursor reset with `--reset-state`

## Recommended operator commands

### 1) Validate parsing without writing files

```bash
financial-news-ingest \
  --dsn 'postgresql://username:password@db-host:5432/adobi' \
  --output-root /path/to/obsidian/vault/financial_news \
  --dry-run \
  --limit 5
```

Use this when validating connectivity, schema discovery, and parsing before touching markdown or state.

### 2) Normal incremental run

```bash
financial-news-ingest \
  --dsn 'postgresql://username:password@db-host:5432/adobi' \
  --output-root /path/to/obsidian/vault/financial_news
```

Behavior:

- reads `.state/ingest_state.json` if present
- fetches rows with `id > last_processed_id`
- appends markdown blocks for each row
- copies local attachments into `attachments/YYYY-MM-DD/`
- updates `.state/ingest_state.json` after a successful non-dry-run ingest

### 3) Controlled backfill from a known row id

```bash
financial-news-ingest \
  --dsn 'postgresql://username:password@db-host:5432/adobi' \
  --output-root /path/to/obsidian/vault/financial_news \
  --since-id 1200 \
  --limit 25
```

Use this when you want to replay a specific window without deleting the saved state file.

### 4) Reset the saved cursor and start fresh

```bash
financial-news-ingest \
  --dsn 'postgresql://username:password@db-host:5432/adobi' \
  --output-root /path/to/obsidian/vault/financial_news \
  --reset-state
```

Use this only when you intentionally want to discard the saved cursor.

## What to verify after a run

- `.state/ingest_state.json` contains the newest processed row id
- the expected day note exists under `YYYY-MM/YYYY-MM-DD_summary.md`
- appended blocks contain the HTML source id comment for traceability
- attachments referenced by local filesystem paths were copied into `attachments/YYYY-MM-DD/`
- rerunning the command does not duplicate already-processed rows unless you changed `--since-id` or reset state

## Troubleshooting

### `Unable to connect to Postgres`

- Verify the DSN is correct.
- If you omitted `--dsn`, remember the tool falls back to local `adobi` connection candidates.
- Confirm Postgres is reachable from the machine where you are running the command.

### `Table 'summaries' not found in current schema`

- Confirm you are connected to the expected database/schema.
- Confirm the table exists in the current schema visible to the selected user.

### Command appears to run but no markdown changes show up

- Check whether `--dry-run` was enabled.
- Check the current `.state/ingest_state.json` cursor.
- Check whether you accidentally wrote to the repo snapshot instead of the real vault, or vice versa.

### Attachments are missing from the note

- Only local filesystem paths are copied.
- Remote URLs are intentionally not downloaded.
- Missing local files are skipped with a warning.

## Repo hygiene expectations

- Keep `.venv/`, `.pytest_cache/`, coverage output, and `.state/` untracked.
- Do not commit ad-hoc local scratch files into the repo root or package root.
- Treat the checked-in markdown/attachment snapshot as reference content unless you are intentionally updating fixtures or sample output.
