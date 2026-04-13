from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from financial_news.parser import normalize_content, parse_summary_record


def test_normalize_content_decodes_json_strings_and_preserves_plain_text() -> None:
    assert normalize_content('{"agent": "Agent CNBC"}') == {"agent": "Agent CNBC"}
    assert normalize_content("plain text") == "plain text"
    assert normalize_content(None) is None


def test_parse_summary_record_extracts_nested_fields_dedupes_and_classifies_topics() -> None:
    row_timestamp = datetime(2026, 3, 30, 7, 0, tzinfo=timezone.utc)
    content = {
        "assistant": "Agent CNBC",
        "generated_at": "2026-03-30T08:15:00+00:00",
        "payload": {
            "headlines": [
                {"title": "Stocks rally on easing yields"},
                {"headline": "Oil slips as dollar strengthens"},
                {"title": "Stocks rally on easing yields"},
            ],
            "analysis": [
                {"summary": "Fed pause odds are rising."},
                "Risk appetite improved through the session.",
                {"summary": "Fed pause odds are rising."},
            ],
            "tickers": [
                "TLT",
                {"symbol": "JPM"},
                {"value": "NVDA"},
                "TLT",
            ],
            "attachments": [
                "/tmp/chart-one.png",
                "./captures/chart-two.png",
                "https://example.com/chart.png",
                "chart-three.png",
            ],
        },
    }

    record = parse_summary_record(row_id=7, row_timestamp=row_timestamp, content=content)

    assert record.agent == "Agent CNBC"
    assert record.content_timestamp == datetime(2026, 3, 30, 8, 15, tzinfo=timezone.utc)
    assert record.effective_timestamp == datetime(2026, 3, 30, 8, 15, tzinfo=timezone.utc)
    assert record.headlines == [
        "Stocks rally on easing yields",
        "Oil slips as dollar strengthens",
    ]
    assert record.insights == [
        "Fed pause odds are rising.",
        "Risk appetite improved through the session.",
    ]
    assert record.attachments == [Path("/tmp/chart-one.png"), Path("./captures/chart-two.png")]
    assert record.tickers == ["TLT", "JPM", "NVDA"]
    assert record.categories == ["AI", "Tech", "Energy", "Rates and Fed", "Financials"]


def test_parse_summary_record_uses_fallback_agent_and_row_timestamp_when_needed() -> None:
    row_timestamp = datetime(2026, 3, 30, 9, 45, tzinfo=timezone.utc)

    record = parse_summary_record(
        row_id=8,
        row_timestamp=row_timestamp,
        content={"summary": "Markets were mixed into the close."},
        fallback_agent="Agent Fox Business",
    )

    assert record.agent == "Agent Fox Business"
    assert record.content_timestamp is None
    assert record.effective_timestamp == row_timestamp
    assert record.headlines == []
    assert record.insights == ["Markets were mixed into the close."]
    assert record.attachments == []
    assert record.tickers == []
    assert record.categories == []


def test_parse_summary_record_extracts_tickers_from_common_nested_symbol_fields() -> None:
    row_timestamp = datetime(2026, 3, 30, 11, 30, tzinfo=timezone.utc)
    content = {
        "assistant": "Agent Benzinga",
        "payload": {
            "items": [
                {
                    "title": "Chipmakers climb",
                    "symbols": "NVDA, AMD; AVGO",
                },
                {
                    "headline": "Utilities catch a bid",
                    "security": {"symbol": "NEE", "exchange": "NYSE"},
                },
                {
                    "summary": "Crypto proxies rally with bitcoin.",
                    "mentioned_tickers": ["COIN", {"ticker": "MSTR"}],
                },
            ]
        },
    }

    record = parse_summary_record(row_id=9, row_timestamp=row_timestamp, content=content)

    assert record.tickers == ["NVDA", "AMD", "AVGO", "NEE", "COIN", "MSTR"]
    assert record.categories == ["AI", "Tech", "Utilities", "Crypto"]
