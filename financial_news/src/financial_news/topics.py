from __future__ import annotations

from typing import Iterable

from financial_news.models import SummaryRecord

TOPIC_KEYWORDS: dict[str, tuple[str, ...]] = {
    "AI": (" ai ", "artificial intelligence", "openai", "anthropic", "llm", "large language model", "gpu"),
    "Tech": (
        "software",
        "cybersecurity",
        "cloud",
        "enterprise software",
        "semiconductor",
        "nasdaq",
        "tech",
        "servicenow",
        "palo alto",
    ),
    "Energy": ("energy", "oil", "brent", "wti", "refinery", "opec", "chevron", "exxon"),
    "Utilities": ("utilities", "utility", "power grid", "electric utility", "nextera", "duke energy"),
    "Trump": ("trump", "maga", "truth social"),
    "War and Geopolitics": ("war", "geopolitic", "iran", "israel", "ukraine", "russia", "china", "hormuz", "sanction", "tariff"),
    "Rates and Fed": (
        "fed",
        "powell",
        "rate",
        "rates",
        "yield",
        "yields",
        "treasury",
        "cpi",
        "pce",
        "inflation",
        "bond",
        "cut expectations",
        "hike",
    ),
    "Financials": ("financial", "bank", "banks", "credit", "mortgage", "fnma", "bx", "broker", "insurance"),
    "Retail and Consumer": (
        "retail",
        "consumer",
        "costco",
        "walmart",
        "target",
        "nike",
        "warehouse",
        "traffic",
        "discretionary",
        "barbie",
        "mattel",
    ),
    "Healthcare": ("healthcare", "medical", "biotech", "pharma", "fda", "hospital", "bsx"),
    "Telecom and Media": ("telecom", "wireless", "verizon", "netflix", "communication services", "streaming", "vz ", " t "),
}


def _normalized_search_text(parts: Iterable[str]) -> str:
    text = " ".join(part.strip().lower() for part in parts if part and part.strip())
    return f" {text} "


def infer_topics(record: SummaryRecord) -> list[str]:
    search_text = _normalized_search_text([*record.headlines, *record.insights, record.agent or ""])
    matches: list[str] = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(keyword in search_text for keyword in keywords):
            matches.append(topic)
    return matches


def topic_links_for_record(record: SummaryRecord) -> list[tuple[str, str]]:
    return [(f"Topics/{topic}", topic) for topic in infer_topics(record)]
