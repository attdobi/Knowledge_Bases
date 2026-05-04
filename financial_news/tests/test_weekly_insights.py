from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import financial_news.ingest as ingest
from financial_news.db import validate_identifier
from financial_news.models import DecisionRecord, SummaryRecord, TableSchema
from financial_news.obsidian import WEEKLY_INSIGHTS_START, upsert_weekly_insights
from financial_news.parser import parse_decision_record


class DummyConnection:
    def __enter__(self) -> "DummyConnection":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def make_summary(row_id: int = 1) -> SummaryRecord:
    return SummaryRecord(
        row_id=row_id,
        row_timestamp=datetime(2026, 3, 30, 14, 5, tzinfo=timezone.utc),
        agent="Agent CNBC",
        content_timestamp=None,
        headlines=["Yields fall as megacap tech leads risk appetite"],
        insights=["Fed-cut pricing supported growth stocks while oil stayed the macro risk."],
        tickers=["NVDA", "MSFT"],
        categories=["Rates and Fed", "Tech", "Energy"],
        raw_content={"ok": True},
    )


def make_decision(row_id: int = 7) -> DecisionRecord:
    return DecisionRecord(
        row_id=row_id,
        row_timestamp=datetime(2026, 3, 31, 15, 0, tzinfo=timezone.utc),
        decision_timestamp=None,
        agent="Portfolio Decider",
        ticker="NVDA",
        action="hold",
        confidence="medium",
        rationale=["Momentum remained constructive, but rates risk argued against adding."],
        raw_content={"ok": True},
    )


def make_schema(table_name: str = "summaries") -> TableSchema:
    return TableSchema(
        table_name=table_name,
        columns=[
            {"column_name": "id", "data_type": "integer"},
            {"column_name": "created_at", "data_type": "timestamp with time zone"},
            {"column_name": "content", "data_type": "jsonb"},
        ],
        id_column="id",
        content_column="content",
        timestamp_column="created_at",
    )


def test_parse_week_key_returns_iso_week_bounds() -> None:
    week_key, start, end = ingest.parse_week_key("2026-W14")

    assert week_key == "2026-W14"
    assert start.isoformat() == "2026-03-30"
    assert end.isoformat() == "2026-04-06"


def test_validate_identifier_rejects_sql_injection_shapes() -> None:
    assert validate_identifier("summaries") == "summaries"
    assert validate_identifier("decision_log_2026") == "decision_log_2026"
    with pytest.raises(ValueError):
        validate_identifier("summaries; drop table summaries")
    with pytest.raises(ValueError):
        validate_identifier("public.summaries")


def test_parse_decision_record_extracts_action_ticker_and_rationale() -> None:
    record = parse_decision_record(
        row_id=44,
        row_timestamp=datetime(2026, 3, 31, tzinfo=timezone.utc),
        content={
            "agent": "Portfolio Decider",
            "symbol": "NVDA",
            "action": "trim",
            "confidence": "high",
            "rationale": ["Valuation risk rose after a crowded AI rally."],
        },
    )

    assert record.agent == "Portfolio Decider"
    assert record.ticker == "NVDA"
    assert record.action == "trim"
    assert record.confidence == "high"
    assert record.rationale == ["Valuation risk rose after a crowded AI rally."]


def test_upsert_weekly_insights_is_idempotent_and_digestible(tmp_path: Path) -> None:
    output_root = tmp_path / "vault"
    path = upsert_weekly_insights(
        output_root,
        week_key="2026-W14",
        start_date="2026-03-30",
        end_date="2026-04-05",
        summaries=[make_summary()],
        decisions=[make_decision()],
    )
    first = path.read_text(encoding="utf-8")

    upsert_weekly_insights(
        output_root,
        week_key="2026-W14",
        start_date="2026-03-30",
        end_date="2026-04-05",
        summaries=[make_summary(2)],
        decisions=[],
    )
    second = path.read_text(encoding="utf-8")

    assert path == output_root / "Themes" / "2026-W14.md"
    assert first.count(WEEKLY_INSIGHTS_START) == 1
    assert second.count(WEEKLY_INSIGHTS_START) == 1
    assert "### Executive takeaways" in second
    assert "**Theme concentration:**" in second
    assert "`NVDA`" in second
    assert "no decision rows were available" in second


def test_weekly_insights_cli_uses_db_summaries_and_skips_missing_decisions(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "vault"
    seen = {}

    monkeypatch.setattr(ingest, "connect", lambda dsn: DummyConnection())
    monkeypatch.setattr(ingest, "discover_schema", lambda conn, table_name="summaries": make_schema(table_name))

    def fake_fetch_summaries_between(conn, schema, *, start, end):
        seen["range"] = (start.isoformat(), end.isoformat())
        return [make_summary()]

    monkeypatch.setattr(ingest, "fetch_summaries_between", fake_fetch_summaries_between)
    monkeypatch.setattr(ingest, "discover_decision_schema", lambda conn, table_name=None: None)

    result = ingest.main(
        [
            "--weekly-insights",
            "--week",
            "2026-W14",
            "--output-root",
            str(output_root),
        ]
    )

    assert result == 0
    assert seen == {"range": ("2026-03-30T00:00:00+00:00", "2026-04-06T00:00:00+00:00")}
    text = (output_root / "Themes" / "2026-W14.md").read_text(encoding="utf-8")
    assert "Yields fall as megacap tech leads risk appetite" in text
    assert "no decision rows were available" in text


def test_weekly_insights_cli_dry_run_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    output_root = tmp_path / "vault"
    monkeypatch.setattr(ingest, "connect", lambda dsn: DummyConnection())
    monkeypatch.setattr(ingest, "discover_schema", lambda conn, table_name="summaries": make_schema(table_name))
    monkeypatch.setattr(ingest, "fetch_summaries_between", lambda conn, schema, *, start, end: [make_summary()])
    monkeypatch.setattr(ingest, "discover_decision_schema", lambda conn, table_name=None: None)

    result = ingest.main(
        [
            "--weekly-insights",
            "--week",
            "2026-W14",
            "--output-root",
            str(output_root),
            "--dry-run",
        ]
    )

    assert result == 0
    assert not output_root.exists()
