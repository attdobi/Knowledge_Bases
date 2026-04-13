from __future__ import annotations

import getpass
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from financial_news.models import SummaryRecord, TableSchema
from financial_news.parser import normalize_content, parse_summary_record

LOGGER = logging.getLogger(__name__)

CONTENT_COLUMN_CANDIDATES = ("content", "payload", "data", "summary", "body", "result")
TIMESTAMP_COLUMN_CANDIDATES = ("created_at", "timestamp", "generated_at", "published_at", "updated_at")


def connection_candidates(explicit_dsn: str | None) -> list[str]:
    if explicit_dsn:
        return [explicit_dsn]

    user = getpass.getuser()
    candidates = [
        "dbname=adobi host=127.0.0.1 port=5432",
        f"dbname=adobi host=127.0.0.1 port=5432 user={user}",
        "dbname=adobi",
        f"dbname=adobi user={user}",
    ]
    seen: set[str] = set()
    return [candidate for candidate in candidates if not (candidate in seen or seen.add(candidate))]


def connect(dsn: str | None) -> psycopg.Connection[Any]:
    errors: list[str] = []
    for candidate in connection_candidates(dsn):
        try:
            LOGGER.debug("Trying Postgres connection with DSN: %s", candidate)
            return psycopg.connect(candidate, row_factory=dict_row)
        except Exception as exc:  # pragma: no cover - exercised in runtime validation
            errors.append(f"{candidate!r}: {exc}")
    joined = "\n".join(errors) if errors else "no connection candidates"
    raise RuntimeError(f"Unable to connect to Postgres. Tried:\n{joined}")


def discover_schema(conn: psycopg.Connection[Any], table_name: str = "summaries") -> TableSchema:
    query = """
        select
            column_name,
            data_type,
            udt_name,
            is_nullable
        from information_schema.columns
        where table_schema = current_schema()
          and table_name = %s
        order by ordinal_position
    """
    with conn.cursor() as cur:
        cur.execute(query, (table_name,))
        columns = list(cur.fetchall())

    if not columns:
        raise RuntimeError(f"Table {table_name!r} not found in current schema")

    column_names = [column["column_name"] for column in columns]
    if "id" not in column_names:
        raise RuntimeError(f"Table {table_name!r} is missing required id column")

    content_column = next((name for name in CONTENT_COLUMN_CANDIDATES if name in column_names), None)
    if not content_column:
        jsonish = [
            column["column_name"]
            for column in columns
            if column["data_type"] in {"json", "jsonb", "text", "character varying"}
        ]
        if not jsonish:
            raise RuntimeError(f"Could not identify JSON/text content column on {table_name!r}")
        content_column = jsonish[0]

    timestamp_column = next((name for name in TIMESTAMP_COLUMN_CANDIDATES if name in column_names), None)
    return TableSchema(
        table_name=table_name,
        columns=columns,
        id_column="id",
        content_column=content_column,
        timestamp_column=timestamp_column,
    )


def fetch_summaries(
    conn: psycopg.Connection[Any],
    schema: TableSchema,
    since_id: int | None = None,
    limit: int | None = None,
) -> list[SummaryRecord]:
    columns = [schema.id_column]
    if "agent" in [column["column_name"] for column in schema.columns]:
        columns.append("agent")
    if schema.timestamp_column:
        columns.append(schema.timestamp_column)
    columns.append(schema.content_column)

    where_clause = "where id > %s" if since_id is not None else ""
    params: list[Any] = [since_id] if since_id is not None else []
    limit_clause = "limit %s" if limit is not None else ""
    if limit is not None:
        params.append(limit)

    query = f"""
        select {", ".join(columns)}
        from {schema.table_name}
        {where_clause}
        order by {schema.id_column} asc
        {limit_clause}
    """
    LOGGER.debug("Fetching summaries with query=%s params=%s", query, params)

    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = list(cur.fetchall())

    records: list[SummaryRecord] = []
    for row in rows:
        raw_content = normalize_content(row[schema.content_column])
        row_timestamp = row.get(schema.timestamp_column) if schema.timestamp_column else None
        if isinstance(row_timestamp, str):
            try:
                row_timestamp = datetime.fromisoformat(row_timestamp.replace("Z", "+00:00"))
            except ValueError:
                row_timestamp = None
        record = parse_summary_record(
            row_id=int(row[schema.id_column]),
            row_timestamp=row_timestamp,
            content=raw_content,
            fallback_agent=row.get("agent"),
        )
        records.append(record)
    return records


def schema_summary(schema: TableSchema) -> list[dict[str, Any]]:
    return [asdict(schema) | {"columns": schema.columns}]
