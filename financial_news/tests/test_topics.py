from __future__ import annotations

from datetime import datetime, timezone

from financial_news.models import SummaryRecord
from financial_news.topics import classify_topics, topic_links_for_record, topic_slug


def make_record(*, headlines: list[str], insights: list[str], agent: str = "Agent CNBC", categories: list[str] | None = None) -> SummaryRecord:
    return SummaryRecord(
        row_id=7,
        row_timestamp=datetime(2026, 3, 30, 9, 30, tzinfo=timezone.utc),
        agent=agent,
        content_timestamp=None,
        headlines=headlines,
        insights=insights,
        attachments=[],
        tickers=[],
        categories=categories or [],
        raw_content={"ok": True},
    )


def test_classify_topics_detects_multiple_expected_categories() -> None:
    categories = classify_topics(
        "Agent CNBC",
        [
            "Nvidia rallies as AI spending lifts chip outlook",
            "Treasury yields slip after Fed speakers signal patience",
            "Oil advances on renewed Middle East tensions",
        ],
        ["Banks outperformed as investors rotated into financials."],
        [],
    )

    assert categories == ["AI", "Tech", "Energy", "War and Geopolitics", "Rates and Fed", "Financials"]


def test_classify_topics_can_use_ticker_heuristics_without_text_matches() -> None:
    categories = classify_topics(
        "Agent Benzinga",
        [],
        [],
        ["NVDA", "JPM", "NEE", "MSTR"],
    )

    assert categories == ["AI", "Tech", "Utilities", "Financials", "Crypto"]


def test_topic_slug_uses_known_slugs_and_safe_fallback() -> None:
    assert topic_slug("Rates and Fed") == "rates-fed"
    assert topic_slug("Rates/Fed") == "rates-fed"
    assert topic_slug("Custom Topic") == "custom-topic"


def test_topic_links_for_record_prefers_assigned_categories() -> None:
    record = make_record(
        headlines=["Trump tariff rhetoric collides with AI infrastructure trade"],
        insights=["Utilities could benefit if power demand from data centers keeps rising."],
        categories=["AI", "Utilities", "Trump", "War and Geopolitics"],
    )

    assert topic_links_for_record(record) == [
        ("Topics/AI", "AI"),
        ("Topics/Utilities", "Utilities"),
        ("Topics/Trump", "Trump"),
        ("Topics/War and Geopolitics", "War and Geopolitics"),
    ]


def test_topic_links_for_record_can_fall_back_to_classification() -> None:
    record = make_record(
        headlines=[
            "Palo Alto and ServiceNow lead tech while Treasury yields ease",
            "Oil spikes on Iran/Hormuz risk as FNMA responds to Powell",
            "Costco gains on retail traffic story",
        ],
        insights=[
            "Fed-cut expectations help financials, while healthcare names like BSX remain defensive.",
            "Netflix supports communication-services momentum.",
        ],
    )

    assert topic_links_for_record(record) == [
        ("Topics/Tech", "Tech"),
        ("Topics/Energy", "Energy"),
        ("Topics/War and Geopolitics", "War and Geopolitics"),
        ("Topics/Rates and Fed", "Rates and Fed"),
        ("Topics/Financials", "Financials"),
        ("Topics/Retail and Consumer", "Retail and Consumer"),
        ("Topics/Healthcare", "Healthcare"),
        ("Topics/Telecom and Media", "Telecom and Media"),
    ]
