from __future__ import annotations

from datetime import datetime, timezone

from financial_news.models import SummaryRecord
from financial_news.topics import infer_topics, topic_links_for_record


def make_record(*, headlines: list[str], insights: list[str], agent: str = "Agent CNBC") -> SummaryRecord:
    return SummaryRecord(
        row_id=7,
        row_timestamp=datetime(2026, 3, 30, 9, 30, tzinfo=timezone.utc),
        agent=agent,
        content_timestamp=None,
        headlines=headlines,
        insights=insights,
        attachments=[],
        raw_content={"ok": True},
    )


def test_infer_topics_matches_supported_category_mocs() -> None:
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

    assert infer_topics(record) == [
        "Tech",
        "Energy",
        "War and Geopolitics",
        "Rates and Fed",
        "Financials",
        "Retail and Consumer",
        "Healthcare",
        "Telecom and Media",
    ]


def test_topic_links_for_record_returns_obsidian_targets() -> None:
    record = make_record(
        headlines=["Trump tariff rhetoric collides with AI infrastructure trade"],
        insights=["Utilities could benefit if power demand from data centers keeps rising."],
    )

    assert topic_links_for_record(record) == [
        ("Topics/AI", "AI"),
        ("Topics/Utilities", "Utilities"),
        ("Topics/Trump", "Trump"),
        ("Topics/War and Geopolitics", "War and Geopolitics"),
    ]
