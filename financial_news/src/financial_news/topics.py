from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable


@dataclass(frozen=True, slots=True)
class TopicDefinition:
    name: str
    slug: str
    patterns: tuple[re.Pattern[str], ...]


TOPIC_DEFINITIONS: tuple[TopicDefinition, ...] = (
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
            )
        ),
    ),
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
            )
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
                r"\bnvidia\b",
                r"\bapple\b",
                r"\bmicrosoft\b",
                r"\bgoogle\b",
                r"\balphabet\b",
                r"\bmeta\b",
                r"\bamazon\b",
                r"\btesla\b",
            )
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
                r"\belectricity\b",
                r"\btransmission\b",
                r"\bdistribution\b",
            )
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
            )
        ),
    ),
    TopicDefinition(
        name="War/Geopolitics",
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
                r"\bsanction(?:s|ed)?\b",
                r"\bmissile(?:s)?\b",
                r"\bconflict\b",
                r"\bceasefire\b",
            )
        ),
    ),
    TopicDefinition(
        name="Rates/Fed",
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
                r"\bppi\b",
                r"\bpowell\b",
                r"\bcentral bank\b",
                r"\becb\b",
            )
        ),
    ),
    TopicDefinition(
        name="Financials",
        slug="financials",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bbank(?:s|ing)?\b",
                r"\blender(?:s)?\b",
                r"\binsurer(?:s)?\b",
                r"\bbroker(?:age)?\b",
                r"\basset manager(?:s)?\b",
                r"\bcredit\b",
                r"\bpayments?\b",
                r"\bvisa\b",
                r"\bmastercard\b",
                r"\bgoldman\b",
                r"\bjpmorgan\b",
                r"\bmorgan stanley\b",
                r"\bcitigroup\b",
                r"\bwells fargo\b",
            )
        ),
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
    ),
    TopicDefinition(
        name="Consumer",
        slug="consumer",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bconsumer(?:s)?\b",
                r"\bretail(?:er|ers)?\b",
                r"\be-commerce\b",
                r"\bconsumer spending\b",
                r"\btravel\b",
                r"\bairline(?:s)?\b",
            )
        ),
    ),
    TopicDefinition(
        name="Healthcare",
        slug="healthcare",
        patterns=tuple(
            re.compile(pattern, re.IGNORECASE)
            for pattern in (
                r"\bhealthcare\b",
                r"\bpharma\b",
                r"\bbiotech\b",
                r"\bdrug(?:s)?\b",
                r"\bmedtech\b",
                r"\bfda\b",
            )
        ),
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
    ),
)

TOPIC_BY_NAME = {topic.name: topic for topic in TOPIC_DEFINITIONS}


def classify_topics(agent: str | None, headlines: Iterable[str], insights: Iterable[str]) -> list[str]:
    haystack = "\n".join(part for part in [agent or "", *headlines, *insights] if part).strip()
    if not haystack:
        return []

    matched: list[str] = []
    for topic in TOPIC_DEFINITIONS:
        if any(pattern.search(haystack) for pattern in topic.patterns):
            matched.append(topic.name)
    return matched


def topic_slug(name: str) -> str:
    topic = TOPIC_BY_NAME.get(name)
    if topic:
        return topic.slug
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "uncategorized"
