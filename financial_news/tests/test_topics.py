from __future__ import annotations

from financial_news.topics import classify_topics, topic_slug


def test_classify_topics_detects_multiple_expected_categories() -> None:
    categories = classify_topics(
        "Agent CNBC",
        [
            "Nvidia rallies as AI spending lifts chip outlook",
            "Treasury yields slip after Fed speakers signal patience",
            "Oil advances on renewed Middle East tensions",
        ],
        ["Banks outperformed as investors rotated into financials."],
    )

    assert categories == ["Energy", "AI", "Tech", "War/Geopolitics", "Rates/Fed", "Financials"]


def test_topic_slug_uses_known_slugs_and_safe_fallback() -> None:
    assert topic_slug("Rates/Fed") == "rates-fed"
    assert topic_slug("Custom Topic") == "custom-topic"
