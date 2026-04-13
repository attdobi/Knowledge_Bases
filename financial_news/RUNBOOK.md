# financial_news operator runbook

This runbook is for the person operating the `financial_news` import from the repo without reverse-engineering the package.

## First principles

### What belongs in git

Keep these tracked:

- code
- docs
- tests
- templates
- source profiles under `Sources/`
- topic MOCs under `Topics/`
- weekly theme notes under `Themes/`

### What must stay out of git

Do **not** commit or push:

- imported day-note folders like `2026-04/`
- screenshot attachment folders under `attachments/`
- runtime state under `.state/`
- other bulky generated vault payload

The importer is meant to write into the real vault output path, not into the repo checkout.

## One-command repo entrypoint

Preferred operator entrypoint:

```bash
./financial_news/scripts/run_remote_import.sh
```

The script:

- runs from the repo
- uses `.venv/bin/python` if available
- defaults the vault output to `/Volumes/adobi/d-ai-trader/Knowledge_Bases/financial_news`
- refuses to write imported output into the repo checkout
- optionally forwards a path remap into the Python CLI

## First-time setup

From `financial_news/`:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install '.[dev]'
pytest -q
```

## Environment to set on the operator machine

### 1) DSN

Prefer a real psycopg key/value DSN:

```bash
export FINANCIAL_NEWS_DSN='host=192.168.1.50 port=5432 dbname=adobi user=postgres password=REDACTED sslmode=disable connect_timeout=5'
```

Implemented today:

- `--dsn` overrides environment
- `FINANCIAL_NEWS_DSN` is accepted
- `DATABASE_URL` still works as a fallback in the Python package

### 2) Output root

Recommended:

```bash
export FINANCIAL_NEWS_OUTPUT_ROOT='/Volumes/adobi/d-ai-trader/Knowledge_Bases/financial_news'
```

That path should be the real vault location, outside the repo checkout.

### 3) Path remap, if attachment paths were captured on another machine

Example:

```bash
export FINANCIAL_NEWS_PATH_REMAP_FROM='/Users/attila/d-ai-trader'
export FINANCIAL_NEWS_PATH_REMAP_TO='/Volumes/adobi/d-ai-trader'
```

Meaning: rewrite attachment paths from the source machine's local root to the mounted share root before checking whether the file exists.

## Pre-flight checklist

Before a real run:

1. Confirm the share is mounted at `/Volumes/adobi/d-ai-trader`.
2. Confirm you are on branch `feat/financial-news-followup-pass-x2` or the intended successor branch.
3. Activate the virtualenv if you are running Python commands manually.
4. Confirm `FINANCIAL_NEWS_DSN` is set to the real remote/local-network database.
5. Confirm `FINANCIAL_NEWS_OUTPUT_ROOT` points outside the repo.
6. Decide whether path remap is needed.
7. Decide whether this is a dry run, normal incremental run, controlled backfill, or state reset.

## Recommended commands

### 1) Smoke-test the operator wiring without writing files

```bash
./financial_news/scripts/run_remote_import.sh --dry-run --limit 5 --log-level INFO
```

Use this first when validating:

- DB connectivity
- schema discovery
- row parsing
- path-remap wiring

### 2) Normal incremental run

```bash
./financial_news/scripts/run_remote_import.sh --log-level INFO
```

Behavior:

- reads the saved cursor from `.state/ingest_state.json` under the real output root
- fetches rows with `id > last_processed_id`
- appends new markdown blocks into the real vault output path
- copies local attachments into `attachments/YYYY-MM-DD/`
- updates the saved cursor after a successful non-dry-run import

### 3) Controlled backfill from a known row id

```bash
./financial_news/scripts/run_remote_import.sh --since-id 1200 --limit 25 --log-level INFO
```

Use this when you want a narrow replay window without deleting the saved cursor first.

### 4) Reset cursor and start fresh

```bash
./financial_news/scripts/run_remote_import.sh --reset-state --log-level INFO
```

Important: this clears only the cursor. It does **not** prune old notes or attachments.

## Implemented vs optional behavior

### Implemented in this branch

- repo-native shell entrypoint
- path-remap support via `--path-remap FROM=TO`
- direct source-profile links for recognized agents
- heuristic topic-MOC links in imported summary blocks
- reduced generic `Home` links in generated notes
- gitignore rules to keep generated screenshot/day-note payload out of commits

### Explicitly not implemented here

- automatic pruning of old vault content
- image recompression/transcoding pipeline
- network transfer of missing files
- regex remap rules
- fully curated topic classification; the topic links are still keyword heuristics

## Attachment behavior

### Implemented today

- copy local files only
- do not download remote URLs
- skip missing files with warnings
- remap source prefixes before existence checks when `--path-remap` is supplied
- preserve metadata via `copy2`
- generate collision-safe attachment filenames with a short hash suffix

### Compression / optimization behavior today

- there is no lossy or lossless image recompression in this branch
- there is no PNG/JPEG transcoding in this branch
- the only storage optimization implemented is avoiding recopy when the exact destination file already exists

## Safe pruning stance

There is no automatic prune mode.

Safe operator stance:

- do not assume `--reset-state` cleans anything except the cursor
- if you want to remove stale attachments, do it manually and intentionally
- prune only after verifying the files are truly orphaned in the real vault

## What the new vault structure means operationally

- `Sources/` = manual source reference notes
- `Topics/` = manual topic MOCs that generated summaries may link into heuristically
- `Themes/` = manual weekly consolidation notes
- generated daily imports should stay lightweight and should not become graph hubs

If a heuristic topic link is wrong, edit the topic note or weekly note manually; do not treat the generated link set as ground truth.

## Recommended cron

For weekday daily operation on America/Los_Angeles time:

```cron
CRON_TZ=America/Los_Angeles
20 14 * * 1-5 cd /path/to/Knowledge_Bases && FINANCIAL_NEWS_DSN='host=192.168.1.50 port=5432 dbname=adobi user=postgres password=REDACTED sslmode=disable connect_timeout=5' FINANCIAL_NEWS_OUTPUT_ROOT='/Volumes/adobi/d-ai-trader/Knowledge_Bases/financial_news' FINANCIAL_NEWS_PATH_REMAP_FROM='/Users/attila/d-ai-trader' FINANCIAL_NEWS_PATH_REMAP_TO='/Volumes/adobi/d-ai-trader' ./financial_news/scripts/run_remote_import.sh --log-level INFO >> ~/Library/Logs/financial_news_remote_import.log 2>&1
```

Why this shape:

- weekday-only fits U.S. market rhythm
- 14:20 PT lands after the 13:00 PT market close
- the DB host and mounted share are assumed to be on the same local network, so this is a reasonable same-day consolidation window

## Troubleshooting

### The script refuses to run because output_root is inside the repo

That is intentional. Point `FINANCIAL_NEWS_OUTPUT_ROOT` at the real vault, not the git checkout.

### The script says `/Volumes/adobi` is not mounted

Mount the share first, or override `FINANCIAL_NEWS_OUTPUT_ROOT` to another real vault path.

### Postgres connection fails

- verify `FINANCIAL_NEWS_DSN`
- verify the DB host is reachable on the local network
- verify the selected role can see the `summaries` table

### Notes import but attachments are missing

- verify whether attachment paths in the rows need a remap
- verify `FINANCIAL_NEWS_PATH_REMAP_FROM` / `TO`
- remember remote URLs are intentionally not downloaded
- remember missing files are skipped, not retried by another transport layer

### Topic links look incomplete or noisy

That is expected sometimes. Topic MOC links are heuristic, not fully curated classification.
