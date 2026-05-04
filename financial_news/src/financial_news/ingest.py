from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Sequence

from financial_news.config import AppConfig, parse_path_remap
from financial_news.db import (
    connect,
    discover_decision_schema,
    discover_schema,
    fetch_decisions_between,
    fetch_summaries,
    fetch_summaries_between,
    validate_identifier,
)
from financial_news.obsidian import append_summary, prune_orphan_attachments, upsert_weekly_insights
from financial_news.state import StateStore

LOGGER = logging.getLogger(__name__)
WEEK_RE = re.compile(r"^(?P<year>\d{4})-W(?P<week>\d{2})$")


def parse_week_key(value: str) -> tuple[str, date, date]:
    match = WEEK_RE.fullmatch(value.strip())
    if not match:
        raise ValueError(f"Invalid ISO week {value!r}; expected YYYY-Www, e.g. 2026-W14")
    year = int(match.group("year"))
    week = int(match.group("week"))
    try:
        start = date.fromisocalendar(year, week, 1)
    except ValueError as exc:
        raise ValueError(f"Invalid ISO week {value!r}: {exc}") from exc
    end = start + timedelta(days=7)
    return f"{year}-W{week:02d}", start, end


def parse_date_range(start_date: str, end_date: str) -> tuple[str, date, date]:
    start = date.fromisoformat(start_date)
    inclusive_end = date.fromisoformat(end_date)
    if inclusive_end < start:
        raise ValueError("--end-date must be on or after --start-date")
    iso_year, iso_week, _ = start.isocalendar()
    return f"{iso_year}-W{iso_week:02d}", start, inclusive_end + timedelta(days=1)


def date_to_utc_datetime(value: date) -> datetime:
    return datetime.combine(value, time.min, tzinfo=timezone.utc)


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
    parser.add_argument(
        "--weekly-insights",
        action="store_true",
        help="Generate/update one weekly financial summary in Themes/YYYY-Www.md from DB summaries and decisions if available.",
    )
    parser.add_argument("--week", help="ISO week to summarize for --weekly-insights, e.g. 2026-W14. Defaults to the current UTC week.")
    parser.add_argument("--start-date", help="Inclusive YYYY-MM-DD start date for --weekly-insights. Use with --end-date.")
    parser.add_argument("--end-date", help="Inclusive YYYY-MM-DD end date for --weekly-insights. Use with --start-date.")
    parser.add_argument("--summaries-table", default="summaries", help="Summaries table name (strict SQL identifier, default: summaries).")
    parser.add_argument("--decisions-table", help="Optional decisions table name (strict SQL identifier). If omitted, common names are discovered.")
    parser.add_argument(
        "--skip-decisions-if-missing",
        action="store_true",
        help="Compatibility flag: decision data is optional by default and skipped when no decision table is found.",
    )
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
    if args.weekly_insights:
        if bool(args.start_date) != bool(args.end_date):
            parser.error("--start-date and --end-date must be supplied together")
        try:
            if args.start_date and args.end_date:
                week_key, start_day, end_day_exclusive = parse_date_range(args.start_date, args.end_date)
            else:
                if args.week:
                    week_key, start_day, end_day_exclusive = parse_week_key(args.week)
                else:
                    today = datetime.now(timezone.utc).date()
                    iso_year, iso_week, _ = today.isocalendar()
                    week_key, start_day, end_day_exclusive = parse_week_key(f"{iso_year}-W{iso_week:02d}")
            summaries_table = validate_identifier(args.summaries_table, label="summaries table name")
            decisions_table = validate_identifier(args.decisions_table, label="decisions table name") if args.decisions_table else None
        except ValueError as exc:
            parser.error(str(exc))

        start_dt = date_to_utc_datetime(start_day)
        end_dt = date_to_utc_datetime(end_day_exclusive)
        with connect(config.dsn) as conn:
            summary_schema = discover_schema(conn, summaries_table)
            summaries = fetch_summaries_between(conn, summary_schema, start=start_dt, end=end_dt)
            decision_schema = discover_decision_schema(conn, decisions_table)
            decisions = (
                fetch_decisions_between(conn, decision_schema, start=start_dt, end=end_dt)
                if decision_schema is not None
                else []
            )
        if args.dry_run:
            LOGGER.info(
                "Dry run weekly insights week=%s summaries=%s decisions=%s output_root=%s",
                week_key,
                len(summaries),
                len(decisions),
                config.output_root,
            )
            return 0
        output_path = upsert_weekly_insights(
            config.output_root,
            week_key=week_key,
            start_date=start_day.isoformat(),
            end_date=(end_day_exclusive - timedelta(days=1)).isoformat(),
            summaries=summaries,
            decisions=decisions,
        )
        LOGGER.info(
            "Updated weekly insights at %s from %s summaries and %s decisions",
            output_path,
            len(summaries),
            len(decisions),
        )
        return 0

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
