from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from financial_news.models import SummaryRecord


@dataclass(frozen=True, slots=True)
class TopicDefinition:
    name: str
    slug: str
    patterns: tuple[re.Pattern[str], ...]
    tickers: frozenset[str] = frozenset()


TOPIC_DEFINITIONS: tuple[TopicDefinition, ...] = (
    TopicDefinition(
        name="AI",
        slug="ai",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bai\b",
                r"artificial intelligence",
                r"\bllm(?:s)?\b",
                r"\bgenerative ai\b",
                r"\bopenai\b",
                r"\banthropic\b",
                r"\binference\b",
                r"\bmodel training\b",
                r"\bgpu(?:s)?\b",
                r"data center(?:s)?",
            )
        ),
        tickers=frozenset(
            {
                "AI",
                "AMD",
                "AMZN",
                "ANET",
                "ARM",
                "AVGO",
                "GOOG",
                "GOOGL",
                "META",
                "MSFT",
                "MU",
                "NVDA",
                "ORCL",
                "PLTR",
                "SMCI",
                "TSM",
            }
        ),
    ),
    TopicDefinition(
        name="Tech",
        slug="tech",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\btech\b",
                r"\bsoftware\b",
                r"\bcloud\b",
                r"\bsemiconductor(?:s)?\b",
                r"\bchip(?:s|maker|makers)?\b",
                r"\bcybersecurity\b",
                r"\bservicenow\b",
                r"\bpalo alto\b",
                r"\bnvidia\b",
                r"\bapple\b",
                r"\bmicrosoft\b",
                r"\bgoogle\b",
                r"\balphabet\b",
                r"\bmeta\b",
                r"\bamazon\b",
                r"\bnasdaq\b",
            )
        ),
        tickers=frozenset(
            {
                "AAPL",
                "ADBE",
                "AMD",
                "AMZN",
                "AVGO",
                "CRM",
                "CRWD",
                "GOOG",
                "GOOGL",
                "INTC",
                "META",
                "MSFT",
                "NFLX",
                "NOW",
                "NVDA",
                "ORCL",
                "PANW",
                "QCOM",
                "SMCI",
                "TSLA",
                "TSM",
                "ZS",
            }
        ),
    ),
    TopicDefinition(
        name="Energy",
        slug="energy",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\benergy\b",
                r"\boil\b",
                r"\bcrude\b",
                r"\bbrent\b",
                r"\bwti\b",
                r"\bgas\b",
                r"\blng\b",
                r"\bopec\b",
                r"\brefiner(?:y|ies)?\b",
                r"\bsolar\b",
                r"\bwind\b",
                r"\bnuclear\b",
                r"\bpower demand\b",
                r"\bchevron\b",
                r"\bexxon\b",
            )
        ),
        tickers=frozenset(
            {
                "APA",
                "AR",
                "BP",
                "COP",
                "CVX",
                "DVN",
                "EOG",
                "EQT",
                "FANG",
                "HAL",
                "LNG",
                "MPC",
                "OXY",
                "PSX",
                "SHEL",
                "SLB",
                "TTE",
                "VLO",
                "XOM",
            }
        ),
    ),
    TopicDefinition(
        name="Utilities",
        slug="utilities",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\butilit(?:y|ies)\b",
                r"\bpower grid\b",
                r"\bgrid\b",
                r"\belectric(?:ity| utility)\b",
                r"\btransmission\b",
                r"\bdistribution\b",
                r"\bnextera\b",
                r"\bduke energy\b",
            )
        ),
        tickers=frozenset(
            {
                "AEP",
                "AWK",
                "D",
                "DUK",
                "ED",
                "EIX",
                "EXC",
                "NEE",
                "PEG",
                "SO",
                "SRE",
                "XEL",
            }
        ),
    ),
    TopicDefinition(
        name="Trump",
        slug="trump",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\btrump\b",
                r"donald trump",
                r"trump administration",
                r"trump campaign",
                r"\bmaga\b",
                r"truth social",
            )
        ),
    ),
    TopicDefinition(
        name="War and Geopolitics",
        slug="war-geopolitics",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bwar\b",
                r"\bgeopolitic(?:s|al)\b",
                r"\bukraine\b",
                r"\brussia\b",
                r"\bchina\b",
                r"\btaiwan\b",
                r"\bisrael\b",
                r"\biran\b",
                r"\bgaza\b",
                r"\bmiddle east\b",
                r"\bhormuz\b",
                r"\bsanction(?:s|ed)?\b",
                r"\bmissile(?:s)?\b",
                r"\bconflict\b",
                r"\bceasefire\b",
                r"\btariff(?:s)?\b",
            )
        ),
    ),
    TopicDefinition(
        name="Rates and Fed",
        slug="rates-fed",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bfed\b",
                r"\bfomc\b",
                r"\brate(?:s| cut| cuts| hike| hikes)?\b",
                r"\byield(?:s)?\b",
                r"\btreasur(?:y|ies)\b",
                r"\binflation\b",
                r"\bcpi\b",
                r"\bpce\b",
                r"\bppi\b",
                r"\bpowell\b",
                r"\bcentral bank\b",
                r"\becb\b",
                r"\bbond(?:s)?\b",
                r"cut expectations",
            )
        ),
        tickers=frozenset({"IEF", "KRE", "SHY", "TLT", "XLF"}),
    ),
    TopicDefinition(
        name="Financials",
        slug="financials",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bfinancial(?:s)?\b",
                r"\bbank(?:s|ing)?\b",
                r"\blender(?:s)?\b",
                r"\binsurer(?:s|ance)?\b",
                r"\bbroker(?:age)?\b",
                r"\basset manager(?:s)?\b",
                r"\bcredit\b",
                r"\bmortgage\b",
                r"\bpayments?\b",
                r"\bvisa\b",
                r"\bmastercard\b",
                r"\bgoldman\b",
                r"\bjpmorgan\b",
                r"\bmorgan stanley\b",
                r"\bcitigroup\b",
                r"\bwells fargo\b",
                r"\bfnma\b",
                r"\bbx\b",
            )
        ),
        tickers=frozenset(
            {
                "AXP",
                "BAC",
                "BEN",
                "BLK",
                "BX",
                "C",
                "CFG",
                "CME",
                "COF",
                "DFS",
                "FIS",
                "FISV",
                "GS",
                "ICE",
                "JPM",
                "KKR",
                "MA",
                "MS",
                "PYPL",
                "SCHW",
                "USB",
                "V",
                "WFC",
            }
        ),
    ),
    TopicDefinition(
        name="Retail and Consumer",
        slug="retail-consumer",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bconsumer(?:s)?\b",
                r"\bretail(?:er|ers)?\b",
                r"\be-commerce\b",
                r"\bconsumer spending\b",
                r"\btravel\b",
                r"\bairline(?:s)?\b",
                r"\bcostco\b",
                r"\bwalmart\b",
                r"\btarget\b",
                r"\bnike\b",
                r"\bwarehouse\b",
                r"\btraffic\b",
                r"\bdiscretionary\b",
                r"\bbarbie\b",
                r"\bmattel\b",
            )
        ),
        tickers=frozenset(
            {
                "AAL",
                "ABNB",
                "AMZN",
                "BKNG",
                "CCL",
                "COST",
                "DAL",
                "DIS",
                "LOW",
                "MCD",
                "NKE",
                "RCL",
                "SBUX",
                "TGT",
                "UAL",
                "WMT",
            }
        ),
    ),
    TopicDefinition(
        name="Healthcare",
        slug="healthcare",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bhealthcare\b",
                r"\bmedical\b",
                r"\bpharma\b",
                r"\bbiotech\b",
                r"\bdrug(?:s)?\b",
                r"\bmedtech\b",
                r"\bfda\b",
                r"\bhospital\b",
                r"\bbsx\b",
            )
        ),
        tickers=frozenset(
            {
                "ABBV",
                "ABT",
                "AMGN",
                "BMY",
                "BSX",
                "CI",
                "GILD",
                "HUM",
                "ISRG",
                "JNJ",
                "LLY",
                "MDT",
                "MRK",
                "NVO",
                "PFE",
                "REGN",
                "TMO",
                "UNH",
            }
        ),
    ),
    TopicDefinition(
        name="Telecom and Media",
        slug="telecom-media",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\btelecom\b",
                r"\bwireless\b",
                r"\bverizon\b",
                r"\bnetflix\b",
                r"communication services",
                r"\bstreaming\b",
                r"\bmedia\b",
            )
        ),
        tickers=frozenset({"CHTR", "CMCSA", "DIS", "NFLX", "PARA", "T", "TMUS", "VZ"}),
    ),
    TopicDefinition(
        name="Crypto",
        slug="crypto",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bcrypto\b",
                r"\bbitcoin\b",
                r"\bethereum\b",
                r"\bstablecoin(?:s)?\b",
                r"\btoken(?:s)?\b",
                r"\bblockchain\b",
            )
        ),
        tickers=frozenset({"BTC", "BTC-USD", "COIN", "ETH", "ETH-USD", "ETHA", "GBTC", "HOOD", "IBIT", "MARA", "MSTR", "RIOT"}),
    ),
    TopicDefinition(
        name="Industrials",
        slug="industrials",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bindustrial(?:s)?\b",
                r"\bmanufactur(?:er|ers|ing)\b",
                r"\baerospace\b",
                r"\bdefense\b",
                r"\bshipping\b",
                r"\blogistics\b",
            )
        ),
        tickers=frozenset(
            {
                "BA",
                "CAT",
                "CSX",
                "DE",
                "ETN",
                "FDX",
                "GD",
                "GE",
                "HON",
                "LMT",
                "MMM",
                "NOC",
                "NSC",
                "PH",
                "RTX",
                "UNP",
                "UPS",
            }
        ),
    ),
)

TOPIC_BY_NAME = {topic.name: topic for topic in TOPIC_DEFINITIONS}
TOPIC_ALIASES = {
    "Consumer": "Retail and Consumer",
    "Rates/Fed": "Rates and Fed",
    "War/Geopolitics": "War and Geopolitics",
}


def normalize_topic_name(name: str) -> str:
    return TOPIC_ALIASES.get(name, name)


def classify_topics(
    agent: str | None,
    headlines: Iterable[str],
    insights: Iterable[str],
    tickers: Iterable[str] = (),
) -> list[str]:
    haystack = "\n".join(part for part in [agent or "", *headlines, *insights] if part).strip()
    normalized_tickers = {ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()}
    if not haystack and not normalized_tickers:
        return []

    matched: list[str] = []
    for topic in TOPIC_DEFINITIONS:
        text_match = bool(haystack) and any(pattern.search(haystack) for pattern in topic.patterns)
        ticker_match = bool(topic.tickers.intersection(normalized_tickers))
        if text_match or ticker_match:
            matched.append(topic.name)
    return matched


def topic_slug(name: str) -> str:
    canonical_name = normalize_topic_name(name)
    topic = TOPIC_BY_NAME.get(canonical_name)
    if topic:
        return topic.slug
    slug = re.sub(r"[^a-z0-9]+", "-", canonical_name.strip().lower()).strip("-")
    return slug or "uncategorized"


def topic_links_for_record(record: SummaryRecord) -> list[tuple[str, str]]:
    categories = record.categories or classify_topics(record.agent, record.headlines, record.insights, record.tickers)
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for category in categories:
        canonical_name = normalize_topic_name(category)
        if canonical_name in seen:
            continue
        seen.add(canonical_name)
        links.append((f"Topics/{canonical_name}", canonical_name))
    return links
