from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Sequence

from financial_news.config import AppConfig
from financial_news.db import connect, discover_schema, fetch_summaries
from financial_news.obsidian import append_summary
from financial_news.state import StateStore

LOGGER = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest financial-news summaries from Postgres into Obsidian markdown files.")
    parser.add_argument("--dsn", help="Postgres DSN. Overrides FINANCIAL_NEWS_DSN/DATABASE_URL.")
    parser.add_argument("--limit", type=int, help="Maximum number of rows to ingest.")
    parser.add_argument("--since-id", type=int, help="Start from rows with id greater than this value.")
    parser.add_argument("--output-root", help="Override the Obsidian output root. Defaults to the package project root.")
    parser.add_argument("--state-path", help="Override the ingestion state file path.")
    parser.add_argument("--reset-state", action="store_true", help="Delete the stored state before ingesting.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and log rows without writing files or state.")
    parser.add_argument("--log-level", default="INFO", help="Python logging level (default: INFO).")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    config = AppConfig.load(dsn=args.dsn, output_root=args.output_root, state_path=args.state_path)
    state_store = StateStore(config.state_path)

    if args.reset_state:
        state_store.reset()
        LOGGER.info("Reset state file at %s", config.state_path)

    state = state_store.load()
    since_id = args.since_id if args.since_id is not None else state.get("last_processed_id")
    LOGGER.info("Starting ingestion with output_root=%s state_path=%s since_id=%s", config.output_root, config.state_path, since_id)

    with connect(config.dsn) as conn:
        schema = discover_schema(conn)
        LOGGER.info(
            "Discovered summaries schema: %s",
            json.dumps(
                {
                    "table": schema.table_name,
                    "id_column": schema.id_column,
                    "content_column": schema.content_column,
                    "timestamp_column": schema.timestamp_column,
                    "columns": schema.columns,
                },
                default=str,
            ),
        )
        records = fetch_summaries(conn, schema, since_id=since_id, limit=args.limit)

    if not records:
        LOGGER.info("No new summaries found.")
        return 0

    written_paths: list[Path] = []
    last_id = since_id
    for record in records:
        if args.dry_run:
            LOGGER.info(
                "Dry run row=%s agent=%s headlines=%s insights=%s attachments=%s",
                record.row_id,
                record.agent,
                len(record.headlines),
                len(record.insights),
                len(record.attachments),
            )
        else:
            written_paths.append(append_summary(config.output_root, record))
            LOGGER.info("Appended row %s to %s", record.row_id, written_paths[-1])
        last_id = record.row_id

    if not args.dry_run and last_id is not None:
        state_store.save({"last_processed_id": last_id})
        LOGGER.info("Updated state file %s with last_processed_id=%s", config.state_path, last_id)

    LOGGER.info("Processed %s summaries", len(records))
    if written_paths:
        unique_paths = sorted({path.as_posix() for path in written_paths})
        LOGGER.info("Touched markdown files: %s", unique_paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
