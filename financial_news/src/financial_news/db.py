from __future__ import annotations

import getpass
import logging
import re
from dataclasses import asdict
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from financial_news.models import DecisionRecord, SummaryRecord, TableSchema
from financial_news.parser import normalize_content, parse_decision_record, parse_summary_record

LOGGER = logging.getLogger(__name__)

CONTENT_COLUMN_CANDIDATES = ("content", "payload", "data", "summary", "body", "result")
TIMESTAMP_COLUMN_CANDIDATES = ("created_at", "timestamp", "generated_at", "published_at", "updated_at")
DECISION_TABLE_CANDIDATES = ("decisions", "decision_log", "trading_decisions", "ai_decisions")
DECISION_ACTION_COLUMN_CANDIDATES = ("action", "decision", "signal", "recommendation", "stance")
DECISION_TICKER_COLUMN_CANDIDATES = ("ticker", "symbol", "asset", "security")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_identifier(value: str, *, label: str = "identifier") -> str:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"Invalid SQL {label}: {value!r}")
    return value


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
    table_name = validate_identifier(table_name, label="table name")
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
    table_name = validate_identifier(schema.table_name, label="table name")
    id_column = validate_identifier(schema.id_column, label="id column")
    content_column = validate_identifier(schema.content_column, label="content column")
    columns = [id_column]
    column_names = [column["column_name"] for column in schema.columns]
    if "agent" in column_names:
        columns.append("agent")
    timestamp_column = validate_identifier(schema.timestamp_column, label="timestamp column") if schema.timestamp_column else None
    if timestamp_column:
        columns.append(timestamp_column)
    columns.append(content_column)

    where_clause = f"where {id_column} > %s" if since_id is not None else ""
    params: list[Any] = [since_id] if since_id is not None else []
    limit_clause = "limit %s" if limit is not None else ""
    if limit is not None:
        params.append(limit)

    query = f"""
        select {", ".join(columns)}
        from {table_name}
        {where_clause}
        order by {id_column} asc
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


def _row_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def fetch_summaries_between(
    conn: psycopg.Connection[Any],
    schema: TableSchema,
    *,
    start: datetime,
    end: datetime,
) -> list[SummaryRecord]:
    """Fetch summaries whose DB timestamp falls in [start, end)."""
    if not schema.timestamp_column:
        raise RuntimeError("Cannot fetch summaries by week: summaries table has no timestamp column")

    table_name = validate_identifier(schema.table_name, label="table name")
    id_column = validate_identifier(schema.id_column, label="id column")
    content_column = validate_identifier(schema.content_column, label="content column")
    timestamp_column = validate_identifier(schema.timestamp_column, label="timestamp column")
    column_names = [column["column_name"] for column in schema.columns]
    columns = [id_column]
    if "agent" in column_names:
        columns.append("agent")
    columns.extend([timestamp_column, content_column])

    query = f"""
        select {", ".join(columns)}
        from {table_name}
        where {timestamp_column} >= %s
          and {timestamp_column} < %s
        order by {timestamp_column} asc, {id_column} asc
    """
    with conn.cursor() as cur:
        cur.execute(query, (start, end))
        rows = list(cur.fetchall())

    records: list[SummaryRecord] = []
    for row in rows:
        raw_content = normalize_content(row[schema.content_column])
        row_timestamp = _row_datetime(row.get(schema.timestamp_column))
        records.append(
            parse_summary_record(
                row_id=int(row[schema.id_column]),
                row_timestamp=row_timestamp,
                content=raw_content,
                fallback_agent=row.get("agent"),
            )
        )
    return records


def table_exists(conn: psycopg.Connection[Any], table_name: str) -> bool:
    table_name = validate_identifier(table_name, label="table name")
    with conn.cursor() as cur:
        cur.execute(
            """
            select exists (
                select 1
                from information_schema.tables
                where table_schema = current_schema()
                  and table_name = %s
            )
            """,
            (table_name,),
        )
        row = cur.fetchone()
    if isinstance(row, dict):
        return bool(next(iter(row.values())))
    return bool(row[0]) if row else False


def discover_decision_schema(conn: psycopg.Connection[Any], table_name: str | None = None) -> TableSchema | None:
    candidates = [table_name] if table_name else list(DECISION_TABLE_CANDIDATES)
    for candidate in candidates:
        if not candidate:
            continue
        validated = validate_identifier(candidate, label="decision table name")
        if not table_exists(conn, validated):
            continue
        return discover_schema(conn, validated)
    return None


def fetch_decisions_between(
    conn: psycopg.Connection[Any],
    schema: TableSchema,
    *,
    start: datetime,
    end: datetime,
) -> list[DecisionRecord]:
    if not schema.timestamp_column:
        return []

    table_name = validate_identifier(schema.table_name, label="table name")
    id_column = validate_identifier(schema.id_column, label="id column")
    content_column = validate_identifier(schema.content_column, label="content column")
    timestamp_column = validate_identifier(schema.timestamp_column, label="timestamp column")
    column_names = [column["column_name"] for column in schema.columns]

    columns = [id_column, timestamp_column, content_column]
    if "agent" in column_names:
        columns.append("agent")
    action_column = next((name for name in DECISION_ACTION_COLUMN_CANDIDATES if name in column_names), None)
    ticker_column = next((name for name in DECISION_TICKER_COLUMN_CANDIDATES if name in column_names), None)
    if action_column:
        columns.append(validate_identifier(action_column, label="action column"))
    if ticker_column:
        columns.append(validate_identifier(ticker_column, label="ticker column"))

    query = f"""
        select {", ".join(columns)}
        from {table_name}
        where {timestamp_column} >= %s
          and {timestamp_column} < %s
        order by {timestamp_column} asc, {id_column} asc
    """
    with conn.cursor() as cur:
        cur.execute(query, (start, end))
        rows = list(cur.fetchall())

    decisions: list[DecisionRecord] = []
    for row in rows:
        raw_content = normalize_content(row[schema.content_column])
        decisions.append(
            parse_decision_record(
                row_id=int(row[schema.id_column]),
                row_timestamp=_row_datetime(row.get(schema.timestamp_column)),
                content=raw_content,
                fallback_agent=row.get("agent"),
                fallback_action=row.get(action_column) if action_column else None,
                fallback_ticker=row.get(ticker_column) if ticker_column else None,
            )
        )
    return decisions


def schema_summary(schema: TableSchema) -> list[dict[str, Any]]:
    return [asdict(schema) | {"columns": schema.columns}]
