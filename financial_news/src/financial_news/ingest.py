from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Sequence

from financial_news.config import AppConfig, parse_path_remap
from financial_news.db import connect, discover_schema, fetch_summaries
from financial_news.obsidian import append_summary, prune_orphan_attachments
from financial_news.state import StateStore

LOGGER = logging.getLogger(__name__)


def parse_path_remaps(values: Sequence[str] | None) -> list[tuple[Path, Path]]:
    remaps: list[tuple[Path, Path]] = []
    for value in values or []:
        parsed = parse_path_remap(value)
        remaps.append((parsed.source, parsed.destination))
    return remaps


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ingest financial-news summaries from Postgres into Obsidian markdown files.")
    parser.add_argument("--dsn", help="Postgres DSN. Overrides FINANCIAL_NEWS_DSN/DATABASE_URL.")
    parser.add_argument("--limit", type=int, help="Maximum number of rows to ingest.")
    parser.add_argument("--since-id", type=int, help="Start from rows with id greater than this value.")
    parser.add_argument("--output-root", help="Override the Obsidian output root. Defaults to FINANCIAL_NEWS_OUTPUT_ROOT or a local .generated/vault directory.")
    parser.add_argument("--state-path", help="Override the ingestion state file path.")
    parser.add_argument(
        "--path-remap",
        action="append",
        default=[],
        metavar="FROM=TO",
        help="Repeatable attachment remap applied before local copy, e.g. /Users/adobi/d-ai-trader=/Volumes/adobi/d-ai-trader",
    )
    parser.add_argument(
        "--attachment-mode",
        choices=("preserve", "optimize"),
        help="Attachment copy strategy. optimize recompresses/resizes static images for local vault storage.",
    )
    parser.add_argument(
        "--attachment-format",
        choices=("webp", "jpeg", "png"),
        help="Image format to use when --attachment-mode=optimize (default: webp).",
    )
    parser.add_argument(
        "--attachment-quality",
        type=int,
        help="Quality for optimized image attachments (1-100, default: 82).",
    )
    parser.add_argument(
        "--attachment-max-dimension",
        type=int,
        help="Maximum width/height for optimized image attachments in pixels (default: 2200).",
    )
    parser.add_argument(
        "--prune-orphan-attachments",
        action="store_true",
        help="Opt-in cleanup: remove copied attachments under output_root/attachments that are no longer referenced by markdown notes.",
    )
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

    try:
        cli_path_remaps = parse_path_remaps(args.path_remap)
    except ValueError as exc:
        parser.error(str(exc))

    config = AppConfig.load(
        dsn=args.dsn,
        output_root=args.output_root,
        state_path=args.state_path,
        path_remaps=args.path_remap,
        attachment_mode=args.attachment_mode,
        attachment_format=args.attachment_format,
        attachment_quality=args.attachment_quality,
        attachment_max_dimension=args.attachment_max_dimension,
    )
    state_store = StateStore(config.state_path)

    if args.reset_state:
        state_store.reset()
        LOGGER.info("Reset state file at %s", config.state_path)

    state = state_store.load()
    since_id = args.since_id if args.since_id is not None else state.get("last_processed_id")
    LOGGER.info(
        "Starting ingestion with output_root=%s state_path=%s since_id=%s attachments=%s",
        config.output_root,
        config.state_path,
        since_id,
        {
            "mode": config.attachments.mode,
            "image_format": config.attachments.image_format,
            "image_quality": config.attachments.image_quality,
            "max_dimension": config.attachments.max_dimension,
            "path_remaps": [
                {"source": remap.source.as_posix(), "destination": remap.destination.as_posix()}
                for remap in config.attachments.path_remaps
            ],
            "cli_path_remaps": [
                {"source": source.as_posix(), "destination": destination.as_posix()}
                for source, destination in cli_path_remaps
            ],
        },
    )

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
        if args.prune_orphan_attachments and not args.dry_run:
            pruned = prune_orphan_attachments(config.output_root)
            LOGGER.info("Pruned %s orphan attachments", len(pruned))
        return 0

    written_paths: list[Path] = []
    for record in records:
        if args.dry_run:
            LOGGER.info(
                "Dry run row=%s agent=%s headlines=%s insights=%s attachments=%s categories=%s",
                record.row_id,
                record.agent,
                len(record.headlines),
                len(record.insights),
                len(record.attachments),
                record.categories,
            )
            continue

        written_paths.append(append_summary(config.output_root, record, config.attachments))
        LOGGER.info("Appended row %s to %s", record.row_id, written_paths[-1])
        state_store.save({"last_processed_id": record.row_id})
        LOGGER.info("Updated state file %s with last_processed_id=%s", config.state_path, record.row_id)

    if args.prune_orphan_attachments and not args.dry_run:
        pruned = prune_orphan_attachments(config.output_root)
        LOGGER.info("Pruned %s orphan attachments", len(pruned))
    LOGGER.info("Processed %s summaries", len(records))
    if written_paths:
        unique_paths = sorted({path.as_posix() for path in written_paths})
        LOGGER.info("Touched markdown files: %s", unique_paths)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
